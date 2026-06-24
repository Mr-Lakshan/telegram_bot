"""
CONVERSATION LEARNING — Extract Knowledge from Daily Chats
=============================================================
Opus reads all conversations from the day, identifies:
  - Business rules ("Geld muss in Überschrift")
  - Customer-specific info ("Frau Müller will Haltegriff")
  - Process guides ("Screenshot vor Veröffentlichung anfordern")
  - Contact info ("premiobad.lothararens@gmail.com" - admin only)

Runs once daily (alongside the report at 19:00).
"""

import os
import json
import sqlite3
import requests
from datetime import datetime
from typing import Dict, List, Optional

# KB Scorer — filters which messages are worth considering (Lothar's score system)
try:
    from bot.knowledge.kb_scorer import KBScorer
except ImportError:
    KBScorer = None

# Use same timezone as daily report
try:
    from zoneinfo import ZoneInfo
    GERMANY_TZ = ZoneInfo("Europe/Berlin")
except ImportError:
    from datetime import timezone, timedelta
    GERMANY_TZ = timezone(timedelta(hours=2))


def _now_germany():
    return datetime.now(GERMANY_TZ)


OPUS_MODEL = "claude-opus-4-7"

# Max messages to send to Opus (token limit)
MAX_MESSAGES_PER_BATCH = 150
MAX_CHARS_TOTAL = 30000


class ConversationLearner:
    """
    Reads daily conversations and extracts knowledge using Claude Opus.
    """

    def __init__(
        self,
        db_path: str = "bot_data.db",
        anthropic_api_key: str = "",
        kb=None,
        suggestion_manager=None,
    ):
        self.db_path = db_path
        self.api_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.kb = kb
        self.suggestion_manager = suggestion_manager
        self.scorer = KBScorer() if KBScorer else None

        print(f"✅ ConversationLearner initialized" + (" (with KB scorer)" if self.scorer else ""))

    def _get_db(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    # ══════════════════════════════════════════════════════════════════════
    #  MAIN: Extract learnings from today's conversations
    # ══════════════════════════════════════════════════════════════════════

    def extract_daily_learnings(self) -> Dict:
        """
        Main entry point. Called from daily report at 19:00.
        Returns: {'learnings_saved': int, 'learnings': [...]}
        """
        print(f"📚 Extracting learnings from today's conversations...")

        # Step 1: Collect today's messages grouped by chat
        conversations = self._collect_conversations()

        if not conversations:
            print(f"   ℹ️ No conversations to analyze")
            return {'learnings_saved': 0, 'learnings': []}

        # Step 2: Send to Opus for analysis
        learnings = self._analyze_with_opus(conversations)

        if not learnings:
            print(f"   ℹ️ No learnings extracted")
            return {'learnings_saved': 0, 'learnings': []}

        # Step 3: Save to KB
        saved = self._save_learnings(learnings)

        print(f"   ✅ {saved} neue Erkenntnisse in Wissensdatenbank gespeichert")
        return {'learnings_saved': saved, 'learnings': learnings}

    async def suggest_daily_learnings(self) -> Dict:
        """
        Phase B: Instead of auto-saving, SUGGEST learnings with buttons.
        Called from daily report at 19:00. Lothar confirms each via buttons.
        """
        print(f"💡 Extracting learnings to SUGGEST (evening)...")

        conversations = self._collect_conversations()
        if not conversations:
            print(f"   ℹ️ No conversations with KB candidates")
            return {'suggested': 0, 'learnings': []}

        learnings = self._analyze_with_opus(conversations)
        if not learnings:
            print(f"   ℹ️ No learnings extracted")
            return {'suggested': 0, 'learnings': []}

        # Send as suggestions with buttons (not auto-save)
        if self.suggestion_manager:
            sent = await self.suggestion_manager.send_suggestions(learnings)
            print(f"   💡 {sent} Vorschläge an KI Freigaben gesendet")
            return {'suggested': sent, 'learnings': learnings}
        else:
            # Fallback: auto-save if no suggestion manager
            saved = self._save_learnings(learnings)
            return {'suggested': 0, 'learnings_saved': saved, 'learnings': learnings}

    # ══════════════════════════════════════════════════════════════════════
    #  COLLECT: Get today's conversations grouped by chat
    # ══════════════════════════════════════════════════════════════════════

    def _collect_conversations(self) -> List[Dict]:
        """Collect today's group messages, grouped by chat."""
        conn = self._get_db()
        c = conn.cursor()

        today_start = _now_germany().replace(
            hour=0, minute=0, second=0
        ).strftime('%Y-%m-%d %H:%M:%S')

        c.execute("""
            SELECT chat_title, sender_name, message_text, timestamp
            FROM group_messages
            WHERE timestamp >= ? AND message_text != '' AND chat_title != ''
            ORDER BY chat_title, timestamp
        """, (today_start,))

        rows = c.fetchall()
        conn.close()

        if not rows:
            return []

        # Group by chat
        chats = {}
        for row in rows:
            title = row['chat_title']
            if title not in chats:
                chats[title] = []
            chats[title].append({
                'sender': row['sender_name'] or '?',
                'text': (row['message_text'] or '')[:300],  # Truncate long msgs
                'time': row['timestamp'] or '',
            })

        # Convert to list, truncate if too many
        conversations = []
        total_chars = 0

        for title, messages in chats.items():
            # Skip very small conversations (< 3 messages = no real dialog)
            if len(messages) < 3:
                continue

            # Score messages — only chats with real KB candidates proceed
            candidate_count = 0
            conv_lines = []
            for m in messages[-30:]:  # Last 30 messages per chat
                marker = ""
                if self.scorer:
                    result = self.scorer.score(m['text'], sender_name=m['sender'])
                    if result['is_candidate']:
                        candidate_count += 1
                        marker = " ⭐"  # Mark high-value lines for Opus
                line = f"{m['sender']}: {m['text']}{marker}"
                conv_lines.append(line)

            # Skip chats with NO KB candidates (pure chat/coordination)
            if self.scorer and candidate_count == 0:
                print(f"   ⏭️ Skip '{title}' — keine KB-Kandidaten (nur Chat/Koordination)")
                continue

            conv_text = "\n".join(conv_lines)

            # Check total size limit
            if total_chars + len(conv_text) > MAX_CHARS_TOTAL:
                break

            conversations.append({
                'chat_title': title,
                'message_count': len(messages),
                'candidate_count': candidate_count if self.scorer else None,
                'text': conv_text,
            })
            total_chars += len(conv_text)

        if self.scorer:
            print(f"   📋 {len(conversations)} Gespräche mit KB-Kandidaten ({total_chars} Zeichen)")
        else:
            print(f"   📋 {len(conversations)} Gespräche gesammelt ({total_chars} Zeichen)")
        return conversations

    # ══════════════════════════════════════════════════════════════════════
    #  ANALYZE: Opus extracts learnings
    # ══════════════════════════════════════════════════════════════════════

    def _analyze_with_opus(self, conversations: List[Dict]) -> List[Dict]:
        """Send conversations to Opus, get structured learnings back."""

        if not self.api_key:
            print(f"   ⚠️ No API key for Opus")
            return []

        # Build conversation text for Opus
        conv_text_parts = []
        for conv in conversations:
            conv_text_parts.append(
                f"=== GRUPPE: {conv['chat_title']} ({conv['message_count']} Nachrichten) ===\n"
                f"{conv['text']}\n"
            )

        all_conversations = "\n\n".join(conv_text_parts)

        system_prompt = """Du bist ein KI-Analyst fuer ein Bauunternehmen (Premiobad/Seniorex — Badsanierung).

Lies die Gespraeche des Tages und extrahiere WICHTIGE ERKENNTNISSE, die fuer die Zukunft nuetzlich sind.

Typen von Erkenntnissen:
- "rule": Allgemeine Geschaeftsregeln (z.B. "Geld muss in Anzeigen-Ueberschrift")
- "process": Arbeitsablaeufe (z.B. "Screenshot vor Veroeffentlichung anfordern")  
- "customer_info": Kundenspezifische Infos (z.B. "Herr Mueller moechte Haltegriff")
- "contact": Kontaktdaten (z.B. E-Mail-Adressen, Telefonnummern)
- "mistake": Haeufige Fehler die vermieden werden sollen

WICHTIG:
- Nur WERTVOLLE, WIEDERVERWENDBARE Erkenntnisse
- KEINE Smalltalk, Gruesse, Terminabsprachen
- KEINE offensichtlichen Dinge
- Maximal 10 Erkenntnisse pro Tag
- Wenn keine wichtigen Erkenntnisse: leeres Array []

Antworte NUR mit einem JSON-Array. Kein Text davor oder danach.
Format:
[
  {"type": "rule", "title": "Kurzer Titel", "content": "Ausfuehrliche Beschreibung", "scope": "global", "chat": "Gruppenname"},
  {"type": "customer_info", "title": "Kunde X will Y", "content": "Details", "scope": "customer", "chat": "Baustart Kunde X"}
]

scope: "global" = gilt fuer alle, "customer" = gilt nur fuer diesen Kunden"""

        user_prompt = f"GESPRAECHE VOM {_now_germany().strftime('%d.%m.%Y')}:\n\n{all_conversations}\n\nExtrahiere die wichtigsten Erkenntnisse als JSON-Array:"

        try:
            headers = {
                "x-api-key": self.api_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": OPUS_MODEL,
                "max_tokens": 3000,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            }

            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers, json=payload, timeout=90,
            )

            if resp.status_code != 200:
                print(f"   ⚠️ Opus error {resp.status_code}: {resp.text[:200]}")
                return []

            result = resp.json()
            text = ""
            for block in result.get("content", []):
                if block.get("type") == "text":
                    text += block["text"]

            # Parse JSON from response
            text = text.strip()
            # Remove markdown code blocks if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            learnings = json.loads(text)

            if not isinstance(learnings, list):
                print(f"   ⚠️ Opus returned non-list: {type(learnings)}")
                return []

            print(f"   🧠 Opus extrahierte {len(learnings)} Erkenntnisse")
            return learnings[:10]  # Max 10

        except json.JSONDecodeError as e:
            # Try to recover truncated JSON — find last complete object
            try:
                last_close = text.rfind('}')
                if last_close > 0:
                    fixed = text[:last_close + 1] + ']'
                    learnings = json.loads(fixed)
                    if isinstance(learnings, list) and learnings:
                        print(f"   🧠 Opus extrahierte {len(learnings)} Erkenntnisse (truncated recovery)")
                        return learnings[:10]
            except Exception:
                pass
            print(f"   ⚠️ JSON parse error: {e}")
            print(f"   Raw text: {text[:200]}")
            return []
        except Exception as e:
            print(f"   ⚠️ Opus analysis error: {e}")
            return []

    # ══════════════════════════════════════════════════════════════════════
    #  SAVE: Store learnings in Knowledge Base
    # ══════════════════════════════════════════════════════════════════════

    def _save_learnings(self, learnings: List[Dict]) -> int:
        """Save extracted learnings to KB."""
        if not self.kb:
            print(f"   ⚠️ No KB instance — cannot save")
            return 0

        saved = 0
        for learning in learnings:
            try:
                l_type = learning.get('type', 'rule')
                title = learning.get('title', '')
                content = learning.get('content', '')
                scope = learning.get('scope', 'global')
                chat = learning.get('chat', '')

                if not title or not content:
                    continue

                # Use title as question, content as answer
                success = self.kb.save_answer(
                    question=title,
                    answer=content,
                    topic=l_type,
                    intent=l_type,
                    classification_type=f'auto_learned_{l_type}',
                    chat_id=0,  # Global by default
                    customer_name=chat,
                    approval_token='',
                    approved_by='opus_auto_learning',
                )

                if success:
                    saved += 1
                    print(f"   📝 [{l_type}] {title[:60]}")

            except Exception as e:
                print(f"   ⚠️ Save error: {e}")

        return saved