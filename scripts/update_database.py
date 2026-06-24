#!/usr/bin/env python3
"""
MIGRATION SCRIPT - v2
Naye tables add karo:
  - dm_messages       : DM conversations save karne ke liye
  - message_analysis  : Har message ki AI analysis save karne ke liye
  - group_messages mein topic_name + chat_title columns add karo

Run once before starting updated bot.
"""

import sqlite3

DB = "bot_data.db"


def migrate():
    print("🔄 Database migration starting...\n")
    conn = sqlite3.connect(DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()

    # ── 1. dm_messages table ────────────────────────────────────────────
    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS dm_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id   INTEGER,
                sender_name TEXT,
                message_text TEXT,
                timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("✅ dm_messages table ready")
    except Exception as e:
        print(f"⚠️  dm_messages: {e}")

    # ── 2. message_analysis table ────────────────────────────────────────
    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS message_analysis (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_message_id  INTEGER,
                source_type     TEXT,        -- 'group' or 'dm'
                sender_name     TEXT,
                chat_id         INTEGER,
                message_type    TEXT,
                urgency         TEXT,
                confidence      INTEGER,
                ai_topic        TEXT,        -- AI se detect hua topic (content se)
                intent          TEXT,
                suggested_action TEXT,
                needs_db_lookup INTEGER DEFAULT 0,
                entities_json   TEXT,        -- JSON string of extracted entities
                should_respond  INTEGER DEFAULT 1,
                response_reason TEXT,
                timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("✅ message_analysis table ready")
    except Exception as e:
        print(f"⚠️  message_analysis: {e}")

    # ── 3. group_messages mein naye columns ──────────────────────────────
    existing_cols_q = c.execute("PRAGMA table_info(group_messages)").fetchall()
    existing_cols = [col[1] for col in existing_cols_q]

    new_cols = [
        ("chat_title",  "TEXT DEFAULT ''"),
        ("topic_name",  "TEXT DEFAULT ''"),
    ]
    for col_name, col_def in new_cols:
        if col_name not in existing_cols:
            try:
                c.execute(f"ALTER TABLE group_messages ADD COLUMN {col_name} {col_def}")
                print(f"✅ group_messages: added column '{col_name}'")
            except Exception as e:
                print(f"⚠️  group_messages.{col_name}: {e}")
        else:
            print(f"ℹ️  group_messages: '{col_name}' already exists")

    # ── 4. outgoing_messages — ensure sender_type + message_category ────
    out_cols_q = c.execute("PRAGMA table_info(outgoing_messages)").fetchall()
    out_cols = [col[1] for col in out_cols_q]
    for col_name, col_def in [
        ("sender_type",      "TEXT DEFAULT 'user'"),
        ("message_category", "TEXT DEFAULT 'response'"),
    ]:
        if col_name not in out_cols:
            try:
                c.execute(f"ALTER TABLE outgoing_messages ADD COLUMN {col_name} {col_def}")
                print(f"✅ outgoing_messages: added column '{col_name}'")
            except Exception as e:
                print(f"⚠️  outgoing_messages.{col_name}: {e}")
        else:
            print(f"ℹ️  outgoing_messages: '{col_name}' already exists")

    # ── 5. bot_translation_messages ──────────────────────────────────────
    try:
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
        print("✅ bot_translation_messages table ready")
    except Exception as e:
        print(f"⚠️  bot_translation_messages: {e}")

    # ── 6. Indexes for fast queries ──────────────────────────────────────
    indexes = [
        ("idx_analysis_timestamp",  "message_analysis(timestamp)"),
        ("idx_analysis_source",     "message_analysis(source_type)"),
        ("idx_analysis_topic",      "message_analysis(ai_topic)"),
        ("idx_dm_messages_sender",  "dm_messages(sender_id)"),
        ("idx_group_msg_chat",      "group_messages(chat_id)"),
    ]
    for idx_name, idx_target in indexes:
        try:
            c.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_target}")
        except Exception:
            pass
    print("✅ Indexes created")

    conn.commit()
    conn.close()
    print("\n✅ Migration complete! Bot start karo ab.")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("DATABASE MIGRATION v2")
    print("=" * 60 + "\n")
    try:
        migrate()
    except Exception as e:
        import traceback
        print(f"\n❌ Migration failed: {e}")
        traceback.print_exc()
    print("\n" + "=" * 60)