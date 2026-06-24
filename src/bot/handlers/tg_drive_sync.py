"""
TELEGRAM → GOOGLE DRIVE PHOTO SYNC HANDLER (FIXED v2)
=====================================================
Catches photos/documents/videos in CUSTOMER groups and uploads them to the
customer's Google Drive folder via the CRM API.

What changed vs v1 (and WHY):
  1. CUSTOMER CHECK BEFORE DOWNLOAD.
     v1 downloaded EVERY photo/video in EVERY non-excluded group (videos up to
     50 MB) and only then let the CRM reject non-customers. Those big downloads
     run on the SAME user_client that does translation -> bandwidth contention,
     slower translation, and a higher chance of "[Errno 104] Connection reset".
     v2 asks the CRM "is this group a customer?" (a tiny request, NO file) and
     only downloads when the answer is yes. The result is cached per group, so
     it is one cheap check per group, not per message.
     - Graceful fallback: if the CRM check endpoint is not available, v2 behaves
       exactly like v1 (download, let CRM filter) - so nothing breaks if you
       deploy this before adding the tiny CRM snippet.

  2. BACKGROUND TASKS ARE KEPT REFERENCED.
     v1 did asyncio.create_task(...) without storing the task; Python may
     garbage-collect a pending task -> "Task was destroyed but it is pending"
     and half-finished uploads. v2 keeps a reference until the task completes.

  3. ORDERED, BOUNDED DEDUP.
     v1 trimmed a set with list(set)[-N:] - sets are unordered, so it kept a
     RANDOM subset and could reprocess (double-upload) or forget recent IDs.
     v2 uses a deque (insertion-ordered, fixed maxlen).

Still safe for the bot:
  - No database writes -> cannot cause "database is locked".
  - Upload + CRM check run in a thread pool -> never block the event loop.
  - Download is awaited inside a background task -> never blocks the handler.
"""

import os
import asyncio
import requests
import logging
import time
from collections import deque
from telethon import events
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger('tg_drive_sync')

CRM_BASE_URL = os.getenv('CRM_BASE_URL', 'https://teampflegeinfo.de/crm')
CRM_UPLOAD_URL = f'{CRM_BASE_URL}/api/upload_telegram_media.php'
BOT_SECRET = os.getenv('TG_DRIVE_SECRET', 'tg_drive_sync_2026_premiobad')
MAX_PHOTO_SIZE = 10 * 1024 * 1024
MAX_VIDEO_SIZE = 50 * 1024 * 1024
MAX_DOC_SIZE = 20 * 1024 * 1024
TEMP_DIR = '/tmp/tg_drive_downloads'

# Groups we never sync (office, test, approval chat, language-only, personal).
# Comma-separated Telegram chat IDs in env TG_DRIVE_EXCLUDE_GROUPS.
_EXCLUDED_GROUP_IDS = set()
for _g in os.getenv('TG_DRIVE_EXCLUDE_GROUPS', '').split(','):
    _g = _g.strip()
    if _g:
        try:
            _EXCLUDED_GROUP_IDS.add(abs(int(_g)))
        except ValueError:
            pass

# Per-group customer cache: abs(chat_id) -> (is_customer: bool, checked_at: float)
_customer_cache = {}
_CUSTOMER_TTL = 1800          # re-check a customer group at most every 30 min
_NEG_TTL = 1800               # a "not a customer" answer is trusted for 30 min
                              # (so a group that later becomes a customer is picked up)
_check_endpoint_available = True   # flipped off if CRM doesn't understand check_only

# Ordered, bounded dedup of processed messages
_processed_messages = deque(maxlen=500)
_processed_set = set()

# Keep references to in-flight background tasks so they are not GC'd
_inflight_tasks = set()

_executor = ThreadPoolExecutor(max_workers=3)

os.makedirs(TEMP_DIR, exist_ok=True)


def _is_excluded(chat_id):
    try:
        return abs(int(chat_id)) in _EXCLUDED_GROUP_IDS
    except (TypeError, ValueError):
        return False


def _crm_check_customer_sync(chat_id):
    """Ask the CRM whether this group maps to a customer - NO file uploaded.

    Returns one of: 'customer', 'not_customer', 'unavailable'.
    'unavailable' means the CRM endpoint does not support check_only (or errored)
    -> caller should fall back to the old download-and-let-CRM-filter behaviour.
    """
    global _check_endpoint_available
    if not _check_endpoint_available:
        return 'unavailable'
    try:
        resp = requests.post(
            CRM_UPLOAD_URL,
            data={
                'bot_secret': BOT_SECRET,
                'telegram_group_id': str(chat_id),
                'check_only': '1',
            },
            timeout=15,
        )
        if resp.status_code == 404:
            _check_endpoint_available = False
            return 'unavailable'
        if resp.status_code != 200:
            return 'unavailable'
        data = resp.json()
        # Endpoint understands check_only only if it returns the explicit flag.
        if 'is_customer' not in data:
            _check_endpoint_available = False
            return 'unavailable'
        return 'customer' if data.get('is_customer') else 'not_customer'
    except Exception:
        # Network/timeout/parse error -> don't permanently disable; just fall back.
        return 'unavailable'


def _upload_to_crm_sync(file_path, filename, chat_id, mime_type='image/jpeg'):
    try:
        with open(file_path, 'rb') as f:
            resp = requests.post(CRM_UPLOAD_URL,
                data={
                    'bot_secret': BOT_SECRET,
                    'telegram_group_id': str(chat_id),
                    'filename': filename,
                },
                files={'file': (filename, f, mime_type)},
                timeout=120
            )
        if resp.status_code == 200:
            result = resp.json()
            if result.get('success'):
                print(f"   OK Drive sync: {filename} -> {result.get('customer_name', '?')}")
                return result
            else:
                print(f"   WARN Drive sync rejected: {result.get('error', '?')}")
        else:
            print(f"   WARN Drive sync HTTP {resp.status_code}")
    except requests.Timeout:
        print(f"   ERR Drive sync timeout: {filename}")
    except Exception as e:
        print(f"   ERR Drive sync error: {e}")
    finally:
        try:
            os.remove(file_path)
        except Exception:
            pass
    return None


def _get_mime(filename):
    ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
    return {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
        'gif': 'image/gif', 'webp': 'image/webp', 'heic': 'image/heic',
        'mp4': 'video/mp4', 'mov': 'video/quicktime',
        'pdf': 'application/pdf', 'doc': 'application/msword',
    }.get(ext, 'application/octet-stream')


def _get_max_size(event):
    if event.photo:
        return MAX_PHOTO_SIZE
    elif event.video:
        return MAX_VIDEO_SIZE
    else:
        return MAX_DOC_SIZE


async def _is_customer_group(chat_id):
    """Return True if we should download media for this group."""
    key = abs(int(chat_id)) if chat_id is not None else 0
    now = time.time()

    cached = _customer_cache.get(key)
    if cached is not None:
        is_cust, checked_at = cached
        ttl = _CUSTOMER_TTL if is_cust else _NEG_TTL
        if now - checked_at < ttl:
            return is_cust

    loop = asyncio.get_event_loop()
    status = await loop.run_in_executor(_executor, _crm_check_customer_sync, str(chat_id))

    if status == 'customer':
        _customer_cache[key] = (True, now)
        return True
    if status == 'not_customer':
        _customer_cache[key] = (False, now)
        return False
    # 'unavailable' -> don't cache; fall back to allowing download (v1 behaviour)
    return True


async def _sync_media(event, file_path, filename, chat_id, mime_type):
    """Background task: customer-check -> download -> upload. Fully non-blocking."""
    try:
        if not await _is_customer_group(chat_id):
            return  # non-customer group -> skip, no download, no bandwidth used

        await event.download_media(file=file_path)
        if not os.path.exists(file_path):
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            _executor, _upload_to_crm_sync,
            file_path, filename, str(chat_id), mime_type
        )
    except Exception as e:
        print(f"   ERR Drive sync download/upload error: {e}")
        try:
            os.remove(file_path)
        except Exception:
            pass


def _track_task(coro):
    """Schedule a background task and keep a reference until it finishes."""
    task = asyncio.create_task(coro)
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return task


def register_drive_sync_handler(user_client, bot_client=None):
    """Register media sync handler - CUSTOMER GROUPS ONLY (resolved via CRM)."""

    @user_client.on(events.NewMessage(func=lambda e: e.is_group and (e.photo or e.document or e.video)))
    async def drive_sync_handler(event):
        # Ordered, bounded dedup
        msg_key = f"{event.chat_id}_{event.id}"
        if msg_key in _processed_set:
            return
        if len(_processed_messages) == _processed_messages.maxlen:
            oldest = _processed_messages[0]   # evicted by the append below
            _processed_set.discard(oldest)
        _processed_messages.append(msg_key)
        _processed_set.add(msg_key)

        try:
            if _is_excluded(event.chat_id):
                return

            # Skip voice notes & audio (e.g. the bot's own TTS .ogg messages,
            # or voice replies). These are NOT construction photos/documents and
            # must never be uploaded to a customer's Drive.
            if getattr(event, 'voice', None) or getattr(event, 'audio', None):
                return
            # Extra guard: skip any audio mime-type document
            try:
                if event.document and event.document.mime_type and \
                        event.document.mime_type.startswith('audio'):
                    return
            except Exception:
                pass

            chat_id = event.chat_id
            sender = await event.get_sender()
            sender_name = ''
            if sender:
                sender_name = (getattr(sender, 'first_name', '') or '') + '_' + (getattr(sender, 'last_name', '') or '')
                sender_name = sender_name.strip('_').replace(' ', '_') or 'Unknown'

            file_size = event.file.size if event.file else 0
            max_size = _get_max_size(event)
            if file_size > max_size:
                return

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            # Include the unique Telegram message id so photos sent in the SAME
            # second (albums) don't collide on the same filename/path. Without
            # this, multiple tasks shared one path -> a race where one task
            # deleted the file while another was still reading it ("No such file").
            uid = event.id

            if event.photo:
                filename = f"TG_{sender_name}_{timestamp}_{uid}.jpg"
            elif event.document:
                filename = None
                if event.document.attributes:
                    for attr in event.document.attributes:
                        if hasattr(attr, 'file_name') and attr.file_name:
                            filename = attr.file_name
                            break
                if not filename:
                    filename = f"TG_Doc_{sender_name}_{timestamp}_{uid}"
            elif event.video:
                filename = f"TG_Video_{sender_name}_{timestamp}_{uid}.mp4"
            else:
                return

            # Path is always unique per message (uid prefix) — no shared-path race
            file_path = os.path.join(TEMP_DIR, f"{abs(chat_id)}_{uid}_{filename}")
            mime_type = _get_mime(filename)

            print(f"   Drive sync queued: {filename} ({file_size // 1024}KB)")

            # Non-blocking background task (customer-check -> download -> upload),
            # reference kept so it isn't garbage-collected mid-flight.
            _track_task(_sync_media(event, file_path, filename, chat_id, mime_type))

        except Exception as e:
            print(f"   ERR Drive sync error: {e}")

    print("Drive sync registered (customer-check before download)")
    return drive_sync_handler