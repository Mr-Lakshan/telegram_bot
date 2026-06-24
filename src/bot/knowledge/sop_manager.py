"""
SOP MANAGER — Voice/Text → AI Structure → Google Drive
========================================================
Lothar speaks or types in KI Freigaben with prefix:
  "Regel: ..." → saved to Drive/Arbeitsanweisungen/Regeln/
  "Idee: ..." → saved to Drive/Arbeitsanweisungen/Ideen/
  "Prozess: ..." → saved to Drive/Arbeitsanweisungen/Prozesse/
  No prefix → AI detects category

Voice messages: Whisper transcribe → AI structure → Drive save
Text messages: AI structure → Drive save
"""

import os
import asyncio
import tempfile
import requests
from datetime import datetime
from typing import Optional, Tuple

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    from zoneinfo import ZoneInfo
    GERMANY_TZ = ZoneInfo("Europe/Berlin")
except ImportError:
    from datetime import timezone, timedelta
    GERMANY_TZ = timezone(timedelta(hours=2))


# Category prefixes Lothar can use
CATEGORIES = {
    'regel': 'Regeln',
    'rule': 'Regeln',
    'idee': 'Ideen',
    'idea': 'Ideen',
    'prozess': 'Prozesse',
    'process': 'Prozesse',
    'checkliste': 'Checklisten',
    'checklist': 'Checklisten',
    'kontakt': 'Kontakte',
    'contact': 'Kontakte',
    'notiz': 'Notizen',
    'note': 'Notizen',
}

DEFAULT_CATEGORY = 'Unsortiert'
PARENT_FOLDER = 'Arbeitsanweisungen'


class SOPManager:
    """Manages SOPs — voice/text → AI structure → Drive save."""

    def __init__(
        self,
        openai_api_key: str = "",
        anthropic_api_key: str = "",
        crm_api_url: str = "",
        crm_api_key: str = "",
        sop_trigger=None,
    ):
        self.openai_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.anthropic_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.crm_url = crm_api_url or os.getenv("CRM_API_URL", "")
        self.crm_key = crm_api_key or os.getenv("CRM_BOT_API_KEY", "")
        self.openai_client = OpenAI(api_key=self.openai_key) if HAS_OPENAI and self.openai_key else None
        self.sop_trigger = sop_trigger  # for indexing saved SOPs

        print(f"✅ SOPManager initialized")

    # ══════════════════════════════════════════════════════════════════════
    #  MAIN: Process voice or text message
    # ══════════════════════════════════════════════════════════════════════

    async def process_message(self, text: str = "", voice_path: str = "") -> Optional[str]:
        """
        Process a voice or text SOP message.
        Returns confirmation text or None on failure.
        """
        try:
            # Step 1: Transcribe voice if needed
            if voice_path and not text:
                text = self._transcribe_voice(voice_path)
                if not text:
                    return "⚠️ Sprachnachricht konnte nicht erkannt werden."

            if not text or len(text.strip()) < 5:
                return None

            # Step 2: Detect category from prefix
            category, clean_text = self._detect_category(text)

            # Step 3: AI structures the text
            structured = self._structure_with_ai(clean_text, category)

            # Step 4: Create title
            now = datetime.now(GERMANY_TZ)
            title = self._generate_title(clean_text, now)

            # Step 5: Save to Google Drive
            result = self._save_to_drive(
                folder_name=category,
                file_name=title,
                content=structured,
            )

            if result and result.get('success'):
                link = result.get('web_link', '')
                link_text = f"\n🔗 {link}" if link else ""

                # Index the SOP for retrieval (trigger feature)
                if self.sop_trigger:
                    try:
                        self.sop_trigger.index_sop(
                            title=title, category=category,
                            content=structured, drive_link=link,
                        )
                    except Exception as e:
                        print(f"   ⚠️ SOP index error: {e}")

                return f"✅ Gespeichert als **{category}** → Ordner {category}{link_text}"
            else:
                error = result.get('error', 'Unbekannter Fehler') if result else 'Keine Antwort'
                return f"⚠️ Konnte nicht gespeichert werden: {error}"

        except Exception as e:
            print(f"   ⚠️ SOP error: {e}")
            return f"⚠️ Fehler: {str(e)[:100]}"

    # ══════════════════════════════════════════════════════════════════════
    #  VOICE: Whisper transcription
    # ══════════════════════════════════════════════════════════════════════

    def _transcribe_voice(self, audio_path: str) -> Optional[str]:
        """Transcribe voice message using OpenAI Whisper."""
        if not self.openai_client:
            print(f"   ⚠️ No OpenAI client for Whisper")
            return None

        try:
            with open(audio_path, 'rb') as f:
                result = self.openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                )
            text = result.text.strip() if result.text else ""
            print(f"   🎤 Transcribed: {text[:80]}...")
            return text if text else None

        except Exception as e:
            print(f"   ⚠️ Whisper error: {e}")
            return None

    # ══════════════════════════════════════════════════════════════════════
    #  CATEGORY: Detect from prefix
    # ══════════════════════════════════════════════════════════════════════

    def _detect_category(self, text: str) -> Tuple[str, str]:
        """Detect category from prefix, return (category, clean_text)."""
        text = text.strip()

        # Check for prefix pattern: "Regel: ..." or "Regel - ..."
        for prefix, folder in CATEGORIES.items():
            patterns = [f"{prefix}:", f"{prefix} -", f"{prefix} –"]
            for p in patterns:
                if text.lower().startswith(p):
                    clean = text[len(p):].strip()
                    return folder, clean

        # No prefix — let AI decide
        category = self._ai_detect_category(text)
        return category, text

    def _ai_detect_category(self, text: str) -> str:
        """Use AI to detect category when no prefix given."""
        if not self.anthropic_key:
            return DEFAULT_CATEGORY

        try:
            headers = {
                "x-api-key": self.anthropic_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": "claude-sonnet-4-6",
                "max_tokens": 20,
                "system": "Kategorisiere den Text in GENAU eine Kategorie. Antworte NUR mit dem Kategorienamen.\nKategorien: Regeln, Ideen, Prozesse, Checklisten, Kontakte, Notizen, Unsortiert",
                "messages": [{"role": "user", "content": text[:500]}],
            }
            resp = requests.post("https://api.anthropic.com/v1/messages",
                headers=headers, json=payload, timeout=10)

            if resp.status_code == 200:
                result = resp.json()
                for block in result.get("content", []):
                    if block.get("type") == "text":
                        cat = block["text"].strip()
                        if cat in ['Regeln', 'Ideen', 'Prozesse', 'Checklisten', 'Kontakte', 'Notizen']:
                            return cat
            return DEFAULT_CATEGORY

        except Exception:
            return DEFAULT_CATEGORY

    # ══════════════════════════════════════════════════════════════════════
    #  STRUCTURE: AI formats the text
    # ══════════════════════════════════════════════════════════════════════

    def _structure_with_ai(self, text: str, category: str) -> str:
        """AI structures the raw text into clean format."""
        if not self.anthropic_key:
            return text

        try:
            headers = {
                "x-api-key": self.anthropic_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": "claude-sonnet-4-6",
                "max_tokens": 800,
                "system": f"""Strukturiere den folgenden Text sauber und uebersichtlich. 
Kategorie: {category}
- Behalte den GESAMTEN Inhalt bei
- Mache Aufzaehlungen wo sinnvoll
- Korrigiere Grammatik/Rechtschreibung
- Fuege eine kurze Ueberschrift hinzu
- Schreibe auf Deutsch
- Wenn es eine Regel ist: formuliere sie klar und eindeutig
- Wenn es ein Prozess ist: nummeriere die Schritte""",
                "messages": [{"role": "user", "content": text}],
            }

            resp = requests.post("https://api.anthropic.com/v1/messages",
                headers=headers, json=payload, timeout=30)

            if resp.status_code == 200:
                result = resp.json()
                structured = ""
                for block in result.get("content", []):
                    if block.get("type") == "text":
                        structured += block["text"]
                return structured.strip() if structured else text

        except Exception as e:
            print(f"   ⚠️ AI structuring error: {e}")

        return text

    # ══════════════════════════════════════════════════════════════════════
    #  TITLE: Generate filename
    # ══════════════════════════════════════════════════════════════════════

    def _generate_title(self, text: str, now: datetime) -> str:
        """Generate a short title for the file."""
        date_str = now.strftime('%Y-%m-%d')
        # Take first 40 chars, clean up
        short = text[:40].replace('\n', ' ').strip()
        # Remove special chars
        for ch in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
            short = short.replace(ch, '')
        short = short.strip()
        if not short:
            short = "Eintrag"
        return f"{date_str} — {short}"

    # ══════════════════════════════════════════════════════════════════════
    #  DRIVE: Save via CRM API
    # ══════════════════════════════════════════════════════════════════════

    def _save_to_drive(self, folder_name: str, file_name: str, content: str) -> Optional[dict]:
        """Save structured text to Google Drive via CRM API."""
        if not self.crm_url or not self.crm_key:
            print(f"   ⚠️ No CRM config for Drive save")
            return None

        try:
            resp = requests.post(
                self.crm_url,
                params={'action': 'create_drive_file'},
                data={
                    'folder_name': folder_name,
                    'file_name': file_name,
                    'content': content,
                    'parent_folder': PARENT_FOLDER,
                },
                headers={'X-Bot-Api-Key': self.crm_key},
                timeout=30,
            )
            return resp.json()

        except Exception as e:
            print(f"   ⚠️ Drive save error: {e}")
            return None