"""
DRIVE FILE ANALYZER — AI-Powered Google Drive Document Analysis
================================================================
Downloads customer files from Google Drive, analyzes with AI:
  - Images: describe what's visible (construction progress, materials, damage)
  - PDFs/Docs: extract key info, summarize content
  - Mixed: analyze everything, provide structured overview

Two trigger modes:
  1. Question-triggered: someone asks "was ist im Drive?" → analyze and respond
  2. Auto-triggered: new file uploaded → analyze automatically

Flow:
  CRM API → Drive file list → Download via Drive API → AI analyze → Format response

Usage:
    analyzer = DriveFileAnalyzer(openai_api_key, crm_api_url, crm_api_key)
    result = analyzer.analyze_customer_files(folder_id, intent="full_analysis")
"""

import os
import json
import base64
import tempfile
import requests
from typing import Dict, Optional, List
from openai import OpenAI


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

TEMP_DIR = os.getenv("DRIVE_TEMP_DIR", "/tmp/drive_analysis")
MAX_FILES_TO_ANALYZE = int(os.getenv("MAX_FILES_TO_ANALYZE", "10"))
MAX_IMAGE_SIZE_MB = int(os.getenv("MAX_IMAGE_SIZE_MB", "10"))

os.makedirs(TEMP_DIR, exist_ok=True)

# Load model config
try:
    from bot.core.model_config import MODEL_CONFIG
    _drive_cfg = MODEL_CONFIG.get("drive_analysis", {})
    _image_cfg = MODEL_CONFIG.get("image_analysis", {})
    ANALYSIS_MODEL = _drive_cfg.get("model", "claude-sonnet-4-6")
    ANALYSIS_PROVIDER = _drive_cfg.get("provider", "anthropic")
    IMAGE_MODEL = _image_cfg.get("model", "claude-sonnet-4-6")
    IMAGE_PROVIDER = _image_cfg.get("provider", "anthropic")
    FALLBACK_MODEL = _drive_cfg.get("fallback_model", "gpt-4o")
except ImportError:
    ANALYSIS_MODEL = "claude-sonnet-4-6"
    ANALYSIS_PROVIDER = "anthropic"
    IMAGE_MODEL = "claude-sonnet-4-6"
    IMAGE_PROVIDER = "anthropic"
    FALLBACK_MODEL = "gpt-4o"


class DriveFileAnalyzer:
    """
    Downloads and analyzes customer files from Google Drive.
    Uses Claude Sonnet for intelligent analysis (primary).
    Falls back to OpenAI GPT-4o if Claude not available.
    """

    def __init__(
        self,
        openai_api_key: str = "",
        anthropic_api_key: str = "",
        crm_api_url: str = "",
        crm_api_key: str = "",
    ):
        self.openai_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.anthropic_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.crm_url = crm_api_url or os.getenv("CRM_API_URL", "")
        self.crm_key = crm_api_key or os.getenv("CRM_BOT_API_KEY", "")

        self.openai_client = OpenAI(api_key=self.openai_key) if self.openai_key else None
        self.use_claude = bool(self.anthropic_key)

        provider = "Claude Sonnet" if self.use_claude else "OpenAI GPT-4o"
        self._stats = {'analyzed': 0, 'images': 0, 'docs': 0, 'errors': 0}
        print(f"✅ DriveFileAnalyzer initialized (AI: {provider})")

    # ══════════════════════════════════════════════════════════════════════
    #  MAIN: Analyze customer's Drive files
    # ══════════════════════════════════════════════════════════════════════

    def analyze_customer_files(
        self,
        folder_id: str,
        customer_name: str = "",
        intent: str = "full_analysis",
        question: str = "",
        max_files: int = None,
    ) -> Optional[Dict]:
        """
        Analyze files from customer's Drive folder.
        NOTE: Currently analyzing documents only (text, PDFs, contracts).
        Image analysis disabled for now — will be enabled later.
        """
        if not folder_id:
            return None

        max_files = max_files or MAX_FILES_TO_ANALYZE

        # Step 1: Get file list from CRM API
        files = self._get_drive_files(folder_id)
        if not files:
            return {
                'analysis_text': "📂 Keine Dateien im Drive-Ordner gefunden.",
                'files_analyzed': 0, 'images_analyzed': 0, 'docs_analyzed': 0,
                'file_details': [],
            }

        # Step 2: Filter — DOCUMENTS ONLY for now (Lothar: "Fotos erst später")
        # Skip images, focus on text/PDFs/contracts
        docs_only = [f for f in files if not f.get('isImage')]
        images_skipped = len(files) - len(docs_only)

        if intent == 'images_only':
            # If explicitly asked for images, still allow but note it
            docs_only = [f for f in files if f.get('isImage')]

        files = docs_only[:max_files]

        # ── Special mode: ANSWER A SPECIFIC QUESTION from documents ──
        if intent == 'answer_question' and question:
            return self._answer_from_documents(files, customer_name, question)

        # Step 3: Download and analyze each file (full_analysis mode)
        file_details = []
        images_analyzed = 0
        docs_analyzed = 0

        for f in files:
            try:
                if f.get('isImage'):
                    analysis = self._analyze_image(f, customer_name, question)
                    if analysis:
                        file_details.append(analysis)
                        images_analyzed += 1
                else:
                    analysis = self._analyze_document(f, customer_name, question)
                    if analysis:
                        file_details.append(analysis)
                        docs_analyzed += 1
            except Exception as e:
                print(f"   ⚠️ Error analyzing {f['name']}: {e}")
                self._stats['errors'] += 1

        # Step 4: Generate summary
        analysis_text = self._format_analysis(
            file_details, customer_name, intent, question,
            images_analyzed, docs_analyzed,
        )

        self._stats['analyzed'] += len(file_details)
        self._stats['images'] += images_analyzed
        self._stats['docs'] += docs_analyzed

        return {
            'analysis_text': analysis_text,
            'files_analyzed': len(file_details),
            'images_analyzed': images_analyzed,
            'docs_analyzed': docs_analyzed,
            'file_details': file_details,
        }

    # ══════════════════════════════════════════════════════════════════════
    #  ANSWER QUESTION FROM DOCUMENTS — single AI call with all doc context
    # ══════════════════════════════════════════════════════════════════════

    def _answer_from_documents(
        self,
        files: List[Dict],
        customer_name: str,
        question: str,
    ) -> Optional[Dict]:
        """
        Download PDFs/docs, send to Claude for analysis.
        Claude can read PDFs natively via base64.
        Skip videos, large files, and images.
        """
        if not files:
            return {
                'analysis_text': "📂 Keine Dokumente im Kundenordner gefunden.",
                'files_analyzed': 0, 'images_analyzed': 0, 'docs_analyzed': 0,
                'file_details': [],
            }

        # Step 1: Filter — only PDFs and text docs, skip videos and large files
        readable_files = []
        for f in files:
            mime = f.get('mimeType', '')
            size = f.get('size', 0)
            name = f.get('name', '')

            # Skip videos
            if mime.startswith('video/'):
                continue
            # Skip very large files (>5MB)
            if size > 5 * 1024 * 1024:
                continue
            # Only PDFs, text, Google Docs
            if 'pdf' in mime or 'text' in mime or 'google-apps' in mime or name.endswith(('.txt', '.csv', '.md')):
                readable_files.append(f)

        if not readable_files:
            return {
                'analysis_text': "📂 Keine lesbaren Dokumente gefunden (nur Bilder/Videos im Ordner).",
                'files_analyzed': 0, 'images_analyzed': 0, 'docs_analyzed': 0,
                'file_details': [],
            }

        print(f"   📚 {len(readable_files)} lesbare Dokumente gefunden (Videos/Bilder übersprungen)")

        # Step 2: Download PDFs and send to Claude as document content
        pdf_contents = []  # (filename, base64_data)
        text_contents = []  # (filename, text)
        docs_read = 0

        for f in readable_files[:5]:  # Max 5 docs to keep token cost low
            file_id = f['id']
            file_name = f['name']
            mime_type = f.get('mimeType', '')

            print(f"   📄 Reading: {file_name}...")

            try:
                # Google Docs → export as text
                if 'google-apps' in mime_type:
                    text = self._export_google_doc(file_id, mime_type)
                    if text and len(text.strip()) > 10:
                        text_contents.append((file_name, text[:5000]))
                        docs_read += 1
                    continue

                # Download file
                file_data = self._download_file(file_id)
                if not file_data:
                    continue

                # PDF → send as base64 document to Claude
                if 'pdf' in mime_type:
                    b64_data = base64.b64encode(file_data).decode('utf-8')
                    pdf_contents.append((file_name, b64_data))
                    docs_read += 1
                # Text files → extract directly
                elif 'text' in mime_type or file_name.endswith(('.txt', '.csv', '.md')):
                    try:
                        text = file_data.decode('utf-8')
                    except UnicodeDecodeError:
                        text = file_data.decode('latin-1', errors='ignore')
                    if text and len(text.strip()) > 10:
                        text_contents.append((file_name, text[:5000]))
                        docs_read += 1

            except Exception as e:
                print(f"   ⚠️ Error reading {file_name}: {e}")

        if docs_read == 0:
            return {
                'analysis_text': "📂 Dokumente konnten nicht gelesen werden.",
                'files_analyzed': 0, 'images_analyzed': 0, 'docs_analyzed': 0,
                'file_details': [],
            }

        print(f"   📚 {docs_read} Dokumente gelesen")

        # Step 3: Send to Claude (PDFs as documents, text inline)
        system_prompt = f"""Du bist ein Bauexperte-Assistent für die Firma Premiobad/Seniorex (Badsanierung & Renovierung).

Du hast Zugriff auf die Kundendokumente von "{customer_name}" aus Google Drive.
Beantworte die Frage NUR basierend auf den Dokumenten.

Regeln:
- Antworte auf Deutsch
- Wenn die Antwort in den Dokumenten steht → gib eine klare, präzise Antwort
- Wenn die Antwort NICHT in den Dokumenten steht → sage "Diese Information ist in den vorliegenden Dokumenten nicht enthalten. Bitte Weronika fragen."
- Nenne das Dokument, aus dem die Antwort stammt
- Halte die Antwort kurz (3-6 Sätze)
- Erfinde NICHTS — nur was in den Dokumenten steht"""

        # Build message content for Claude
        message_content = []

        # Add PDFs as document blocks (Claude reads PDFs natively)
        for filename, b64_data in pdf_contents:
            message_content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64_data,
                },
            })
            message_content.append({
                "type": "text",
                "text": f"[Dokument: {filename}]",
            })

        # Add text content inline
        for filename, text in text_contents:
            message_content.append({
                "type": "text",
                "text": f"=== DOKUMENT: {filename} ===\n{text}\n",
            })

        # Add the question
        message_content.append({
            "type": "text",
            "text": f"\nFRAGE: {question}\n\nBeantworte die Frage basierend auf den obigen Dokumenten.",
        })

        try:
            if self.use_claude:
                answer = self._call_claude_with_documents(system_prompt, message_content)
            else:
                # OpenAI fallback — can't send PDFs, use text only
                combined_text = "\n\n".join([f"=== {fn} ===\n(PDF-Inhalt nicht als Text verfügbar)" for fn, _ in pdf_contents])
                combined_text += "\n\n" + "\n\n".join([f"=== {fn} ===\n{txt}" for fn, txt in text_contents])
                combined_text += f"\n\nFRAGE: {question}"
                answer = self._call_openai_text(system_prompt, combined_text)

            if answer:
                self._stats['analyzed'] += docs_read
                self._stats['docs'] += docs_read

                doc_names = [fn for fn, _ in pdf_contents] + [fn for fn, _ in text_contents]
                doc_list = "\n".join([f"  📄 {n}" for n in doc_names])

                return {
                    'analysis_text': f"📄 **Antwort aus Kundendokumenten** ({docs_read} Dokumente gelesen):\n{doc_list}\n\n{answer}",
                    'files_analyzed': docs_read,
                    'images_analyzed': 0,
                    'docs_analyzed': docs_read,
                    'file_details': [],
                }

        except Exception as e:
            print(f"   ⚠️ AI analysis error: {e}")

        return None

    # ══════════════════════════════════════════════════════════════════════
    #  IMAGE ANALYSIS — Claude/GPT-4o vision
    # ══════════════════════════════════════════════════════════════════════

    def _analyze_image(self, file_info: Dict, customer_name: str, question: str = "") -> Optional[Dict]:
        """Download image from Drive and analyze with GPT-4o vision."""

        file_id = file_info['id']
        file_name = file_info['name']
        size_mb = file_info.get('size', 0) / (1024 * 1024)

        if size_mb > MAX_IMAGE_SIZE_MB:
            print(f"   ⏭️ Skipping {file_name} — too large ({size_mb:.1f} MB)")
            return None

        print(f"   🖼️ Analyzing image: {file_name}...")

        # Download image via CRM API
        image_data = self._download_file(file_id)
        if not image_data:
            return None

        # Convert to base64
        b64_image = base64.b64encode(image_data).decode('utf-8')

        # Determine mime type
        mime_type = file_info.get('mimeType', 'image/jpeg')
        if 'png' in mime_type:
            mime_type = 'image/png'
        elif 'gif' in mime_type:
            mime_type = 'image/gif'
        elif 'webp' in mime_type:
            mime_type = 'image/webp'
        else:
            mime_type = 'image/jpeg'

        # Analyze with Claude (primary) or GPT-4o (fallback)
        system_prompt = f"""Du bist ein Baustellen-Experte. Analysiere dieses Bild aus dem Google Drive Ordner von Kunde "{customer_name}".

Beschreibe auf Deutsch:
1. Was ist auf dem Bild zu sehen? (Raum, Material, Baufortschritt)
2. Welcher Bauabschnitt ist das? (Rohbau, Fliesen, Sanitär, Fertigstellung etc.)
3. Gibt es Auffälligkeiten oder mögliche Probleme?

Halte die Beschreibung kurz und präzise (3-5 Sätze)."""

        if question:
            system_prompt += f"\n\nDer Benutzer hat folgende Frage gestellt: {question}\nBeantworte die Frage basierend auf dem Bild."

        try:
            if self.use_claude:
                description = self._call_claude_vision(system_prompt, b64_image, mime_type)
            else:
                description = self._call_openai_vision(system_prompt, b64_image, mime_type)

            if not description:
                return None

            return {
                'file_name': file_name,
                'file_type': 'image',
                'folder': file_info.get('folder', '(root)'),
                'analysis': description,
                'size_mb': round(size_mb, 1),
            }

        except Exception as e:
            print(f"   ⚠️ Image analysis error: {e}")
            return None

    # ══════════════════════════════════════════════════════════════════════
    #  DOCUMENT ANALYSIS — Text extraction + GPT summary
    # ══════════════════════════════════════════════════════════════════════

    def _analyze_document(self, file_info: Dict, customer_name: str, question: str = "") -> Optional[Dict]:
        """Download document and analyze/summarize."""

        file_id = file_info['id']
        file_name = file_info['name']
        mime_type = file_info.get('mimeType', '')

        print(f"   📄 Analyzing document: {file_name}...")

        # For Google Docs/Sheets, export as text
        if 'google-apps' in mime_type:
            text_content = self._export_google_doc(file_id, mime_type)
        else:
            # Download file and try to extract text
            file_data = self._download_file(file_id)
            if not file_data:
                return None
            text_content = self._extract_text(file_data, file_name, mime_type)

        if not text_content or len(text_content.strip()) < 10:
            return {
                'file_name': file_name,
                'file_type': 'document',
                'folder': file_info.get('folder', '(root)'),
                'analysis': 'Dokument konnte nicht gelesen werden.',
                'size_mb': file_info.get('size', 0) / (1024 * 1024),
            }

        # Truncate if too long
        if len(text_content) > 3000:
            text_content = text_content[:3000] + "\n... (gekürzt)"

        # Summarize with Claude (primary) or OpenAI (fallback)
        system_prompt = f"""Du bist ein Assistent für ein Bauunternehmen. Fasse dieses Dokument aus dem Google Drive von Kunde "{customer_name}" zusammen.

Gib auf Deutsch eine kurze Zusammenfassung (3-5 Sätze):
- Worum geht es?
- Wichtige Details (Termine, Beträge, Vereinbarungen)
- Relevanz für die Baustelle"""

        if question:
            system_prompt += f"\n\nBeantworte basierend auf dem Dokument: {question}"

        try:
            user_msg = f"Dokument: {file_name}\n\nInhalt:\n{text_content}"

            if self.use_claude:
                summary = self._call_claude_text(system_prompt, user_msg)
            else:
                summary = self._call_openai_text(system_prompt, user_msg)

            if not summary:
                return None

            return {
                'file_name': file_name,
                'file_type': 'document',
                'folder': file_info.get('folder', '(root)'),
                'analysis': summary,
                'size_mb': file_info.get('size', 0) / (1024 * 1024),
            }

        except Exception as e:
            print(f"   ⚠️ Document analysis error: {e}")
            return None

    # ══════════════════════════════════════════════════════════════════════
    #  FILE DOWNLOAD
    # ══════════════════════════════════════════════════════════════════════

    def _download_file(self, file_id: str) -> Optional[bytes]:
        """Download file content from Google Drive via CRM API."""
        try:
            resp = requests.get(
                self.crm_url,
                params={'action': 'download_drive_file', 'file_id': file_id},
                headers={'X-Bot-Api-Key': self.crm_key},
                timeout=30,
            )

            if resp.status_code == 200:
                # Check if response is JSON (error) or binary (file content)
                content_type = resp.headers.get('Content-Type', '')
                if 'json' in content_type:
                    data = resp.json()
                    if data.get('success') and data.get('content'):
                        return base64.b64decode(data['content'])
                    print(f"   ⚠️ Download error: {data.get('error', 'unknown')}")
                    return None
                else:
                    return resp.content

            print(f"   ⚠️ Download HTTP {resp.status_code}")
            return None

        except Exception as e:
            print(f"   ⚠️ Download error: {e}")
            return None

    def _export_google_doc(self, file_id: str, mime_type: str) -> Optional[str]:
        """Export Google Docs/Sheets as text via CRM API."""
        try:
            resp = requests.get(
                self.crm_url,
                params={
                    'action': 'export_drive_file',
                    'file_id': file_id,
                    'mime_type': mime_type,
                },
                headers={'X-Bot-Api-Key': self.crm_key},
                timeout=30,
            )
            data = resp.json()
            if data.get('success'):
                return data.get('content', '')
            return None
        except Exception as e:
            print(f"   ⚠️ Export error: {e}")
            return None

    def _extract_text(self, file_data: bytes, file_name: str, mime_type: str) -> Optional[str]:
        """Extract text from downloaded file."""
        # Plain text files
        if 'text' in mime_type or file_name.endswith(('.txt', '.csv', '.md')):
            try:
                return file_data.decode('utf-8')
            except UnicodeDecodeError:
                return file_data.decode('latin-1', errors='ignore')

        # For PDFs, images of text etc. — would need additional libraries
        # For now, return None and let the AI handle the raw file
        return None

    # ══════════════════════════════════════════════════════════════════════
    #  DRIVE FILE LIST (via CRM API)
    # ══════════════════════════════════════════════════════════════════════

    def _get_drive_files(self, folder_id: str) -> List[Dict]:
        """Get file list from CRM API."""
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
            print(f"   ⚠️ Drive list error: {e}")
            return []

    # ══════════════════════════════════════════════════════════════════════
    #  FORMAT: Build analysis response
    # ══════════════════════════════════════════════════════════════════════

    def _format_analysis(
        self,
        file_details: List[Dict],
        customer_name: str,
        intent: str,
        question: str,
        images_count: int,
        docs_count: int,
    ) -> str:
        """Format AI analysis into a Telegram-friendly response."""

        if not file_details:
            return "📂 Keine analysierbaren Dokumente gefunden."

        lines = [f"📊 **Drive-Analyse** — {customer_name}\n"]

        total = len(file_details)
        lines.append(f"📄 {total} Dokumente analysiert")
        if docs_count:
            lines.append(f"  📄 {docs_count} Textdateien/Verträge")
        if images_count:
            lines.append(f"  🖼️ {images_count} Bilder analysiert")
        lines.append("")

        # Per-file analysis
        for i, detail in enumerate(file_details, 1):
            icon = "🖼️" if detail['file_type'] == 'image' else "📄"
            folder = f" [{detail.get('folder', '')}]" if detail.get('folder', '') not in ('', '(root)') else ""
            lines.append(f"**{i}. {icon} {detail['file_name']}{folder}**")
            lines.append(f"   {detail['analysis']}")
            lines.append("")

        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════════
    #  AI PROVIDERS — Claude (primary) + OpenAI (fallback)
    # ══════════════════════════════════════════════════════════════════════

    def _call_claude_with_documents(self, system: str, content: list) -> Optional[str]:
        """Call Claude with PDF documents (native PDF support)."""
        try:
            headers = {
                "x-api-key": self.anthropic_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": ANALYSIS_MODEL,
                "max_tokens": 600,
                "system": system,
                "messages": [{"role": "user", "content": content}],
            }

            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers, json=payload, timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()

            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block["text"]
            return text.strip() if text else None

        except Exception as e:
            print(f"   ⚠️ Claude document analysis error: {e}")
            # Fallback to OpenAI text-only
            combined = "\n".join([b.get("text", "") for b in content if b.get("type") == "text"])
            return self._call_openai_text(system, combined)

    def _call_claude_vision(self, prompt: str, b64_image: str, mime_type: str) -> Optional[str]:
        """Call Claude Sonnet with image analysis."""
        try:
            headers = {
                "x-api-key": self.anthropic_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": ANALYSIS_MODEL,
                "max_tokens": 400,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": b64_image,
                        }},
                    ],
                }],
            }

            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers, json=payload, timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block["text"]
            return text.strip() if text else None

        except Exception as e:
            print(f"   ⚠️ Claude vision error: {e}, falling back to OpenAI")
            return self._call_openai_vision(prompt, b64_image, mime_type)

    def _call_openai_vision(self, prompt: str, b64_image: str, mime_type: str) -> Optional[str]:
        """Fallback: OpenAI GPT-4o vision."""
        if not self.openai_client:
            return None
        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:{mime_type};base64,{b64_image}",
                            "detail": "low",
                        }},
                    ],
                }],
                max_tokens=300,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"   ⚠️ OpenAI vision error: {e}")
            return None

    def _call_claude_text(self, system: str, user: str) -> Optional[str]:
        """Call Claude Sonnet for text analysis."""
        try:
            headers = {
                "x-api-key": self.anthropic_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": ANALYSIS_MODEL,
                "max_tokens": 400,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }

            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers, json=payload, timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block["text"]
            return text.strip() if text else None

        except Exception as e:
            print(f"   ⚠️ Claude text error: {e}, falling back to OpenAI")
            return self._call_openai_text(system, user)

    def _call_openai_text(self, system: str, user: str) -> Optional[str]:
        """Fallback: OpenAI for text analysis."""
        if not self.openai_client:
            return None
        try:
            response = self.openai_client.chat.completions.create(
                model=FALLBACK_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=300,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"   ⚠️ OpenAI text error: {e}")
            return None

    # ══════════════════════════════════════════════════════════════════════
    #  STATS
    # ══════════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        return self._stats.copy()