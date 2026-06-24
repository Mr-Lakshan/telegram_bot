"""
LINK & INFO EXTRACTOR — Knowledge Capture from Monitored Groups
================================================================
Watches the dev/admin groups (Lothar & Rohit, Lothar & Manisha) and
automatically captures reusable knowledge from messages:

  • Links        → registration URL, login URL, docs, invoice portal, etc.
  • Procedures   → numbered step-by-step instructions
  • Guidelines   → anything marked "Important:", "Note:", "Wichtig:", etc.

Captured items are stored permanently and become searchable, so when
someone later asks in the Freigaben group "Was ist der Registrierungs-Link?"
the bot can answer instantly without an AI call.

Integration notes:
  • Uses the shared db_writer queue (no "database is locked" conflicts).
  • Synchronous reads use a short-lived connection with busy_timeout.
  • Tables self-create on first init — safe to import anywhere.

Usage:
    from bot.handlers.link_extractor import LinkExtractor
    extractor = LinkExtractor(db_path=DB)

    # On each monitored-group message (after storing it):
    result = await extractor.extract_from_message(
        message_text=text, sender_name=name, sender_id=uid,
        chat_id=cid, chat_title=title, message_id=mid,
        source_group_name="Lothar & Rohit",
    )

    # When answering a question:
    hits = extractor.search(query_text)   # sync read, safe in handler
"""

import re
import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

# Shared single-writer queue — same one knowledge_base.py uses.
try:
    from bot.core.db_writer import db_writer
except ImportError:
    db_writer = None


# ── Tunables ──────────────────────────────────────────────────────────────
MIN_PROCEDURE_STEPS = 2      # need at least this many steps to count as a procedure
MAX_DESC_LEN        = 160    # truncate stored context/description
SEARCH_LIMIT        = 5      # max hits returned per search


class LinkExtractor:
    """Capture and retrieve links, procedures and guidelines."""

    def __init__(self, db_path: str = "bot_data.db"):
        self.db_path = db_path

        # URLs (http/https). Trailing punctuation is trimmed in _clean_url.
        self._url_re = re.compile(r'https?://[^\s<>"\'`\]\)]+', re.IGNORECASE)

        # Numbered / "Step N" lines → procedure steps.
        self._step_re = re.compile(
            r'(?:^|\n)\s*(?:step\s+)?(\d{1,2})[\.\)]\s*:?\s*(.+?)(?=\n|$)',
            re.IGNORECASE,
        )

        # Importance markers (German + English).
        self._markers = [
            'important:', 'note:', 'attention:', 'remember:', 'warning:',
            'key:', 'critical:', 'must:', 'reminder:',
            'wichtig:', 'achtung:', 'hinweis:', 'merke:', 'beachte:',
            'bitte beachten:', 'zur info:',
        ]
        marker_alt = '|'.join(re.escape(m) for m in self._markers)
        self._marker_re = re.compile(rf'(?:^|\n)\s*({marker_alt})\s*(.+?)(?=\n|$)', re.IGNORECASE)

        # URL-path hints — strongest signal, checked first (path is unambiguous).
        self._path_hints = [
            ('registration', ['/register', '/signup', '/sign-up', '/registrier', '/anmeldung']),
            ('login',        ['/login', '/signin', '/sign-in', '/auth', '/einloggen']),
            ('documentation',['/docs', '/documentation', '/guide', '/wiki', '/help', '/anleitung']),
            ('invoice',      ['/invoice', '/rechnung', '/billing', '/pay', '/zahlung']),
            ('form',         ['/form', '/formular', '/apply', '/antrag']),
        ]

        # Keyword → link_type from surrounding text. Order matters (first hit wins).
        # NOTE: ambiguous German words like "anmelden/anmeldung" are intentionally
        # NOT used here because they mean both register and login depending on context;
        # the URL path (above) disambiguates those cases.
        self._type_keywords = [
            ('registration', ['regist', 'sign up', 'signup', 'sign-up',
                              'create account', 'konto erstell', 'onboard', 'neu registrieren']),
            ('login',        ['login', 'log in', 'sign in', 'signin', 'sign-in', 'einlogg', 'einloggen',
                              'password', 'passwort', 'credentials', 'zugangsdaten', 'anmeldedaten']),
            ('documentation',['documentation', 'docs', 'guide', 'anleitung', 'manual',
                              'handbuch', 'tutorial', 'how to', 'wiki']),
            ('invoice',      ['invoice', 'rechnung', 'billing', 'payment', 'zahlung',
                              'receipt', 'beleg']),
            ('form',         ['form', 'formular', 'application', 'antrag', 'apply',
                              'questionnaire', 'umfrage', 'survey']),
            ('crm',          ['crm', 'customer', 'kunde', 'lead', 'pipeline']),
            ('drive',        ['drive', 'dropbox', 'sharepoint', 'onedrive', 'cloud',
                              'ordner', 'folder']),
        ]

        self._ensure_tables()
        print("✅ LinkExtractor initialized")

    # ════════════════════════════════════════════════════════════════════
    #  SCHEMA
    # ════════════════════════════════════════════════════════════════════

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _ensure_tables(self):
        """Create tables + indexes if absent. Runs once at startup (sync is fine here)."""
        try:
            conn = self._conn()
            c = conn.cursor()

            c.execute("""
                CREATE TABLE IF NOT EXISTS extracted_links (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    url                 TEXT NOT NULL UNIQUE,
                    link_type           TEXT,
                    description         TEXT,
                    source_chat_id      INTEGER,
                    source_chat_title   TEXT,
                    source_group_name   TEXT,
                    source_message_id   INTEGER,
                    source_sender_name  TEXT,
                    source_message_text TEXT,
                    is_active           INTEGER DEFAULT 1,
                    importance          INTEGER DEFAULT 5,
                    usage_count         INTEGER DEFAULT 0,
                    extracted_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_used           DATETIME,
                    tags                TEXT
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS important_info (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    info_type           TEXT,         -- 'procedure' | 'guideline'
                    title               TEXT,
                    content             TEXT,         -- procedure: JSON list of steps
                    source_group_name   TEXT,
                    source_sender_name  TEXT,
                    source_message_id   INTEGER,
                    is_procedural       INTEGER DEFAULT 0,
                    step_count          INTEGER DEFAULT 0,
                    related_links       TEXT,         -- JSON list of URLs
                    importance          INTEGER DEFAULT 5,
                    usage_count         INTEGER DEFAULT 0,
                    last_used           DATETIME,
                    tags                TEXT,
                    content_hash        TEXT UNIQUE,  -- dedupe identical info
                    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            for name, target in [
                ("idx_links_type",      "extracted_links(link_type)"),
                ("idx_links_active",    "extracted_links(is_active)"),
                ("idx_info_type",       "important_info(info_type)"),
            ]:
                c.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️  LinkExtractor table init error: {e}")

    # ════════════════════════════════════════════════════════════════════
    #  WRITE HELPER (routes through db_writer when available)
    # ════════════════════════════════════════════════════════════════════

    async def _write(self, query: str, params: tuple):
        """Single async write. Falls back to a direct connection if no db_writer."""
        if db_writer is not None and getattr(db_writer, "_started", False):
            try:
                return await db_writer.execute(query, params)
            except Exception as e:
                print(f"⚠️  db_writer failed, using direct write: {e}")
        # Fallback path — db_writer not started (e.g. tests) or unavailable.
        try:
            conn = self._conn()
            cur = conn.execute(query, params)
            conn.commit()
            rid = cur.lastrowid
            conn.close()
            return rid
        except Exception as e:
            print(f"⚠️  direct write error: {e}")
            return None

    # ════════════════════════════════════════════════════════════════════
    #  MAIN — extract everything from one message
    # ════════════════════════════════════════════════════════════════════

    async def extract_from_message(
        self,
        message_text: str,
        sender_name: str,
        sender_id: int,
        chat_id: int,
        chat_title: str,
        message_id: int,
        source_group_name: str,
    ) -> Dict:
        """
        Extract links, procedures and guidelines from a single message.
        Returns a summary dict; all saves go through the write queue.
        """
        summary = {"links": [], "procedures": [], "guidelines": [], "saved": 0}

        if not message_text or not message_text.strip():
            return summary

        # ── 1. Links ──
        urls = self._extract_urls(message_text)
        for url in urls:
            link_type = self._classify(message_text, url)
            desc = self._context_around(message_text, url)
            importance = 9 if link_type in ("registration", "login") else 6
            rid = await self._save_link(
                url=url, link_type=link_type, description=desc,
                chat_id=chat_id, chat_title=chat_title,
                group_name=source_group_name, sender_name=sender_name,
                message_text=message_text[:500], message_id=message_id,
                importance=importance,
            )
            if rid is not None:
                summary["links"].append({"url": url, "type": link_type})
                summary["saved"] += 1

        # ── 2. Procedure (numbered steps) ──
        steps = self._extract_steps(message_text)
        if len(steps) >= MIN_PROCEDURE_STEPS:
            title = self._procedure_title(message_text)
            related = self._extract_urls(" ".join(steps))
            rid = await self._save_procedure(
                title=title, steps=steps, related_links=related,
                group_name=source_group_name, sender_name=sender_name,
                message_id=message_id,
            )
            if rid is not None:
                summary["procedures"].append({"title": title, "steps": len(steps)})
                summary["saved"] += 1

        # ── 3. Guidelines (marked important) ──
        for content in self._extract_guidelines(message_text):
            rid = await self._save_guideline(
                content=content, group_name=source_group_name,
                sender_name=sender_name, message_id=message_id,
            )
            if rid is not None:
                summary["guidelines"].append(content[:50])
                summary["saved"] += 1

        return summary

    # ════════════════════════════════════════════════════════════════════
    #  PARSING
    # ════════════════════════════════════════════════════════════════════

    def _extract_urls(self, text: str) -> List[str]:
        seen, out = set(), []
        for raw in self._url_re.findall(text):
            url = self._clean_url(raw)
            if url and url not in seen:
                seen.add(url)
                out.append(url)
        return out

    @staticmethod
    def _clean_url(url: str) -> str:
        # Strip common trailing punctuation that isn't part of the URL.
        return url.rstrip('.,;:!?)>\'"')

    def _classify(self, text: str, url: str = None) -> str:
        """
        Classify a link type. When a URL is given, its path is the strongest
        signal (unambiguous), so it's checked before the surrounding text —
        this correctly separates a /login URL from a message that also says
        "Anmeldung". Falls back to text keywords, then 'general'.
        """
        if url:
            url_low = url.lower()
            for link_type, hints in self._path_hints:
                if any(h in url_low for h in hints):
                    return link_type
        low = text.lower()
        for link_type, kws in self._type_keywords:
            if any(kw in low for kw in kws):
                return link_type
        return "general"

    def _context_around(self, text: str, url: str) -> str:
        # Use the message minus the URL as a human-readable description.
        ctx = text.replace(url, "").strip()
        ctx = re.sub(r'\s+', ' ', ctx)
        return (ctx[:MAX_DESC_LEN] or "Link from group").strip()

    def _extract_steps(self, text: str) -> List[str]:
        matches = self._step_re.findall(text)
        # matches: list of (number, content). Keep order, strip blanks.
        steps = [c.strip() for _, c in matches if c.strip()]
        return steps

    def _procedure_title(self, text: str) -> str:
        first = text.strip().split('\n', 1)[0]
        first = self._url_re.sub('', first).strip(' :-')
        return (first[:60] or "Procedure").strip()

    def _extract_guidelines(self, text: str) -> List[str]:
        out = []
        for _marker, content in self._marker_re.findall(text):
            content = content.strip()
            if content:
                out.append(content[:MAX_DESC_LEN])
        return out

    # ════════════════════════════════════════════════════════════════════
    #  SAVE
    # ════════════════════════════════════════════════════════════════════

    async def _save_link(self, *, url, link_type, description, chat_id,
                         chat_title, group_name, sender_name, message_text,
                         message_id, importance) -> Optional[int]:
        # Dedupe on URL: if present, bump usage and skip insert.
        try:
            conn = self._conn()
            row = conn.execute("SELECT id FROM extracted_links WHERE url = ?", (url,)).fetchone()
            conn.close()
        except Exception:
            row = None

        if row:
            await self._write(
                "UPDATE extracted_links SET usage_count = usage_count + 1, last_used = ? WHERE url = ?",
                (datetime.now().isoformat(), url),
            )
            return None  # not a new capture

        return await self._write(
            """INSERT INTO extracted_links
               (url, link_type, description, source_chat_id, source_chat_title,
                source_group_name, source_message_id, source_sender_name,
                source_message_text, importance, tags)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (url, link_type, description, chat_id, chat_title, group_name,
             message_id, sender_name, message_text, importance,
             json.dumps([link_type])),
        )

    async def _save_procedure(self, *, title, steps, related_links,
                             group_name, sender_name, message_id) -> Optional[int]:
        content_json = json.dumps(steps, ensure_ascii=False)
        chash = self._hash(title + content_json)
        if self._exists_hash(chash):
            return None
        return await self._write(
            """INSERT OR IGNORE INTO important_info
               (info_type, title, content, source_group_name, source_sender_name,
                source_message_id, is_procedural, step_count, related_links,
                importance, tags, content_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("procedure", title, content_json, group_name, sender_name,
             message_id, 1, len(steps), json.dumps(related_links),
             8, json.dumps(["procedure", "steps"]), chash),
        )

    async def _save_guideline(self, *, content, group_name,
                             sender_name, message_id) -> Optional[int]:
        chash = self._hash("guideline:" + content)
        if self._exists_hash(chash):
            return None
        return await self._write(
            """INSERT OR IGNORE INTO important_info
               (info_type, title, content, source_group_name, source_sender_name,
                source_message_id, is_procedural, importance, tags, content_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("guideline", content[:60], content, group_name, sender_name,
             message_id, 0, 9, json.dumps(["important", "guideline"]), chash),
        )

    # ── dedupe helpers ──
    @staticmethod
    def _hash(s: str) -> str:
        import hashlib
        return hashlib.sha256(s.encode("utf-8", "ignore")).hexdigest()[:32]

    def _exists_hash(self, chash: str) -> bool:
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT 1 FROM important_info WHERE content_hash = ?", (chash,)
            ).fetchone()
            conn.close()
            return row is not None
        except Exception:
            return False

    # ════════════════════════════════════════════════════════════════════
    #  SEARCH / RETRIEVE  (sync reads — safe inside async handlers)
    # ════════════════════════════════════════════════════════════════════

    def search(self, query: str, limit: int = SEARCH_LIMIT) -> Dict:
        """
        Keyword search across links + info.
        Returns {'links': [...], 'info': [...], 'found': int}.
        Also maps the query through type-keywords so "Registrierung" finds
        registration links even if the word isn't stored verbatim.
        """
        q = (query or "").lower().strip()
        if not q:
            return {"links": [], "info": [], "found": 0}

        # Expand: detect a link_type from the query for better link matching.
        detected_type = self._classify(q)

        like = f"%{q}%"
        links, info = [], []
        try:
            conn = self._conn()
            c = conn.cursor()

            c.execute(
                """SELECT url, link_type, description, importance
                   FROM extracted_links
                   WHERE is_active = 1 AND (
                       LOWER(description) LIKE ? OR
                       LOWER(link_type)   LIKE ? OR
                       LOWER(tags)        LIKE ? OR
                       link_type = ?
                   )
                   ORDER BY importance DESC, usage_count DESC
                   LIMIT ?""",
                (like, like, like, detected_type, limit),
            )
            links = [
                {"url": r[0], "type": r[1], "description": r[2], "importance": r[3]}
                for r in c.fetchall()
            ]

            c.execute(
                """SELECT id, info_type, title, content, is_procedural,
                          step_count, related_links, importance
                   FROM important_info
                   WHERE LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(tags) LIKE ?
                   ORDER BY importance DESC, usage_count DESC
                   LIMIT ?""",
                (like, like, like, limit),
            )
            for r in c.fetchall():
                item = {
                    "id": r[0], "info_type": r[1], "title": r[2],
                    "is_procedural": bool(r[4]), "step_count": r[5],
                    "importance": r[7],
                }
                if r[4]:  # procedural → content is JSON list
                    try:
                        item["steps"] = json.loads(r[3])
                    except Exception:
                        item["steps"] = [r[3]]
                    try:
                        item["related_links"] = json.loads(r[6] or "[]")
                    except Exception:
                        item["related_links"] = []
                else:
                    item["content"] = r[3]
                info.append(item)

            conn.close()
        except Exception as e:
            print(f"⚠️  LinkExtractor.search error: {e}")

        return {"links": links, "info": info, "found": len(links) + len(info)}

    def by_category(self, link_type: str, limit: int = 20) -> List[Dict]:
        """Return all active links of a given type (e.g. 'registration')."""
        try:
            conn = self._conn()
            rows = conn.execute(
                """SELECT url, description, importance
                   FROM extracted_links
                   WHERE is_active = 1 AND link_type = ?
                   ORDER BY importance DESC, usage_count DESC
                   LIMIT ?""",
                (link_type, limit),
            ).fetchall()
            conn.close()
            return [{"url": r[0], "description": r[1], "importance": r[2]} for r in rows]
        except Exception as e:
            print(f"⚠️  by_category error: {e}")
            return []

    def mark_used(self, *, url: str = None, info_id: int = None):
        """Fire-and-forget usage bump. Uses db_writer.execute_nowait if available."""
        now = datetime.now().isoformat()
        if url:
            q = "UPDATE extracted_links SET usage_count = usage_count + 1, last_used = ? WHERE url = ?"
            p = (now, url)
        elif info_id is not None:
            q = "UPDATE important_info SET usage_count = usage_count + 1, last_used = ? WHERE id = ?"
            p = (now, info_id)
        else:
            return
        if db_writer is not None and getattr(db_writer, "_started", False) and hasattr(db_writer, "execute_nowait"):
            try:
                db_writer.execute_nowait(q, p)
                return
            except Exception:
                pass
        try:
            conn = self._conn()
            conn.execute(q, p)
            conn.commit()
            conn.close()
        except Exception:
            pass

    # ════════════════════════════════════════════════════════════════════
    #  FORMATTING — turn search hits into a ready-to-post message
    # ════════════════════════════════════════════════════════════════════

    def format_answer(self, hits: Dict) -> Optional[str]:
        """
        Build a clean group message from search hits.
        Returns None if there's nothing worth sending.
        """
        if not hits or hits.get("found", 0) == 0:
            return None

        parts = []

        for link in hits.get("links", [])[:3]:
            label = {
                "registration": "🔗 Registrierungs-Link",
                "login": "🔗 Login-Link",
                "documentation": "📖 Dokumentation",
                "invoice": "🧾 Rechnung/Portal",
                "form": "📝 Formular",
            }.get(link["type"], "🔗 Link")
            parts.append(f"{label}:\n{link['url']}")

        for item in hits.get("info", [])[:2]:
            if item.get("is_procedural"):
                steps = item.get("steps", [])
                lines = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
                block = f"📋 {item.get('title','Ablauf')}:\n{lines}"
                if item.get("related_links"):
                    block += "\n" + "\n".join(item["related_links"])
                parts.append(block)
            else:
                parts.append(f"⭐ {item.get('content','')}")

        return "\n\n".join(parts) if parts else None

    # ════════════════════════════════════════════════════════════════════
    #  STATS (for daily report)
    # ════════════════════════════════════════════════════════════════════

    def get_stats(self) -> Dict:
        try:
            conn = self._conn()
            c = conn.cursor()
            links = c.execute("SELECT COUNT(*) FROM extracted_links WHERE is_active=1").fetchone()[0]
            procs = c.execute("SELECT COUNT(*) FROM important_info WHERE info_type='procedure'").fetchone()[0]
            guides = c.execute("SELECT COUNT(*) FROM important_info WHERE info_type='guideline'").fetchone()[0]
            top = c.execute(
                "SELECT url, usage_count FROM extracted_links ORDER BY usage_count DESC LIMIT 5"
            ).fetchall()
            conn.close()
            return {
                "links": links, "procedures": procs, "guidelines": guides,
                "top_used": [{"url": r[0], "count": r[1]} for r in top],
            }
        except Exception as e:
            print(f"⚠️  get_stats error: {e}")
            return {"links": 0, "procedures": 0, "guidelines": 0, "top_used": []}
