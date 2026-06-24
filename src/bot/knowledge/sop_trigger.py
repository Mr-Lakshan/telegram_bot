"""
SOP TRIGGER — Retrieve saved SOPs when someone needs them
==========================================================
SOPs are saved to Drive (sop_manager). This module indexes them locally
and sends the right SOP when a message asks for it.

Example: Paulina writes "Ich möchte eine Anzeige schalten"
  → bot finds the matching "Stellenanzeige" SOP → posts it in the group.

Matching uses embeddings (semantic), so it works even if the wording differs.

Usage:
  from bot.knowledge.sop_trigger import SOPTrigger
  sop_trigger = SOPTrigger(openai_api_key=..., db_path=...)

  # When an SOP is saved:
  sop_trigger.index_sop(title, category, content, drive_link)

  # On each group message:
  match = sop_trigger.find_match(message_text)
  if match: send match['content'] + match['drive_link']
"""

import os
import json
import sqlite3
import hashlib
from datetime import datetime

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    from bot.core.db_writer import db_writer
except ImportError:
    db_writer = None

EMBEDDING_MODEL = "text-embedding-3-small"
MATCH_THRESHOLD = 0.45   # semantic similarity needed to trigger (tuned for intent match)


class SOPTrigger:
    def __init__(self, openai_api_key="", db_path="bot_data.db"):
        self.db_path = db_path
        self.client = OpenAI(api_key=openai_api_key or os.getenv("OPENAI_API_KEY", "")) if HAS_OPENAI else None
        self._init_table()
        print("✅ SOPTrigger initialized")

    def _get_db(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_table(self):
        conn = self._get_db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sop_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                category TEXT,
                content TEXT,
                drive_link TEXT,
                keywords TEXT,
                embedding TEXT,
                use_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sop_cat ON sop_index(category)")
        conn.commit()
        conn.close()

    def _get_embedding(self, text):
        if not self.client:
            return None
        try:
            resp = self.client.embeddings.create(
                model=EMBEDDING_MODEL, input=text.strip()[:2000]
            )
            return resp.data[0].embedding
        except Exception as e:
            print(f"   ⚠️ SOP embedding error: {e}")
            return None

    @staticmethod
    def _cosine(a, b):
        if not a or not b:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    # ── Index a saved SOP ──
    def index_sop(self, title, category, content, drive_link=""):
        # Embed the title + content so intent matching works
        embed_text = f"{title}\n{content[:1000]}"
        embedding = self._get_embedding(embed_text)
        embedding_json = json.dumps(embedding) if embedding else ""

        # Extract simple keywords from title + category
        keywords = f"{title} {category}".lower()

        conn = self._get_db()
        # Avoid duplicates (same title)
        existing = conn.execute("SELECT id FROM sop_index WHERE title = ?", (title,)).fetchone()
        if existing:
            conn.execute("""
                UPDATE sop_index SET category=?, content=?, drive_link=?, keywords=?, embedding=?
                WHERE id=?
            """, (category, content, drive_link, keywords, embedding_json, existing['id']))
        else:
            conn.execute("""
                INSERT INTO sop_index (title, category, content, drive_link, keywords, embedding)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (title, category, content, drive_link, keywords, embedding_json))
        conn.commit()
        conn.close()
        print(f"   📇 SOP indexed: {title} ({category})")

    # ── Find a matching SOP for an incoming message ──
    def find_match(self, message: str):
        if not message or len(message.strip()) < 4:
            return None

        conn = self._get_db()
        rows = conn.execute("SELECT * FROM sop_index").fetchall()
        conn.close()
        if not rows:
            return None

        msg_lower = message.lower()

        # 1. Fast keyword match — title word appears in message
        for row in rows:
            kws = (row['keywords'] or '').split()
            for kw in kws:
                if len(kw) >= 4 and kw in msg_lower:
                    self._bump_use(row['id'])
                    return self._row_to_dict(row, 1.0, 'keyword')

        # 2. Semantic match via embeddings
        msg_emb = self._get_embedding(message)
        if not msg_emb:
            return None

        best = None
        best_sim = 0.0
        for row in rows:
            if not row['embedding']:
                continue
            try:
                emb = json.loads(row['embedding'])
            except Exception:
                continue
            sim = self._cosine(msg_emb, emb)
            if sim > best_sim:
                best_sim = sim
                best = row

        if best and best_sim >= MATCH_THRESHOLD:
            self._bump_use(best['id'])
            return self._row_to_dict(best, best_sim, 'semantic')
        return None

    def _row_to_dict(self, row, similarity, match_type):
        return {
            'id': row['id'],
            'title': row['title'],
            'category': row['category'],
            'content': row['content'],
            'drive_link': row['drive_link'],
            'similarity': round(similarity, 3),
            'match_type': match_type,
        }

    def _bump_use(self, sop_id):
        try:
            conn = self._get_db()
            conn.execute("UPDATE sop_index SET use_count = use_count + 1 WHERE id = ?", (sop_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass

    # ── Format the SOP for sending to a group ──
    def format_sop_message(self, match):
        cat_emoji = {
            'Regeln': '📋', 'Ideen': '💡', 'Prozesse': '⚙️',
            'Checklisten': '✅', 'Kontakte': '📇', 'Notizen': '📝',
        }.get(match['category'], '📌')

        msg = (
            f"{cat_emoji} **{match['title']}**\n"
            f"_(Aus Wissensdatenbank: {match['category']})_\n\n"
            f"{match['content']}"
        )
        if match['drive_link']:
            msg += f"\n\n🔗 {match['drive_link']}"
        return msg
