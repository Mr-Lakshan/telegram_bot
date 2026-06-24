"""
APPROVAL HANDLER — Telegram-Based AI Answer Approval
======================================================
Replaces dashboard approval with Telegram inline buttons.

Flow:
  1. AI generates answer suggestion
  2. Bot sends to APPROVAL CHAT (private group/DM with Lothar):
     - Original question
     - AI suggestion
     - [✅ Freigeben] [✏️ Bearbeiten] [❌ Ablehnen] buttons
  3. Lothar clicks button:
     - Freigeben → Bot posts answer in original group
     - Bearbeiten → Lothar types corrected answer → Bot posts that
     - Ablehnen → AI suggestion discarded
  4. Employee answers first → AI suggestion auto-marked as "handled"

Requires:
  - APPROVAL_CHAT_ID in .env (Lothar's DM or a private approval group)
  - bot_client must be initialized before registering handlers
"""

import sqlite3
import json
import time
from datetime import datetime
from typing import Optional, Dict, Tuple
from telethon import events, Button
import os
import threading

# Centralized DB writer — permanent fix for "database is locked"
try:
    from bot.core.db_writer import db_writer
except ImportError:
    db_writer = None

# ── Approval-via-form (CRM) config ──
CRM_BASE = os.getenv('CRM_BASE_URL', 'https://teampflegeinfo.de/crm')
CRM_FORM_URL = os.getenv('APPROVAL_FORM_URL', f'{CRM_BASE}/approval_answer.php')
CRM_PUSH_URL = os.getenv('APPROVAL_PUSH_URL', f'{CRM_BASE}/api/approval_push.php')
APPROVAL_BOT_SECRET = os.getenv('APPROVAL_BOT_SECRET', 'tg_approval_2026_premiobad')


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

def init_approval_db(db_path: str = "bot_data.db"):
    """Create approval tables if they don't exist."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS ai_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE NOT NULL,
        status TEXT DEFAULT 'pending',
        
        -- Original question details
        question_text TEXT,
        sender_name TEXT,
        sender_id INTEGER,
        chat_id INTEGER,
        chat_title TEXT,
        topic_id INTEGER,
        topic_name TEXT,
        source_language TEXT,
        original_message TEXT,
        
        -- AI response
        ai_suggestion TEXT,
        ai_confidence INTEGER DEFAULT 0,
        ai_topic TEXT,
        classification_type TEXT,
        
        -- Approval details
        approved_answer TEXT,
        approved_by TEXT,
        approved_at DATETIME,
        
        -- Telegram message tracking
        approval_msg_id INTEGER,
        
        -- Auto-skip tracking
        employee_answered INTEGER DEFAULT 0,
        employee_name TEXT,
        employee_answer TEXT,
        
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Index for quick lookups
    c.execute("CREATE INDEX IF NOT EXISTS idx_approvals_status ON ai_approvals(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_approvals_chat ON ai_approvals(chat_id, status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_approvals_token ON ai_approvals(token)")

    conn.commit()
    conn.close()
    print("✅ AI Approval tables initialized")


# ═══════════════════════════════════════════════════════════════════════════════
# APPROVAL HANDLER CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class ApprovalHandler:
    """
    Manages AI answer approvals via Telegram inline buttons.
    """

    def __init__(
        self,
        bot_client,
        user_client,
        approval_chat_id: int,
        db_path: str = "bot_data.db",
        translator=None,
        your_language: str = "de",
        knowledge_base=None,
    ):
        self.bot = bot_client
        self.user = user_client
        self.approval_chat_id = approval_chat_id
        self.db_path = db_path
        self.translator = translator
        self.your_language = your_language
        self.kb = knowledge_base  # KnowledgeBase instance for saving approved answers

        # Track edit mode: approval_token → True (waiting for corrected text)
        self._edit_mode = {}

        # Stats
        self._stats = {
            'sent': 0, 'approved': 0, 'edited': 0,
            'rejected': 0, 'auto_skipped': 0,
        }

        init_approval_db(db_path)
        print(f"✅ ApprovalHandler initialized (chat: {approval_chat_id})")

    def _get_db(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    # ══════════════════════════════════════════════════════════════════════
    #  SEND: AI suggestion to approval chat
    # ══════════════════════════════════════════════════════════════════════

    async def send_for_approval(
        self,
        token: str,
        question_text: str,
        ai_suggestion: str,
        sender_name: str,
        sender_id: int,
        chat_id: int,
        chat_title: str,
        topic_id: int = None,
        topic_name: str = "",
        source_language: str = "",
        original_message: str = "",
        ai_confidence: int = 0,
        ai_topic: str = "",
        classification_type: str = "",
    ) -> bool:
        """
        Send AI suggestion to approval chat with inline buttons.
        Returns True if sent successfully.
        """
        try:
            # ── Save to DB via centralized writer ──
            if db_writer is not None:
                await db_writer.execute("""
                    INSERT INTO ai_approvals
                    (token, question_text, sender_name, sender_id, chat_id, chat_title,
                     topic_id, topic_name, source_language, original_message,
                     ai_suggestion, ai_confidence, ai_topic, classification_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    token, question_text, sender_name, sender_id, chat_id, chat_title,
                    topic_id or 0, topic_name, source_language, original_message,
                    ai_suggestion, ai_confidence, ai_topic, classification_type,
                ))
            else:
                conn = self._get_db()
                c = conn.cursor()
                c.execute("""
                    INSERT INTO ai_approvals
                    (token, question_text, sender_name, sender_id, chat_id, chat_title,
                     topic_id, topic_name, source_language, original_message,
                     ai_suggestion, ai_confidence, ai_topic, classification_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    token, question_text, sender_name, sender_id, chat_id, chat_title,
                    topic_id or 0, topic_name, source_language, original_message,
                    ai_suggestion, ai_confidence, ai_topic, classification_type,
                ))
                conn.commit()
                conn.close()

            # ── Build approval message ──
            group_info = f"**{chat_title}**"
            if topic_name:
                group_info += f" → {topic_name}"

            # Timestamp in German timezone
            try:
                from zoneinfo import ZoneInfo
                _tz = ZoneInfo("Europe/Berlin")
            except ImportError:
                from datetime import timezone, timedelta
                _tz = timezone(timedelta(hours=2))
            now_de = datetime.now(_tz)
            timestamp_str = now_de.strftime('%d.%m.%Y um %H:%M Uhr')

            msg_text = (
                f"❓ **Neue Frage**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **Von:** {sender_name}\n"
                f"📣 **Gruppe:** {group_info}\n"
                f"🕐 {timestamp_str}\n"
            )

            if source_language and source_language.lower() not in ('de', 'german', 'deutsch'):
                msg_text += f"🌍 **Sprache:** {source_language}\n"

            msg_text += (
                f"\n💬 **Frage:**\n{question_text}\n"
                f"\n🤖 **KI-Vorschlag:**\n{ai_suggestion}\n"
            )

            if ai_confidence:
                msg_text += f"\n📊 Konfidenz: {ai_confidence}%"
            if ai_topic:
                msg_text += f" | Thema: {ai_topic}"

            # ── Inline buttons ──
            buttons = [
                [
                    Button.inline("✅ Freigeben", data=f"approve:{token}"),
                    Button.inline("✏️ Bearbeiten", data=f"edit:{token}"),
                    Button.inline("❌ Ablehnen", data=f"reject:{token}"),
                ],
            ]

            # ✍️ Answer-via-form button — opens CRM form (fill now OR later)
            try:
                buttons.append([Button.url("✍️ Antwort eingeben", f"{CRM_FORM_URL}?token={token}")])
            except Exception as e:
                print(f"   ⚠️ Form button error: {e}")

            # Add 🔊 listen button (TTS — read the AI suggestion aloud)
            tts_mgr = getattr(self, 'tts_manager', None)
            if tts_mgr is not None:
                try:
                    tts_id = tts_mgr.store_tts(ai_suggestion)
                    row = tts_mgr.make_button_row(tts_id)
                    if row:
                        buttons.append(row)
                except Exception as e:
                    print(f"   ⚠️ TTS button error: {e}")

            # Send via bot to approval chat
            sent_msg = await self.bot.send_message(
                self.approval_chat_id,
                msg_text,
                buttons=buttons,
                parse_mode='md',
            )

            # Save message ID for later update
            if sent_msg:
                if db_writer is not None:
                    db_writer.execute_nowait(
                        "UPDATE ai_approvals SET approval_msg_id = ? WHERE token = ?",
                        (sent_msg.id, token),
                    )
                else:
                    conn = self._get_db()
                    c = conn.cursor()
                    c.execute(
                        "UPDATE ai_approvals SET approval_msg_id = ? WHERE token = ?",
                        (sent_msg.id, token),
                    )
                    conn.commit()
                    conn.close()

            # Push to CRM so the answer-form can show the question (non-blocking)
            self._push_to_crm(token, question_text, ai_suggestion, chat_title,
                              sender_name, topic_name, source_language, ai_confidence, ai_topic)

            self._stats['sent'] += 1
            print(f"📬 Approval sent to Telegram (token: {token[:8]}...)")
            return True

        except Exception as e:
            print(f"⚠️ Failed to send approval: {e}")
            import traceback; traceback.print_exc()
            return False

    # ══════════════════════════════════════════════════════════════════════
    #  HANDLE: Button callbacks
    # ══════════════════════════════════════════════════════════════════════

    def _push_to_crm(self, token, question, suggestion, chat_title,
                     sender_name, topic_name, source_language, ai_confidence, ai_topic):
        """Send approval data to the CRM so approval_answer.php can render the form."""
        def _do():
            try:
                import requests
                requests.post(CRM_PUSH_URL, data={
                    'bot_secret': APPROVAL_BOT_SECRET,
                    'token': token,
                    'question': question or '',
                    'suggestion': suggestion or '',
                    'chat_title': chat_title or '',
                    'sender_name': sender_name or '',
                    'topic_name': topic_name or '',
                    'source_language': source_language or '',
                    'ai_confidence': str(ai_confidence or 0),
                    'ai_topic': ai_topic or '',
                }, timeout=15)
            except Exception as e:
                print(f"   ⚠️ CRM approval push failed: {e}")
        try:
            threading.Thread(target=_do, daemon=True).start()
        except Exception as e:
            print(f"   ⚠️ CRM push thread error: {e}")

    async def deliver_answer(self, token: str, answer_text: str, by: str = 'lothar') -> dict:
        """Deliver an externally-submitted (CRM form) answer to the customer group
        and resolve the approval. Event-free version of _do_approve.
        Returns {'ok': bool, 'error': str}."""
        answer_text = (answer_text or '').strip()
        if not answer_text:
            return {'ok': False, 'error': 'empty answer'}

        conn = self._get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM ai_approvals WHERE token = ?", (token,))
        row = c.fetchone()
        conn.close()
        if not row:
            return {'ok': False, 'error': 'not found'}

        columns = [
            'id', 'token', 'status', 'question_text', 'sender_name', 'sender_id',
            'chat_id', 'chat_title', 'topic_id', 'topic_name', 'source_language',
            'original_message', 'ai_suggestion', 'ai_confidence', 'ai_topic',
            'classification_type', 'approved_answer', 'approved_by', 'approved_at',
            'approval_msg_id', 'employee_answered', 'employee_name', 'employee_answer',
            'created_at',
        ]
        approval = dict(zip(columns, row))

        if approval['status'] != 'pending':
            return {'ok': False, 'error': f"already {approval['status']}"}

        chat_id = approval['chat_id']
        topic_id = approval['topic_id'] if approval['topic_id'] else None

        try:
            # Detect answer language
            answer_code = 'de'
            if self.translator:
                try:
                    al = self.translator.detect_language(answer_text)
                    answer_code = al.get('code', 'de') if isinstance(al, dict) else 'de'
                except Exception:
                    answer_code = 'de'

            # Send original answer via user account
            if topic_id:
                await self.user.send_message(chat_id, answer_text, reply_to=topic_id)
            else:
                await self.user.send_message(chat_id, answer_text)

            # Send translation via bot
            if self.translator and self.bot:
                target_lang = 'en' if answer_code == 'de' else 'de'
                try:
                    tr = self.translator.translate(text=answer_text, target_lang=target_lang, source_lang=answer_code)
                    translated = tr.get('translated_text', '')
                    if translated and translated.strip() and translated != answer_text:
                        target_chat = int(f"-100{chat_id}") if chat_id > 0 else chat_id
                        await self.bot.send_message(target_chat, translated, reply_to=topic_id if topic_id else None)
                except Exception as e:
                    print(f"   ⚠️ Form-answer translation error: {e}")

            # Update DB
            if db_writer is not None:
                db_writer.execute_nowait("""
                    UPDATE ai_approvals
                    SET status = 'edited', approved_answer = ?, approved_by = ?, approved_at = ?
                    WHERE token = ?
                """, (answer_text, by, datetime.now(), token))
            else:
                conn = self._get_db()
                c = conn.cursor()
                c.execute("""
                    UPDATE ai_approvals
                    SET status = 'edited', approved_answer = ?, approved_by = ?, approved_at = ?
                    WHERE token = ?
                """, (answer_text, by, datetime.now(), token))
                conn.commit()
                conn.close()

            # Edit the approval card (remove buttons) — no event, use stored msg id
            try:
                amid = approval.get('approval_msg_id')
                if amid:
                    await self.bot.edit_message(
                        self.approval_chat_id, amid,
                        f"✍️ **Beantwortet (Formular)**\n\n"
                        f"👤 {approval.get('sender_name','')} — {approval.get('chat_title','')}\n"
                        f"💬 {approval.get('question_text','')}\n\n"
                        f"✅ **Antwort:**\n{answer_text}",
                        buttons=None, parse_mode='md',
                    )
            except Exception as e:
                print(f"   ⚠️ Could not edit approval card: {e}")

            self._stats['edited'] += 1
            print(f"✍️ Approval ANSWERED via form (token: {token[:8]}...)")

            # Save to Knowledge Base
            if self.kb:
                try:
                    self.kb.save_answer(
                        question=approval.get('question_text', ''),
                        answer=answer_text,
                        topic=approval.get('ai_topic', ''),
                        intent=approval.get('ai_topic', ''),
                        classification_type=approval.get('classification_type', ''),
                        chat_id=chat_id,
                        customer_name=approval.get('chat_title', ''),
                        approval_token=token,
                    )
                except Exception as e:
                    print(f"   ⚠️ KB save error: {e}")

            return {'ok': True}
        except Exception as e:
            print(f"❌ deliver_answer error: {e}")
            import traceback; traceback.print_exc()
            return {'ok': False, 'error': str(e)[:200]}

    async def handle_callback(self, event):
        """Handle inline button press (approve/edit/reject)."""
        data = event.data.decode('utf-8')

        if ':' not in data:
            return

        action, token = data.split(':', 1)

        # Get approval record
        conn = self._get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM ai_approvals WHERE token = ?", (token,))
        row = c.fetchone()
        conn.close()

        if not row:
            await event.answer("⚠️ Eintrag nicht gefunden", alert=True)
            return

        # Map row to dict
        columns = [
            'id', 'token', 'status', 'question_text', 'sender_name', 'sender_id',
            'chat_id', 'chat_title', 'topic_id', 'topic_name', 'source_language',
            'original_message', 'ai_suggestion', 'ai_confidence', 'ai_topic',
            'classification_type', 'approved_answer', 'approved_by', 'approved_at',
            'approval_msg_id', 'employee_answered', 'employee_name', 'employee_answer',
            'created_at',
        ]
        approval = dict(zip(columns, row))

        if approval['status'] != 'pending':
            await event.answer(f"Bereits verarbeitet: {approval['status']}", alert=True)
            return

        # Get the original message (CallbackQuery doesn't have .message directly)
        msg = await event.get_message()

        # ── APPROVE ──
        if action == 'approve':
            await self._do_approve(event, msg, approval, approval['ai_suggestion'])

        # ── EDIT ──
        elif action == 'edit':
            await self._do_edit_start(event, msg, approval)

        # ── REJECT ──
        elif action == 'reject':
            await self._do_reject(event, msg, approval)

    async def _do_approve(self, event, msg, approval: Dict, answer_text: str):
        """Approve and post answer to original group."""
        token = approval['token']
        chat_id = approval['chat_id']
        topic_id = approval['topic_id'] if approval['topic_id'] else None
        source_lang = approval.get('source_language', '')

        try:
            # Send the answer in its original language (usually German)
            response = answer_text

            # Detect answer language
            answer_code = 'de'
            if self.translator:
                try:
                    al = self.translator.detect_language(response)
                    answer_code = al.get('code', 'de') if isinstance(al, dict) else 'de'
                except Exception:
                    answer_code = 'de'

            # Send original answer via user account
            if topic_id:
                await self.user.send_message(chat_id, response, reply_to=topic_id)
            else:
                await self.user.send_message(chat_id, response)
            print(f"   ✅ Approved answer sent ({answer_code}) via user account")

            # Send translation via bot (other language for multilingual group)
            if self.translator and self.bot:
                target_lang = 'en' if answer_code == 'de' else 'de'
                try:
                    tr = self.translator.translate(text=response, target_lang=target_lang, source_lang=answer_code)
                    translated = tr.get('translated_text', '')
                    if translated and translated.strip() and translated != response:
                        target_chat = int(f"-100{chat_id}") if chat_id > 0 else chat_id
                        await self.bot.send_message(target_chat, translated, reply_to=topic_id if topic_id else None)
                        print(f"   ✅ Translation sent ({target_lang}) via bot")
                except Exception as e:
                    print(f"   ⚠️ Approval translation error: {e}")

            # Update DB
            if db_writer is not None:
                db_writer.execute_nowait("""
                    UPDATE ai_approvals
                    SET status = 'approved', approved_answer = ?, approved_by = 'lothar',
                        approved_at = ?
                    WHERE token = ?
                """, (answer_text, datetime.now(), token))
            else:
                conn = self._get_db()
                c = conn.cursor()
                c.execute("""
                    UPDATE ai_approvals
                    SET status = 'approved', approved_answer = ?, approved_by = 'lothar',
                        approved_at = ?
                    WHERE token = ?
                """, (answer_text, datetime.now(), token))
                conn.commit()
                conn.close()

            # Update approval message (remove buttons, show status)
            await event.edit(
                msg.text + "\n\n✅ **Freigegeben und gesendet!**",
                buttons=None,
            )
            await event.answer("✅ Antwort gesendet!")

            self._stats['approved'] += 1
            print(f"✅ Approval: APPROVED (token: {token[:8]}...)")

            # Save to Knowledge Base for future reuse
            if self.kb:
                try:
                    self.kb.save_answer(
                        question=approval.get('question_text', ''),
                        answer=answer_text,
                        topic=approval.get('ai_topic', ''),
                        intent=approval.get('ai_topic', ''),
                        classification_type=approval.get('classification_type', ''),
                        chat_id=chat_id,
                        customer_name=approval.get('chat_title', ''),
                        approval_token=token,
                    )
                except Exception as e:
                    print(f"   ⚠️ KB save error: {e}")

        except Exception as e:
            await event.answer(f"❌ Fehler: {str(e)[:100]}", alert=True)
            print(f"❌ Approve error: {e}")

    async def _do_edit_start(self, event, msg, approval: Dict):
        """Start edit mode — wait for corrected text from Lothar."""
        token = approval['token']

        # Enter edit mode
        self._edit_mode[token] = {
            'approval': approval,
            'started_at': time.time(),
        }

        await event.edit(
            msg.text +
            "\n\n✏️ **Bearbeitungsmodus aktiv**"
            "\nBitte schreiben Sie jetzt die korrigierte Antwort als nächste Nachricht."
            "\nOder /cancel um abzubrechen.",
            buttons=[
                [Button.inline("❌ Abbrechen", data=f"cancel_edit:{token}")],
            ],
        )
        await event.answer("✏️ Schreiben Sie die korrigierte Antwort")
        print(f"✏️ Approval: EDIT MODE (token: {token[:8]}...)")

    async def handle_edit_message(self, event):
        """
        Handle incoming message in approval chat during edit mode.
        Call this for every message in the approval chat.
        Returns True if message was handled (was an edit response).
        """
        if not self._edit_mode:
            return False

        text = event.message.text
        if not text:
            return False

        # Cancel command
        if text.strip().lower() == '/cancel':
            # Cancel all edit modes
            for token in list(self._edit_mode.keys()):
                del self._edit_mode[token]
            await event.reply("❌ Bearbeitung abgebrochen.")
            return True

        # Find active edit (most recent)
        # Use the most recently started edit
        latest_token = None
        latest_time = 0
        for token, info in self._edit_mode.items():
            if info['started_at'] > latest_time:
                latest_time = info['started_at']
                latest_token = token

        if not latest_token:
            return False

        edit_info = self._edit_mode.pop(latest_token)
        approval = edit_info['approval']
        corrected_answer = text.strip()

        # Send corrected answer to original group
        try:
            chat_id = approval['chat_id']
            topic_id = approval['topic_id'] if approval['topic_id'] else None
            source_lang = approval.get('source_language', '')

            response = corrected_answer
            if self.translator and source_lang and source_lang.lower() not in ('de', 'german', 'deutsch'):
                try:
                    translation = self.translator.translate(
                        text=response,
                        target_lang=source_lang,
                        source_lang=self.your_language,
                    )
                    response = translation.get('translated_text', response)
                except Exception:
                    pass

            if topic_id:
                await self.user.send_message(chat_id, response, reply_to=topic_id)
            else:
                await self.user.send_message(chat_id, response)

            # Also send translation via bot (for multilingual groups)
            if self.translator and self.bot:
                try:
                    answer_lang = self.translator.detect_language(response)
                    answer_code = answer_lang.get('code', 'de') if isinstance(answer_lang, dict) else 'de'
                    target_langs = {'en'} if answer_code == 'de' else {'de'} if answer_code == 'en' else set()
                    for tl in target_langs:
                        try:
                            tr = self.translator.translate(text=response, target_lang=tl, source_lang=answer_code)
                            translated = tr.get('translated_text', '')
                            if translated and translated != response:
                                target_chat = int(f"-100{chat_id}") if chat_id > 0 else chat_id
                                await self.bot.send_message(target_chat, translated, reply_to=topic_id if topic_id else None)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"   ⚠️ Edit translation error: {e}")

            # Update DB
            if db_writer is not None:
                db_writer.execute_nowait("""
                    UPDATE ai_approvals
                    SET status = 'edited', approved_answer = ?, approved_by = 'lothar',
                        approved_at = ?
                    WHERE token = ?
                """, (corrected_answer, datetime.now(), latest_token))
            else:
                conn = self._get_db()
                c = conn.cursor()
                c.execute("""
                    UPDATE ai_approvals
                    SET status = 'edited', approved_answer = ?, approved_by = 'lothar',
                        approved_at = ?
                    WHERE token = ?
                """, (corrected_answer, datetime.now(), latest_token))
                conn.commit()
                conn.close()

            # Update original approval message
            if approval.get('approval_msg_id'):
                try:
                    await self.bot.edit_message(
                        self.approval_chat_id,
                        approval['approval_msg_id'],
                        event.message.text[:50] + "...\n\n✏️ **Bearbeitet und gesendet!**",
                        buttons=None,
                    )
                except Exception:
                    pass

            await event.reply("✅ Korrigierte Antwort gesendet!")
            self._stats['edited'] += 1
            print(f"✏️ Approval: EDITED + SENT (token: {latest_token[:8]}...)")

            # Save EDITED answer to KB (Lothar's correction is the gold standard)
            if self.kb:
                try:
                    self.kb.save_answer(
                        question=approval.get('question_text', ''),
                        answer=corrected_answer,
                        topic=approval.get('ai_topic', ''),
                        intent=approval.get('ai_topic', ''),
                        classification_type=approval.get('classification_type', ''),
                        chat_id=approval.get('chat_id', 0),
                        customer_name=approval.get('chat_title', ''),
                        approval_token=latest_token,
                        approved_by='lothar_edited',
                    )
                except Exception as e:
                    print(f"   ⚠️ KB save error: {e}")
            return True

        except Exception as e:
            await event.reply(f"❌ Fehler beim Senden: {e}")
            print(f"❌ Edit send error: {e}")
            return True

    async def _do_reject(self, event, msg, approval: Dict):
        """Reject AI suggestion."""
        token = approval['token']

        if db_writer is not None:
            db_writer.execute_nowait(
                "UPDATE ai_approvals SET status = 'rejected', approved_at = ? WHERE token = ?",
                (datetime.now(), token),
            )
        else:
            conn = self._get_db()
            c = conn.cursor()
            c.execute(
                "UPDATE ai_approvals SET status = 'rejected', approved_at = ? WHERE token = ?",
                (datetime.now(), token),
            )
            conn.commit()
            conn.close()

        await event.edit(
            msg.text + "\n\n❌ **Abgelehnt — nicht gesendet.**",
            buttons=None,
        )
        await event.answer("❌ Abgelehnt")

        self._stats['rejected'] += 1
        print(f"❌ Approval: REJECTED (token: {token[:8]}...)")

    # ══════════════════════════════════════════════════════════════════════
    #  AUTO-SKIP: Employee answered first
    # ══════════════════════════════════════════════════════════════════════

    async def check_employee_answered(
        self,
        chat_id: int,
        topic_id: int = None,
        sender_name: str = "",
        answer_text: str = "",
        sender_id: int = 0,
        bot_user_id: int = 0,
        your_user_id: int = 0,
    ):
        """
        Call this when a new message arrives in a group.
        If there's a pending approval for this group and someone
        (not the bot, not the original question asker) replied,
        auto-mark as employee-answered.
        """
        if sender_id in (bot_user_id, 0):
            return  # Bot's own message, ignore

        conn = self._get_db()
        c = conn.cursor()

        # Find pending approvals for this chat
        if topic_id:
            c.execute("""
                SELECT id, token, sender_id, approval_msg_id, question_text, ai_topic, classification_type, chat_title
                FROM ai_approvals
                WHERE chat_id = ? AND topic_id = ? AND status = 'pending'
                ORDER BY created_at DESC LIMIT 5
            """, (chat_id, topic_id))
        else:
            c.execute("""
                SELECT id, token, sender_id, approval_msg_id, question_text, ai_topic, classification_type, chat_title
                FROM ai_approvals
                WHERE chat_id = ? AND status = 'pending'
                ORDER BY created_at DESC LIMIT 5
            """, (chat_id,))

        pending = c.fetchall()

        for row in pending:
            approval_id, token, original_sender_id, approval_msg_id, question_text, ai_topic, classification_type, chat_title_db = row

            # Don't auto-skip if the original question asker is talking again
            if sender_id == original_sender_id:
                continue

            # Someone else replied → employee answered
            if db_writer is not None:
                db_writer.execute_nowait("""
                    UPDATE ai_approvals
                    SET status = 'employee_answered', employee_answered = 1,
                        employee_name = ?, employee_answer = ?, approved_at = ?
                    WHERE id = ?
                """, (sender_name, answer_text[:500], datetime.now(), approval_id))
            else:
                c.execute("""
                    UPDATE ai_approvals
                    SET status = 'employee_answered', employee_answered = 1,
                        employee_name = ?, employee_answer = ?, approved_at = ?
                    WHERE id = ?
                """, (sender_name, answer_text[:500], datetime.now(), approval_id))

            # Save employee's answer to Knowledge Base
            if self.kb and question_text and answer_text and len(answer_text.strip()) > 5:
                try:
                    self.kb.save_answer(
                        question=question_text,
                        answer=answer_text[:500],
                        topic=ai_topic or '',
                        intent=ai_topic or '',
                        classification_type=classification_type or '',
                        chat_id=chat_id,
                        customer_name=chat_title_db or '',
                        approval_token=token,
                        approved_by=f'employee_{sender_name}',
                    )
                    print(f"   📝 KB: Saved {sender_name}'s answer for future use")
                except Exception as e:
                    print(f"   ⚠️ KB save error: {e}")

            # Update approval message in approval chat
            if approval_msg_id:
                try:
                    await self.bot.edit_message(
                        self.approval_chat_id,
                        approval_msg_id,
                        f"👤 **Bereits beantwortet von {sender_name}**\n"
                        f"💬 {answer_text[:200]}\n\n"
                        f"⏭️ KI-Vorschlag automatisch verworfen.",
                        buttons=None,
                    )
                except Exception as e:
                    print(f"   ⚠️ Could not update approval message: {e}")

            self._stats['auto_skipped'] += 1
            print(f"⏭️ Auto-skip: Employee {sender_name} answered in [{chat_id}] (token: {token[:8]}...)")

        try:
            conn.commit()
        except Exception:
            pass
        conn.close()

    # ══════════════════════════════════════════════════════════════════════
    #  CANCEL EDIT callback
    # ══════════════════════════════════════════════════════════════════════

    async def handle_cancel_edit(self, event):
        """Handle cancel_edit button press."""
        data = event.data.decode('utf-8')
        if not data.startswith('cancel_edit:'):
            return

        token = data.split(':', 1)[1]
        if token in self._edit_mode:
            del self._edit_mode[token]

        msg = await event.get_message()
        await event.edit(
            msg.text.replace(
                "✏️ **Bearbeitungsmodus aktiv**",
                "❌ **Bearbeitung abgebrochen**"
            ),
            buttons=None,
        )
        await event.answer("❌ Abgebrochen")

    # ══════════════════════════════════════════════════════════════════════
    #  STATS
    # ══════════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        return self._stats.copy()

    def reset_stats(self):
        for key in self._stats:
            self._stats[key] = 0

    def get_pending_count(self) -> int:
        """Get number of pending approvals."""
        try:
            conn = self._get_db()
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM ai_approvals WHERE status = 'pending'")
            count = c.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0