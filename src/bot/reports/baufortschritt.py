"""
BAUFORTSCHRITT — Tägliche KI-Foto-Analyse der Baustellen
=========================================================
Jeden Abend (Standard 19:05 Uhr) werden die HEUTE in den Baustellen-Gruppen
hochgeladenen Fotos eingesammelt und per Vision-KI ausgewertet:
"Was wurde heute gemacht?" (z. B. Fliesen verlegt, Rohbau, Dusche montiert).
Das Ergebnis wird in die KI-Freigaben-Gruppe gepostet.

Integration (in telegram_bot_groups.py, main()):
    from bot.reports.baufortschritt import BaufortschrittReporter
    bf = BaufortschrittReporter(user_client, bot_client, APPROVAL_CHAT_ID, OPENAI_API_KEY)
    bf.start_scheduler()
"""

import os
import base64
import asyncio
from io import BytesIO
from datetime import datetime, timedelta, timezone

from openai import OpenAI

# Pillow ist optional — wenn vorhanden, werden Bilder verkleinert (spart Kosten)
try:
    from PIL import Image
    _PIL = True
except Exception:
    _PIL = False

# ── Konfiguration (per .env überschreibbar) ──────────────────────────────────
GERMANY_TZ      = timezone(timedelta(hours=2))                       # wie daily_report
BF_ENABLED      = os.getenv("BAUFORTSCHRITT_ENABLED", "True") == "True"
BF_HOUR         = int(os.getenv("BAUFORTSCHRITT_HOUR", "19"))
BF_MINUTE       = int(os.getenv("BAUFORTSCHRITT_MINUTE", "5"))       # 5 Min nach Daily Report
VISION_MODEL    = os.getenv("VISION_MODEL", "gpt-4o-mini")           # Qualität: "gpt-4o"
VISION_DETAIL   = os.getenv("VISION_DETAIL", "auto")                 # low | auto | high
MAX_IMAGES      = int(os.getenv("BAUFORTSCHRITT_MAX_IMAGES", "8"))   # pro Gruppe
MAX_SCAN        = int(os.getenv("BAUFORTSCHRITT_MAX_SCAN", "300"))   # Nachrichten-Scan-Limit
IMG_MAX_DIM     = int(os.getenv("BAUFORTSCHRITT_IMG_DIM", "1024"))
BF_SEND_PHOTOS  = os.getenv("BAUFORTSCHRITT_SEND_PHOTOS", "True") == "True"  # Fotos beschriftet mitschicken
# Optional: Gruppen-Namensteile, die NIE erscheinen sollen (neue Kunden ohne Baustart)
BF_EXCLUDE = [x.strip().lower() for x in os.getenv("BAUFORTSCHRITT_EXCLUDE", "").split(",") if x.strip()]

# Baustellen-Gruppen erkennen (gleiche Logik wie telegram_bot_groups.py)
CONSTRUCTION_PREFIXES = ['baustart', 'baustelle', 'in bau', 'construction', 'nacharbeit', 'reklamation']


class BaufortschrittReporter:
    def __init__(self, user_client, bot_client, approval_chat_id, openai_api_key=""):
        self.user = user_client          # Userbot (liest Gruppen + lädt Fotos)
        self.bot = bot_client            # Bot (postet in KI Freigaben)
        self.chat_id = approval_chat_id
        self.openai = OpenAI(api_key=openai_api_key or os.getenv("OPENAI_API_KEY", ""))
        self._task = None
        print(f"✅ BaufortschrittReporter initialisiert "
              f"(Modell: {VISION_MODEL}, Zeit: {BF_HOUR}:{BF_MINUTE:02d}, max {MAX_IMAGES} Fotos/Gruppe)")

    # ── Scheduler ────────────────────────────────────────────────────────────
    def start_scheduler(self):
        if not BF_ENABLED:
            print("⚠️  Baufortschritt DEAKTIVIERT (BAUFORTSCHRITT_ENABLED=False)")
            return
        self._task = asyncio.ensure_future(self._loop())
        print(f"🕐 Baufortschritt-Scheduler gestartet (täglich {BF_HOUR}:{BF_MINUTE:02d} Uhr)")

    async def _loop(self):
        while True:
            try:
                now = datetime.now(GERMANY_TZ)
                target = now.replace(hour=BF_HOUR, minute=BF_MINUTE, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                wait = (target - now).total_seconds()
                print(f"🕐 Nächste Baufortschritt-Analyse in {wait/3600:.1f}h "
                      f"({target.strftime('%d.%m.%Y %H:%M')})")
                await asyncio.sleep(wait)
                await self.generate_and_send()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ Baufortschritt-Scheduler Fehler: {e}")
                await asyncio.sleep(3600)

    # ── Hauptlauf ──────────────────────────────────────────────────────────────
    async def generate_and_send(self):
        print("🏗️ Baufortschritt-Analyse startet…")
        try:
            groups = await self._construction_groups()
        except Exception as e:
            print(f"⚠️ Konnte Gruppen nicht laden: {e}")
            return
        print(f"   {len(groups)} Baustellen-Gruppe(n) gefunden")

        posted = 0
        for entity, name in groups:
            posted += await self.analyze_group(entity, name)
            await asyncio.sleep(1.0)

        if posted == 0:
            today = datetime.now(GERMANY_TZ).strftime('%d.%m.%Y')
            try:
                await self.bot.send_message(
                    self.chat_id,
                    f"🏗️ **Baufortschritt — {today}**\nKein Baufortschritt heute (keine neuen Fotos von den Baustellen)."
                )
            except Exception:
                pass
        print(f"🏗️ Baufortschritt fertig — {posted} Gruppe(n) gepostet")
        return posted

    # ── Eine Gruppe analysieren + posten (von Scheduler & /fortschritt genutzt) ──
    async def analyze_group(self, entity, name) -> int:
        """Analysiert HEUTIGE Fotos einer Gruppe und postet in KI-Freigaben. 1 = gepostet."""
        try:
            msgs = await self._todays_photos(entity)
            if not msgs:
                print(f"   ⏭️  {name}: keine Fotos heute")
                return 0
            b64s = []
            raws = []
            for m in msgs:
                try:
                    raw = await self.user.download_media(m, file=bytes)
                    if raw:
                        raws.append(raw)
                        b64s.append(self._to_b64(raw))
                except Exception as de:
                    print(f"      ⚠️ Foto-Download Fehler: {de}")
            if not b64s:
                return 0
            loop = asyncio.get_event_loop()
            summary = await loop.run_in_executor(None, self._vision, name, b64s)
            if not summary:
                return 0
            today = datetime.now(GERMANY_TZ).strftime('%d.%m.%Y')
            text = (f"🏗️ **Baufortschritt — {name}**\n"
                    f"📅 {today}  ·  📸 {len(b64s)} Foto(s)\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"{summary}")
            # Fotos beschriftet mitschicken (Lothar: "sagen, welche Baustelle")
            sent = False
            if BF_SEND_PHOTOS and raws:
                try:
                    imgs = []
                    for i, r in enumerate(raws):
                        bio = BytesIO(r); bio.name = f"baustelle_{i}.jpg"; imgs.append(bio)
                    cap = text if len(text) <= 1000 else f"🏗️ **Baufortschritt — {name}** · 📅 {today}"
                    await self.bot.send_file(self.chat_id, imgs, caption=cap)
                    sent = True
                    if cap != text:
                        await self.bot.send_message(self.chat_id, text)
                except Exception as pe:
                    print(f"      ⚠️ Foto-Album Fehler: {pe} — sende nur Text")
            if not sent:
                try:
                    await self.bot.send_message(self.chat_id, text)
                except Exception:
                    await self.bot.send_message(self.chat_id, text.replace('**', ''))
            print(f"   ✅ {name}: gepostet ({len(b64s)} Fotos)")
            return 1
        except Exception as e:
            print(f"   ⚠️ {name}: Fehler — {e}")
            return 0

    async def run_for_chat(self, chat_id) -> int:
        """Manuell für EINE Gruppe (z. B. /fortschritt in der Baustellen-Gruppe)."""
        try:
            entity = await self.user.get_entity(chat_id)
            name = getattr(entity, 'title', None) or str(chat_id)
            return await self.analyze_group(entity, name)
        except Exception as e:
            print(f"⚠️ run_for_chat Fehler: {e}")
            return 0

    # ── Baustellen-Gruppen finden ───────────────────────────────────────────────
    async def _construction_groups(self):
        out = []
        async for d in self.user.iter_dialogs():
            try:
                if not getattr(d, 'is_group', False):
                    continue
                title = (d.name or '').strip()
                tl = title.lower()
                if any(tl.startswith(p) for p in CONSTRUCTION_PREFIXES):
                    if BF_EXCLUDE and any(x in tl for x in BF_EXCLUDE):
                        continue  # ausgeschlossen (z. B. neuer Kunde)
                    out.append((d.entity, title))
            except Exception:
                continue
        return out

    # ── Heutige Fotos einer Gruppe einsammeln ──────────────────────────────────
    async def _todays_photos(self, entity):
        day_start = datetime.now(GERMANY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        out = []
        async for m in self.user.iter_messages(entity, limit=MAX_SCAN):
            if not getattr(m, 'date', None):
                continue
            md = m.date.astimezone(GERMANY_TZ)
            if md < day_start:
                break  # älter als heute → Schluss (Nachrichten kommen neueste zuerst)
            is_img = bool(getattr(m, 'photo', None)) or (
                getattr(m, 'document', None)
                and getattr(m.document, 'mime_type', '')
                and str(m.document.mime_type).startswith('image/')
            )
            if is_img:
                out.append(m)
        return out[:MAX_IMAGES]  # neueste N

    # ── Bild → (verkleinertes) base64-JPEG ──────────────────────────────────────
    def _to_b64(self, raw: bytes) -> str:
        if _PIL:
            try:
                im = Image.open(BytesIO(raw)).convert("RGB")
                w, h = im.size
                if max(w, h) > IMG_MAX_DIM:
                    r = IMG_MAX_DIM / float(max(w, h))
                    im = im.resize((int(w * r), int(h * r)))
                buf = BytesIO()
                im.save(buf, format="JPEG", quality=80)
                raw = buf.getvalue()
            except Exception:
                pass  # Fallback: Originalbytes
        return base64.b64encode(raw).decode()

    # ── Vision-Aufruf (synchron, läuft im Executor) ─────────────────────────────
    def _vision(self, group_name: str, images_b64) -> str:
        prompt = (
            f"Du bist Bauleiter-Assistent einer Badsanierungs-Firma. "
            f"Dies sind Baustellen-Fotos von HEUTE aus der Gruppe „{group_name}\". "
            f"Beschreibe kurz, konkret und auf Deutsch, welche Arbeiten heute sichtbar sind bzw. "
            f"durchgeführt wurden — z. B. Abbruch/Rohbau, Wasser-/Stromleitungen verlegt, Abdichtung, "
            f"Fliesen verlegt, Dusche/Armaturen montiert, Trockenbau, Endreinigung. "
            f"Maximal 4–6 Sätze. Nenne nur, was wirklich auf den Fotos zu sehen ist — keine Erfindungen. "
            f"Wenn der Fortschritt unklar ist, beschreibe einfach den sichtbaren Zustand."
        )
        content = [{"type": "text", "text": prompt}]
        for b in images_b64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": VISION_DETAIL},
            })
        try:
            resp = self.openai.chat.completions.create(
                model=VISION_MODEL,
                max_tokens=600,
                temperature=0.3,
                messages=[{"role": "user", "content": content}],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            print(f"      ⚠️ Vision-Fehler: {e}")
            return ""