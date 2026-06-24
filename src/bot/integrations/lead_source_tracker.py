"""
LEAD SOURCE TRACKER — Ask Lothar where leads come from
========================================================
Periodically checks CRM for new leads without lead_herkunft.
Sends Telegram notification with inline buttons to KI Freigaben.
Lothar taps source → CRM updated.

Sources: Zeitung, Influencer, Verbund Pflegehilfe, Empfehlung, Sonstiges
"""

import os
import json
import asyncio
import requests
from telethon import events
from telethon.tl.custom import Button

# ── Config ──
CHECK_INTERVAL = 1800  # Check every 30 minutes
LEAD_SOURCES = [
    "Zeitung",
    "Sanitätshaus",
    "Empfehlung",
    "Google Ads",
    "Influencer",
    "Verbund Pflegehilfe",
    "Sonstiges",
]

# Auto-detect source by product (Lothar's rules)
# Rollstuhlrampen + Badewannentür → Verbund Pflegehilfe (come via email)
# Badumbau + Wanne zur Dusche → Zeitung (newspaper ads, Berlin area)
PRODUCT_SOURCE_MAP = {
    'rollstuhlrampe': 'Verbund Pflegehilfe',
    'rollstuhlrampen': 'Verbund Pflegehilfe',
    'badewannentür': 'Verbund Pflegehilfe',
    'badewannentuer': 'Verbund Pflegehilfe',
    'wannentür': 'Verbund Pflegehilfe',
    'badumbau': 'Zeitung',
    'wanne zur dusche': 'Zeitung',
    'wanne zu dusche': 'Zeitung',
    'dusche': 'Zeitung',
    'badsanierung': 'Zeitung',
}


def _detect_source_by_product(product: str):
    """Auto-detect lead source from product name. Returns source or None."""
    if not product:
        return None
    p = product.lower().strip()
    # Exact match first
    if p in PRODUCT_SOURCE_MAP:
        return PRODUCT_SOURCE_MAP[p]
    # Partial match
    for keyword, source in PRODUCT_SOURCE_MAP.items():
        if keyword in p:
            return source
    return None


class LeadSourceTracker:
    """
    Checks for leads without source and asks Lothar via Telegram buttons.
    """

    def __init__(
        self,
        bot_client,
        approval_chat_id: int,
        crm_api_url: str = "",
        crm_api_key: str = "",
    ):
        self.bot = bot_client
        self.chat_id = approval_chat_id
        self.crm_url = crm_api_url or os.getenv("CRM_API_URL", "")
        self.crm_key = crm_api_key or os.getenv("CRM_BOT_API_KEY", "")

        # Track which leads we already asked about (avoid duplicates)
        self._asked_leads = set()
        self._scheduler_task = None

        print(f"✅ LeadSourceTracker initialized (interval: {CHECK_INTERVAL}s)")

    # ══════════════════════════════════════════════════════════════════════
    #  SCHEDULER
    # ══════════════════════════════════════════════════════════════════════

    def start_scheduler(self):
        self._scheduler_task = asyncio.ensure_future(self._check_loop())
        print(f"🔍 Lead source checker started (every {CHECK_INTERVAL//60} min)")

    async def _check_loop(self):
        """Check CRM periodically for leads without source."""
        await asyncio.sleep(60)  # Wait 1 min after startup
        while True:
            try:
                await self._check_and_notify()
            except Exception as e:
                print(f"⚠️ Lead source check error: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    # ══════════════════════════════════════════════════════════════════════
    #  CHECK: Find leads without source
    # ══════════════════════════════════════════════════════════════════════

    async def _check_and_notify(self):
        """Fetch leads without source from CRM, send notifications."""
        if not self.crm_url or not self.crm_key:
            return

        try:
            resp = requests.get(
                self.crm_url,
                params={'action': 'get_leads_without_source', 'days': 2, 'limit': 5},
                headers={'X-Bot-Api-Key': self.crm_key},
                timeout=15,
            )
            data = resp.json()

            if not data.get('success') or not data.get('leads'):
                return

            for lead in data['leads']:
                record_id = lead.get('id')
                if not record_id or record_id in self._asked_leads:
                    continue

                name = f"{lead.get('vorname', '')} {lead.get('nachname', '')}".strip() or f"Lead #{record_id}"
                city = lead.get('city', '')
                product = lead.get('product', '')
                created = lead.get('created_at', '')[:10]

                # ── AUTO-DETECT source by product (skip asking if matched) ──
                auto_source = _detect_source_by_product(product)
                if auto_source:
                    try:
                        resp = requests.post(
                            self.crm_url,
                            params={'action': 'update_lead_source'},
                            data={'record_id': record_id, 'source': auto_source},
                            headers={'X-Bot-Api-Key': self.crm_key},
                            timeout=15,
                        )
                        if resp.status_code == 200 and resp.json().get('success'):
                            self._asked_leads.add(record_id)
                            print(f"🎯 Lead #{record_id} ({name}) auto-tagged: {auto_source} (product: {product})")
                            # Notify Lothar (info only, no buttons)
                            await self.bot.send_message(
                                self.chat_id,
                                f"🎯 **Lead automatisch zugeordnet**\n"
                                f"👤 {name}\n"
                                f"🏷️ {product} → **{auto_source}**\n\n"
                                f"_(Automatisch erkannt. Falls falsch, im CRM ändern.)_"
                            )
                            continue  # Skip the question — already assigned
                    except Exception as e:
                        print(f"   ⚠️ Auto-detect update error: {e}")
                        # Fall through to manual ask

                # Build notification (unknown product → ask manually)
                info_parts = []
                if city:
                    info_parts.append(f"📍 {city}")
                if product:
                    info_parts.append(f"🏷️ {product}")
                if created:
                    info_parts.append(f"📅 {created}")
                info_line = " | ".join(info_parts)

                text = (
                    f"📥 **Neuer Lead ohne Herkunft**\n"
                    f"👤 {name}\n"
                    f"{info_line}\n\n"
                    f"Woher kommt dieser Lead?"
                )

                # Build inline buttons (2 per row)
                buttons = []
                row = []
                for source in LEAD_SOURCES:
                    callback_data = f"leadsrc:{record_id}:{source}"
                    row.append(Button.inline(source, data=callback_data.encode()))
                    if len(row) == 2:
                        buttons.append(row)
                        row = []
                if row:
                    buttons.append(row)

                await self.bot.send_message(
                    self.chat_id,
                    text,
                    buttons=buttons,
                )

                self._asked_leads.add(record_id)
                print(f"📥 Lead source question sent: {name} (#{record_id})")

                # Limit to avoid flooding
                if len(self._asked_leads) > 500:
                    self._asked_leads = set(list(self._asked_leads)[-100:])

        except Exception as e:
            print(f"⚠️ Lead source check error: {e}")

    # ══════════════════════════════════════════════════════════════════════
    #  CALLBACK: Handle button press
    # ══════════════════════════════════════════════════════════════════════

    async def handle_callback(self, event):
        """Handle lead source button press."""
        try:
            data = event.data.decode()
            if not data.startswith('leadsrc:'):
                return False  # Not our callback

            parts = data.split(':', 2)
            if len(parts) != 3:
                return False

            _, record_id_str, source = parts
            record_id = int(record_id_str)

            # Update CRM
            resp = requests.post(
                self.crm_url,
                params={'action': 'update_lead_source'},
                data={'record_id': record_id, 'source': source},
                headers={'X-Bot-Api-Key': self.crm_key},
                timeout=15,
            )
            result = resp.json()

            if result.get('success'):
                # Update the message to show it's done
                msg = await event.get_message()
                await msg.edit(
                    f"✅ Lead #{record_id} — Herkunft: **{source}**\n(gespeichert)",
                    buttons=None,
                )
                print(f"✅ Lead #{record_id} source updated: {source}")
            else:
                await event.answer(f"❌ Fehler: {result.get('error', '?')}", alert=True)

            return True

        except Exception as e:
            print(f"⚠️ Lead source callback error: {e}")
            return False