"""
CALL ANALYSIS — Kundengespräch-Aufnahmen analysieren
=====================================================
Lothar lädt eine Gesprächsaufnahme (Audio/Video) in die KI-Freigaben-Gruppe hoch
MIT Caption „Anruf" oder „Gespräch" → transkribieren (Whisper) → Vertriebs-Analyse
(überflüssige Fragen, Kundenreaktionen, Verbesserungsvorschläge) → zurück in KI-Freigaben.

Wiederverwendung der bestehenden Transkriptions-Pipeline aus construction_video_handler:
    download_media -> extract_audio -> transcribe_audio (Whisper)

Integration (telegram_bot_groups.py / src/bot/main.py):
    from bot.reports.call_analysis import CallAnalyzer        # (Paket-Layout)
    # bzw.  from call_analysis import CallAnalyzer            # (flach)
    call_analyzer = CallAnalyzer(user_client, bot_client, APPROVAL_CHAT_ID, OPENAI_API_KEY)
"""

import os
import asyncio
from openai import OpenAI

# Transkriptions-Bausteine WIEDERVERWENDEN (kein Code dupliziert):
from bot.handlers.construction_video_handler import (
    download_media, extract_audio, transcribe_audio,
)

CALL_ANALYSIS_MODEL = os.getenv("CALL_ANALYSIS_MODEL", "gpt-4o")
TG_LIMIT = 3500   # Telegram 4096 — sicher darunter chunked

CALL_PROMPT = """Du bist ein erfahrener, ehrlicher Vertriebs-Coach für eine Badsanierungs-/Pflege-Firma.
Unten ist das Transkript eines aufgezeichneten KUNDENGESPRÄCHS (Telefon).

Analysiere das Gespräch und gib eine strukturierte Auswertung auf DEUTSCH mit genau diesen Abschnitten:

📋 ZUSAMMENFASSUNG
Kurz, 2–3 Sätze: worum ging es, wie lief das Gespräch.

❓ ÜBERFLÜSSIGE / UNNÖTIGE FRAGEN
Welche Fragen waren redundant, zu lang, doppelt oder hätten weggelassen werden können? Konkret mit Beispiel.

😊 KUNDENREAKTIONEN
Wie hat der Kunde auf bestimmte Fragen/Themen reagiert (interessiert, zögerlich, genervt, positiv)? Wo gab es Reibung?

✅ VERBESSERUNGSVORSCHLÄGE
Konkrete, umsetzbare Tipps, wie die Fragen / der Gesprächsablauf besser gestaltet werden können.

Stütze dich NUR auf das Transkript. Sei konkret, ehrlich und praxisnah. Keine Erfindungen.

TRANSKRIPT:
"""


class CallAnalyzer:
    def __init__(self, user_client, bot_client, approval_chat_id, openai_api_key=""):
        self.user = user_client
        self.bot = bot_client
        self.chat_id = approval_chat_id
        self.openai = OpenAI(api_key=openai_api_key or os.getenv("OPENAI_API_KEY", ""))
        self.model = CALL_ANALYSIS_MODEL
        print(f"✅ CallAnalyzer initialisiert (Modell: {self.model})")

    async def analyze(self, message):
        """Volle Pipeline: download -> audio -> Whisper -> Analyse -> posten."""
        try:
            await self.bot.send_message(
                self.chat_id,
                "⏳ Kundengespräch wird transkribiert & analysiert… (kann 1–2 Min dauern)")
        except Exception:
            pass

        media_path = audio_path = None
        try:
            # 1) Download + 2) Audio extrahieren + 3) Whisper (wiederverwendet)
            media_path, media_type = await download_media(self.user, message)
            audio_path = extract_audio(media_path, media_type)
            # Sprache wird erkannt statt erzwungen — ein polnisch
            # gefuehrtes Gespraech kam als deutscher Unsinn zurueck.
            tr = transcribe_audio(audio_path)
            transcript = (tr.get("text") or "").strip()

            if len(transcript) < 10:
                await self.bot.send_message(self.chat_id, "⚠️ Keine verständliche Sprache erkannt.")
                return

            # 4) Analyse (GPT-4o, im Executor — blockiert den Bot nicht)
            loop = asyncio.get_event_loop()
            analysis = await loop.run_in_executor(None, self._analyze, transcript)
            if not analysis:
                await self.bot.send_message(self.chat_id, "⚠️ Analyse fehlgeschlagen.")
                return

            # 5) Ergebnis posten (Analyse zuerst, dann Transkript)
            await self._send_long("📞 **Gesprächsanalyse**\n━━━━━━━━━━━━━━━━━━━━\n" + analysis)
            await self._send_long("📝 **Transkript**\n━━━━━━━━━━━━━━━━━━━━\n" + transcript)
            print("   ✅ Gesprächsanalyse gepostet")

        except ValueError as ve:   # z. B. Datei zu groß
            try: await self.bot.send_message(self.chat_id, f"⚠️ {ve}")
            except Exception: pass
        except Exception as e:
            print(f"   ⚠️ Call-Analyse Fehler: {e}")
            try: await self.bot.send_message(self.chat_id, f"⚠️ Fehler bei der Analyse: {str(e)[:200]}")
            except Exception: pass
        finally:
            for p in (media_path, audio_path):
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass

    def _analyze(self, transcript: str) -> str:
        try:
            resp = self.openai.chat.completions.create(
                model=self.model,
                max_tokens=1400,
                temperature=0.4,
                messages=[
                    {"role": "system", "content": "Du bist ein erfahrener, ehrlicher Vertriebs-Coach."},
                    {"role": "user", "content": CALL_PROMPT + transcript},
                ],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            print(f"      ⚠️ GPT-Fehler: {e}")
            return ""

    async def _send_long(self, text: str):
        """Telegram 4096-Zeichen-Limit umgehen — in Stücke senden."""
        for i in range(0, len(text), TG_LIMIT):
            chunk = text[i:i + TG_LIMIT]
            try:
                await self.bot.send_message(self.chat_id, chunk)
            except Exception:
                await self.bot.send_message(self.chat_id, chunk.replace('**', ''))
            await asyncio.sleep(0.3)