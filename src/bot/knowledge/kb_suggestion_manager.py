"""
KB SUGGESTION MANAGER — Phase B
================================
Evening suggestions with 4 buttons (Lothar's spec):
  ✅ OK            → save to knowledge base
  ❌ Nicht OK      → discard
  ✏️ Überarbeiten  → mark for later revision (revise list)
  ❓               → show short detailed explanation

Instead of auto-saving learnings, the AI SUGGESTS them in the evening.
Lothar confirms each with a tap. Buttons are spaced for easy mobile use.
Works for all types: rules, texts, videos.
"""

import sqlite3
from datetime import datetime

try:
    from bot.core.db_writer import db_writer
except ImportError:
    db_writer = None

try:
    from telethon.tl.custom import Button
except ImportError:
    Button = None


TYPE_LABELS = {
    'rule': '📋 Regel',
    'regel': '📋 Regel',
    'process': '⚙️ Prozess',
    'prozess': '⚙️ Prozess',
    'idea': '💡 Idee',
    'idee': '💡 Idee',
    'checklist': '✅ Checkliste',
    'video': '🎥 Video',
    'fact': '📌 Info',
    'tip': '💡 Tipp',
}


class KBSuggestionManager:
    def __init__(self, db_path="bot_data.db", bot_client=None, kb=None, approval_chat_id=None):
        self.db_path = db_path
        self.bot = bot_client
        self.kb = kb
        self.chat_id = approval_chat_id
        self._init_table()
        print("✅ KBSuggestionManager initialized")

    def _get_db(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_table(self):
        conn = self._get_db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kb_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                content TEXT,
                l_type TEXT DEFAULT 'rule',
                scope TEXT DEFAULT 'global',
                source_chat TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    # ── Store a suggestion, return its id ──
    def store_suggestion(self, title, content, l_type='rule', scope='global', source_chat=''):
        conn = self._get_db()
        cur = conn.execute("""
            INSERT INTO kb_suggestions (title, content, l_type, scope, source_chat, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """, (title, content, l_type, scope, source_chat, datetime.now()))
        conn.commit()
        sug_id = cur.lastrowid
        conn.close()
        return sug_id

    # ── Send suggestions to KI Freigaben with buttons ──
    async def send_suggestions(self, learnings):
        """Send each learning as a suggestion with 4 buttons. Returns count sent."""
        if not self.bot or not self.chat_id or Button is None:
            print("   ⚠️ KBSuggestionManager: bot/chat/Button not available")
            return 0

        sent = 0
        # Limit to top 3 (Lothar: "2-3 candidates")
        for learning in learnings[:3]:
            title = learning.get('title', '').strip()
            content = learning.get('content', '').strip()
            l_type = learning.get('type', 'rule')
            scope = learning.get('scope', 'global')
            chat = learning.get('chat', '')

            if not title or not content:
                continue

            sug_id = self.store_suggestion(title, content, l_type, scope, chat)
            type_label = TYPE_LABELS.get(l_type.lower(), '📌 Info')

            # Short, concise message (Lothar: read in seconds)
            short_content = content if len(content) <= 200 else content[:197] + "..."
            msg = (
                f"💡 **Vorschlag für Wissensdatenbank**\n"
                f"{type_label}\n\n"
                f"**{title}**\n"
                f"{short_content}\n\n"
                f"_Quelle: {chat or 'Chat'}_"
            )

            # 4 buttons, spaced in 2 rows for easy mobile tapping
            buttons = [
                [Button.inline("✅ OK", f"kbsug:ok:{sug_id}".encode()),
                 Button.inline("❌ Nicht OK", f"kbsug:no:{sug_id}".encode())],
                [Button.inline("✏️ Überarbeiten", f"kbsug:rev:{sug_id}".encode()),
                 Button.inline("❓", f"kbsug:exp:{sug_id}".encode())],
            ]

            try:
                await self.bot.send_message(self.chat_id, msg, buttons=buttons)
                sent += 1
                print(f"   💡 Suggestion sent: [{l_type}] {title[:50]}")
            except Exception as e:
                print(f"   ⚠️ Suggestion send error: {e}")

        return sent

    # ── Handle button callbacks ──
    async def handle_callback(self, event):
        """Process kbsug:* button taps. Returns True if handled."""
        try:
            data = event.data.decode() if isinstance(event.data, bytes) else str(event.data)
        except Exception:
            return False

        if not data.startswith("kbsug:"):
            return False

        parts = data.split(":")
        if len(parts) != 3:
            return False
        _, action, sug_id_str = parts
        try:
            sug_id = int(sug_id_str)
        except ValueError:
            return False

        # Fetch the suggestion
        conn = self._get_db()
        row = conn.execute("SELECT * FROM kb_suggestions WHERE id = ?", (sug_id,)).fetchone()
        conn.close()

        if not row:
            await event.answer("Vorschlag nicht gefunden", alert=True)
            return True

        title = row['title']
        content = row['content']
        l_type = row['l_type']
        scope = row['scope']
        chat = row['source_chat']

        if action == 'ok':
            # Save to KB
            ok = False
            if self.kb:
                try:
                    ok = self.kb.save_answer(
                        question=title, answer=content,
                        topic=l_type, intent=l_type,
                        classification_type=f'evening_confirmed_{l_type}',
                        chat_id=0, customer_name=chat,
                        approval_token='', approved_by='lothar_evening',
                    )
                except Exception as e:
                    print(f"   ⚠️ KB save error: {e}")
            self._set_status(sug_id, 'saved')
            await event.edit(f"✅ **Gespeichert**\n{title}")
            await event.answer("In Wissensdatenbank gespeichert ✅")

        elif action == 'no':
            self._set_status(sug_id, 'discarded')
            await event.edit(f"❌ **Verworfen**\n{title}")
            await event.answer("Verworfen")

        elif action == 'rev':
            self._set_status(sug_id, 'revise')
            await event.edit(
                f"✏️ **Zum Überarbeiten markiert**\n{title}\n\n"
                f"_(Später überarbeiten — im Revise-Ordner gespeichert)_"
            )
            await event.answer("Zum Überarbeiten markiert ✏️")

        elif action == 'exp':
            # Show full detailed explanation (no edit — just popup/message)
            type_label = TYPE_LABELS.get(l_type.lower(), '📌 Info')
            full = (
                f"❓ **Detaillierte Erklärung**\n"
                f"{type_label}\n\n"
                f"**{title}**\n\n"
                f"{content}\n\n"
                f"_Quelle: {chat or 'Chat'} | Typ: {l_type}_"
            )
            try:
                await self.bot.send_message(self.chat_id, full)
                await event.answer("Erklärung unten ⬇️")
            except Exception:
                await event.answer(content[:200], alert=True)

        return True

    def _set_status(self, sug_id, status):
        # Direct write — status updates are infrequent (button taps) and need
        # immediate consistency (so revise list is correct right away)
        for attempt in range(4):
            try:
                conn = self._get_db()
                conn.execute("UPDATE kb_suggestions SET status = ? WHERE id = ?", (status, sug_id))
                conn.commit()
                conn.close()
                return
            except sqlite3.OperationalError as e:
                if 'locked' in str(e) and attempt < 3:
                    import time; time.sleep(0.2 * (attempt + 1))
                    continue
                print(f"   ⚠️ Status update error: {e}")
                return

    # ── Get revise list (for later review) ──
    def get_revise_list(self):
        conn = self._get_db()
        rows = conn.execute(
            "SELECT * FROM kb_suggestions WHERE status = 'revise' ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]