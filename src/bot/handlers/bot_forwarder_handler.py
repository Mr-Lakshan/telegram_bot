# ══════════════════════════════════════════════════════════════════
# BOT FORWARDER HANDLER — Dynamic Multi-Group Support
#
# CHANGES FROM ORIGINAL:
#   1. Loads .env rules (backward compatible) + SQLite dynamic mappings
#   2. Periodic refresh of dynamic mappings (every 30 seconds)
#   3. No chats= filter on handlers — runtime check instead
#      (because dynamic groups can be added without restart)
#   4. Worker group messages: include [GroupName] WorkerName: prefix
#      in destination groups
#   5. Reply routing works for ALL mapped pairs (not just one)
#
# KAHAN ADD KARO:
#   Existing @user_client.on(events.NewMessage...) handler ke BAAD,
#   lekin main() function se PEHLE
# ══════════════════════════════════════════════════════════════════


import os, json, sqlite3, threading, time
from datetime import datetime
from telethon import events

DB = os.getenv("DB_PATH", "bot_data.db")


# ══════════════════════════════════════════════════════════
#  DB HELPERS
# ══════════════════════════════════════════════════════════

def _get_db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _norm_id(chat_id) -> int:
    """Normalize to positive integer for consistent storage/lookup."""
    return abs(int(chat_id))


def _strip_100(chat_id) -> int:
    """
    Strip -100 prefix that Telegram adds for supergroups.
    -1003788638904 → 3788638904
    1003788638904  → 3788638904
    3788638904     → 3788638904 (unchanged)
    """
    s = str(abs(int(chat_id)))
    if s.startswith("100") and len(s) > 10:
        return int(s[3:])
    return abs(int(chat_id))


def _to_tg_id(stored_id: int) -> int:
    """
    Convert stored ID back to Telegram -100XXXXXXXXXX format.
    3553532270    → -1003553532270
    1003553532270 → -1003553532270
    -1003553532270 → -1003553532270 (unchanged)
    """
    n = abs(int(stored_id))
    s = str(n)
    if s.startswith("100") and len(s) > 10:
        return -n  # already has 100 prefix
    else:
        return int(f"-100{n}")  # add -100 prefix


# ══════════════════════════════════════════════════════════
#  FORWARD RULES — .env (static) + SQLite (dynamic)
# ══════════════════════════════════════════════════════════

def _load_env_forward_rules(silent=False):
    """Load static rules from .env (backward compatible)."""
    rules = []

    raw = os.getenv("FORWARD_RULES", "")
    if raw:
        try:
            rules = json.loads(raw)
            if not silent:
                print(f"✅ Bot Forwarder: {len(rules)} static rule(s) from FORWARD_RULES")
            return rules
        except Exception as e:
            print(f"⚠️  FORWARD_RULES parse error: {e}")

    src  = os.getenv("SOURCE_GROUP_ID", "")
    dest = os.getenv("DESTINATION_GROUP_ID", "")
    if src and dest:
        rules = [{"source": int(src), "destination": int(dest), "label": "Default (.env)"}]
        if not silent:
            print(f"✅ Bot Forwarder: 1 static rule from .env (SOURCE→DESTINATION)")

    return rules


def _load_dynamic_mappings(silent=False):
    """Load active mappings from SQLite group_pair_mappings table."""
    rules = []
    try:
        conn = _get_db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS group_pair_mappings (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                source_chat_id        INTEGER NOT NULL,
                destination_chat_id   INTEGER NOT NULL,
                source_group_name     TEXT    DEFAULT NULL,
                source_topic_id       INTEGER DEFAULT NULL,
                destination_topic_id  INTEGER DEFAULT NULL,
                is_active             INTEGER NOT NULL DEFAULT 1,
                created_at            TEXT    DEFAULT NULL,
                updated_at            TEXT    DEFAULT NULL,
                UNIQUE(source_chat_id, destination_chat_id)
            )
        """)
        cursor = conn.execute("""
            SELECT source_chat_id, destination_chat_id, source_group_name
            FROM group_pair_mappings
            WHERE is_active = 1
        """)
        for row in cursor.fetchall():
            rules.append({
                "source":      int(row[0]),
                "destination": int(row[1]),
                "label":       row[2] or f"Dynamic {row[0]}→{row[1]}",
                "is_dynamic":  True,
            })
        conn.close()
        if rules and not silent:
            print(f"✅ Bot Forwarder: {len(rules)} dynamic mapping(s) from SQLite")
    except Exception as e:
        print(f"⚠️  Dynamic mappings load error: {e}")
    except Exception as e:
        print(f"⚠️  Dynamic mappings load error: {e}")
    return rules


# ── Thread-safe mapping state ─────────────────────────────
class _MappingState:
    """Thread-safe container for forward rules and lookup maps."""

    def __init__(self):
        self.lock = threading.Lock()
        self.forward_rules = []
        self.source_ids    = []       # All source chat IDs
        self.dest_map      = {}       # source_id → dest_id
        self.reverse_map   = {}       # norm(dest) → norm(source)
        self.dest_chat_ids = []       # All destination chat IDs (abs)
        self.group_names   = {}       # source_id → group_name
        self.reload()

    def reload(self, silent=False):
        """Reload all rules from .env + SQLite."""
        env_rules     = _load_env_forward_rules(silent=silent)
        dynamic_rules = _load_dynamic_mappings(silent=silent)
        all_rules     = env_rules + dynamic_rules

        with self.lock:
            self.forward_rules = all_rules

            # Build dest_map with ALL possible key formats:
            # Telethon gives chat.id as positive (3788638904)
            # .env stores as negative (-1003788638904)
            # We need lookup to work for BOTH
            self.dest_map = {}
            self.group_names = {}
            for r in all_rules:
                src = int(r["source"])
                dst = int(r["destination"])
                label = r.get("label", "")

                # Store original format
                self.dest_map[src] = dst
                self.group_names[src] = label

                # Store abs (no -100 prefix) format too
                src_abs = _norm_id(src)  # e.g. 1003788638904
                self.dest_map[src_abs] = dst
                self.group_names[src_abs] = label

                # Store short format (strip 100 prefix) — what Telethon gives
                src_short = _strip_100(src)  # e.g. 3788638904
                self.dest_map[src_short] = dst
                self.group_names[src_short] = label

            self.source_ids = list(self.dest_map.keys())
            self.reverse_map = {}
            for src, dst in self.dest_map.items():
                self.reverse_map[_norm_id(dst)] = _norm_id(src)
                self.reverse_map[_strip_100(dst)] = _strip_100(src)
            self.dest_chat_ids = list(self.reverse_map.keys())

        total = len(all_rules)
        env_count = len(env_rules)
        dyn_count = len(dynamic_rules)
        if not silent:
            print(f"📊 Mapping state: {total} rules ({env_count} static + {dyn_count} dynamic)")

    def is_source(self, chat_id) -> bool:
        with self.lock:
            return int(chat_id) in self.source_ids

    def is_destination(self, chat_id) -> bool:
        with self.lock:
            return _norm_id(chat_id) in self.dest_chat_ids

    def get_dest(self, source_id) -> int:
        with self.lock:
            return self.dest_map.get(int(source_id))

    def get_source_from_dest(self, dest_id) -> int:
        with self.lock:
            return self.reverse_map.get(_norm_id(dest_id))

    def get_group_name(self, source_id) -> str:
        with self.lock:
            return self.group_names.get(int(source_id), "")


# Global state
MAPPING = _MappingState()
ADD_SENDER_PREFIX = os.getenv("FORWARD_ADD_SENDER", "False") == "True"

# ── Backward compatibility ────────────────────────────────
# telegram_bot_groups.py imports BOT_DEST_MAP — keep it working
# This is a LIVE reference that updates when MAPPING.reload() runs
class _CompatDictProxy(dict):
    """Dict proxy that always reflects MAPPING.dest_map current state.
    Tries multiple key formats: original, abs, stripped-100."""
    def _lookup(self, key):
        k = int(key)
        v = MAPPING.dest_map.get(k)
        if v is not None: return v
        v = MAPPING.dest_map.get(abs(k))
        if v is not None: return v
        v = MAPPING.dest_map.get(_strip_100(k))
        if v is not None: return v
        return None
    def __getitem__(self, key):
        return self._lookup(key)
    def get(self, key, default=None):
        v = self._lookup(key)
        return v if v is not None else default
    def items(self):
        return MAPPING.dest_map.items()
    def values(self):
        return MAPPING.dest_map.values()
    def keys(self):
        return MAPPING.dest_map.keys()
    def __contains__(self, key):
        return self._lookup(key) is not None
    def __len__(self):
        return len(MAPPING.dest_map)
    def __bool__(self):
        return bool(MAPPING.dest_map)
    def __repr__(self):
        return repr(MAPPING.dest_map)

BOT_DEST_MAP = _CompatDictProxy()


# ── Background refresh thread ────────────────────────────
_REFRESH_INTERVAL = 30  # seconds

def _mapping_refresh_loop():
    """Periodically reload dynamic mappings from SQLite."""
    while True:
        time.sleep(_REFRESH_INTERVAL)
        try:
            MAPPING.reload(silent=True)
        except Exception as e:
            print(f"⚠️  Mapping refresh error: {e}")

_refresh_thread = threading.Thread(target=_mapping_refresh_loop, daemon=True)
_refresh_thread.start()
print(f"🔄 Mapping auto-refresh started (every {_REFRESH_INTERVAL}s)")


# ══════════════════════════════════════════════════════════
#  CROSS-GROUP REPLY MAP
# ══════════════════════════════════════════════════════════

def _track_forwarded_message(dest_msg_id, dest_chat_id, source_chat_id,
                              source_topic_id, sender_name):
    try:
        conn = _get_db()
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS cross_group_reply_map (
                dest_msg_id     INTEGER NOT NULL,
                dest_chat_id    INTEGER NOT NULL,
                source_chat_id  INTEGER NOT NULL,
                source_topic_id INTEGER,
                sender_name     TEXT,
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (dest_msg_id, dest_chat_id)
            )
        """)
        c.execute("""
            INSERT OR IGNORE INTO cross_group_reply_map
            (dest_msg_id, dest_chat_id, source_chat_id, source_topic_id, sender_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            dest_msg_id,
            _norm_id(dest_chat_id),
            _norm_id(source_chat_id),
            source_topic_id,
            sender_name,
            datetime.now()
        ))
        conn.commit()
        conn.close()
        print(f"   🗺️  Reply map saved: dest_msg={dest_msg_id} → source={source_chat_id}")
    except Exception as e:
        print(f"   ⚠️  _track_forwarded_message error: {e}")


def _get_reply_source(dest_msg_id, dest_chat_id):
    try:
        conn = _get_db()
        c = conn.cursor()
        c.execute("""
            SELECT source_chat_id, source_topic_id, sender_name
            FROM cross_group_reply_map
            WHERE dest_msg_id = ? AND dest_chat_id = ?
        """, (dest_msg_id, _norm_id(dest_chat_id)))
        row = c.fetchone()
        conn.close()
        if row:
            return {
                'source_chat_id' : row[0],
                'source_topic_id': row[1],
                'sender_name'    : row[2]
            }
    except Exception as e:
        print(f"⚠️  _get_reply_source error: {e}")
    return None


# ══════════════════════════════════════════════════════════
#  REGISTER HANDLERS
# ══════════════════════════════════════════════════════════

def register_bot_forwarder(bot_client_instance, user_client_instance=None):
    """
    Call this in main() after both clients are initialized:
        register_bot_forwarder(bot_client, user_client)

    Registers TWO handlers:
      1. Source → Destination forwarder (worker group → baustart group)
      2. Destination → Source reply router (baustart group reply → worker group)

    NO chats= filter — checks dynamically at runtime so new mappings
    work WITHOUT bot restart.
    """

    if not MAPPING.forward_rules:
        print("⚠️  No forward rules at startup — handlers still registered (dynamic mappings may arrive)")

    # ── Handler 1: DISABLED ───────────────────────────────────
    # telegram_bot_groups.py already handles source→destination forwarding
    # with translation for ALL pairs (static + dynamic) via BOT_DEST_MAP.
    # No separate forward handler needed — it only caused duplicates.
    # ────────────────────────────────────────────────────────

    print(f"✅ Bot forwarder registered (data layer only — forwarding delegated to telegram_bot_groups.py)")
    for r in MAPPING.forward_rules:
        print(f"   📡 {r.get('label','')}: {r['source']} → {r['destination']}")


    # ── Handler 2: DISABLED ──────────────────────────────────
    # telegram_bot_groups.py cross_group_reply_handler already handles
    # dest→source reply routing for ALL pairs (static + dynamic) with
    # translation support. No need for duplicate handler here.
    # ────────────────────────────────────────────────────────

    dest_count = len(MAPPING.dest_chat_ids)
    print(f"✅ Destination→Source reply router: delegated to telegram_bot_groups.py ({dest_count} destination group(s))")
    for norm_dest, norm_src in MAPPING.reverse_map.items():
        print(f"   ↩️  dest={norm_dest} → source={norm_src}")
        print("changes")