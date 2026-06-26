#!/usr/bin/env python3
"""
DUAL-CLIENT TELEGRAM BOT - v3
✅ Bot = ONLY translation messages in groups
✅ Personal account = Everything else (all DMs + all approved replies in groups)
✅ StringSession — no SQLite .session file conflict with app.py
"""

import asyncio
import os
import sqlite3
import time
import secrets
import json
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.custom import Button
from telethon.sessions import StringSession
from telethon.tl.types import Message, MessageService
from openai import OpenAI
from bot.ai.group_aware_handler import GroupAwareMessageHandler
from bot.translation.translator_openai import OpenAITranslator
from bot.handlers.special_group_setup import get_special_group_config, init_special_groups_table, add_special_group, remove_special_group
from bot.handlers.bot_forwarder_handler import register_bot_forwarder, BOT_DEST_MAP

# ── Phase 1: Smart Pre-Filter + Classifier (token optimization) ──
from bot.ai.message_prefilter import MessagePreFilter
from bot.ai.question_classifier import QuestionClassifier

# ── Phase 2: Telegram-based Approval System ──
from bot.approval.approval_handler import ApprovalHandler

# ── Phase 3: Dynamic Handler (CRM/Drive data) ──
from bot.integrations.dynamic_handler import DynamicHandler

# ── Phase 4: Knowledge Base ──
from bot.knowledge.knowledge_base import KnowledgeBase

# ── Phase 5: Daily Report ──
from bot.reports.daily_report import DailyReport

# ── Phase 6: Lead Source Tracking ──
from bot.integrations.lead_source_tracker import LeadSourceTracker

# ── Phase 7: SOP Manager ──
from bot.knowledge.sop_manager import SOPManager

# ── Phase 8: Link & Info Extraction (monitored dev groups) ──
from bot.handlers.link_extractor import LinkExtractor
from bot.handlers.link_query_handler import LinkQueryHandler

try:
    from bot.config import (
        OPENAI_API_KEY, BUSINESS_INFO, AI_SETTINGS, DASHBOARD_URL, ENABLE_AUTO_REPLY,
        YOUR_API_ID, YOUR_API_HASH, YOUR_PHONE, YOUR_LANGUAGE,
        BOT_TOKEN, BOT_USERNAME, TRANSLATION_SETTINGS,
        SESSION_STRING, BAUDOKU_GROUP_ID,
    )
except ImportError:
    print("❌ Error: config.py not found!")
    exit(1)

# ===== GLOBAL VARIABLES =====
YOUR_USER_ID = None
BOT_USER_ID  = None
DB = "bot_data.db"
# Initialize centralized DB writer
from bot.core.db_writer import db_writer as _dbw
_dbw.init(DB)

_processed_translation_events = set()
_processed_incoming = set()  # Separate dedup for incoming messages (prevents double translation)
_processed_incoming_order = []  # FIFO order for bounded cleanup
_processed_events_lock = None
_topic_name_cache = {}
tts_manager = None  # TTS Manager — set in main(), used by outgoing worker for 🔊 buttons

user_client = TelegramClient(
    StringSession(SESSION_STRING),
    YOUR_API_ID,
    YOUR_API_HASH,
    connection_retries=None,      # retry forever (never give up reconnecting)
    retry_delay=2,                # wait 2s between retries
    auto_reconnect=True,          # auto-reconnect on disconnect
    request_retries=5,            # retry failed requests
    timeout=30,                   # connection timeout
)
bot_client = None

print("✅ Clients initialized")

translator = OpenAITranslator(openai_api_key=OPENAI_API_KEY, db_path=DB)
print("✅ Translation system initialized")

ai_handler = GroupAwareMessageHandler(
    openai_api_key=OPENAI_API_KEY,
    business_info=BUSINESS_INFO,
    db_path=DB,
    enable_auto_reply=ENABLE_AUTO_REPLY,
    bot_username=BOT_USERNAME
)
print("✅ AI Handler initialized")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ── Phase 1: Pre-filter + Classifier (token optimization) ──
prefilter = MessagePreFilter()
classifier = QuestionClassifier(openai_api_key=OPENAI_API_KEY)
# Smart filter enabled flag — set False to disable and use old flow
SMART_FILTER_ENABLED = os.getenv("SMART_FILTER_ENABLED", "True") == "True"
if SMART_FILTER_ENABLED:
    print("✅ Smart Filter ENABLED (pre-filter + classifier active)")
else:
    print("⚠️  Smart Filter DISABLED (old flow active)")

# ── Phase 2: Approval system config ──
APPROVAL_CHAT_ID = int(os.getenv("APPROVAL_CHAT_ID", "0"))
APPROVAL_ENABLED = os.getenv("APPROVAL_ENABLED", "True") == "True"
approval_handler = None  # Initialized in main() after bot_client is ready
call_analyzer = None     # Call-recording analyzer (KI Freigaben), set in main()

# ── Phase 3: Dynamic Handler ──
dynamic_handler = DynamicHandler(
    crm_api_url=os.getenv("CRM_API_URL", ""),
    crm_api_key=os.getenv("CRM_BOT_API_KEY", ""),
    translator=translator,
    your_language=YOUR_LANGUAGE,
)

# ── Phase 4: Knowledge Base ──
kb = KnowledgeBase(db_path=DB, openai_api_key=OPENAI_API_KEY)

# ── Phase 7: SOP Manager ──
# ── SOP Trigger (retrieval — sends saved SOPs when someone needs them) ──
from bot.knowledge.sop_trigger import SOPTrigger
sop_trigger = SOPTrigger(openai_api_key=OPENAI_API_KEY, db_path=DB)

sop_manager = SOPManager(
    openai_api_key=OPENAI_API_KEY,
    anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
    crm_api_url=os.getenv("CRM_API_URL", ""),
    crm_api_key=os.getenv("CRM_BOT_API_KEY", ""),
    sop_trigger=sop_trigger,
)

# ── Phase 8: Link & Info Extraction (monitored dev groups) ──
# Captures reusable knowledge (links, step-by-step procedures, guidelines)
# from the admin/dev groups and answers retrieval questions for free.
link_extractor = LinkExtractor(db_path=DB)
link_query     = LinkQueryHandler(link_extractor)

# ── Selbstauskunft: Bot erklärt seine eigenen Funktionen ──
from bot.knowledge.self_knowledge import SelfKnowledge, is_capability_question
self_knowledge = SelfKnowledge(openai_api_key=OPENAI_API_KEY)

LINK_EXTRACTION_ENABLED = os.getenv("LINK_EXTRACTION_ENABLED", "True") == "True"

# Groups we watch for reusable knowledge. Override IDs via env if they change.
MONITORED_GROUPS = {
    int(os.getenv("GROUP_LOTHAR_ROHIT",   "-1003552835240")): "Lothar & Rohit",
    int(os.getenv("GROUP_LOTHAR_MANISHA", "-1003790666199")): "Lothar & Manisha",
}
# Inside handlers Telethon gives chat_id WITHOUT the -100 prefix
# (e.g. 3552835240), so precompute the short forms for matching.
MONITORED_SHORT = {abs(gid) % 10**10: name for gid, name in MONITORED_GROUPS.items()}

# ── Vertrauliche Gruppen: KEINE Analyse, KEINE Erfassung (nur Übersetzung) ──
# z. B. Lothar↔Rohit und Lothar↔Manisha (private Strategie-Chats).
EXCLUDED_GROUPS = set()
for _g in os.getenv("EXCLUDED_GROUPS", "-1003552835240,-1003790666199").split(","):
    _g = _g.strip()
    if _g:
        try: EXCLUDED_GROUPS.add(int(_g))
        except Exception: pass
EXCLUDED_SHORT = {abs(g) % 10**10 for g in EXCLUDED_GROUPS}
if EXCLUDED_SHORT:
    print(f"🔒 {len(EXCLUDED_SHORT)} vertrauliche Gruppe(n) — keine Analyse/Erfassung (nur Übersetzung)")
if LINK_EXTRACTION_ENABLED:
    print(f"✅ Link extraction ENABLED for {len(MONITORED_GROUPS)} group(s)")
else:
    print("⚠️  Link extraction DISABLED")


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

# Global async lock — ensures only ONE DB write at a time
# Prevents "database is locked" errors from concurrent handlers
import asyncio
_db_write_lock = asyncio.Lock()

# Centralized DB writer — permanent fix for "database is locked"
from bot.core.db_writer import db_writer


async def safe_db_write(func, *args, **kwargs):
    """Async wrapper — ensures only ONE DB write at a time."""
    async with _db_write_lock:
        return func(*args, **kwargs)


async def async_store_group_message(chat_id, topic_id, sender_id, sender_name,
                                     message_text, chat_title='', topic_name=''):
    """Store group message via centralized DB writer."""
    await db_writer.execute(
        """INSERT INTO group_messages
           (chat_id, topic_id, sender_id, sender_name, message_text, chat_title, topic_name, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (chat_id, topic_id or 0, sender_id, sender_name, message_text,
         chat_title, topic_name, datetime.now())
    )


async def async_queue_message(user_id, message, chat_id=None, topic_id=None, target_language=None,
                               message_category='response', sender_type='user', is_group=False,
                               sender_name=None, original_msg_id=None):
    """Queue outgoing message via centralized DB writer."""
    await db_writer.execute(
        """INSERT INTO outgoing_messages
           (user_id, message, created_at, is_group, chat_id, topic_id,
            target_language, sender_type, message_category, sender_name, original_msg_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, message, datetime.now(), int(is_group), chat_id, topic_id,
         target_language, sender_type, message_category, sender_name, original_msg_id)
    )

def get_db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS user_languages (
        user_id INTEGER PRIMARY KEY,
        language TEXT DEFAULT 'en',
        language_name TEXT,
        auto_translate INTEGER DEFAULT 1,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS translation_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        original_text TEXT,
        source_lang TEXT,
        target_lang TEXT,
        translated_text TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS pending_approvals (
        token TEXT PRIMARY KEY,
        user_id INTEGER,
        sender_name TEXT,
        incoming_msg TEXT,
        ai_suggestion TEXT,
        language TEXT,
        timestamp DATETIME,
        is_group INTEGER DEFAULT 0,
        chat_id INTEGER,
        chat_title TEXT,
        topic_id INTEGER,
        topic_name TEXT,
        source_language TEXT,
        translated_message TEXT,
        original_message TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS outgoing_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        created_at DATETIME,
        is_group INTEGER DEFAULT 0,
        chat_id INTEGER,
        topic_id INTEGER,
        target_language TEXT,
        original_message TEXT,
        sender_type TEXT DEFAULT 'user',
        message_category TEXT DEFAULT 'response',
        sender_name TEXT,
        original_msg_id INTEGER
    )
    """)
    # Migrate old DB — add missing columns if needed
    existing_cols = [r[1] for r in c.execute("PRAGMA table_info(outgoing_messages)").fetchall()]
    for col, default in [
        ('sender_type',    "'user'"),
        ('message_category', "'response'"),
        ('sender_name',    'NULL'),
        ('original_msg_id','NULL'),
    ]:
        if col not in existing_cols:
            try:
                c.execute(f"ALTER TABLE outgoing_messages ADD COLUMN {col} TEXT DEFAULT {default}")
                conn.commit()
            except Exception:
                pass

    c.execute("""
    CREATE TABLE IF NOT EXISTS message_corrections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        user_name TEXT,
        incoming_message TEXT,
        ai_suggestion TEXT,
        your_edit TEXT,
        language TEXT,
        timestamp DATETIME,
        is_group INTEGER DEFAULT 0,
        chat_title TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS group_messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id     INTEGER,
        topic_id    INTEGER,
        sender_id   INTEGER,
        sender_name TEXT,
        message_text TEXT,
        chat_title  TEXT DEFAULT '',
        topic_name  TEXT DEFAULT '',
        timestamp   DATETIME
    )
    """)
    gm_cols = [r[1] for r in c.execute("PRAGMA table_info(group_messages)").fetchall()]
    for col_name, col_def in [("chat_title", "TEXT DEFAULT ''"), ("topic_name", "TEXT DEFAULT ''")]:
        if col_name not in gm_cols:
            try:
                c.execute(f"ALTER TABLE group_messages ADD COLUMN {col_name} {col_def}")
            except Exception:
                pass

    c.execute("""
    CREATE TABLE IF NOT EXISTS dm_messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id   INTEGER,
        sender_name TEXT,
        message_text TEXT,
        timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS message_analysis (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_message_id   INTEGER,
        source_type      TEXT,
        sender_name      TEXT,
        chat_id          INTEGER,
        message_type     TEXT,
        urgency          TEXT,
        confidence       INTEGER,
        ai_topic         TEXT,
        intent           TEXT,
        suggested_action TEXT,
        needs_db_lookup  INTEGER DEFAULT 0,
        entities_json    TEXT,
        should_respond   INTEGER DEFAULT 1,
        response_reason  TEXT,
        timestamp        DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS bot_translation_messages (
        chat_id     INTEGER NOT NULL,
        message_id  INTEGER NOT NULL,
        topic_id    INTEGER,
        original_message_text TEXT,
        language    TEXT,
        sent_at     DATETIME,
        PRIMARY KEY (chat_id, message_id)
    )
    """)
    try:
        c.execute("SELECT chat_id FROM bot_translation_messages LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("DROP TABLE IF EXISTS bot_translation_messages")
        c.execute("""
        CREATE TABLE bot_translation_messages (
            chat_id     INTEGER NOT NULL,
            message_id  INTEGER NOT NULL,
            topic_id    INTEGER,
            original_message_text TEXT,
            language    TEXT,
            sent_at     DATETIME,
            PRIMARY KEY (chat_id, message_id)
        )
        """)

    for idx, tbl in [
        ("idx_analysis_timestamp", "message_analysis(timestamp)"),
        ("idx_analysis_topic",     "message_analysis(ai_topic)"),
        ("idx_analysis_source",    "message_analysis(source_type)"),
        ("idx_dm_sender",          "dm_messages(sender_id)"),
        ("idx_group_chat",         "group_messages(chat_id)"),
    ]:
        try:
            c.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON {tbl}")
        except Exception:
            pass

    # ── cross_group_reply_map — source_msg_id column included ──────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS cross_group_reply_map (
        dest_msg_id     INTEGER NOT NULL,
        dest_chat_id    INTEGER NOT NULL,
        source_chat_id  INTEGER NOT NULL,
        source_topic_id INTEGER,
        source_msg_id   INTEGER,
        sender_name     TEXT,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (dest_msg_id, dest_chat_id)
    )
    """)
    # Migrate existing DB if source_msg_id column missing
    cgr_cols = [r[1] for r in c.execute("PRAGMA table_info(cross_group_reply_map)").fetchall()]
    if 'source_msg_id' not in cgr_cols:
        try:
            c.execute("ALTER TABLE cross_group_reply_map ADD COLUMN source_msg_id INTEGER")
        except Exception:
            pass

    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_reply_map ON cross_group_reply_map(dest_msg_id, dest_chat_id)")
    except Exception:
        pass

    conn.commit()
    conn.close()
    print("✅ Database initialized")
    init_special_groups_table()


# ═══════════════════════════════════════════════════════════════════════════════
# TOPIC NAME FETCHER
# ═══════════════════════════════════════════════════════════════════════════════

async def get_topic_name(chat_id, topic_id) -> str:
    if not topic_id:
        return ''
    cache_key = (chat_id, topic_id)
    if cache_key in _topic_name_cache:
        return _topic_name_cache[cache_key]
    try:
        from telethon.tl.functions.channels import GetForumTopicsByIDRequest
        result = await user_client(GetForumTopicsByIDRequest(
            channel=chat_id, topics=[topic_id]
        ))
        if result and result.topics:
            name = result.topics[0].title
            _topic_name_cache[cache_key] = name
            print(f"   📌 Telegram thread name: '{name}'")
            return name
    except Exception as e:
        print(f"   ⚠️  Could not fetch topic name: {e}")
    fallback = f"Topic #{topic_id}"
    _topic_name_cache[cache_key] = fallback
    return fallback


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP MESSAGE TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

def store_group_message(chat_id, topic_id, sender_id, sender_name, message_text,
                        chat_title='', topic_name=''):
    for attempt in range(5):
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute("""
                INSERT INTO group_messages
                (chat_id, topic_id, sender_id, sender_name, message_text, chat_title, topic_name, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (chat_id, topic_id or 0, sender_id, sender_name, message_text,
                  chat_title, topic_name, datetime.now()))
            conn.commit()
            conn.close()
            return
        except Exception as e:
            if 'locked' in str(e) and attempt < 4:
                import time; time.sleep(0.2 * (attempt + 1))
                continue
            print(f"⚠️  store_group_message error: {e}")


def get_recent_group_messages(chat_id, topic_id=None, limit=10):
    try:
        conn = get_db()
        c = conn.cursor()
        if topic_id:
            c.execute("""
                SELECT sender_name, message_text FROM group_messages
                WHERE chat_id = ? AND topic_id = ?
                ORDER BY timestamp DESC LIMIT ?
            """, (chat_id, topic_id, limit))
        else:
            c.execute("""
                SELECT sender_name, message_text FROM group_messages
                WHERE chat_id = ? ORDER BY timestamp DESC LIMIT ?
            """, (chat_id, limit))
        results = c.fetchall()
        conn.close()
        return [{'sender': row[0], 'text': row[1]} for row in reversed(results)]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# OUTGOING QUEUE
# ═══════════════════════════════════════════════════════════════════════════════

def get_next_outgoing():
    conn = get_db()
    conn.isolation_level = None   # manual txn control so BEGIN IMMEDIATE works cleanly
    try:
        c = conn.cursor()
        c.execute("PRAGMA table_info(outgoing_messages)")
        columns = [col[1] for col in c.fetchall()]
        select_fields = "id, user_id, message, is_group, chat_id, topic_id, target_language"
        select_fields += ", sender_type"     if 'sender_type'     in columns else ", 'user' as sender_type"
        select_fields += ", message_category" if 'message_category' in columns else ", 'response' as message_category"
        select_fields += ", sender_name"     if 'sender_name'     in columns else ", NULL as sender_name"
        select_fields += ", original_msg_id" if 'original_msg_id' in columns else ", NULL as original_msg_id"
        # ATOMIC CLAIM: fetch the oldest row AND delete it inside ONE committed
        # transaction. This eliminates the read->delete window entirely, so the same
        # row can never be fetched (and sent) twice — not by a fast worker loop, and
        # not by a second bot process sharing this DB. The `id` tie-breaker keeps the
        # ordering stable when two rows share the same created_at timestamp.
        c.execute("BEGIN IMMEDIATE")
        c.execute(f"SELECT {select_fields} FROM outgoing_messages ORDER BY created_at, id LIMIT 1")
        row = c.fetchone()
        if row is not None:
            c.execute("DELETE FROM outgoing_messages WHERE id = ?", (row[0],))
        c.execute("COMMIT")
        return row
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


async def delete_outgoing(msg_id):
    # Await the commit so the row is GONE before outgoing_worker re-SELECTs it.
    # Previously this used execute_nowait (fire-and-forget): the DELETE was only
    # queued, and because the worker loops back to get_next_outgoing() with no await
    # in between, db_writer never got a chance to commit it — so the same row was
    # fetched and SENT A SECOND TIME. Awaiting the write closes that race.
    await db_writer.execute("DELETE FROM outgoing_messages WHERE id = ?", (msg_id,))


def queue_message(user_id, message, chat_id=None, topic_id=None, target_language=None,
                  message_category='response', sender_type='user', is_group=False,
                  sender_name=None, original_msg_id=None):
    for attempt in range(5):
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute("""
                INSERT INTO outgoing_messages
                (user_id, message, created_at, is_group, chat_id, topic_id,
                 target_language, sender_type, message_category, sender_name, original_msg_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, message, datetime.now(), int(is_group), chat_id, topic_id,
                  target_language, sender_type, message_category, sender_name, original_msg_id))
            conn.commit()
            conn.close()
            return
        except Exception as e:
            if 'locked' in str(e) and attempt < 4:
                import time; time.sleep(0.2 * (attempt + 1))
                continue
            raise


def track_bot_translation_message(message_id, chat_id, topic_id, original_text, language,
                                   source_chat_id=None, source_topic_id=None,
                                   sender_name=None, source_msg_id=None):
    """Bot ne jo message destination group mein bheja uska record rakho."""
    try:
        # Use centralized writer (fire-and-forget)
        db_writer.execute_nowait(
            """INSERT OR IGNORE INTO bot_translation_messages
               (chat_id, message_id, topic_id, original_message_text, language, sent_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (chat_id, message_id, topic_id or 0, original_text, language, datetime.now())
        )

        # Cross-group reply map
        if source_chat_id and abs(int(source_chat_id)) != abs(int(chat_id)):
            db_writer.execute_nowait(
                """INSERT OR IGNORE INTO cross_group_reply_map
                   (dest_msg_id, dest_chat_id, source_chat_id, source_topic_id,
                    source_msg_id, sender_name, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (message_id, abs(int(chat_id)), abs(int(source_chat_id)),
                 source_topic_id, source_msg_id, sender_name, datetime.now())
            )

        _processed_translation_events.add((chat_id, message_id))
    except Exception as e:
        print(f"⚠️  track_bot_translation_message error: {e}")


def get_reply_source(dest_msg_id, dest_chat_id):
    """
    Destination message ID se source group info nikalo.
    FIX: SELECT mein source_msg_id bhi include — tuple index out of range fix.
    FIX: LIKE fallback — Telethon short ID (3611248396) vs DB full ID (1003611248396).
    """
    try:
        conn = get_db()
        c = conn.cursor()

        normalized = abs(int(dest_chat_id))

        # Primary: exact match
        c.execute("""
            SELECT source_chat_id, source_topic_id, sender_name, source_msg_id
            FROM cross_group_reply_map
            WHERE dest_msg_id = ? AND dest_chat_id = ?
        """, (dest_msg_id, normalized))
        row = c.fetchone()

        # Fallback: suffix match (short ID fix)
        if not row:
            suffix = str(normalized)
            c.execute("""
                SELECT source_chat_id, source_topic_id, sender_name, source_msg_id
                FROM cross_group_reply_map
                WHERE dest_msg_id = ?
                  AND CAST(dest_chat_id AS TEXT) LIKE ?
            """, (dest_msg_id, f"%{suffix}"))
            row = c.fetchone()
            if row:
                print(f"   🔍 ID suffix match: {normalized} matched in DB")

        conn.close()
        if row:
            return {
                'source_chat_id' : row[0],
                'source_topic_id': row[1],
                'sender_name'    : row[2],
                'source_msg_id'  : row[3],   # ← None agar purana record hai
            }
    except Exception as e:
        print(f"⚠️  get_reply_source error: {e}")
    return None


def is_bot_translation_message(message_id, chat_id):
    if (chat_id, message_id) in _processed_translation_events:
        return True
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "SELECT 1 FROM bot_translation_messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id)
        )
        result = c.fetchone()
        conn.close()
        return result is not None
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# OUTGOING WORKER
# ═══════════════════════════════════════════════════════════════════════════════

async def outgoing_worker():
    global bot_client, user_client
    print("🚀 Outgoing message worker started")
    while True:
        try:
            row = get_next_outgoing()
            if row:
                msg_id             = row[0]
                user_id            = row[1]
                message            = row[2]
                is_group           = row[3]
                chat_id            = row[4]
                topic_id           = row[5]
                target_language    = row[6]  if len(row) > 6  else None
                sender_type        = row[7]  if len(row) > 7  else 'user'
                message_category   = row[8]  if len(row) > 8  else 'response'
                queued_sender_name = row[9]  if len(row) > 9  else None
                original_msg_id    = row[10] if len(row) > 10 else None   # ← source group msg ID

                print(f"\n📤 Sending | category={message_category} | sender={sender_type} | group={is_group}")

                try:
                    if sender_type == 'bot' and message_category == 'translation':
                        print("   🤖 Using BOT for translation...")
                        if is_group and chat_id:
                            raw_chat_id = int(chat_id)
                            formatted_chat_id = raw_chat_id if raw_chat_id < 0 else int(f"-100{abs(raw_chat_id)}")
                            print(f"   📡 Sending to chat: {formatted_chat_id}")
                            try:
                                # Attach 🔊 listen button (TTS) to every translation
                                tts_buttons = None
                                if tts_manager is not None:
                                    try:
                                        _tid = tts_manager.store_tts(message)
                                        _row = tts_manager.make_button_row(_tid)
                                        if _row:
                                            tts_buttons = [_row]
                                    except Exception as _te:
                                        print(f"   ⚠️ TTS button error: {_te}")

                                if topic_id:
                                    sent_msg = await bot_client.send_message(formatted_chat_id, message, reply_to=topic_id, buttons=tts_buttons)
                                else:
                                    sent_msg = await bot_client.send_message(formatted_chat_id, message, buttons=tts_buttons)

                                reverse_map    = {abs(int(v)): int(k) for k, v in BOT_DEST_MAP.items()}
                                lookup_id      = abs(int(formatted_chat_id))
                                source_chat_id = reverse_map.get(lookup_id)
                                # Try stripped format if not found (3670840175 vs 1003670840175)
                                if source_chat_id is None:
                                    stripped = str(lookup_id)
                                    if stripped.startswith("100") and len(stripped) > 10:
                                        source_chat_id = reverse_map.get(int(stripped[3:]))
                                    else:
                                        source_chat_id = reverse_map.get(int(f"100{stripped}"))
                                print(f"   🗺️  reverse_map={reverse_map}, looking up {lookup_id} → {source_chat_id}")

                                track_bot_translation_message(
                                    sent_msg.id, formatted_chat_id, topic_id,
                                    message, target_language or 'unknown',
                                    source_chat_id  = source_chat_id,
                                    source_topic_id = topic_id,
                                    sender_name     = queued_sender_name,
                                    source_msg_id   = original_msg_id,   # ← original message ID
                                )
                                await asyncio.sleep(0.3)
                                print(f"   ✅ Bot sent to group (msg_id: {sent_msg.id})")
                            except Exception as e:
                                print(f"   ❌ Bot send failed: {e}")
                                print(f"   ℹ️  Make sure @{BOT_USERNAME} is admin in group {formatted_chat_id}")
                                await delete_outgoing(msg_id)
                                continue
                        else:
                            print("   ⚠️  Bot translations only for groups, skipping...")

                    else:
                        print("   👤 Using PERSONAL ACCOUNT...")
                        if is_group and chat_id:
                            if topic_id:
                                await user_client.send_message(chat_id, message, reply_to=topic_id)
                            else:
                                await user_client.send_message(chat_id, message)
                            print("   ✅ User sent to group")

                            if (message_category == 'response'
                                    and TRANSLATION_SETTINGS.get('enabled')
                                    and TRANSLATION_SETTINGS.get('use_bot_for_translations')):
                                print("   🌍 Broadcasting translations for approved reply...")
                                source_lang_code = target_language or YOUR_LANGUAGE or 'en'
                                special_cfg = get_special_group_config(chat_id) if chat_id else None
                                if special_cfg:
                                    langs_to_send = [l for l in _special_langs(special_cfg) if l != source_lang_code]
                                else:
                                    langs_to_send = TRANSLATION_SETTINGS.get('group_languages', [])
                                for t_lang in langs_to_send:
                                    if t_lang == source_lang_code:
                                        continue
                                    try:
                                        lang_name   = translator.LANGUAGES.get(t_lang, t_lang)
                                        translation = translator.translate(
                                            text=message, target_lang=t_lang, source_lang=source_lang_code
                                        )
                                        broadcast_msg = f"{lang_name}:\n{translation['translated_text']}"
                                        await async_queue_message(
                                            user_id=user_id, message=broadcast_msg,
                                            chat_id=chat_id, topic_id=topic_id,
                                            target_language=t_lang,
                                            message_category='translation',
                                            sender_type='bot', is_group=True,
                                            sender_name=queued_sender_name
                                        )
                                        print(f"      ✅ Queued {lang_name} translation")
                                    except Exception as te:
                                        print(f"      ❌ Translation failed for {t_lang}: {te}")
                        else:
                            await user_client.send_message(user_id, message)
                            print("   ✅ User sent DM")

                    await delete_outgoing(msg_id)

                except Exception as e:
                    print(f"   ❌ Send failed: {e}")
                    import traceback; traceback.print_exc()
                    await delete_outgoing(msg_id)
            else:
                await asyncio.sleep(1)
        except Exception as e:
            print(f"❌ Worker error: {e}")
            import traceback; traceback.print_exc()
            await asyncio.sleep(2)


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

@user_client.on(events.NewMessage(incoming=True, outgoing=True))
async def handle_incoming_message(event):
    try:
        print(f"\n{'='*70}")
        print(f"🔔 MESSAGE EVENT")
        print(f"{'='*70}")

        if isinstance(event.message, MessageService):
            return

        message   = event.message
        sender    = await event.get_sender()
        if not sender:
            return

        sender_id = sender.id
        text      = message.text or ''

        # Voice messages have no text — allow through for KI Freigaben SOP
        is_voice = bool(message.voice or message.audio or
            (message.document and hasattr(message.document, 'mime_type') and
             message.document.mime_type and 'audio' in message.document.mime_type))

        if not text and not is_voice:
            return

        # Skip bot commands — don't translate them
        if text.startswith('/setlang') or text.startswith('/removelang') or text.startswith('/grouplang') or text.startswith('/language') or text.startswith('/status') or text.startswith('/fortschritt'):
            return

        # Skip our own daily expense report (posted into KI Freigaben via userbot)
        # — no translation, no AI pipeline (sonst entstehen Polish-Übersetzung + KB-Antwort)
        _erpt = text.lstrip()
        if _erpt.startswith('🧾') and 'Ausgaben' in _erpt[:40]:
            print("⏭️  Skipping expense report — no translation/AI")
            return

        if sender_id == BOT_USER_ID:
            print("⏭️  Skipping bot message — loop prevention")
            return

        chat_for_check    = await event.get_chat()
        chat_id_for_check = getattr(chat_for_check, 'id', None)
        if chat_id_for_check and is_bot_translation_message(message.id, chat_id_for_check):
            print("⏭️  Skipping bot translation message")
            return

        chat     = await event.get_chat()
        is_group = hasattr(chat, 'broadcast') or hasattr(chat, 'megagroup')

        sender_name = getattr(sender, 'first_name', 'Unknown')
        if hasattr(sender, 'last_name') and sender.last_name:
            sender_name += f" {sender.last_name}"

        chat_id    = chat.id if is_group else None
        chat_title = getattr(chat, 'title', '') if is_group else ''

        if is_group:
            print(f"🔍 chat_id={chat_id} | special_group={'YES ✅' if get_special_group_config(chat_id) else 'NO'}")

        if message.reply_to:
            topic_id = (getattr(message.reply_to, 'reply_to_top_id', None)
                        or getattr(message.reply_to, 'reply_to_msg_id', None))
        else:
            topic_id = None

        topic_name = await get_topic_name(chat_id, topic_id) if (is_group and topic_id) else ''
        user_id    = sender_id

        if is_group:
            print(f"📣 GROUP: {chat_title} | thread: {topic_name or 'main'}")
            await async_store_group_message(
                chat_id=chat_id, topic_id=topic_id,
                sender_id=sender_id, sender_name=sender_name,
                message_text=text, chat_title=chat_title, topic_name=topic_name,
            )

            # ── Capture reusable knowledge from monitored dev groups (24/7) ──
            # Links, numbered procedures and "Wichtig:" guidelines get saved so
            # they can be answered instantly later in KI Freigaben.
            if (LINK_EXTRACTION_ENABLED and chat_id
                    and chat_id in MONITORED_SHORT and chat_id not in EXCLUDED_SHORT
                    and not get_special_group_config(chat_id) and text):
                try:
                    _grp_name = MONITORED_SHORT[chat_id]
                    _cap = await link_extractor.extract_from_message(
                        message_text=text,
                        sender_name=sender_name,
                        sender_id=sender_id,
                        chat_id=chat_id,
                        chat_title=chat_title,
                        message_id=message.id,
                        source_group_name=_grp_name,
                    )
                    if _cap["saved"] > 0:
                        _bits = []
                        if _cap["links"]:      _bits.append(f"{len(_cap['links'])} Link(s)")
                        if _cap["procedures"]: _bits.append(f"{len(_cap['procedures'])} Ablauf")
                        if _cap["guidelines"]: _bits.append(f"{len(_cap['guidelines'])} Hinweis")
                        print(f"   📚 Gespeichert aus '{_grp_name}': {', '.join(_bits)}")
                except Exception as e:
                    print(f"   ⚠️  Link-Extraktion Fehler: {e}")

        else:
            print(f"💬 DM from {sender_name}")

        if is_group and chat_id:
            event_key = (chat_id, message.id)
            if event_key in _processed_incoming:
                print(f"⏭️  Skipping duplicate event")
                return
            _processed_incoming.add(event_key)
            _processed_incoming_order.append(event_key)
            # FIFO cleanup — remove oldest, never clear all (prevents re-processing)
            if len(_processed_incoming_order) > 5000:
                old = _processed_incoming_order.pop(0)
                _processed_incoming.discard(old)

        # Voice messages: skip translation (no text), go straight to SOP handler
        source_language = {'code': YOUR_LANGUAGE, 'name': 'German'}
        translated_for_you = text

        if not (is_voice and not text):
            source_language    = translator.detect_language(text)
            translated_for_you = text
            if source_language['code'] != YOUR_LANGUAGE:
                translation        = translator.translate(
                    text=text, target_lang=YOUR_LANGUAGE, source_lang=source_language['code']
                )
                translated_for_you = translation['translated_text']

        if not (is_voice and not text) and is_group and TRANSLATION_SETTINGS['enabled'] and TRANSLATION_SETTINGS['use_bot_for_translations']:
            print(f"\n🌍 Broadcasting Translations via BOT")
            special_cfg = get_special_group_config(chat_id) if chat_id else None

            dest_chat_id = (BOT_DEST_MAP.get(chat_id)
                            or BOT_DEST_MAP.get(int(f"-100{abs(chat_id)}"))) if chat_id else None
            target_groups = [chat_id]
            if dest_chat_id and abs(int(dest_chat_id)) != abs(int(chat_id)):
                target_groups.append(dest_chat_id)
            print(f"   📡 Sending to groups: {target_groups}")

            langs = []
            if special_cfg:
                # Special group: translate to ALL configured languages, skip the source
                all_langs = _special_langs(special_cfg)
                langs = [l for l in all_langs if l != source_language['code']]
                print(f"   🌍 Special: {all_langs} | detected={source_language['code']} → {langs}")
            else:
                langs = [l for l in TRANSLATION_SETTINGS['group_languages'] if l != source_language['code']]

            computed = {}
            for tl in langs:
                ln = translator.LANGUAGES.get(tl, tl)
                tr = translator.translate(text=text, target_lang=tl, source_lang=source_language['code'])
                computed[tl] = (ln, tr['translated_text'])
                print(f"   ✅ Translated → {ln}")

            for grp_id in target_groups:
                is_dest = dest_chat_id and abs(int(grp_id)) == abs(int(dest_chat_id)) and abs(int(grp_id)) != abs(int(chat_id))
                group_tag = f"📍 {chat_title}\n" if is_dest else ""
                for tl, (ln, translated_text) in computed.items():
                    if is_dest:
                        msg_text = f"{group_tag}👤 {sender_name}:\n{text}\n\n{ln}:\n{translated_text}"
                    else:
                        msg_text = translated_text
                    await async_queue_message(
                        user_id=user_id, message=msg_text,
                        chat_id=grp_id, topic_id=topic_id,
                        target_language=tl,
                        message_category='translation', sender_type='bot', is_group=True,
                        sender_name=sender_name,
                        original_msg_id=message.id,   # ← original msg ID pass karo
                    )
                print(f"   ✅ Queued to group {grp_id}")

        if sender_id == YOUR_USER_ID:
            # Lothar's messages: allow in groups (he wants to ask AI questions too)
            # Skip only in DMs (to prevent loop with self/saved messages)
            if not is_group:
                print("⏭️  Skipping AI analysis (your own DM)")
                return
            # In groups: let it pass through pre-filter + classifier
            print("👤 Your message — processing through AI pipeline")

        # ══════════════════════════════════════════════════════════════════
        # PHASE 1: Smart Pre-Filter + Classifier (token optimization)
        # Pre-filter runs on ALL messages (groups + DMs)
        # Classifier runs only on group messages
        # Translation already happened above — not affected.
        # Message already stored in DB above — not affected.
        #
        # ⚡ RUNS AS BACKGROUND TASK — does NOT block translation of next message
        # ══════════════════════════════════════════════════════════════════

        # ══════════════════════════════════════════════════════════════════
        # SOP CHECK: Voice/text SOP in KI Freigaben (needs event access)
        # Must be BEFORE _run_ai_analysis because we need event.message
        # ══════════════════════════════════════════════════════════════════
        if is_group and chat_id and APPROVAL_CHAT_ID and chat_id == abs(APPROVAL_CHAT_ID) % 10**10:
            if sender_id != BOT_USER_ID:  # Allow any human member, skip bot
                message = event.message
                # ── Audio/Video in KI Freigaben → fragen: SOP oder Gesprächsanalyse? ──
                _is_media = bool(message and (message.voice or message.audio or message.video or
                    (message.document and getattr(message.document, 'mime_type', '') and
                     (str(message.document.mime_type).startswith('audio/') or
                      str(message.document.mime_type).startswith('video/')))))
                if _is_media:
                    print("🎧 Audio/Video in KI Freigaben — frage SOP vs. Analyse…")
                    try:
                        await bot_client.send_message(
                            APPROVAL_CHAT_ID,
                            "🎧 Aufnahme empfangen — was soll ich tun?",
                            buttons=[[
                                Button.inline("📝 Als SOP speichern", data=f"kifa:sop:{chat_id}:{message.id}".encode()),
                                Button.inline("📞 Gespräch analysieren", data=f"kifa:call:{chat_id}:{message.id}".encode()),
                            ]],
                            reply_to=message.id,
                        )
                    except Exception as e:
                        print(f"   ⚠️ KI Freigaben media prompt error: {e}")
                    return

        # Voice messages in non-KI-Freigaben groups — skip (no text to analyze)
        if is_voice and not text:
            return

        asyncio.create_task(_run_ai_analysis(
            text=text,
            translated_for_you=translated_for_you,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_id=chat_id,
            chat_title=chat_title,
            topic_id=topic_id,
            topic_name=topic_name,
            is_group=is_group,
            source_language=source_language,
        ))
        return  # Translation done, AI analysis runs in background

    except Exception as e:
        print(f"\n❌ MESSAGE HANDLER ERROR: {e}")
        import traceback; traceback.print_exc()


def _is_price_question(question: str) -> bool:
    """Check if the question is asking about prices, costs, or contact details."""
    if not question:
        return False
    q = question.lower()

    # Price/cost question words
    price_words = ['kostet', 'kosten', 'preis', 'bezahlen', 'zahlen', 'teuer',
                   'günstig', 'budget', 'kalkulation', 'angebot machen',
                   'how much', 'cost', 'price', 'expensive', 'cheap', 'pay',
                   'ile kosztuje', 'cena',  # Polish
                   'wieviel', 'wie viel']
    if any(w in q for w in price_words):
        return True

    # Contact info requests
    contact_words = ['telefonnummer', 'email', 'adresse', 'kontakt', 'anrufen',
                     'phone', 'address', 'contact', 'call']
    if any(w in q for w in contact_words):
        return True

    # € symbol in question
    if '€' in question or 'euro' in q or 'eur ' in q:
        return True

    return False


def _contains_sensitive_data(text: str) -> bool:
    """Check if text contains actual prices, payment info, or contact details."""
    if not text:
        return False
    text_lower = text.lower()
    import re

    # Actual price amounts (€ with number, or "Euro/EUR" with number)
    if re.search(r'\d+[\.,]?\d*\s*€', text):
        return True
    if re.search(r'€\s*\d+', text):
        return True
    if re.search(r'\d+[\.,]?\d*\s*(euro|eur)\b', text_lower):
        return True
    if re.search(r'\b(netto|brutto|mwst|mehrwertsteuer)\b', text_lower):
        return True

    # Payment/cost keywords with numbers nearby
    cost_words = ['kosten', 'preis', 'zahlt', 'bezahlt', 'kalkulation', 'marge', 'stundenlohn', 'tagessatz', 'pauschale']
    for word in cost_words:
        if word in text_lower:
            # Only flag if there's also a number nearby (actual price)
            if re.search(r'\d{3,}', text):  # 3+ digit number = likely a price
                return True

    # Contact info
    if re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text):
        return True
    if re.search(r'(\+49|0049|01[5-7]\d|0[2-9]\d{1,3}[\s/-]?\d)', text):
        return True
    if re.search(r'[A-Z]{2}\d{2}[\s]?\d{4}', text):
        return True

    return False


async def _handle_ki_freigaben_question(text, translated_text, sender_name, chat_id):
    """
    Handle Lothar's direct question in KI Freigaben.
    No approval needed — reply directly.
    """
    try:
        # Pre-filter (skip emojis, smalltalk)
        if not prefilter.should_process(text, sender_id=0, chat_id=chat_id):
            return

        question = translated_text or text

        # Schritt -1: Frage nach den Bot-Funktionen? (Selbstauskunft, günstig)
        try:
            if is_capability_question(question):
                ans = self_knowledge.answer(question)
                await bot_client.send_message(APPROVAL_CHAT_ID, ans)
                print("   ✅ Selbstauskunft (Funktionen) gesendet")
                return
        except Exception as e:
            print(f"   ⚠️ Selbstauskunft Fehler: {e}")

        # Step 0: Saved link / procedure / guideline? (instant, free, no AI)
        if LINK_EXTRACTION_ENABLED:
            try:
                _hit = link_query.try_answer(question)
                if _hit:
                    reply = f"📌 **Gespeicherte Info:**\n\n{_hit['answer']}"
                    await bot_client.send_message(APPROVAL_CHAT_ID, reply)
                    link_query.confirm_used(_hit)
                    print(f"   ✅ Link/Info aus Speicher gesendet ({_hit['confidence']})")
                    return
            except Exception as e:
                print(f"   ⚠️  Link-Query Fehler: {e}")

        # Step 1: Search KB first (free)
        kb_match = kb.find_answer(question=question, chat_id=0)
        if kb_match and kb_match.get('match_type') in ('exact', 'high'):
            answer = kb_match['answer']
            use_count = kb_match.get('use_count', 0)
            similarity = kb_match.get('similarity', 0)
            reply = f"📚 **Aus Wissensdatenbank** ({int(similarity*100)}%, {use_count}x verwendet):\n\n{answer}"
            await bot_client.send_message(APPROVAL_CHAT_ID, reply)
            print(f"   ✅ KB answer sent directly to KI Freigaben")
            return

        # Step 2: No KB match → use Claude Sonnet for general answer
        import requests as req
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            await bot_client.send_message(APPROVAL_CHAT_ID, "⚠️ Keine KI verfügbar (API-Key fehlt)")
            return

        system_prompt = """Du bist ein KI-Assistent für Premiobad/Seniorex (Badsanierung & Renovierung in Deutschland).
Beantworte die Frage auf Deutsch, kurz und hilfreich (3-5 Sätze).
Wenn du die Antwort nicht weißt, sage es ehrlich."""

        headers = {
            "x-api-key": api_key,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 400,
            "system": system_prompt,
            "messages": [{"role": "user", "content": question}],
        }

        resp = req.post("https://api.anthropic.com/v1/messages",
            headers=headers, json=payload, timeout=30)

        if resp.status_code == 200:
            result = resp.json()
            answer = ""
            for block in result.get("content", []):
                if block.get("type") == "text":
                    answer += block["text"]

            if answer:
                reply = f"🤖 **KI-Antwort:**\n\n{answer}"
                await bot_client.send_message(APPROVAL_CHAT_ID, reply)
                print(f"   ✅ AI answer sent directly to KI Freigaben")

                # Save to KB for future
                kb.save_answer(
                    question=question, answer=answer.strip(),
                    topic="general", intent="general",
                    classification_type="ki_freigaben_direct",
                    approved_by="lothar_direct",
                )
        else:
            print(f"   ⚠️ Claude error: {resp.status_code}")

    except Exception as e:
        print(f"   ⚠️ KI Freigaben question error: {e}")


async def _run_ai_analysis(
    text, translated_for_you, sender_id, sender_name,
    chat_id, chat_title, topic_id, topic_name, is_group, source_language,
):
    """Background AI analysis — does NOT block message translation."""
    try:

        # ── Gruppentyp ZUERST bestimmen (Privacy-Guard braucht das) ──
        is_construction_group = False
        is_personal_group = False
        if is_group and chat_title:
            title_lower = chat_title.lower()
            construction_prefixes = ['baustart', 'baustelle', 'in bau', 'construction', 'nacharbeit', 'reklamation']
            is_construction_group = any(title_lower.startswith(p) for p in construction_prefixes)
            is_personal_group = not is_construction_group

        # ── PRIVACY-GUARD GANZ OBEN — vor ALLEM (auch vor "Bereits beantwortet") ──
        # DMs + Spezial-/vertrauliche Gruppen: NUR Übersetzung, keine KI-Analyse,
        # keine Freigabe, KEIN "Bereits beantwortet"-Hinweis. Baustellen bleiben aktiv.
        if not is_group:
            print("🔒 Privatchat (DM) — keine KI-Analyse (nur Übersetzung)")
            return
        if chat_id and (chat_id in EXCLUDED_SHORT or
                        (get_special_group_config(chat_id) and not is_construction_group)):
            print("🔒 Privat-/Spezial-Gruppe — keine KI-Analyse/Freigabe (nur Übersetzung)")
            return

        # ── Auto-skip check — hat ein Mitarbeiter eine offene Frage beantwortet? ──
        # (Läuft NACH dem Guard — also nie für ausgeschlossene Gruppen)
        if is_group and approval_handler and APPROVAL_ENABLED:
            try:
                await approval_handler.check_employee_answered(
                    chat_id=chat_id,
                    topic_id=topic_id,
                    sender_name=sender_name,
                    answer_text=text,
                    sender_id=sender_id,
                    bot_user_id=BOT_USER_ID or 0,
                    your_user_id=YOUR_USER_ID or 0,
                )
            except Exception as e:
                print(f"   ⚠️ Auto-skip check error: {e}")

        if SMART_FILTER_ENABLED:
            # ── Step 0: Skip DMs completely (no AI analysis for private chats) ──
            if not is_group:
                print(f"⏭️  DM message — AI analysis skipped (translation only)")
                return

            # ── Step 0b: Skip Telegram system messages ──
            if text and ('anmeldecode' in text.lower() or 'login code' in text.lower()):
                print(f"⏭️  Telegram system message — skipped")
                return

            # ── Step 0c: KI Freigaben group ──
            # Lothar can: ask questions, save SOPs (text with prefix)
            # Voice messages handled above in handle_incoming_message
            if chat_id and APPROVAL_CHAT_ID and chat_id == abs(APPROVAL_CHAT_ID) % 10**10:
                if sender_id == BOT_USER_ID:
                    return  # Skip bot messages

                # Check if it's an SOP message (has category prefix)
                sop_prefixes = ['regel:', 'regel ', 'idee:', 'idee ', 'prozess:', 'prozess ',
                                'checkliste:', 'notiz:', 'kontakt:', 'rule:', 'idea:', 'process:']
                is_sop = any(text.lower().startswith(p) for p in sop_prefixes) if text else False

                if is_sop:
                    print(f"📝 SOP message in KI Freigaben — saving to Drive...")
                    result = await sop_manager.process_message(text=text)
                    if result and bot_client:
                        await bot_client.send_message(APPROVAL_CHAT_ID, result)
                    return

                # Otherwise — treat as question
                print(f"🧠 Lothar's question in KI Freigaben — processing directly...")
                await _handle_ki_freigaben_question(text, translated_for_you, sender_name, chat_id)
                return

            # ── Step 1: Pre-filter (zero cost, ALL group messages) ──
            if not prefilter.should_process(text, sender_id, chat_id, is_group=is_group):
                print(f"⏭️  Pre-filter: skipped (no AI needed)")
                print(f"   (Message stored + translated ✅, AI analysis skipped)")
                return

            # ── SOP TRIGGER: does this message ask for a saved SOP? ──
            # E.g. Paulina: "Ich möchte eine Anzeige schalten" → send Stellenanzeige SOP
            if is_group and 'sop_trigger' in globals() and sop_trigger is not None:
                try:
                    sop_match = sop_trigger.find_match(translated_for_you)
                    if sop_match:
                        print(f"   📇 SOP TRIGGER: '{sop_match['title']}' ({sop_match['match_type']}, sim={sop_match['similarity']})")
                        sop_msg = sop_trigger.format_sop_message(sop_match)
                        target_chat = int(f"-100{chat_id}") if chat_id > 0 else chat_id
                        await bot_client.send_message(
                            target_chat, sop_msg,
                            reply_to=topic_id if topic_id else None,
                        )
                        print(f"   ✅ SOP sent to group")
                        return  # SOP sent — no further AI needed
                except Exception as e:
                    print(f"   ⚠️ SOP trigger error: {e}")

        if SMART_FILTER_ENABLED and is_group:
            # ── Step 2: Classifier (cheap AI, groups only) ──
            try:
                recent_ctx = get_recent_group_messages(chat_id, topic_id, limit=5)
                classification = classifier.classify(
                    message=translated_for_you,
                    sender_name=sender_name,
                    chat_title=chat_title,
                    recent_messages=recent_ctx,
                )

                route = classification.get('route_to', 'skip')
                intent = classification.get('intent', 'none')
                conf = classification.get('confidence', 0)
                is_q = classification.get('is_question', False)
                print(f"🧠 Classifier: route={route} | intent={intent} | conf={conf}% | question={is_q}")

                # ── SKIP: not a question, off-topic, or low confidence ──
                if route == 'skip':
                    print(f"⏭️  Classifier: skipped")
                    return

                # ── KB SEARCH: Check knowledge base FIRST (free, no AI) ──
                kb_match = kb.find_answer(
                    question=translated_for_you,
                    chat_id=chat_id,
                    intent=intent,
                )
                if kb_match and kb_match.get('match_type') in ('exact', 'high'):
                    kb_answer = kb_match['answer']
                    similarity = kb_match.get('similarity', 0)
                    use_count = kb_match.get('use_count', 0)
                    kb_id = kb_match.get('id', 0)
                    print(f"📚 KB MATCH! similarity={similarity} | uses={use_count} | type={kb_match['match_type']}")

                    # ── SENSITIVE DATA CHECK ──
                    # Only block if the QUESTION is about prices/costs (not the answer)
                    # Construction process questions should not be blocked
                    is_price_question = _is_price_question(translated_for_you)
                    is_admin = (sender_id == YOUR_USER_ID)

                    if is_price_question and not is_admin and is_construction_group:
                        print(f"   🔒 Sensitive data detected — blocking for non-admin in construction group")
                        try:
                            target_chat = int(f"-100{chat_id}") if chat_id > 0 else chat_id
                            await bot_client.send_message(
                                target_chat,
                                "🔒 Diese Information enthält vertrauliche Daten (Preise/Kontaktdaten). Bitte im Büro nachfragen.",
                                reply_to=topic_id if topic_id else None,
                            )
                        except Exception as e:
                            print(f"   ⚠️ Sensitive block send error: {e}")
                        return

                    # ── TRUSTED ANSWER: 3+ approvals → answer directly in group ──
                    # ONLY for global rules/processes — NOT customer-specific answers
                    # (customer answers vary: each bathroom design/products differ)
                    kb_scope = kb_match.get('scope', 'global')
                    if use_count >= 3 and similarity >= 0.88 and kb_scope == 'global':
                        print(f"   🟢 Trusted GLOBAL KB answer — sending directly to group with employee buttons")
                        try:
                            confirm_token = f"empconf:{kb_id}:{chat_id}:{secrets.token_urlsafe(8)}"
                            direct_msg = (
                                f"🤖 {kb_answer}\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📚 Bewährte Antwort ({use_count}x bestätigt)"
                            )
                            buttons = [
                                [
                                    Button.inline("✅ Antwort ausreichend", data=f"empok:{confirm_token}".encode()),
                                    Button.inline("📨 An Lothar weiterleiten", data=f"empfwd:{confirm_token}".encode()),
                                ]
                            ]
                            target_chat = int(f"-100{chat_id}") if chat_id > 0 else chat_id
                            await bot_client.send_message(
                                target_chat,
                                direct_msg,
                                buttons=buttons,
                                reply_to=topic_id if topic_id else None,
                            )
                            print(f"   ✅ Direct answer sent to group (employee confirmation)")
                        except Exception as e:
                            print(f"   ⚠️ Direct send failed: {e} — falling to approval")
                            # Fall through to normal approval below
                        else:
                            return  # Direct answer sent, done

                    # ── NOT TRUSTED: send to Lothar for approval ──
                    if use_count >= 3:
                        kb_label = f"📚 **Bewährte Antwort** (Wissensdatenbank — {use_count}x bestätigt, Ähnlichkeit: {int(similarity*100)}%)"
                    else:
                        kb_label = f"📚 Aus Wissensdatenbank ({use_count}x verwendet, Ähnlichkeit: {int(similarity*100)}%)"

                    if approval_handler and APPROVAL_ENABLED:
                        token = secrets.token_urlsafe(16)
                        await approval_handler.send_for_approval(
                            token=token,
                            question_text=translated_for_you,
                            ai_suggestion=f"{kb_label}\n\n{kb_answer}",
                            sender_name=sender_name,
                            sender_id=sender_id,
                            chat_id=chat_id,
                            chat_title=chat_title,
                            topic_id=topic_id,
                            topic_name=topic_name,
                            source_language=source_language.get('code', '') if isinstance(source_language, dict) else '',
                            original_message=text,
                            ai_confidence=int(similarity * 100),
                            ai_topic=f"KB: {intent}",
                            classification_type='kb_match',
                        )
                        print(f"   ✅ KB answer sent to approval (zero AI cost! uses={use_count})")
                    return

                # ── DYNAMIC: customer-specific data (address, phone, drive) ──
                # Drive document analysis ONLY for construction groups
                if route == 'dynamic_handler':
                    if is_construction_group:
                        print(f"📋 Dynamic (intent={intent}) — fetching from CRM + Drive...")
                        try:
                            dyn_result = dynamic_handler.handle(
                                chat_id=chat_id,
                                intent=intent,
                                chat_title=chat_title,
                                topic_id=topic_id,
                                source_language=source_language.get('code', '') if isinstance(source_language, dict) else '',
                                question_text=translated_for_you,
                            )
                            if dyn_result and dyn_result.get('response_text') and dyn_result.get('customer'):
                                response_text = dyn_result['response_text']
                                print(f"   ✅ Dynamic response ready — sending to approval")

                                if approval_handler and APPROVAL_ENABLED:
                                    token = secrets.token_urlsafe(16)
                                    await approval_handler.send_for_approval(
                                        token=token,
                                        question_text=translated_for_you,
                                        ai_suggestion=response_text,
                                        sender_name=sender_name,
                                        sender_id=sender_id,
                                        chat_id=chat_id,
                                        chat_title=chat_title,
                                        topic_id=topic_id,
                                        topic_name=topic_name,
                                        source_language=source_language.get('code', '') if isinstance(source_language, dict) else '',
                                        original_message=text,
                                        ai_confidence=conf,
                                        ai_topic=intent,
                                        classification_type='dynamic',
                                    )
                                    print(f"   ✅ Dynamic response sent to approval!")
                                return
                            else:
                                print(f"   ⚠️ No dynamic data — falling to AI handler")
                        except Exception as e:
                            print(f"   ⚠️ Dynamic handler error: {e} — falling to AI handler")
                    else:
                        # Personal group — no Drive analysis, pass to AI handler
                        print(f"   ℹ️ Personal group — skipping Drive, passing to AI handler")

                # ── STATIC: general knowledge question ──
                if route == 'static_handler':
                    if is_personal_group:
                        print(f"📚 Static question in personal group — passing to AI handler")
                    else:
                        print(f"📚 Static question (intent={intent}) — passing to AI handler")
                    # Falls through to existing ai_handler.process_message below

            except Exception as e:
                print(f"⚠️  Classifier error (falling back to old flow): {e}")

        context_messages = []
        mentioned_users  = []
        if is_group:
            context_messages = get_recent_group_messages(chat_id, topic_id, limit=10)
            if '@' in text:
                mentioned_users = [w.strip('@') for w in text.split() if w.startswith('@')]

        result = ai_handler.process_message(
            message         = translated_for_you,
            sender_name     = sender_name,
            sender_id       = sender_id,
            is_group        = is_group,
            chat_id         = chat_id,
            chat_title      = chat_title,
            topic_name      = topic_name,
            recent_messages = context_messages,
            mentioned_users = mentioned_users,
            sender_language = "German",  # Always German — response goes to Lothar in KI Freigaben
        )

        if not result.get('should_respond'):
            reason = result['final_decision'].get('reason', 'low confidence')
            print(f"⏭️  No reply — {reason}")
            print(f"   (Message + analysis saved ✅)")
            print(f"{'='*70}\n")
            return

        decision      = result['final_decision']
        approval_data = ai_handler.generate_approval_data(result)

        if decision['action'] == 'auto_send' and ENABLE_AUTO_REPLY:
            response = approval_data['ai_suggestion']
            if source_language['code'] != YOUR_LANGUAGE:
                translation = translator.translate(
                    text=response, target_lang=source_language['code'], source_lang=YOUR_LANGUAGE
                )
                response = translation['translated_text']
            if is_group:
                if topic_id:
                    await user_client.send_message(chat_id, response, reply_to=topic_id)
                else:
                    await user_client.send_message(chat_id, response)
            else:
                await user_client.send_message(user_id, response)
            print("✅ Auto-sent via personal account")

        elif decision['action'] != 'skip':
            token = secrets.token_urlsafe(16)

            # ── Phase 2: Send to Telegram approval chat (replaces dashboard) ──
            if approval_handler and APPROVAL_ENABLED:
                ai_topic = result['classification'].get('topic', '')
                ai_confidence = result['final_decision'].get('confidence', 0)

                await approval_handler.send_for_approval(
                    token=token,
                    question_text=translated_for_you,
                    ai_suggestion=approval_data['ai_suggestion'],
                    sender_name=sender_name,
                    sender_id=sender_id,
                    chat_id=chat_id,
                    chat_title=chat_title,
                    topic_id=topic_id,
                    topic_name=topic_name,
                    source_language=source_language['code'],
                    original_message=text,
                    ai_confidence=ai_confidence,
                    ai_topic=ai_topic,
                    classification_type=result['classification'].get('type', ''),
                )
            else:
                # ── Fallback: Old dashboard notification ──
                conn  = get_db()
                c     = conn.cursor()
                c.execute("""
                    INSERT INTO pending_approvals
                    (token, user_id, sender_name, incoming_msg, ai_suggestion, language, timestamp,
                     is_group, chat_id, chat_title, topic_id, topic_name, source_language,
                     translated_message, original_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    token, user_id, sender_name, translated_for_you,
                    approval_data['ai_suggestion'], source_language['name'], datetime.now(),
                    int(is_group), chat_id, chat_title, topic_id, topic_name,
                    source_language['code'], translated_for_you, text
                ))
                conn.commit()
                conn.close()

                me           = await user_client.get_me()
                notification = ai_handler.format_notification(result)
                notification += f"\n🌍 Language: {source_language['name']}"
                if is_group:
                    notification += f"\n📣 Translations by @{BOT_USERNAME}"
                notification += f"\n👤 Replies via personal account"
                ai_topic = result['classification'].get('topic', '')
                if ai_topic:
                    notification += f"\n🧠 AI Topic: {ai_topic}"
                notification += f"\n\n🔗 {DASHBOARD_URL}/approve/{token}"
                await user_client.send_message(me, notification)
                print("📬 Notification sent to dashboard (fallback)")

        print(f"{'='*70}\n")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback; traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

@user_client.on(events.NewMessage(pattern='/language'))
async def set_language_command(event):
    try:
        sender    = await event.get_sender()
        parts     = event.message.message.split()
        if len(parts) < 2:
            lang_list = "\n".join([f"• {code} - {name}"
                                   for code, name in translator.LANGUAGES.items()])
            await event.reply(f"🌍 Available:\n\n{lang_list}\n\nUsage: /language <code>")
            return
        lang_code = parts[1].lower()
        if lang_code not in translator.LANGUAGES:
            await event.reply(f"❌ Invalid: {lang_code}")
            return
        translator.set_user_language(sender.id, lang_code)
        await event.reply(f"✅ Set to: {translator.LANGUAGES[lang_code]}")
    except Exception as e:
        print(f"❌ /language error: {e}")


@user_client.on(events.NewMessage(pattern='/status'))
async def status_command(event):
    try:
        sender    = await event.get_sender()
        user_lang = translator.get_user_language(sender.id)
        lang_name = translator.LANGUAGES.get(user_lang, user_lang)
        status    = (
            f"🤖 **Status**\n\n"
            f"✅ User: Active\n"
            f"✅ Bot: @{BOT_USERNAME}\n\n"
            f"🗣️ Language: {lang_name}\n"
        )
        for lang in TRANSLATION_SETTINGS['group_languages']:
            status += f"• {translator.LANGUAGES.get(lang)}\n"
        status += (
            "\n🤖 Bot: Translations in groups\n"
            "👤 You: Everything else\n\n"
            "/language <code>\n/status"
        )
        await event.reply(status)
    except Exception as e:
        print(f"❌ /status error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# SPECIAL GROUP LANGUAGE COMMANDS (in-chat) — works via user_client
# Bot-client commands are registered in main() after bot_client is available
# ═══════════════════════════════════════════════════════════════════════════════

def _special_langs(cfg):
    """Target langs for special group. N-lang aware; backward-compat old lang_1/lang_2."""
    if not cfg:
        return []
    ls = cfg.get('langs')
    if isinstance(ls, str):
        try:
            ls = json.loads(ls)
        except Exception:
            ls = None
    if not ls:
        ls = [cfg.get('lang_1'), cfg.get('lang_2')]
    out = []
    for l in ls:
        if l and l not in out:
            out.append(l)
    return out


async def _handle_setlang(event, owner_id=None):
    """Shared /setlang logic — anyone in group can use"""
    try:
        chat = await event.get_chat()
        chat_id = event.chat_id

        if not event.is_group:
            return

        parts = event.message.message.split()
        if len(parts) < 3:
            lang_list = "\n".join([f"  `{code}` — {name}"
                                   for code, name in translator.LANGUAGES.items()
                                   if code != 'auto'])
            await event.reply(
                f"🌍 **Spracheinstellungen**\n\n"
                f"Verwendung: `/setlang <sprache1> <sprache2> [sprache3 ...]`\n"
                f"Beispiel: `/setlang de en pl`\n\n"
                f"Verfügbare Sprachen:\n{lang_list}"
            )
            return

        valid = [k for k in translator.LANGUAGES if k != 'auto']
        # Accept 2+ languages; dedupe, keep order
        req_langs = []
        for p in parts[1:]:
            code = p.lower()
            if code and code not in req_langs:
                req_langs.append(code)

        invalid = [c for c in req_langs if c not in valid]
        if invalid:
            await event.reply(f"❌ Ungültiger Sprachcode: {', '.join(invalid)}\nGültig: {', '.join(valid)}")
            return

        if len(req_langs) < 2:
            await event.reply("❌ Mindestens 2 verschiedene Sprachen angeben.")
            return

        chat_title = getattr(chat, 'title', f'Group {chat_id}')
        add_special_group(chat_id, req_langs, name=chat_title)

        names = " ↔ ".join(translator.LANGUAGES.get(c, c) for c in req_langs)
        await event.reply(
            f"✅ **Spezialgruppe registriert!**\n\n"
            f"📍 {chat_title}\n"
            f"🔄 {names}\n\n"
            f"In dieser Gruppe werden nur diese {len(req_langs)} Sprachen übersetzt."
        )
        print(f"✅ /setlang: {chat_title} → {names}")

    except Exception as e:
        print(f"❌ /setlang error: {e}")
        await event.reply(f"❌ Error: {e}")


async def _handle_removelang(event, owner_id=None):
    """Shared /removelang logic — anyone in group can use"""
    try:
        if not event.is_group:
            return

        chat_id = event.chat_id
        cfg = get_special_group_config(chat_id)
        if not cfg:
            await event.reply("ℹ️ Diese Gruppe ist nicht im Spezialmodus.")
            return

        remove_special_group(chat_id)
        await event.reply(
            f"✅ Spezialmodus entfernt.\n"
            f"Diese Gruppe verwendet jetzt die normalen Übersetzungseinstellungen."
        )
        print(f"✅ /removelang: group {chat_id} removed from special mode")

    except Exception as e:
        print(f"❌ /removelang error: {e}")
        await event.reply(f"❌ Error: {e}")


async def _handle_grouplang(event, owner_id=None):
    """Shared /grouplang logic — anyone in group can use"""
    try:
        chat_id = event.chat_id
        cfg = get_special_group_config(chat_id)

        if cfg:
            names = " ↔ ".join(translator.LANGUAGES.get(c, c) for c in _special_langs(cfg))
            await event.reply(
                f"🌍 **Spezialgruppen-Konfiguration**\n\n"
                f"📍 {cfg.get('group_name', 'Unbekannt')}\n"
                f"🔄 {names}\n\n"
                f"Befehle:\n"
                f"  `/setlang <sprache1> <sprache2> [sprache3 ...]` — ändern\n"
                f"  `/removelang` — Spezialmodus entfernen"
            )
        else:
            await event.reply(
                f"ℹ️ Diese Gruppe ist nicht im Spezialmodus.\n"
                f"Normale Übersetzungseinstellungen sind aktiv.\n\n"
                f"`/setlang de en` — Spezialmodus aktivieren"
            )

    except Exception as e:
        print(f"❌ /grouplang error: {e}")


# Register on user_client (for groups where Lothar is member)
@user_client.on(events.NewMessage(pattern='/setlang'))
async def setlang_user(event):
    await _handle_setlang(event, YOUR_USER_ID)

@user_client.on(events.NewMessage(pattern='/removelang'))
async def removelang_user(event):
    await _handle_removelang(event, YOUR_USER_ID)

@user_client.on(events.NewMessage(pattern='/grouplang'))
async def grouplang_user(event):
    await _handle_grouplang(event, YOUR_USER_ID)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    global YOUR_USER_ID, BOT_USER_ID, bot_client, _processed_events_lock
    _processed_events_lock = asyncio.Lock()

    print("\n" + "="*70)
    print("🤖 DUAL-CLIENT BOT v3")
    print("="*70)
    print("🤖 BOT     = Translation messages in groups ONLY")
    print("👤 YOU     = All DMs + All approved replies")
    print("💾 SAVE    = Every message analyzed + saved (group + DM)")
    print("🧠 TOPIC   = AI detects from content (not just thread name)")
    print(f"📊 REPLY   = Only when confidence >= {ai_handler.REPLY_CONFIDENCE_THRESHOLD}%")
    print("✅ SESSION = StringSession — no file lock conflict with app.py")
    print("="*70 + "\n")

    init_db()

    # Start centralized DB writer — permanent fix for "database is locked"
    db_writer.start()

    print("🔌 Connecting user account via StringSession...")
    await user_client.start(phone=YOUR_PHONE)
    me           = await user_client.get_me()
    YOUR_USER_ID = me.id
    translator.set_user_language(YOUR_USER_ID, YOUR_LANGUAGE)
    print(f"✅ User: {me.first_name} (@{me.username}) | ID: {YOUR_USER_ID}\n")

    print("🤖 Connecting bot...")
    bot_client = TelegramClient(
        "bot_session", YOUR_API_ID, YOUR_API_HASH,
        connection_retries=None,      # retry forever
        retry_delay=2,
        auto_reconnect=True,
        request_retries=5,
        timeout=30,
    )
    await bot_client.start(bot_token=BOT_TOKEN)
    bot_me     = await bot_client.get_me()
    BOT_USER_ID = bot_me.id
    print(f"✅ Bot: @{bot_me.username} | ID: {BOT_USER_ID}\n")

    # ── Phase 2: Initialize Approval Handler ──
    global approval_handler
    if APPROVAL_ENABLED and APPROVAL_CHAT_ID:
        approval_handler = ApprovalHandler(
            bot_client=bot_client,
            user_client=user_client,
            approval_chat_id=APPROVAL_CHAT_ID,
            db_path=DB,
            translator=translator,
            your_language=YOUR_LANGUAGE,
            knowledge_base=kb,
        )

        # Register callback handler for inline buttons (approve/edit/reject)
        @bot_client.on(events.CallbackQuery)
        async def approval_callback_handler(event):
            data = event.data.decode('utf-8') if event.data else ''
            if data.startswith(('approve:', 'edit:', 'reject:')):
                await approval_handler.handle_callback(event)
            elif data.startswith('cancel_edit:'):
                await approval_handler.handle_cancel_edit(event)
            elif data.startswith('kifa:'):
                # KI Freigaben: Aufnahme → SOP oder Gesprächsanalyse (Button-Wahl)
                try:
                    parts = data.split(':')
                    action, cid, mid = parts[1], int(parts[2]), int(parts[3])
                    orig = await user_client.get_messages(cid, ids=mid)
                    pm = await event.get_message()
                    if not orig:
                        await event.answer("⚠️ Aufnahme nicht gefunden")
                    elif action == 'call':
                        try: await pm.edit("📞 Gespräch wird analysiert…", buttons=None)
                        except Exception: pass
                        await event.answer("📞 Analyse gestartet")
                        if call_analyzer:
                            await call_analyzer.analyze(orig)
                    elif action == 'sop':
                        try: await pm.edit("📝 Wird als SOP gespeichert…", buttons=None)
                        except Exception: pass
                        await event.answer("📝 SOP gestartet")
                        import tempfile
                        vp = os.path.join(tempfile.gettempdir(), f"voice_{orig.id}.ogg")
                        await user_client.download_media(orig, vp)
                        res = await sop_manager.process_message(voice_path=vp)
                        if res:
                            try: await bot_client.send_message(APPROVAL_CHAT_ID, res)
                            except Exception: await bot_client.send_message(APPROVAL_CHAT_ID, res.replace('**','').replace('__',''))
                        try: os.remove(vp)
                        except Exception: pass
                except Exception as e:
                    print(f"⚠️ kifa callback error: {e}")
                    try: await event.answer("⚠️ Fehler")
                    except Exception: pass
            elif data.startswith('leadsrc:'):
                try:
                    await lead_tracker.handle_callback(event)
                except NameError:
                    pass  # lead_tracker not yet initialized
            elif data.startswith('kbsug:'):
                # Phase B: KB suggestion buttons (OK/NotOK/Revise/Explain)
                try:
                    await suggestion_manager.handle_callback(event)
                except Exception as e:
                    print(f"⚠️ KB suggestion callback error: {e}")
            elif data.startswith('tts:'):
                # TTS: read posting aloud (Fable voice)
                try:
                    await tts_manager.handle_callback(event)
                except Exception as e:
                    print(f"⚠️ TTS callback error: {e}")
            elif data.startswith('empok:'):
                # Employee confirms answer is sufficient
                try:
                    msg = await event.get_message()
                    await msg.edit(
                        msg.text + "\n\n✅ **Beantwortet** — Mitarbeiter hat bestätigt.",
                        buttons=None,
                    )
                    await event.answer("✅ Antwort bestätigt!")
                    print(f"✅ Employee confirmed KB answer (no Lothar notification)")
                except Exception as e:
                    print(f"⚠️ Employee confirm error: {e}")
            elif data.startswith('empfwd:'):
                # Employee wants to forward to Lothar
                try:
                    msg = await event.get_message()
                    # Extract original answer from message
                    original_text = msg.text or ''

                    # Send to KI Freigaben for Lothar
                    fwd_msg = (
                        f"📨 **Weiterleitung von Mitarbeiter**\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"{original_text}\n\n"
                        f"ℹ️ Mitarbeiter war nicht zufrieden mit der KI-Antwort."
                    )
                    await bot_client.send_message(APPROVAL_CHAT_ID, fwd_msg)

                    # Update original message
                    await msg.edit(
                        msg.text + "\n\n📨 **An Lothar weitergeleitet.**",
                        buttons=None,
                    )
                    await event.answer("📨 An Lothar weitergeleitet!")
                    print(f"📨 Employee forwarded to Lothar")
                except Exception as e:
                    print(f"⚠️ Employee forward error: {e}")

        # Register message handler for edit mode (corrected answers from Lothar)
        @bot_client.on(events.NewMessage(chats=[APPROVAL_CHAT_ID], incoming=True))
        async def approval_edit_handler(event):
            if approval_handler:
                await approval_handler.handle_edit_message(event)

        print(f"✅ Approval Handler ENABLED (chat: {APPROVAL_CHAT_ID})")
    else:
        if not APPROVAL_CHAT_ID:
            print("⚠️  Approval Handler DISABLED (set APPROVAL_CHAT_ID in .env)")
        else:
            print("⚠️  Approval Handler DISABLED (APPROVAL_ENABLED=False)")

    print("🚀 Starting outgoing worker...")
    asyncio.create_task(outgoing_worker())
    register_bot_forwarder(bot_client)

    # ── Phase 5: Daily Report at 19:00 ──
    # KB Suggestion Manager (Phase B — evening suggestions with buttons)
    from bot.knowledge.kb_suggestion_manager import KBSuggestionManager
    suggestion_manager = KBSuggestionManager(
        db_path=DB,
        bot_client=bot_client,
        kb=kb,
        approval_chat_id=APPROVAL_CHAT_ID,
    )

    # TTS Manager (text-to-speech — Fable voice, listen to postings)
    global tts_manager
    from bot.reports.tts_manager import TTSManager
    tts_manager = TTSManager(
        openai_api_key=OPENAI_API_KEY,
        db_path=DB,
        bot_client=bot_client,
    )
    if approval_handler:
        approval_handler.tts_manager = tts_manager

    daily_report = DailyReport(
        db_path=DB,
        bot_client=bot_client,
        approval_chat_id=APPROVAL_CHAT_ID,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        prefilter=prefilter,
        classifier=classifier,
        kb=kb,
        # KB-Verbesserungsvorschläge: standardmäßig AUS (Lothar: nerven). Wieder an via .env KB_SUGGESTIONS_ENABLED=True
        suggestion_manager=(suggestion_manager if os.getenv("KB_SUGGESTIONS_ENABLED", "False") == "True" else None),
    )
    daily_report.start_scheduler()

    # ── Baufortschritt: tägliche KI-Foto-Analyse (19:05) → KI Freigaben ──
    from bot.reports.baufortschritt import BaufortschrittReporter
    baufortschritt = BaufortschrittReporter(
        user_client=user_client,
        bot_client=bot_client,
        approval_chat_id=APPROVAL_CHAT_ID,
        openai_api_key=OPENAI_API_KEY,
    )
    baufortschritt.start_scheduler()

    # ── Call-Analyse: Kundengespräche transkribieren + auswerten (KI Freigaben) ──
    global call_analyzer
    from bot.reports.call_analysis import CallAnalyzer
    call_analyzer = CallAnalyzer(
        user_client=user_client,
        bot_client=bot_client,
        approval_chat_id=APPROVAL_CHAT_ID,
        openai_api_key=OPENAI_API_KEY,
    )

    # ── /fortschritt — manuelle Baufortschritt-Analyse (on-demand) ──
    @user_client.on(events.NewMessage(pattern=r'^/fortschritt', incoming=True, outgoing=True))
    async def fortschritt_cmd(event):
        try:
            chat = await event.get_chat()
            tl = (getattr(chat, 'title', '') or '').lower()
            try:
                await event.reply("⏳ Baufortschritt wird analysiert… (kann ~30s dauern)")
            except Exception:
                pass
            _pfx = ['baustart','baustelle','in bau','construction','nacharbeit','reklamation']
            if any(tl.startswith(p) for p in _pfx):
                n = await baufortschritt.run_for_chat(event.chat_id)   # nur diese Gruppe
            else:
                n = await baufortschritt.generate_and_send()           # alle Baustellen
            if not n:
                try:
                    await bot_client.send_message(APPROVAL_CHAT_ID, "ℹ️ Keine Fotos von heute gefunden.")
                except Exception:
                    pass
        except Exception as e:
            print(f"⚠️ /fortschritt error: {e}")
    print("✅ /fortschritt command registered")



    # ── Phase 6: Lead Source Tracker ──
    lead_tracker = LeadSourceTracker(
        bot_client=bot_client,
        approval_chat_id=APPROVAL_CHAT_ID,
        crm_api_url=os.getenv("CRM_API_URL", ""),
        crm_api_key=os.getenv("CRM_BOT_API_KEY", ""),
    )
    # Lead-Tracker: standardmäßig AUS (Lothar: "Lead without origin" Spam). Wieder an via .env LEAD_TRACKER_ENABLED=True
    if os.getenv("LEAD_TRACKER_ENABLED", "False") == "True":
        lead_tracker.start_scheduler()
        print("✅ Lead Source Tracker ENABLED")
    else:
        print("⚠️  Lead Source Tracker DISABLED (weniger Rauschen)")

    # ── Cross-group reply handler ─────────────────────────────────────────────
    # Dynamic — rebuilds every call so new worker group mappings work without restart
    def _get_dest_chat_abs_ids():
        return {abs(int(v)) for v in BOT_DEST_MAP.values()}

    @user_client.on(events.NewMessage(incoming=True, outgoing=True))
    async def cross_group_reply_handler(event):
        """
        Destination group mein quote-reply detect karo → source group mein route karo.
        incoming=True → doosre log reply karein
        outgoing=True → tum khud reply karo
        """
        try:
            msg = event.message
            if not msg or not msg.text or not msg.reply_to:
                return

            # ── Bot ke messages skip karo ─────────────────────
            sender = await event.get_sender()
            if sender and BOT_USER_ID and sender.id == BOT_USER_ID:
                return

            chat    = await event.get_chat()
            chat_id = chat.id

            # ── Sirf destination groups check karo ───────────
            normalized_chat = abs(int(chat_id))
            chat_id_str     = str(normalized_chat)
            dest_chat_abs_ids = _get_dest_chat_abs_ids()
            in_dest = (
                normalized_chat in dest_chat_abs_ids
                or any(str(d).endswith(chat_id_str) for d in dest_chat_abs_ids)
            )
            if not in_dest:
                return

            # ── Actual quoted message ID ──────────────────────
            replied_msg_id = getattr(msg.reply_to, 'reply_to_msg_id', None)
            if not replied_msg_id:
                return

            print(f"\n↩️  DEST→SOURCE REPLY CHECK: dest={chat_id}, quoted_msg={replied_msg_id}")

            source_info = get_reply_source(replied_msg_id, chat_id)
            if not source_info:
                print(f"   ℹ️  Not a tracked bot message — normal reply, skip")
                return

            source_chat_id  = source_info['source_chat_id']   # abs() stored
            source_topic_id = source_info['source_topic_id']
            original_sender = source_info['sender_name'] or 'Unknown'
            source_msg_id   = source_info['source_msg_id']    # original msg to quote

            reply_text  = msg.text
            sender_name = ""
            if sender:
                sender_name = (getattr(sender, 'first_name', '') or '').strip()
                if getattr(sender, 'last_name', ''):
                    sender_name += f" {sender.last_name}"

            dest_title = getattr(chat, 'title', str(chat_id))

            print(f"   ✅ Routing: {chat_id} → source group {source_chat_id}")
            print(f"   💬 Quoting source_msg_id={source_msg_id}")

            # ── src_formatted: abs() stored value → -100XXXXX ─
            src_str = str(source_chat_id)
            if src_str.startswith("100"):
                src_formatted = int(f"-{src_str}")
            else:
                src_formatted = int(f"-100{src_str}")

            # ── Client select karo ────────────────────────────
            client_to_use = user_client
            try:
                await user_client.get_input_entity(src_formatted)
            except ValueError:
                print(f"   ℹ️  user_client not in source group — switching to bot_client")
                client_to_use = bot_client

            # ── Translation — same logic as normal messages ───
            # Source group ki language determine karo
            detected_lang = translator.detect_language(reply_text)
            source_lang_code = detected_lang['code']

            # Special group config check (source group ke liye)
            special_cfg = get_special_group_config(src_formatted)
            if special_cfg:
                langs_to_translate = [l for l in _special_langs(special_cfg) if l != source_lang_code]
            else:
                # Normal: TRANSLATION_SETTINGS se — sender ki language chhodke baaki sab
                langs_to_translate = [
                    l for l in TRANSLATION_SETTINGS.get('group_languages', [])
                    if l != source_lang_code
                ]

            print(f"   🌍 Translating reply: {source_lang_code} → {langs_to_translate}")

            # ── Messages build karo (original + translations) ─
            header = f"↩️ [{dest_title}] {sender_name}:\n{reply_text}"
            messages_to_send = [header]   # pehla message: original reply

            for tl in langs_to_translate:
                try:
                    tr = translator.translate(
                        text=reply_text,
                        target_lang=tl,
                        source_lang=source_lang_code
                    )
                    lang_name = translator.LANGUAGES.get(tl, tl)
                    messages_to_send.append(f"{lang_name}:\n{tr['translated_text']}")
                    print(f"   ✅ Translated → {lang_name}")
                except Exception as te:
                    print(f"   ⚠️  Translation failed ({tl}): {te}")

            # ── Send karo — pehla message quoted reply ke saath ─
            try:
                first = True
                for msg_text in messages_to_send:
                    if first:
                        # Pehla message: original message ko quote karke
                        if source_msg_id:
                            await client_to_use.send_message(
                                src_formatted, msg_text, reply_to=int(source_msg_id)
                            )
                        elif source_topic_id:
                            await client_to_use.send_message(
                                src_formatted, msg_text, reply_to=source_topic_id
                            )
                        else:
                            await client_to_use.send_message(src_formatted, msg_text)
                        first = False
                    else:
                        # Baaki translations: bina quote ke same group mein
                        await client_to_use.send_message(src_formatted, msg_text)
                    await asyncio.sleep(0.3)

                print(f"   ✅ Reply + translations routed to source group {src_formatted}")
            except Exception as e:
                print(f"   ❌ Send failed: {e}")
                import traceback; traceback.print_exc()

        except Exception as e:
            print(f"   ❌ Cross-group reply error: {e}")
            import traceback; traceback.print_exc()

    print(f"✅ Cross-group reply handler registered (watching {len(_get_dest_chat_abs_ids())} destination group(s), dynamic)")

    # ── Bot-only groups handler ───────────────────────────────────────────────
    @bot_client.on(events.NewMessage(incoming=True))
    async def bot_group_message_handler(event):
        try:
            if not event.is_group:
                return
            msg = event.message
            if not msg or not msg.text:
                return
            chat      = await event.get_chat()
            chat_id   = chat.id
            sender    = await event.get_sender()
            if not sender:
                return
            sender_id = sender.id
            if sender_id == BOT_USER_ID:
                return

            try:
                await user_client.get_input_entity(chat_id)
                return
            except Exception:
                pass

            text        = msg.text

            # Skip bot commands — don't translate them
            if text.startswith('/setlang') or text.startswith('/removelang') or text.startswith('/grouplang'):
                return

            sender_name = (getattr(sender, 'first_name', '') or '').strip()
            if getattr(sender, 'last_name', ''):
                sender_name += f" {sender.last_name}"
            chat_title = getattr(chat, 'title', str(chat_id))
            topic_id   = None
            if msg.reply_to:
                topic_id = (getattr(msg.reply_to, 'reply_to_top_id', None)
                            or getattr(msg.reply_to, 'reply_to_msg_id', None))

            print(f"\n🤖 BOT-ONLY GROUP: {chat_title} | From: {sender_name}")
            print(f"   💬 {text[:80]}")

            event_key = (chat_id, msg.id)
            if event_key in _processed_incoming:
                return
            _processed_incoming.add(event_key)
            _processed_incoming_order.append(event_key)
            if len(_processed_incoming_order) > 5000:
                old = _processed_incoming_order.pop(0)
                _processed_incoming.discard(old)

            await async_store_group_message(
                chat_id=chat_id, topic_id=topic_id,
                sender_id=sender_id, sender_name=sender_name,
                message_text=text, chat_title=chat_title, topic_name=''
            )

            if not TRANSLATION_SETTINGS.get('enabled'):
                return

            dest_id = BOT_DEST_MAP.get(chat_id) or BOT_DEST_MAP.get(int(f"-100{abs(chat_id)}"))
            grp_targets = [chat_id]
            if dest_id and abs(int(dest_id)) != abs(int(chat_id)):
                grp_targets.append(dest_id)

            source_lang = translator.detect_language(text)

            # Check special group config first
            special_cfg = get_special_group_config(chat_id)
            if special_cfg:
                all_langs = _special_langs(special_cfg)
                langs = [l for l in all_langs if l != source_lang['code']]
                print(f"   🌍 Special: {all_langs} | detected={source_lang['code']} → {langs}")
            else:
                langs = [l for l in TRANSLATION_SETTINGS.get('group_languages', []) if l != source_lang['code']]

            computed = {}
            for tl in langs:
                ln = translator.LANGUAGES.get(tl, tl)
                tr = translator.translate(text=text, target_lang=tl, source_lang=source_lang['code'])
                computed[tl] = (ln, tr['translated_text'])

            if not computed:
                print(f"   ⏭️  No translation needed")
                return

            for grp_id in grp_targets:
                is_dest = dest_id and abs(int(grp_id)) == abs(int(dest_id)) and abs(int(grp_id)) != abs(int(chat_id))
                group_tag = f"📍 {chat_title}\n" if is_dest else ""
                for tl, (ln, translated_text) in computed.items():
                    if is_dest:
                        msg_text = f"{group_tag}👤 {sender_name}:\n{text}\n\n{ln}:\n{translated_text}"
                    else:
                        msg_text = translated_text
                    await async_queue_message(
                        user_id=sender_id, message=msg_text,
                        chat_id=grp_id, topic_id=topic_id,
                        target_language=tl,
                        sender_name=sender_name,
                        message_category='translation', sender_type='bot', is_group=True,
                        original_msg_id=msg.id,   # ← original msg ID pass karo
                    )
                print(f"   ✅ Queued to group {grp_id}")

        except Exception as e:
            print(f"   ❌ Bot-only handler error: {e}")
            import traceback; traceback.print_exc()

    print("✅ Bot-only group handler registered")

    # ── Bot-client language commands (for groups where only bot is member) ──
    @bot_client.on(events.NewMessage(pattern='/setlang'))
    async def setlang_bot(event):
        await _handle_setlang(event, YOUR_USER_ID)

    @bot_client.on(events.NewMessage(pattern='/removelang'))
    async def removelang_bot(event):
        await _handle_removelang(event, YOUR_USER_ID)

    @bot_client.on(events.NewMessage(pattern='/grouplang'))
    async def grouplang_bot(event):
        await _handle_grouplang(event, YOUR_USER_ID)

    print("✅ Bot language commands registered (/setlang, /removelang, /grouplang)")

    print("\n" + "="*70)
    print("✅ BOT RUNNING — Translation languages:")
    for lang in TRANSLATION_SETTINGS['group_languages']:
        print(f"   • {translator.LANGUAGES.get(lang)}")
    print("\nPress Ctrl+C to stop\n")
    # ── Construction Video Handler ──────────────────────────
    from bot.handlers.construction_telegram_handler import register_construction_handler
    register_construction_handler(user_client, YOUR_USER_ID, DASHBOARD_URL, BAUDOKU_GROUP_ID)
      # ── Receipt Photo Handler ─────────────────────────────
    from bot.handlers.receipt_telegram_handler import register_receipt_handler
    register_receipt_handler(user_client, bot_client)

    # ── Telegram → Google Drive Photo Sync ──────────────────────────
    from bot.handlers.tg_drive_sync import register_drive_sync_handler
    register_drive_sync_handler(user_client, bot_client)
    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected()
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Stopped")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback; traceback.print_exc()