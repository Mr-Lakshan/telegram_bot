#!/usr/bin/env python3
"""
SPECIAL GROUP SETUP UTILITY  (N-language)
Kisi bhi group ko special translation mode mein register karo.
Ab 2 ya zyada languages support hain.

Usage:
  python special_group_setup.py add <chat_id> <lang1> <lang2> [lang3 ...] [--name "Group Name"]
  python special_group_setup.py list
  python special_group_setup.py remove <chat_id>

Example (German-English-Polish group):
  python special_group_setup.py add -1001234567890 de en pl --name "Baustelle Klaus"

Chat ID kaise pata kare?
  - Bot ko group mein add karo
  - Koi bhi message bhejo
  - Terminal mein print hoga: "📣 GROUP: ... | chat_id: -100XXXXXXXXXX"
"""

import sqlite3
import sys
import argparse
import json
from datetime import datetime

DB = "bot_data.db"

VALID_LANGS = ['en', 'de', 'hi', 'pl', 'uk', 'ru', 'es', 'fr', 'it', 'pt', 'nl']
LANG_NAMES = {'en': 'English', 'de': 'German', 'hi': 'Hindi', 'pl': 'Polish',
              'uk': 'Ukrainian', 'ru': 'Russian', 'es': 'Spanish', 'fr': 'French',
              'it': 'Italian', 'pt': 'Portuguese', 'nl': 'Dutch'}


def get_db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_special_groups_table():
    """Create special_groups table + migrate old 2-lang schema to add `langs`."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS special_groups (
            chat_id INTEGER PRIMARY KEY,
            group_name TEXT,
            lang_1 TEXT,
            lang_2 TEXT,
            langs TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        )
    """)
    # Migrate older DBs: add `langs` column if missing
    try:
        cols = [r[1] for r in c.execute("PRAGMA table_info(special_groups)").fetchall()]
        if 'langs' not in cols:
            c.execute("ALTER TABLE special_groups ADD COLUMN langs TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()


def _norm_langs(langs, lang_2=None):
    """Accepts a list, or the legacy (lang_1, lang_2) positional form. Returns deduped lowercase list."""
    if isinstance(langs, str):
        langs = [langs] + ([lang_2] if lang_2 else [])
    out = []
    for l in (langs or []):
        if not l:
            continue
        l = str(l).strip().lower()
        if l and l not in out:
            out.append(l)
    return out


def add_special_group(chat_id, langs, lang_2=None, name: str = "", notes: str = ""):
    """Register a group in special mode.

    New form : add_special_group(chat_id, ['de','en','pl'], name='...')
    Legacy   : add_special_group(chat_id, 'de', 'en', name='...')  (still works)
    """
    init_special_groups_table()

    lang_list = _norm_langs(langs, lang_2)

    bad = [l for l in lang_list if l not in VALID_LANGS]
    if bad:
        print(f"❌ Invalid language code: {', '.join(bad)}. Valid: {', '.join(VALID_LANGS)}")
        return False
    if len(lang_list) < 2:
        print("❌ Need at least 2 different languages.")
        return False

    l1 = lang_list[0]
    l2 = lang_list[1]   # kept for backward compatibility with any old reader

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO special_groups
        (chat_id, group_name, lang_1, lang_2, langs, created_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (chat_id, name or f"Group {chat_id}", l1, l2, json.dumps(lang_list), datetime.now(), notes))
    conn.commit()
    conn.close()

    pretty = ' ↔ '.join(LANG_NAMES.get(l, l) for l in lang_list)
    print(f"✅ Special group registered!")
    print(f"   Name    : {name or f'Group {chat_id}'}")
    print(f"   Chat ID : {chat_id}")
    print(f"   Mode    : {pretty}")
    return True


def remove_special_group(chat_id: int):
    """Group ko special mode se hata do"""
    init_special_groups_table()
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM special_groups WHERE chat_id = ?", (chat_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()

    if affected:
        print(f"✅ Group {chat_id} removed from special mode")
    else:
        print(f"⚠️  Group {chat_id} was not in special groups list")


def _row_langs(l1, l2, langs_json):
    """Build the languages list from a row (prefer JSON `langs`, fall back to lang_1/lang_2)."""
    langs = None
    if langs_json:
        try:
            langs = json.loads(langs_json)
        except Exception:
            langs = None
    if not langs:
        langs = [l for l in [l1, l2] if l]
    # dedupe keep order
    out = []
    for l in langs:
        if l and l not in out:
            out.append(l)
    return out


def list_special_groups():
    """Saare special groups dikhao"""
    init_special_groups_table()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT chat_id, group_name, lang_1, lang_2, langs, created_at FROM special_groups")
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("📭 No special groups registered yet.")
        print("   Add one: python special_group_setup.py add <chat_id> de en pl --name 'My Group'")
        return

    print(f"\n{'='*55}")
    print(f"📋 SPECIAL GROUPS ({len(rows)} registered)")
    print(f"{'='*55}")
    for row in rows:
        chat_id, name, l1, l2, langs_json, created = row
        langs = _row_langs(l1, l2, langs_json)
        pretty = ' ↔ '.join(LANG_NAMES.get(l, l) for l in langs)
        print(f"  🔹 {name}")
        print(f"     Chat ID : {chat_id}")
        print(f"     Mode    : {pretty} only")
        print(f"     Added   : {str(created)[:10]}")
        print()


# DB function jo telegram_bot_groups.py import karega
def get_special_group_config(chat_id: int):
    """
    Returns special config agar group registered hai, warna None.
    Handles -100 prefix mismatch automatically.

    Returns:
        {'langs': ['de','en','pl'], 'lang_1': 'de', 'lang_2': 'en', 'group_name': '...'} or None
    """
    try:
        conn = get_db()
        c = conn.cursor()

        def fetch(cid):
            # Tolerant of older DBs that don't have the `langs` column yet
            try:
                c.execute(
                    "SELECT lang_1, lang_2, group_name, langs FROM special_groups WHERE chat_id = ?",
                    (cid,)
                )
                r = c.fetchone()
                return (r[0], r[1], r[2], r[3]) if r else None
            except Exception:
                c.execute(
                    "SELECT lang_1, lang_2, group_name FROM special_groups WHERE chat_id = ?",
                    (cid,)
                )
                r = c.fetchone()
                return (r[0], r[1], r[2], None) if r else None

        row = fetch(chat_id)

        # If not found, try with/without -100 prefix
        if not row:
            bare_id = abs(chat_id)
            if bare_id > 1000000000000:
                bare_id = bare_id - 1000000000000
            candidates = [bare_id, -bare_id, int(f"100{bare_id}"), -int(f"100{bare_id}")]
            for cid in candidates:
                if cid == chat_id:
                    continue
                row = fetch(cid)
                if row:
                    break

        conn.close()
        if not row:
            return None

        l1, l2, gname, langs_json = row
        langs = _row_langs(l1, l2, langs_json)
        return {'langs': langs, 'lang_1': l1, 'lang_2': l2, 'group_name': gname}
    except Exception:
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Special Group Translation Setup")
    subparsers = parser.add_subparsers(dest="command")

    # add command — now accepts 2+ language codes
    add_parser = subparsers.add_parser("add", help="Register a special group")
    add_parser.add_argument("chat_id", type=int, help="Telegram chat ID (negative number)")
    add_parser.add_argument("langs", nargs='+', help="Language codes (2+), e.g. de en pl")
    add_parser.add_argument("--name", default="", help="Friendly group name")
    add_parser.add_argument("--notes", default="", help="Optional notes")

    # remove command
    rm_parser = subparsers.add_parser("remove", help="Remove a special group")
    rm_parser.add_argument("chat_id", type=int, help="Telegram chat ID")

    # list command
    subparsers.add_parser("list", help="List all special groups")

    args = parser.parse_args()

    if args.command == "add":
        add_special_group(args.chat_id, args.langs, name=args.name, notes=args.notes)
    elif args.command == "remove":
        remove_special_group(args.chat_id)
    elif args.command == "list":
        list_special_groups()
    else:
        parser.print_help()