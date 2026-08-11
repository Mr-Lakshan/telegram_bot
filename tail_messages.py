#!/usr/bin/env python3
"""
Live view of Telegram messages, in the terminal.

    python3 tail_messages.py

Shows two things as they happen:

    IN   a message someone sent in a group          (group_messages)
    OUT  a translation the bot queued in reply      (outgoing_messages)

Reading, not tailing a log: the bot's stdout is interleaved with everything else
it prints, while these two tables are exactly the messages and nothing else.

The database is opened READ-ONLY. bot_data.db is shared by the bot, the
dashboard and the group creator, and it already has write contention between
them — this must not add a fourth writer. Read-only also means a mistake here
can never corrupt the file.

Options
    --db PATH         database (default: ./bot_data.db)
    --chat ID         only this chat
    --grep TEXT       only messages containing TEXT (case-insensitive)
    --out             include the bot's outgoing translations (off by default)
    --history N       print the last N messages before following (default 10)
    --no-colour       plain output, for piping to a file
    --once            print the history and exit instead of following
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime

POLL_SECONDS = 1.0


class C:
    """ANSI colours, switched off when the output is not a terminal."""
    on = sys.stdout.isatty()

    def __getattr__(self, name):
        codes = {
            "dim": "\033[2m", "bold": "\033[1m", "reset": "\033[0m",
            "blue": "\033[34m", "cyan": "\033[36m", "green": "\033[32m",
            "yellow": "\033[33m", "magenta": "\033[35m", "grey": "\033[90m",
        }
        return codes.get(name, "") if self.on else ""


c = C()


def connect(path: str) -> sqlite3.Connection:
    """
    Open read-only.

    The bot writes in WAL mode, where a committed row lives in bot_data.db-wal
    until a checkpoint folds it back into the main file. A long-lived read-only
    connection can keep serving an older snapshot in that situation, which looks
    exactly like "the message never arrived" — the data is there, this process
    just cannot see it yet.

    The follow loop therefore reconnects on every poll rather than holding one
    connection open. At one connection per second that costs nothing, and it
    removes the whole class of problem instead of reasoning about when a
    snapshot does or does not refresh.
    """
    if not os.path.exists(path):
        sys.exit(f"Database not found: {path}\n"
                 f"Run this from the bot directory, or pass --db /path/to/bot_data.db")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def when(value) -> str:
    """Timestamps are written by different code paths and are not all the same
    shape, so anything unparseable is shown as-is rather than crashing."""
    if not value:
        return "--:--:--"
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    return text[:19]


def short(chat_id) -> str:
    """Group ids are long and all start alike; the tail is what distinguishes
    them at a glance."""
    s = str(chat_id or "")
    return s[-6:] if len(s) > 6 else s


def wrap(text: str, indent: int = 11, width: int = 0) -> str:
    """Wrap to the terminal so long messages stay readable in a narrow window."""
    if not text:
        return ""
    width = width or (os.get_terminal_size().columns if sys.stdout.isatty() else 100)
    room = max(width - indent, 30)
    out, line = [], ""
    for word in text.replace("\n", " ⏎ ").split():
        if len(line) + len(word) + 1 > room:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    pad = " " * indent
    return ("\n" + pad).join(out)


def show_in(row) -> None:
    title = row["chat_title"] or f"chat {short(row['chat_id'])}"
    topic = f"/{row['topic_name']}" if row["topic_name"] else ""
    print(f"{c.grey}{when(row['timestamp'])}{c.reset} "
          f"{c.green}IN {c.reset} "
          f"{c.cyan}{title}{topic}{c.reset} "
          f"{c.dim}·{c.reset} {c.bold}{row['sender_name'] or row['sender_id']}{c.reset}")
    print(f"           {wrap(row['message_text'] or '')}")


def show_out(row) -> None:
    lang = (row["target_language"] or "?").upper()
    kind = row["message_category"] or "message"
    print(f"{c.grey}{when(row['created_at'])}{c.reset} "
          f"{c.magenta}OUT{c.reset} "
          f"{c.cyan}chat {short(row['chat_id'])}{c.reset} "
          f"{c.dim}·{c.reset} {c.yellow}{lang}{c.reset} "
          f"{c.dim}{kind}{c.reset}")
    print(f"           {wrap(row['message'] or '')}")


def main() -> None:
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("--db", default="bot_data.db")
    p.add_argument("--chat", type=int, default=None)
    p.add_argument("--grep", default=None)
    p.add_argument("--out", action="store_true",
                   help="also show the bot's outgoing translations")
    p.add_argument("--history", type=int, default=10)
    p.add_argument("--no-colour", action="store_true")
    p.add_argument("--once", action="store_true")
    args = p.parse_args()

    if args.no_colour:
        C.on = False

    conn = connect(args.db)
    if not has_table(conn, "group_messages"):
        sys.exit("No group_messages table — is this the right database?")
    show_outgoing = args.out and has_table(conn, "outgoing_messages")

    needle = args.grep.lower() if args.grep else None

    def keep(text, chat_id) -> bool:
        if args.chat is not None and chat_id != args.chat:
            return False
        if needle and needle not in (text or "").lower():
            return False
        return True

    # Start from the newest ids so following begins at "now", after the history
    # has been printed once.
    last_in = conn.execute("SELECT COALESCE(MAX(id), 0) FROM group_messages").fetchone()[0]
    last_out = (conn.execute("SELECT COALESCE(MAX(id), 0) FROM outgoing_messages").fetchone()[0]
                if show_outgoing else 0)

    if args.history:
        # Both tables, merged by time. Reading only the incoming one made
        # --out useless in --once mode, which is exactly when it matters.
        recent = [("in", r) for r in conn.execute(
            "SELECT * FROM group_messages ORDER BY id DESC LIMIT ?", (args.history,))]
        if show_outgoing:
            recent += [("out", r) for r in conn.execute(
                "SELECT * FROM outgoing_messages ORDER BY id DESC LIMIT ?", (args.history,))]

        def stamp(pair):
            kind, row = pair
            return str(row["timestamp"] if kind == "in" else row["created_at"] or "")

        for kind, row in sorted(recent, key=stamp)[-args.history:]:
            text = row["message_text"] if kind == "in" else row["message"]
            if keep(text, row["chat_id"]):
                (show_in if kind == "in" else show_out)(row)

        if recent:
            print(f"{c.dim}{'─' * 40} live {'─' * 40}{c.reset}")

    if args.once:
        return

    where = " (Ctrl+C to stop)"
    print(f"{c.dim}watching {args.db}"
          f"{' · chat ' + str(args.chat) if args.chat else ''}"
          f"{' · filter: ' + args.grep if args.grep else ''}"
          f"{where}{c.reset}")

    conn.close()

    try:
        while True:
            # Fresh connection each cycle — see connect() for why.
            poll = connect(args.db)
            try:
                for row in poll.execute(
                        "SELECT * FROM group_messages WHERE id > ? ORDER BY id", (last_in,)):
                    last_in = row["id"]
                    if keep(row["message_text"], row["chat_id"]):
                        show_in(row)

                if show_outgoing:
                    for row in poll.execute(
                            "SELECT * FROM outgoing_messages WHERE id > ? ORDER BY id",
                            (last_out,)):
                        last_out = row["id"]
                        if keep(row["message"], row["chat_id"]):
                            show_out(row)
            finally:
                poll.close()

            time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        print(f"\n{c.dim}stopped{c.reset}")
    except sqlite3.OperationalError as exc:
        # "database is locked" can happen if a writer holds it during a
        # checkpoint. Worth naming rather than dying with a bare traceback.
        sys.exit(f"\nDatabase error: {exc}")
    finally:
        pass


if __name__ == "__main__":
    main()