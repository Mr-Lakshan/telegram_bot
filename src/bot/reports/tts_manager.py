"""
TTS MANAGER — Text-to-Speech for postings
===========================================
Lothar taps a 🔊 button under a posting → AI reads it aloud (German, Fable voice).
Perfect for listening in the car.

Features:
  - OpenAI tts-1, voice = "fable" (Lothar's choice)
  - On-click generation (cheap — only what he listens to)
  - Numbers converted to German words ("4.180 €" → "viertausendeinhundert­achtzig Euro")
    so they're read correctly (TTS often mangles raw digits)
  - Sent as a voice message (playable inline)

Usage:
  from bot.reports.tts_manager import TTSManager
  tts = TTSManager(openai_api_key=..., db_path=..., bot_client=...)

  # When sending a posting, attach the button:
  tts_id = tts.store_tts(text)
  buttons = [...other buttons..., tts.make_button_row(tts_id)]

  # Callback (tts:* taps) handled by:
  await tts.handle_callback(event)
"""

import os
import re
import sqlite3
import tempfile
from datetime import datetime

from openai import OpenAI

try:
    from telethon.tl.custom import Button
except ImportError:
    Button = None

try:
    from num2words import num2words
    _HAS_NUM2WORDS = True
except ImportError:
    _HAS_NUM2WORDS = False

TTS_MODEL = "tts-1"
TTS_VOICE = "fable"   # Lothar's choice
TTS_FORMAT = "opus"   # best for Telegram voice messages


class TTSManager:
    def __init__(self, openai_api_key="", db_path="bot_data.db", bot_client=None):
        self.client = OpenAI(api_key=openai_api_key or os.getenv("OPENAI_API_KEY", ""))
        self.db_path = db_path
        self.bot = bot_client
        self._init_table()
        print(f"✅ TTSManager initialized (voice={TTS_VOICE})" +
              ("" if _HAS_NUM2WORDS else " ⚠️ num2words missing — install for better numbers"))

    def _get_db(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_table(self):
        conn = self._get_db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tts_texts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT,
                voice_msg_id INTEGER,
                voice_chat_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migration: add columns if upgrading an old table
        for col in ("voice_msg_id INTEGER", "voice_chat_id INTEGER"):
            try:
                conn.execute(f"ALTER TABLE tts_texts ADD COLUMN {col}")
            except Exception:
                pass
        conn.commit()
        conn.close()

    # ── Number → German words (so TTS reads them correctly) ──
    def preprocess_text(self, text: str) -> str:
        """Clean markdown + convert numbers/currency to German words for natural speech."""
        if not text:
            return ""

        t = text
        # Strip markdown symbols
        t = t.replace('**', '').replace('__', '').replace('`', '')
        t = re.sub(r'[#*_~>]', '', t)
        # Remove emojis / decorative lines
        t = re.sub(r'[━─—]{2,}', '. ', t)
        t = re.sub(r'[📋📄🔗✅❌💡📌🎥⚙️🟢🔒📊🤖👤📚⭐🕐📍🏷️📅]', '', t)

        # Currency: "4.180 €" / "4180€" / "4.180 Euro" → German words + " Euro"
        def euro_repl(m):
            num = m.group(1).replace('.', '').replace(',', '.')
            return self._num_to_words(num) + " Euro"
        t = re.sub(r'(\d[\d.,]*)\s*(?:€|euro|eur)(?![a-zA-Z])', euro_repl, t, flags=re.IGNORECASE)

        # Percentages: "15%" → "fünfzehn Prozent"
        def pct_repl(m):
            return self._num_to_words(m.group(1)) + " Prozent"
        t = re.sub(r'(\d[\d.,]*)\s*%', pct_repl, t)

        # Plain standalone numbers → words (e.g. "28 Tage" → "achtundzwanzig Tage")
        def num_repl(m):
            return self._num_to_words(m.group(0))
        t = re.sub(r'\b\d[\d.,]*\b', num_repl, t)

        # Collapse whitespace
        t = re.sub(r'\s+', ' ', t).strip()
        return t

    def _num_to_words(self, num_str: str) -> str:
        """Convert a numeric string to German words."""
        try:
            clean = num_str.replace('.', '').replace(',', '.')
            if '.' in clean:
                val = float(clean)
                if val.is_integer():
                    val = int(val)
            else:
                val = int(clean)
            if _HAS_NUM2WORDS:
                return num2words(val, lang='de')
            return str(val)  # fallback: leave as digits
        except Exception:
            return num_str

    # ── Store text, return id (for the button) ──
    def store_tts(self, text: str) -> int:
        conn = self._get_db()
        cur = conn.execute("INSERT INTO tts_texts (text, created_at) VALUES (?, ?)",
                           (text, datetime.now()))
        conn.commit()
        tts_id = cur.lastrowid
        conn.close()
        return tts_id

    def make_button_row(self, tts_id: int):
        """Return a button row with the 🔊 listen button."""
        if Button is None:
            return []
        return [Button.inline("🔊 Vorlesen", f"tts:{tts_id}".encode())]

    # ── Generate audio + send as voice message (once per posting) ──
    async def generate_and_send(self, tts_id: int, chat_id, reply_to=None):
        conn = self._get_db()
        row = conn.execute(
            "SELECT text, voice_msg_id, voice_chat_id FROM tts_texts WHERE id = ?",
            (tts_id,),
        ).fetchone()
        conn.close()
        if not row:
            return False

        spoken = self.preprocess_text(row['text'])
        if not spoken:
            return False
        spoken = spoken[:4000]   # OpenAI TTS 4096-char limit

        cache_dir = os.path.join(tempfile.gettempdir(), "tts_cache")
        os.makedirs(cache_dir, exist_ok=True)
        audio_path = os.path.join(cache_dir, f"tts_{tts_id}.ogg")

        try:
            # Reuse cached audio file if present (avoids OpenAI call), else generate
            import asyncio
            if not (os.path.exists(audio_path) and os.path.getsize(audio_path) > 0):
                def _generate():
                    resp = self.client.audio.speech.create(
                        model=TTS_MODEL, voice=TTS_VOICE,
                        input=spoken, response_format=TTS_FORMAT,
                    )
                    resp.stream_to_file(audio_path)
                await asyncio.to_thread(_generate)
                print(f"   🔊 TTS generated (tts_id={tts_id})")

            # Send as voice message
            sent = await self.bot.send_file(
                chat_id, audio_path, voice_note=True, reply_to=reply_to,
            )

            # Remember this voice message so re-taps point here instead of regenerating
            try:
                vid = sent.id if sent else None
                if vid:
                    conn = self._get_db()
                    conn.execute(
                        "UPDATE tts_texts SET voice_msg_id = ?, voice_chat_id = ? WHERE id = ?",
                        (vid, chat_id, tts_id),
                    )
                    conn.commit()
                    conn.close()
            except Exception as e:
                print(f"   ⚠️ TTS msg_id store error: {e}")

            return True
        except Exception as e:
            print(f"   ⚠️ TTS generate/send error: {e}")
            return False
        # NOTE: cached audio file is kept for reuse (cleaned periodically)

    # ── Callback handler (tts:* taps) ──
    async def handle_callback(self, event) -> bool:
        try:
            data = event.data.decode() if isinstance(event.data, bytes) else str(event.data)
        except Exception:
            return False
        if not data.startswith("tts:"):
            return False

        parts = data.split(":")
        if len(parts) != 2:
            return False
        try:
            tts_id = int(parts[1])
        except ValueError:
            return False

        # ── Already generated in this chat? Show a popup, don't post anything ──
        conn = self._get_db()
        row = conn.execute(
            "SELECT voice_msg_id, voice_chat_id FROM tts_texts WHERE id = ?", (tts_id,)
        ).fetchone()
        conn.close()
        if row and row['voice_msg_id'] and row['voice_chat_id'] == event.chat_id:
            # Popup notification only — no message posted, no regeneration
            await event.answer("🔊 Sprachnachricht wurde bereits erstellt ⬆️", alert=True)
            return True

        await event.answer("🔊 Wird vorgelesen...")
        msg = await event.get_message()
        reply_to = msg.id if msg else None
        ok = await self.generate_and_send(tts_id, event.chat_id, reply_to=reply_to)
        if not ok:
            await event.answer("⚠️ Konnte nicht vorlesen", alert=True)
        return True