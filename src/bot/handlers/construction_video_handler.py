#!/usr/bin/env python3
"""
CONSTRUCTION VIDEO HANDLER
===========================
Lothar sends video/voice in bot DM → transcribe → generate construction description
→ match to CRM customer via Google Calendar → approve → save to CRM

Pipeline: Download → FFmpeg → Whisper → AI Description → Approval
"""

import os
import asyncio
import sqlite3
import secrets
import subprocess
import tempfile
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from openai import OpenAI
from bot.config import OPENAI_API_KEY, BUSINESS_INFO

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

MAX_VIDEO_SIZE_MB = int(os.getenv("MAX_VIDEO_SIZE_MB", "50"))
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")
DESCRIPTION_AI_MODEL = os.getenv("DESCRIPTION_AI_MODEL", "gpt-4o")
MEDIA_TEMP_DIR = os.getenv("MEDIA_TEMP_DIR", "/tmp/construction_media")
CALENDAR_MATCH_BEFORE_MINUTES = 30   # Match appointment 30 min before start
CALENDAR_MATCH_AFTER_MINUTES = 120   # Match appointment 2 hours after end

# CRM API Configuration
CRM_API_URL = os.getenv("CRM_API_URL", "https://teampflegeinfo.de/crm/api/bot_construction.php")
CRM_API_KEY = os.getenv("CRM_BOT_API_KEY", "")

# Ensure temp directory
os.makedirs(MEDIA_TEMP_DIR, exist_ok=True)

openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

DB_PATH = os.getenv("BOT_DB_PATH", "bot_data.db")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def init_construction_tables():
    """Create tables for construction description feature"""
    conn = get_db()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS construction_descriptions (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        token             TEXT UNIQUE NOT NULL,
        status            TEXT DEFAULT 'processing',
        -- Source
        video_message_id  INTEGER,
        voice_message_id  INTEGER,
        media_type        TEXT DEFAULT 'video',
        -- Processing
        audio_path        TEXT,
        transcript        TEXT,
        transcript_language TEXT,
        generated_description TEXT,
        -- Customer matching
        crm_record_id     TEXT,
        customer_name      TEXT,
        customer_address   TEXT,
        calendar_event_id  TEXT,
        appointment_date   TEXT,
        matched_via        TEXT DEFAULT 'calendar',
        -- Final
        final_description  TEXT,
        approved_by        TEXT,
        approved_at        DATETIME,
        sent_to_crm        INTEGER DEFAULT 0,
        -- Metadata
        created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at         DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS construction_calendar_cache (
        event_id          TEXT PRIMARY KEY,
        summary           TEXT,
        start_time        TEXT,
        end_time          TEXT,
        location          TEXT,
        crm_record_id     TEXT,
        customer_name     TEXT,
        customer_address  TEXT,
        fetched_at        DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE INDEX IF NOT EXISTS idx_cd_status
        ON construction_descriptions(status)
    """)
    c.execute("""
    CREATE INDEX IF NOT EXISTS idx_cd_token
        ON construction_descriptions(token)
    """)

    conn.commit()
    conn.close()
    print("✅ Construction description tables initialized")


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1: MEDIA DOWNLOAD
# ═══════════════════════════════════════════════════════════════════════════════

async def download_media(client, message) -> Tuple[str, str]:
    """
    Download video or voice message from Telegram.

    Returns:
        (file_path, media_type) — 'video', 'voice', 'video_note', 'document'
    """
    media_type = "unknown"

    if message.video:
        media_type = "video"
        size_mb = (message.video.size or 0) / (1024 * 1024)
    elif message.voice:
        media_type = "voice"
        size_mb = (message.voice.size or 0) / (1024 * 1024)
    elif message.video_note:
        media_type = "video_note"
        size_mb = (message.video_note.size or 0) / (1024 * 1024)
    elif message.document:
        mime = getattr(message.document, 'mime_type', '') or ''
        if mime.startswith('video/') or mime.startswith('audio/'):
            media_type = "document"
            size_mb = (message.document.size or 0) / (1024 * 1024)
        else:
            raise ValueError(f"Unsupported document type: {mime}")
    else:
        raise ValueError("No downloadable media found in message")

    if size_mb > MAX_VIDEO_SIZE_MB:
        raise ValueError(
            f"File too large: {size_mb:.1f}MB (max {MAX_VIDEO_SIZE_MB}MB)"
        )

    print(f"   📥 Downloading {media_type} ({size_mb:.1f}MB)...")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = os.path.join(MEDIA_TEMP_DIR, f"media_{timestamp}")
    downloaded = await client.download_media(message, file=file_path)

    if not downloaded or not os.path.exists(downloaded):
        raise RuntimeError("Download failed — file not saved")

    print(f"   ✅ Downloaded: {downloaded}")
    return downloaded, media_type


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2: AUDIO EXTRACTION (FFmpeg)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_audio(input_path: str, media_type: str) -> str:
    """
    Extract audio from video using FFmpeg.
    For voice messages, just convert to mp3.

    Returns:
        Path to extracted .mp3 file
    """
    output_path = input_path.rsplit('.', 1)[0] if '.' in input_path else input_path
    output_path += "_audio.mp3"

    print(f"   🔊 Extracting audio with FFmpeg...")

    cmd = [
        "ffmpeg", "-i", input_path,
        "-vn",                   # No video
        "-acodec", "libmp3lame", # MP3 codec
        "-ab", "128k",           # 128kbps bitrate (good enough for speech)
        "-ar", "16000",          # 16kHz sample rate (optimal for Whisper)
        "-ac", "1",              # Mono (speech doesn't need stereo)
        "-y",                    # Overwrite
        output_path
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120  # 2 min timeout
        )

        if result.returncode != 0:
            print(f"   ⚠️ FFmpeg stderr: {result.stderr[-500:]}")
            raise RuntimeError(f"FFmpeg failed: {result.stderr[-200:]}")

        if not os.path.exists(output_path):
            raise RuntimeError("FFmpeg produced no output file")

        audio_size = os.path.getsize(output_path) / (1024 * 1024)
        print(f"   ✅ Audio extracted: {audio_size:.1f}MB")
        return output_path

    except subprocess.TimeoutExpired:
        raise RuntimeError("FFmpeg timed out (>2 minutes)")


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3: WHISPER TRANSCRIPTION
# ═══════════════════════════════════════════════════════════════════════════════

def transcribe_audio(audio_path: str, language_hint: str = None) -> Dict:
    """
    Transcribe audio using OpenAI Whisper API.

    Args:
        audio_path: Path to audio file
        language_hint: ISO language code hint (e.g. 'de', 'pl')

    Returns:
        {'text': str, 'language': str}
    """
    print(f"   🎤 Transcribing with Whisper ({WHISPER_MODEL})...")

    # Check file size — Whisper API limit is 25MB
    file_size = os.path.getsize(audio_path) / (1024 * 1024)
    if file_size > 25:
        raise ValueError(f"Audio file too large for Whisper: {file_size:.1f}MB (max 25MB)")

    with open(audio_path, "rb") as audio_file:
        kwargs = {
            "model": WHISPER_MODEL,
            "file": audio_file,
            "response_format": "verbose_json",  # Get language info
        }
        if language_hint:
            kwargs["language"] = language_hint

        response = openai_client.audio.transcriptions.create(**kwargs)

    transcript_text = response.text if hasattr(response, 'text') else str(response)
    detected_lang = getattr(response, 'language', language_hint or 'unknown')

    print(f"   ✅ Transcribed: {len(transcript_text)} chars | Language: {detected_lang}")
    print(f"   📝 Preview: {transcript_text[:150]}...")

    return {
        "text": transcript_text,
        "language": detected_lang,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4: CONSTRUCTION DESCRIPTION GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

CONSTRUCTION_PROMPT_TEMPLATE = """Du bist ein professioneller Baudokumentierer für {company_name}, 
spezialisiert auf {specialization} in {location}.

Aus der folgenden Transkription einer Baustellenvideo-Aufnahme erstelle bitte eine 
strukturierte Baubeschreibung (Baudokumentation).

## Transkription:
{transcript}

## Kunde / Baustelle:
{customer_info}

## Aufnahmedatum:
{date}

## Anweisungen:
Erstelle eine professionelle, strukturierte Baubeschreibung mit folgenden Abschnitten:

1. **Zusammenfassung** — Kurze Zusammenfassung der durchgeführten Arbeiten (2-3 Sätze)
2. **Durchgeführte Arbeiten** — Detaillierte Auflistung aller beschriebenen Arbeiten
3. **Materialien** — Verwendete Materialien, Marken, Maße (wenn erwähnt)
4. **Maße und Flächen** — Alle erwähnten Abmessungen
5. **Probleme / Hinweise** — Aufgetretene Probleme oder besondere Hinweise
6. **Nächste Schritte** — Was als Nächstes gemacht werden muss (wenn erwähnt)

Regeln:
- Schreibe auf Deutsch in professionellem Ton
- Benutze Fachbegriffe der Baubranche (DIN-Normen, wenn relevant)
- Wenn etwas unklar ist, markiere es mit [?]
- Erfinde KEINE Informationen — nur was in der Transkription steht
- Wenn ein Abschnitt nicht aus der Transkription hervorgeht, schreibe "Keine Angabe"
"""

# English fallback template
CONSTRUCTION_PROMPT_TEMPLATE_EN = """You are a professional construction documenter for {company_name},
specializing in {specialization} in {location}.

From the following transcript of a construction site video recording, create a 
structured construction description (site documentation).

## Transcript:
{transcript}

## Customer / Site:
{customer_info}

## Recording date:
{date}

## Instructions:
Create a professional, structured construction description with these sections:

1. **Summary** — Brief summary of completed work (2-3 sentences)
2. **Work Completed** — Detailed list of all described work
3. **Materials** — Materials used, brands, dimensions (if mentioned)
4. **Measurements** — All mentioned dimensions and areas
5. **Issues / Notes** — Problems encountered or special notes
6. **Next Steps** — What needs to be done next (if mentioned)

Rules:
- Write in a professional tone
- Use construction industry terminology
- If something is unclear, mark it with [?]
- Do NOT invent information — only what is in the transcript
- If a section cannot be derived from the transcript, write "Not mentioned"
"""


def generate_construction_description(
    transcript: str,
    customer_name: str = "",
    customer_address: str = "",
    appointment_date: str = "",
    language: str = "de"
) -> Dict:
    """
    Generate construction description from transcript using AI.

    Returns:
        {'description': str, 'model': str, 'tokens_used': int}
    """
    print(f"   🧠 Generating construction description ({DESCRIPTION_AI_MODEL})...")

    customer_info = ""
    if customer_name:
        customer_info += f"Kunde: {customer_name}\n"
    if customer_address:
        customer_info += f"Adresse: {customer_address}\n"
    if not customer_info:
        customer_info = "Noch nicht zugeordnet"

    date_str = appointment_date or datetime.now().strftime("%d.%m.%Y")

    # Choose template based on language
    template = CONSTRUCTION_PROMPT_TEMPLATE if language == "de" else CONSTRUCTION_PROMPT_TEMPLATE_EN

    prompt = template.format(
        company_name=BUSINESS_INFO.get('company_name', 'Construction Co'),
        specialization=BUSINESS_INFO.get('specialization', 'Renovation'),
        location=BUSINESS_INFO.get('location', ''),
        transcript=transcript,
        customer_info=customer_info,
        date=date_str,
    )

    response = openai_client.chat.completions.create(
        model=DESCRIPTION_AI_MODEL,
        messages=[
            {
                "role": "system",
                "content": "Du bist ein präziser Baudokumentierer. Dokumentiere nur was tatsächlich gesagt wurde."
            },
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,  # Low temperature for factual output
        max_tokens=2000,
    )

    description = response.choices[0].message.content
    tokens = response.usage.total_tokens if response.usage else 0

    print(f"   ✅ Description generated: {len(description)} chars | {tokens} tokens")

    return {
        "description": description,
        "model": DESCRIPTION_AI_MODEL,
        "tokens_used": tokens,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5: GOOGLE CALENDAR MATCHING
# ═══════════════════════════════════════════════════════════════════════════════

def get_todays_appointments_from_cache() -> List[Dict]:
    """
    Get today's appointments from local cache.
    (Cache is populated by a separate sync process or API call)
    """
    conn = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT event_id, summary, start_time, end_time, location,
               crm_record_id, customer_name, customer_address
        FROM construction_calendar_cache
        WHERE start_time LIKE ?
        ORDER BY start_time ASC
    """, (f"{today}%",)).fetchall()
    conn.close()

    return [dict(r) for r in rows]


def find_best_calendar_match(appointments: List[Dict]) -> Optional[Dict]:
    """
    Find the best matching appointment based on current time.

    Logic:
    - If currently within an appointment window (±buffer): exact match
    - If between appointments: pick the most recently ended one
    - If before all: pick the first upcoming
    """
    if not appointments:
        return None

    now = datetime.now()

    best_match = None
    best_score = float('inf')

    for apt in appointments:
        try:
            start = datetime.fromisoformat(apt['start_time'].replace('Z', '+00:00'))
            end = datetime.fromisoformat(apt['end_time'].replace('Z', '+00:00'))

            # Make naive for comparison if needed
            if start.tzinfo:
                start = start.replace(tzinfo=None)
            if end.tzinfo:
                end = end.replace(tzinfo=None)

            window_start = start - timedelta(minutes=CALENDAR_MATCH_BEFORE_MINUTES)
            window_end = end + timedelta(minutes=CALENDAR_MATCH_AFTER_MINUTES)

            if window_start <= now <= window_end:
                # Within window — calculate how close to center
                center = start + (end - start) / 2
                score = abs((now - center).total_seconds())
                if score < best_score:
                    best_score = score
                    best_match = apt
        except (ValueError, TypeError):
            continue

    return best_match


def get_active_projects_from_db() -> List[Dict]:
    """
    Fallback: get active projects/customers from group_pair_mappings.
    These are sites with active Telegram groups.
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT DISTINCT source_group_name, source_chat_id
            FROM group_pair_mappings
            WHERE is_active = 1
            ORDER BY source_group_name ASC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        conn.close()
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6: FULL PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

async def process_construction_video(
    client,
    message,
    owner_user_id: int,
    dashboard_url: str = "http://localhost:5000",
) -> Dict:
    """
    Full pipeline: Download → Extract → Transcribe → Generate → Create approval

    Args:
        client: Telethon client (user_client)
        message: The Telegram message with video/voice
        owner_user_id: Lothar's user ID (for sending status updates)
        dashboard_url: URL for approval dashboard

    Returns:
        Dict with processing results
    """
    token = secrets.token_urlsafe(16)
    media_path = None
    audio_path = None

    try:
        # ── Step 1: Download ──────────────────────────────────────────────
        media_path, media_type = await download_media(client, message)

        # ── Step 2: Extract Audio ─────────────────────────────────────────
        if media_type in ('voice',):
            # Voice messages are already audio — just convert format
            audio_path = extract_audio(media_path, media_type)
        elif media_type in ('video', 'video_note', 'document'):
            audio_path = extract_audio(media_path, media_type)
        else:
            raise ValueError(f"Unsupported media type: {media_type}")

        # ── Step 3: Transcribe ────────────────────────────────────────────
        # Hint German since Lothar speaks German
        transcription = transcribe_audio(audio_path, language_hint="de")
        transcript = transcription['text']
        transcript_lang = transcription['language']

        if not transcript or len(transcript.strip()) < 10:
            return {
                "success": False,
                "error": "Transkription zu kurz oder leer. Bitte erneut aufnehmen.",
                "token": token,
            }

        # ── Step 4: CRM Site Match ────────────────────────────────────────
        crm_match = crm_match_by_time()
        crm_sites = crm_match.get('all_sites', [])

        customer_name = ""
        customer_address = ""
        crm_record_id = ""
        calendar_event_id = ""
        appointment_date = datetime.now().strftime("%d.%m.%Y")
        matched_via = "none"

        if crm_match.get('matched') and crm_match.get('best_match'):
            best = crm_match['best_match']
            customer_name = best.get('customer_name', best.get('site_name', ''))
            customer_address = best.get('customer_address', best.get('address', ''))
            crm_record_id = str(best.get('record_id', ''))
            matched_via = "crm_auto"
        elif not crm_sites:
            # Fallback: try local calendar cache
            appointments = get_todays_appointments_from_cache()
            best_local = find_best_calendar_match(appointments)
            if best_local:
                customer_name = best_local.get('customer_name', best_local.get('summary', ''))
                customer_address = best_local.get('customer_address', best_local.get('location', ''))
                crm_record_id = best_local.get('crm_record_id', '')
                calendar_event_id = best_local.get('event_id', '')
                matched_via = "calendar_auto"

        # ── Step 5: Generate Description ──────────────────────────────────
        result = generate_construction_description(
            transcript=transcript,
            customer_name=customer_name,
            customer_address=customer_address,
            appointment_date=appointment_date,
            language=transcript_lang if transcript_lang in ('de', 'en') else 'de',
        )

        # ── Step 6: Save to Database ──────────────────────────────────────
        conn = get_db()
        conn.execute("""
            INSERT INTO construction_descriptions
            (token, status, video_message_id, voice_message_id, media_type,
             audio_path, transcript, transcript_language, generated_description,
             crm_record_id, customer_name, customer_address,
             calendar_event_id, appointment_date, matched_via)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            token,
            'pending_approval',
            message.id if media_type != 'voice' else None,
            message.id if media_type == 'voice' else None,
            media_type,
            audio_path,
            transcript,
            transcript_lang,
            result['description'],
            crm_record_id,
            customer_name,
            customer_address,
            calendar_event_id,
            appointment_date,
            matched_via,
        ))
        conn.commit()
        conn.close()

        return {
            "success": True,
            "token": token,
            "transcript": transcript,
            "transcript_language": transcript_lang,
            "description": result['description'],
            "customer_name": customer_name,
            "customer_address": customer_address,
            "crm_record_id": crm_record_id,
            "matched_via": matched_via,
            "appointments": crm_sites,  # For button selection if no auto-match
            "dashboard_url": f"{dashboard_url}/construction/{token}",
        }

    except Exception as e:
        # Save failed attempt
        try:
            conn = get_db()
            conn.execute("""
                INSERT INTO construction_descriptions
                (token, status, video_message_id, media_type, transcript)
                VALUES (?, 'failed', ?, ?, ?)
            """, (token, message.id, 'unknown', str(e)))
            conn.commit()
            conn.close()
        except Exception:
            pass

        print(f"   ❌ Pipeline error: {e}")
        import traceback; traceback.print_exc()

        return {
            "success": False,
            "error": str(e),
            "token": token,
        }

    finally:
        # Cleanup temporary files (keep audio for potential re-processing)
        if media_path and os.path.exists(media_path):
            try:
                os.remove(media_path)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# APPROVAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_construction_description(token: str) -> Optional[Dict]:
    """Get a construction description by token"""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM construction_descriptions WHERE token = ?", (token,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def approve_construction_description(
    token: str,
    final_description: str = None,
    crm_record_id: str = None,
    customer_name: str = None,
    approved_by: str = "Lothar"
) -> bool:
    """Approve and optionally edit a construction description"""
    conn = get_db()
    try:
        desc = conn.execute(
            "SELECT * FROM construction_descriptions WHERE token = ?", (token,)
        ).fetchone()
        if not desc:
            return False

        final = final_description or desc['generated_description']
        crm_id = crm_record_id or desc['crm_record_id']
        cust_name = customer_name or desc['customer_name']

        conn.execute("""
            UPDATE construction_descriptions
            SET status = 'approved',
                final_description = ?,
                crm_record_id = ?,
                customer_name = ?,
                approved_by = ?,
                approved_at = ?,
                updated_at = ?
            WHERE token = ?
        """, (final, crm_id, cust_name, approved_by,
              datetime.now(), datetime.now(), token))
        conn.commit()
        return True
    except Exception as e:
        print(f"❌ Approve error: {e}")
        return False
    finally:
        conn.close()


def discard_construction_description(token: str) -> bool:
    """Discard a construction description"""
    conn = get_db()
    try:
        conn.execute("""
            UPDATE construction_descriptions
            SET status = 'discarded', updated_at = ?
            WHERE token = ?
        """, (datetime.now(), token))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def update_customer_match(
    token: str,
    crm_record_id: str,
    customer_name: str,
    customer_address: str = "",
    calendar_event_id: str = ""
) -> bool:
    """Update the customer match for a description (when Lothar picks manually)"""
    conn = get_db()
    try:
        conn.execute("""
            UPDATE construction_descriptions
            SET crm_record_id = ?,
                customer_name = ?,
                customer_address = ?,
                calendar_event_id = ?,
                matched_via = 'manual',
                updated_at = ?
            WHERE token = ?
        """, (crm_record_id, customer_name, customer_address,
              calendar_event_id, datetime.now(), token))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_pending_descriptions() -> List[Dict]:
    """Get all pending construction descriptions"""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM construction_descriptions
        WHERE status = 'pending_approval'
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_descriptions(limit: int = 50) -> List[Dict]:
    """Get all construction descriptions (for history view)"""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM construction_descriptions
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM MESSAGE FORMATTING
# ═══════════════════════════════════════════════════════════════════════════════

def format_processing_start_message(media_type: str) -> str:
    """Message sent immediately when video/voice is received"""
    icons = {
        'video': '🎬',
        'voice': '🎤',
        'video_note': '📹',
        'document': '📎',
    }
    icon = icons.get(media_type, '📎')
    return (
        f"{icon} **Baudokumentation wird erstellt...**\n\n"
        f"⏳ Video wird verarbeitet\n"
        f"⏳ Audio wird extrahiert\n"
        f"⏳ Transkription läuft\n"
        f"⏳ Beschreibung wird generiert\n\n"
        f"Das dauert ca. 30-60 Sekunden."
    )


def format_result_message(result: Dict) -> str:
    """Format the result for Telegram DM notification"""
    if not result.get('success'):
        return (
            f"❌ **Fehler bei der Verarbeitung**\n\n"
            f"{result.get('error', 'Unbekannter Fehler')}\n\n"
            f"Bitte erneut versuchen."
        )

    # Build customer match info
    customer_line = ""
    if result.get('customer_name'):
        customer_line = f"📍 **{result['customer_name']}**"
        if result.get('customer_address'):
            customer_line += f"\n   {result['customer_address']}"
        if result['matched_via'] == 'calendar_auto':
            customer_line += "\n   ✅ Automatisch über Kalender zugeordnet"
    else:
        customer_line = "⚠️ **Kunde nicht automatisch erkannt**"

    # Truncate description for Telegram (full version on dashboard)
    desc = result['description']
    if len(desc) > 1500:
        desc = desc[:1500] + "\n\n... (vollständige Version im Dashboard)"

    msg = (
        f"📋 **Baubeschreibung erstellt**\n\n"
        f"{customer_line}\n\n"
        f"{'─' * 30}\n"
        f"{desc}\n"
        f"{'─' * 30}\n\n"
    )

    # Add action buttons hint
    msg += f"🔗 Dashboard: {result.get('dashboard_url', '')}\n\n"

    return msg


def format_appointment_buttons(appointments: List[Dict]) -> str:
    """Format appointments as numbered list for selection"""
    if not appointments:
        return "Keine Termine für heute gefunden."

    lines = ["📅 **Welcher Kunde?**\n"]
    for i, apt in enumerate(appointments, 1):
        name = apt.get('customer_name', apt.get('summary', 'Unbekannt'))
        time_str = ""
        try:
            start = datetime.fromisoformat(apt['start_time'].replace('Z', '+00:00'))
            time_str = start.strftime("%H:%M")
        except (ValueError, TypeError):
            pass
        lines.append(f"  {i}️⃣  {time_str} — {name}")

    lines.append(f"\nAntwort mit der Nummer (1-{len(appointments)})")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# CRM API CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

def crm_match_by_time() -> Dict:
    """
    Call CRM API to find the best matching construction site for current time.
    Uses worker_assignments + construction_sites tables.

    Returns:
        {
            'success': bool,
            'matched': bool,
            'best_match': {'site_id', 'site_name', 'record_id', 'customer_name', 'address'} or None,
            'all_sites': [list of all today's sites],
        }
    """
    if not CRM_API_URL or not CRM_API_KEY:
        print("   ⚠️ CRM API not configured — skipping CRM match")
        return {'success': False, 'error': 'CRM API not configured'}

    try:
        resp = requests.get(
            CRM_API_URL,
            params={'action': 'match_by_time', 'api_key': CRM_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"   📍 CRM match: {data.get('site_count', 0)} sites today, matched={data.get('matched', False)}")
        return data
    except Exception as e:
        print(f"   ❌ CRM match_by_time error: {e}")
        return {'success': False, 'error': str(e)}


def crm_get_today_sites() -> List[Dict]:
    """Get all active construction sites for today from CRM."""
    if not CRM_API_URL or not CRM_API_KEY:
        return []

    try:
        resp = requests.get(
            CRM_API_URL,
            params={'action': 'today_sites', 'api_key': CRM_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('sites', [])
    except Exception as e:
        print(f"   ❌ CRM today_sites error: {e}")
        return []


def crm_search_records(query: str) -> List[Dict]:
    """Search CRM records by customer name."""
    if not CRM_API_URL or not CRM_API_KEY:
        return []

    try:
        resp = requests.get(
            CRM_API_URL,
            params={'action': 'search', 'q': query, 'api_key': CRM_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('records', [])
    except Exception as e:
        print(f"   ❌ CRM search error: {e}")
        return []


def crm_update_description(record_id: int, description: str) -> Dict:
    """
    Push approved construction description to CRM field 137.
    Only called AFTER Lothar confirms the record is correct.

    Returns:
        {'success': bool, 'message': str}
    """
    if not CRM_API_URL or not CRM_API_KEY:
        print("   ⚠️ CRM API not configured — description saved locally only")
        return {'success': False, 'error': 'CRM API not configured'}

    try:
        resp = requests.post(
            CRM_API_URL,
            params={'action': 'update_description', 'api_key': CRM_API_KEY},
            json={
                'record_id': record_id,
                'description': description,
            },
            headers={'X-Bot-Api-Key': CRM_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get('success'):
            print(f"   ✅ CRM updated: record {record_id}, field 137")
        else:
            print(f"   ❌ CRM update failed: {data.get('error', 'unknown')}")
        return data
    except Exception as e:
        print(f"   ❌ CRM update_description error: {e}")
        return {'success': False, 'error': str(e)}


def format_sites_as_buttons(sites: List[Dict]) -> str:
    """Format CRM sites as numbered list for Telegram selection"""
    if not sites:
        return "Keine aktiven Baustellen für heute gefunden."

    lines = ["📍 **Welche Baustelle?**\n"]
    for i, site in enumerate(sites, 1):
        name = site.get('customer_name') or site.get('site_name', 'Unbekannt')
        address = site.get('address', '')
        record_id = site.get('record_id', '')
        line = f"  {i}️⃣  {name}"
        if address:
            line += f"\n      📍 {address}"
        if record_id:
            line += f"  (#{record_id})"
        lines.append(line)

    lines.append(f"\nAntwort mit der Nummer (1-{len(sites)})")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# CALENDAR SYNC HELPER (legacy — kept for backward compatibility)
# ═══════════════════════════════════════════════════════════════════════════════

def sync_calendar_to_cache(events: List[Dict]):
    """
    Sync Google Calendar events to local SQLite cache.
    Called from the Google Calendar integration layer.
    """
    conn = get_db()
    for evt in events:
        conn.execute("""
            INSERT OR REPLACE INTO construction_calendar_cache
            (event_id, summary, start_time, end_time, location,
             crm_record_id, customer_name, customer_address, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            evt.get('event_id', ''),
            evt.get('summary', ''),
            evt.get('start_time', ''),
            evt.get('end_time', ''),
            evt.get('location', ''),
            evt.get('crm_record_id', ''),
            evt.get('customer_name', ''),
            evt.get('customer_address', ''),
            datetime.now(),
        ))
    conn.commit()
    conn.close()
    print(f"✅ Calendar cache synced: {len(events)} events")