#!/usr/bin/env python3
"""
GOOGLE CALENDAR SYNC FOR CONSTRUCTION DESCRIPTIONS
====================================================
Fetches today's appointments from Google Calendar and caches them in SQLite.
The construction video handler reads from this cache for customer matching.

Two modes:
  1. Google Calendar API (service account or OAuth)
  2. CRM API sync (if your CRM exposes calendar data)

Setup:
  - pip install google-api-python-client google-auth-oauthlib
  - Place credentials.json in project root (from Google Cloud Console)
  - Run once to authenticate: python construction_calendar_sync.py --auth
  - Then runs automatically via cron or bot startup

CRM Integration:
  If your CRM has an API that returns today's appointments with customer data,
  implement `sync_from_crm()` instead — it's simpler and gives you crm_record_id directly.
"""

import os
import json
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "calendar_token.json")
CRM_API_URL = os.getenv("CRM_API_URL", "")  # If your CRM has an API
CRM_API_KEY = os.getenv("CRM_API_KEY", "")
DB_PATH = os.getenv("BOT_DB_PATH", "bot_data.db")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════════════════════════════════════════
# OPTION 1: GOOGLE CALENDAR API
# ═══════════════════════════════════════════════════════════════════════════════

def sync_from_google_calendar() -> List[Dict]:
    """
    Fetch today's events from Google Calendar API and cache them.

    Returns list of events synced.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlowcmd
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("❌ Google API libraries not installed.")
        print("   pip install google-api-python-client google-auth-oauthlib")
        return []

    SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                print(f"❌ {CREDENTIALS_FILE} not found. Download from Google Cloud Console.")
                return []
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())

    service = build('calendar', 'v3', credentials=creds)

    # Get today's events
    now = datetime.utcnow()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)

    events_result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=start_of_day.isoformat() + 'Z',
        timeMax=end_of_day.isoformat() + 'Z',
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])
    print(f"📅 Found {len(events)} events for today")

    synced = []
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date', ''))
        end = event['end'].get('dateTime', event['end'].get('date', ''))

        # Try to extract CRM info from event description or extended properties
        description = event.get('description', '')
        crm_record_id = ''
        customer_name = ''
        customer_address = ''

        # Parse CRM info from description (convention: "CRM: xxx" lines)
        for line in description.split('\n'):
            line = line.strip()
            if line.lower().startswith('crm:') or line.lower().startswith('crm-id:'):
                crm_record_id = line.split(':', 1)[1].strip()
            elif line.lower().startswith('kunde:') or line.lower().startswith('customer:'):
                customer_name = line.split(':', 1)[1].strip()
            elif line.lower().startswith('adresse:') or line.lower().startswith('address:'):
                customer_address = line.split(':', 1)[1].strip()

        # Fallback: use event title as customer name
        if not customer_name:
            customer_name = event.get('summary', '')

        # Fallback: use event location as address
        if not customer_address:
            customer_address = event.get('location', '')

        evt_data = {
            'event_id': event['id'],
            'summary': event.get('summary', ''),
            'start_time': start,
            'end_time': end,
            'location': event.get('location', ''),
            'crm_record_id': crm_record_id,
            'customer_name': customer_name,
            'customer_address': customer_address,
        }
        synced.append(evt_data)

    # Save to cache
    _save_to_cache(synced)
    return synced


# ═══════════════════════════════════════════════════════════════════════════════
# OPTION 2: CRM API SYNC
# ═══════════════════════════════════════════════════════════════════════════════

def sync_from_crm() -> List[Dict]:
    """
    Fetch today's appointments from CRM API.
    This is the preferred method if your CRM exposes appointment data,
    because it gives you crm_record_id directly.

    Adjust the API call and response parsing to match your CRM.
    """
    if not CRM_API_URL:
        print("⚠️ CRM_API_URL not set — skipping CRM sync")
        return []

    import requests

    try:
        today = datetime.now().strftime("%Y-%m-%d")
        response = requests.get(
            f"{CRM_API_URL}/appointments",
            params={'date': today},
            headers={
                'Authorization': f'Bearer {CRM_API_KEY}',
                'Content-Type': 'application/json',
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        # ── Adjust this parsing to match YOUR CRM's response format ──
        synced = []
        appointments = data.get('appointments', data.get('data', []))
        for apt in appointments:
            evt_data = {
                'event_id': str(apt.get('id', '')),
                'summary': apt.get('title', apt.get('name', '')),
                'start_time': apt.get('start_time', apt.get('start', '')),
                'end_time': apt.get('end_time', apt.get('end', '')),
                'location': apt.get('address', apt.get('location', '')),
                'crm_record_id': str(apt.get('record_id', apt.get('customer_id', ''))),
                'customer_name': apt.get('customer_name', apt.get('client_name', '')),
                'customer_address': apt.get('address', apt.get('site_address', '')),
            }
            synced.append(evt_data)

        print(f"📅 CRM sync: {len(synced)} appointments for today")
        _save_to_cache(synced)
        return synced

    except Exception as e:
        print(f"❌ CRM sync error: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# OPTION 3: MANUAL / STATIC ENTRIES
# ═══════════════════════════════════════════════════════════════════════════════

def add_manual_appointment(
    customer_name: str,
    crm_record_id: str = "",
    customer_address: str = "",
    start_time: str = "",
    end_time: str = "",
):
    """
    Manually add an appointment to today's cache.
    Useful for testing or when calendar integration isn't set up yet.
    """
    if not start_time:
        start_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    if not end_time:
        end = datetime.now() + timedelta(hours=2)
        end_time = end.strftime("%Y-%m-%dT%H:%M:%S")

    evt = {
        'event_id': f"manual_{datetime.now().strftime('%H%M%S')}",
        'summary': customer_name,
        'start_time': start_time,
        'end_time': end_time,
        'location': customer_address,
        'crm_record_id': crm_record_id,
        'customer_name': customer_name,
        'customer_address': customer_address,
    }
    _save_to_cache([evt])
    print(f"✅ Manual appointment added: {customer_name}")
    return evt


# ═══════════════════════════════════════════════════════════════════════════════
# CACHE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def _save_to_cache(events: List[Dict]):
    """Save events to SQLite cache"""
    conn = get_db()

    # Ensure table exists
    conn.execute("""
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
            datetime.now().isoformat(),
        ))

    conn.commit()
    conn.close()


def clear_old_cache(days_old: int = 7):
    """Remove cache entries older than N days"""
    conn = get_db()
    cutoff = (datetime.now() - timedelta(days=days_old)).isoformat()
    conn.execute(
        "DELETE FROM construction_calendar_cache WHERE fetched_at < ?",
        (cutoff,)
    )
    conn.commit()
    conn.close()


def sync_calendar():
    """
    Main sync function — tries CRM first, then Google Calendar.
    Call this on bot startup and periodically (e.g., every 30 minutes).
    """
    print("\n📅 Syncing calendar for construction descriptions...")

    # Try CRM first (better data — has crm_record_id)
    if CRM_API_URL:
        events = sync_from_crm()
        if events:
            return events

    # Fallback to Google Calendar
    if os.path.exists(CREDENTIALS_FILE) or os.path.exists(TOKEN_FILE):
        events = sync_from_google_calendar()
        if events:
            return events

    print("⚠️ No calendar source configured. Using manual entries only.")
    print("   Set CRM_API_URL or place Google Calendar credentials.json")
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == "--auth":
            print("🔑 Starting Google Calendar authentication...")
            sync_from_google_calendar()

        elif cmd == "--sync":
            events = sync_calendar()
            print(f"\n✅ Synced {len(events)} events:")
            for e in events:
                print(f"   {e['start_time'][:16]} — {e['customer_name'] or e['summary']}")

        elif cmd == "--add":
            if len(sys.argv) < 3:
                print("Usage: python construction_calendar_sync.py --add 'Customer Name' [crm_id] [address]")
                sys.exit(1)
            name = sys.argv[2]
            crm_id = sys.argv[3] if len(sys.argv) > 3 else ""
            address = sys.argv[4] if len(sys.argv) > 4 else ""
            add_manual_appointment(name, crm_id, address)

        elif cmd == "--list":
            conn = get_db()
            rows = conn.execute("SELECT * FROM construction_calendar_cache ORDER BY start_time").fetchall()
            conn.close()
            print(f"\n📅 Cached appointments ({len(rows)}):")
            for r in rows:
                print(f"   {r['start_time'][:16] if r['start_time'] else '?'} — "
                      f"{r['customer_name'] or r['summary'] or '?'} "
                      f"(CRM: {r['crm_record_id'] or '-'})")

        elif cmd == "--clear":
            clear_old_cache(0)
            print("🗑️ Cache cleared")

        else:
            print(f"Unknown command: {cmd}")
            print("Commands: --auth, --sync, --add, --list, --clear")
    else:
        sync_calendar()
