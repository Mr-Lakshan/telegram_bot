"""
DYNAMIC HANDLER — Customer-Specific Question Answerer
=======================================================
Handles questions where the answer changes per customer/group:
  - Address, phone, email, name
  - Construction dates, status
  - Google Drive files/documents

Flow:
  1. Classifier says route=dynamic_handler, intent=customer_address
  2. This handler: group chat_id → CRM API → customer data
  3. Format response → reply in group (no approval needed, factual data)

No expensive AI needed — classifier already identified the intent.
CRM API provides: get_customer_by_group, get_drive_files

Usage:
    handler = DynamicHandler(crm_api_url, crm_api_key, translator)
    result = await handler.handle(chat_id, intent, chat_title, topic_id, source_lang)
    if result:
        # result['response_text'] → send to group
"""

import os
import requests
from typing import Dict, Optional


class DynamicHandler:
    """
    Fetches customer-specific data from CRM and formats responses.
    Zero AI cost for simple lookups (address, phone, name).
    Uses DriveFileAnalyzer for AI-powered file analysis.
    """

    def __init__(
        self,
        crm_api_url: str = "",
        crm_api_key: str = "",
        translator=None,
        your_language: str = "de",
    ):
        self.crm_url = crm_api_url or os.getenv("CRM_API_URL", "")
        self.crm_key = crm_api_key or os.getenv("CRM_BOT_API_KEY", "")
        self.translator = translator
        self.your_language = your_language

        # Drive file analyzer (AI-powered)
        self.file_analyzer = None
        try:
            from bot.integrations.drive_file_analyzer import DriveFileAnalyzer
            self.file_analyzer = DriveFileAnalyzer(
                crm_api_url=self.crm_url,
                crm_api_key=self.crm_key,
                anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
                openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            )
        except Exception as e:
            print(f"   ⚠️ DriveFileAnalyzer not available: {e}")

        # Cache: chat_id → customer data (avoid repeated CRM calls)
        self._customer_cache = {}
        self._cache_ttl = 300  # 5 minutes

        import time
        self._cache_timestamps = {}
        self._time = time

        self._stats = {'total': 0, 'success': 0, 'no_data': 0, 'errors': 0}
        self._last_question = ""

        print(f"✅ DynamicHandler initialized (CRM: {'configured' if self.crm_url else 'NOT configured'})")

    # ══════════════════════════════════════════════════════════════════════
    #  MAIN: Handle a dynamic question
    # ══════════════════════════════════════════════════════════════════════

    def handle(
        self,
        chat_id: int,
        intent: str,
        chat_title: str = "",
        topic_id: int = None,
        source_language: str = "",
        question_text: str = "",
    ) -> Optional[Dict]:
        """
        Handle a dynamic question. Returns response dict or None.

        Returns:
            {
                'response_text': str,    # formatted answer (German)
                'customer': dict,         # customer data
                'intent': str,
                'source': 'crm' or 'drive',
            }
            or None if no data found
        """
        self._stats['total'] += 1

        if not self.crm_url or not self.crm_key:
            print("   ⚠️ DynamicHandler: CRM not configured")
            self._stats['errors'] += 1
            return None

        # ── Get customer data (cached) ──
        customer = self._get_customer(chat_id, chat_title)

        if not customer:
            self._stats['no_data'] += 1
            return {
                'response_text': "⚠️ Kunde konnte für diese Gruppe nicht gefunden werden.",
                'customer': None,
                'intent': intent,
                'source': 'none',
            }

        print(f"   ✅ Customer: {customer.get('full_name', '?')} (#{customer.get('record_id', '?')})")

        # ── Route to intent handler ──
        self._last_question = question_text  # Store for Drive analysis context
        response = self._route_intent(intent, customer, question_text)

        if response:
            self._stats['success'] += 1
            return {
                'response_text': response,
                'customer': customer,
                'intent': intent,
                'source': 'drive' if intent in ('drive_files', 'documents', 'images', 'photos') else 'crm',
            }
        else:
            self._stats['no_data'] += 1
            return None

    # ══════════════════════════════════════════════════════════════════════
    #  INTENT ROUTING
    # ══════════════════════════════════════════════════════════════════════

    def _route_intent(self, intent: str, customer: Dict, question_text: str = "") -> Optional[str]:
        """Route to the right handler based on intent."""

        name = customer.get('full_name', 'Unbekannt')
        record_id = customer.get('record_id', '?')
        header = f"📋 **Kundeninfo** — {name} (#{record_id})\n"

        # ── Simple CRM data lookups (no AI needed) ──
        if intent == 'customer_address':
            return self._format_address(header, customer)
        elif intent == 'customer_phone':
            return self._format_phone(header, customer)
        elif intent == 'customer_email':
            return self._format_email(header, customer)
        elif intent == 'customer_name':
            return self._format_name(header, customer)

        # ── Construction questions → CHECK DRIVE DOCUMENTS FIRST ──
        # "Wie wird der Boden gemacht?" → answer is in Bauvertrag/Auftragsbestätigung
        # "Welche Duschkabine?" → answer is in documents
        # "Welcher Abfluss?" → answer is in documents
        # ANY question about the project → Drive docs have the answer
        folder_id = customer.get('drive_folder_id', '')

        if folder_id and self.file_analyzer:
            print(f"   📂 Checking Drive documents for answer...")
            try:
                analysis = self.file_analyzer.analyze_customer_files(
                    folder_id=folder_id,
                    customer_name=name,
                    intent='answer_question',
                    question=question_text,
                )
                if analysis and analysis.get('files_analyzed', 0) > 0:
                    response = analysis.get('analysis_text', '')
                    if response and 'keine' not in response.lower()[:50]:
                        return header + "\n" + response
                    else:
                        print(f"   ⚠️ Drive docs analyzed but no relevant answer found")
            except Exception as e:
                print(f"   ⚠️ Drive analysis error: {e}")

        # ── Fallback: specific formatters ──
        if intent in ('construction_date', 'schedule'):
            return self._format_dates(header, customer)
        elif intent == 'construction_status':
            return self._format_status(header, customer)
        elif intent in ('drive_files', 'documents', 'images', 'photos', 'files'):
            return self._format_drive(header, customer)
        elif intent == 'all_details':
            return self._format_all(header, customer)

        # ── No answer found — return None so it falls to AI handler ──
        if not folder_id:
            return f"{header}\n⚠️ Kein Google Drive Ordner verknüpft. Bitte Weronika fragen."

        return None

    # ══════════════════════════════════════════════════════════════════════
    #  FORMATTERS
    # ══════════════════════════════════════════════════════════════════════

    def _format_address(self, header: str, customer: Dict) -> str:
        address = customer.get('full_address', '')
        if address:
            maps_q = address.replace(' ', '+')
            return (
                f"{header}\n"
                f"📍 **Adresse:** {address}\n"
                f"🗺️ [Google Maps](https://www.google.com/maps/search/{maps_q})"
            )
        return f"{header}\n📍 Adresse: nicht hinterlegt"

    def _format_phone(self, header: str, customer: Dict) -> str:
        phone = customer.get('phone', '')
        if phone:
            return f"{header}\n📞 **Telefon:** {phone}"
        return f"{header}\n📞 Telefon: nicht hinterlegt"

    def _format_email(self, header: str, customer: Dict) -> str:
        email = customer.get('email', '')
        if email:
            return f"{header}\n📧 **E-Mail:** {email}"
        return f"{header}\n📧 E-Mail: nicht hinterlegt"

    def _format_name(self, header: str, customer: Dict) -> str:
        vorname = customer.get('vorname', '')
        nachname = customer.get('nachname', '')
        full = customer.get('full_name', '')
        return f"{header}\n👤 **Kunde:** {full}\n   Vorname: {vorname}\n   Nachname: {nachname}"

    def _format_dates(self, header: str, customer: Dict) -> str:
        lines = [header]
        all_fields = customer.get('_all_fields', {})

        # Search for date-related fields
        for fid, fdata in all_fields.items():
            name = (fdata.get('name', '') + ' ' + fdata.get('label', '')).lower()
            val = fdata.get('value', '')
            if val and ('datum' in name or 'date' in name or 'baustart' in name or 'termin' in name):
                label = fdata.get('label', fdata.get('name', f'Feld {fid}'))
                lines.append(f"📅 **{label}:** {val}")

        if len(lines) == 1:
            lines.append("📅 Keine Termine hinterlegt")

        return "\n".join(lines)

    def _format_status(self, header: str, customer: Dict) -> str:
        lines = [header]
        all_fields = customer.get('_all_fields', {})

        for fid, fdata in all_fields.items():
            name = (fdata.get('name', '') + ' ' + fdata.get('label', '')).lower()
            val = fdata.get('value', '')
            if val and ('status' in name or 'phase' in name or 'stufe' in name):
                label = fdata.get('label', fdata.get('name', f'Feld {fid}'))
                lines.append(f"📊 **{label}:** {val}")

        if len(lines) == 1:
            lines.append("📊 Kein Status hinterlegt")

        return "\n".join(lines)

    def _format_drive(self, header: str, customer: Dict) -> str:
        drive_url = customer.get('drive_folder_url', '')
        folder_id = customer.get('drive_folder_id', '')

        if not folder_id and not drive_url:
            return f"{header}\n📂 Kein Google Drive Ordner verknüpft."

        # Try AI-powered analysis if analyzer available
        if self.file_analyzer and folder_id:
            try:
                analysis = self.file_analyzer.analyze_customer_files(
                    folder_id=folder_id,
                    customer_name=customer.get('full_name', 'Kunde'),
                    intent='full_analysis',
                    question=self._last_question or '',
                )
                if analysis and analysis.get('files_analyzed', 0) > 0:
                    return header + "\n" + analysis['analysis_text']
            except Exception as e:
                print(f"   ⚠️ File analysis failed, falling back to listing: {e}")

        # Fallback: simple file listing (no AI)
        files = self._get_drive_files(folder_id)

        lines = [header]

        if not files:
            lines.append("📂 Drive Ordner ist leer oder nicht erreichbar.")
            if drive_url:
                lines.append(f"🔗 [Ordner öffnen]({drive_url})")
            return "\n".join(lines)

        images = [f for f in files if f.get('isImage')]
        docs = [f for f in files if not f.get('isImage')]

        lines.append(f"📂 **Google Drive** — {len(files)} Dateien:")
        if images:
            lines.append(f"  🖼️ {len(images)} Bilder")
        if docs:
            lines.append(f"  📄 {len(docs)} Dokumente")
        lines.append("")

        for f in files[:10]:
            icon = "🖼️" if f.get('isImage') else "📄"
            size_mb = f.get('size', 0) / (1024 * 1024)
            size_str = f" ({size_mb:.1f} MB)" if size_mb > 0.1 else ""
            folder_tag = f" [{f.get('folder', '')}]" if f.get('folder', '') not in ('', '(root)') else ""
            lines.append(f"  {icon} {f['name']}{folder_tag}{size_str}")

        if len(files) > 10:
            lines.append(f"  ... und {len(files) - 10} weitere")

        if drive_url:
            lines.append(f"\n🔗 [Alle Dateien im Drive öffnen]({drive_url})")

        return "\n".join(lines)

    def _format_all(self, header: str, customer: Dict) -> str:
        """Format all available customer info."""
        lines = [header]

        address = customer.get('full_address', '')
        phone = customer.get('phone', '')
        email = customer.get('email', '')

        if address:
            maps_q = address.replace(' ', '+')
            lines.append(f"📍 **Adresse:** {address}")
            lines.append(f"🗺️ [Google Maps](https://www.google.com/maps/search/{maps_q})")
        if phone:
            lines.append(f"📞 **Telefon:** {phone}")
        if email:
            lines.append(f"📧 **E-Mail:** {email}")

        if not address and not phone and not email:
            lines.append("ℹ️ Keine Kontaktdaten hinterlegt")

        drive_url = customer.get('drive_folder_url', '')
        if drive_url:
            lines.append(f"\n📂 [Google Drive Ordner]({drive_url})")

        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════════
    #  CRM API CALLS
    # ══════════════════════════════════════════════════════════════════════

    def _get_customer(self, chat_id: int, chat_title: str = "") -> Optional[Dict]:
        """Get customer from CRM, with caching."""
        now = self._time.time()

        # Check cache
        if chat_id in self._customer_cache:
            cached_at = self._cache_timestamps.get(chat_id, 0)
            if now - cached_at < self._cache_ttl:
                return self._customer_cache[chat_id]

        # Fetch from CRM
        customer = self._fetch_customer_by_group(chat_id)

        if not customer and chat_title:
            customer = self._fetch_customer_by_name(chat_title)

        if customer:
            self._customer_cache[chat_id] = customer
            self._cache_timestamps[chat_id] = now

        return customer

    def _fetch_customer_by_group(self, chat_id: int) -> Optional[Dict]:
        """Call CRM API: get_customer_by_group."""
        try:
            resp = requests.get(
                self.crm_url,
                params={'action': 'get_customer_by_group', 'chat_id': str(chat_id)},
                headers={'X-Bot-Api-Key': self.crm_key},
                timeout=15,
            )
            data = resp.json()

            if data.get('success') and data.get('customer'):
                customer = data['customer']
                # Store all fields for date/status lookups
                customer['_all_fields'] = data.get('all_fields', {})
                return customer

            return None
        except Exception as e:
            print(f"   ⚠️ CRM get_customer_by_group error: {e}")
            return None

    def _fetch_customer_by_name(self, chat_title: str) -> Optional[Dict]:
        """Fallback: extract name from group title and search CRM."""
        prefixes = [
            'baustart', 'baustelle', 'fertig', 'in', 'bau', 'geplant',
            'nacharbeit', 'erforderlich', 'reklamation', 'projekt', 'abgebrochen',
            'construction',
        ]

        parts = chat_title.strip().split()
        search_name = None

        for part in parts:
            if part.lower() not in prefixes and not part.replace('.', '').isdigit():
                search_name = part
                break

        if not search_name or len(search_name) < 2:
            return None

        try:
            resp = requests.get(
                self.crm_url,
                params={'action': 'search', 'q': search_name},
                headers={'X-Bot-Api-Key': self.crm_key},
                timeout=15,
            )
            data = resp.json()

            if data.get('success') and data.get('records'):
                rec = data['records'][0]
                return {
                    'record_id': rec['id'],
                    'full_name': rec.get('name', ''),
                    'phone': rec.get('phone', ''),
                    'full_address': rec.get('address', ''),
                    'vorname': '', 'nachname': '',
                    'email': '',
                    'drive_folder_url': '', 'drive_folder_id': '',
                    '_all_fields': {},
                }
            return None
        except Exception as e:
            print(f"   ⚠️ CRM search error: {e}")
            return None

    def _get_drive_files(self, folder_id: str) -> list:
        """Call CRM API: get_drive_files."""
        if not folder_id:
            return []
        try:
            resp = requests.get(
                self.crm_url,
                params={'action': 'get_drive_files', 'folder_id': folder_id},
                headers={'X-Bot-Api-Key': self.crm_key},
                timeout=30,
            )
            data = resp.json()
            if data.get('success'):
                return data.get('files', [])
            return []
        except Exception as e:
            print(f"   ⚠️ Drive files error: {e}")
            return []

    # ══════════════════════════════════════════════════════════════════════
    #  STATS
    # ══════════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        return self._stats.copy()

    def reset_stats(self):
        for key in self._stats:
            self._stats[key] = 0

    def clear_cache(self):
        self._customer_cache.clear()
        self._cache_timestamps.clear()