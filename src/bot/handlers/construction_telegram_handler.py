#!/usr/bin/env python3
"""
CONSTRUCTION VIDEO — TELEGRAM INTEGRATION
==========================================
This module registers the video/voice handler on the user_client.
Lothar sends video/voice in "KI Bautagebuch" group → pipeline runs → result posted in same group.

Usage in telegram_bot_groups.py main():
    from bot.handlers.construction_telegram_handler import register_construction_handler
    register_construction_handler(user_client, YOUR_USER_ID, DASHBOARD_URL, BAUDOKU_GROUP_ID)
"""

import asyncio
from telethon import events
from telethon.tl.types import (
    MessageMediaDocument, MessageMediaPhoto,
    DocumentAttributeVideo, DocumentAttributeAudio,
)
from bot.handlers.construction_video_handler import (
    process_construction_video,
    init_construction_tables,
    format_processing_start_message,
    format_result_message,
    format_sites_as_buttons,
    update_customer_match,
    get_construction_description,
    approve_construction_description,
    discard_construction_description,
    crm_update_description,
    crm_get_today_sites,
)

# Track active sessions: user_id → {token, state, appointments}
_active_sessions = {}


def _has_processable_media(message) -> bool:
    """Check if message contains video, voice, video_note, or video document"""
    if message.video:
        return True
    if message.voice:
        return True
    if message.video_note:
        return True
    if message.document:
        mime = getattr(message.document, 'mime_type', '') or ''
        if mime.startswith('video/') or mime.startswith('audio/'):
            return True
    return False


def _get_media_type(message) -> str:
    """Determine media type string"""
    if message.video:
        return 'video'
    if message.voice:
        return 'voice'
    if message.video_note:
        return 'video_note'
    if message.document:
        return 'document'
    return 'unknown'


def register_construction_handler(
    user_client,
    owner_user_id: int,
    dashboard_url: str = "http://localhost:5000",
    baudoku_group_id: int = 0,
):
    """
    Register the construction video handler on the user_client.

    Watches for video/voice messages in the Baudoku group (by ID).
    Results are posted back in the same group.

    Args:
        user_client: The Telethon user client
        owner_user_id: Lothar's Telegram user ID
        dashboard_url: Dashboard base URL
        baudoku_group_id: Chat ID of the KI Bautagebuch group
    """
    init_construction_tables()

    if not baudoku_group_id:
        print("   ⚠️ BAUDOKU_GROUP_ID not set — construction video handler DISABLED")
        print("   ℹ️  Set BAUDOKU_GROUP_ID in .env to enable")
        return

    # Strip -100 prefix from supergroup IDs for consistent comparison
    def _normalize_chat_id(cid: int) -> int:
        """Telegram supergroup IDs have -100 prefix. Normalize to bare ID."""
        cid = abs(cid)
        if cid > 1000000000000:  # has -100 prefix
            cid = cid - 1000000000000
        return cid

    _target_id = _normalize_chat_id(baudoku_group_id)
    print(f"   🔍 Baudoku normalized ID: {_target_id} (raw: {baudoku_group_id})")

    def _is_baudoku_group(chat) -> bool:
        """Check if chat is the Baudoku group by ID"""
        chat_id = _normalize_chat_id(getattr(chat, 'id', 0))
        return chat_id == _target_id

    @user_client.on(events.NewMessage())
    async def construction_video_handler(event):
        """
        Catches video/voice messages in the Baudoku group from Lothar.
        Handles both outgoing (same device) and incoming (other device) messages.
        """
        try:
            message = event.message

            # ── Only process in Baudoku group ─────────────────────────
            chat = await event.get_chat()
            if not _is_baudoku_group(chat):
                return

            # ── Get sender ID reliably ────────────────────────────────
            sender_id = message.sender_id
            if not sender_id and message.from_id:
                sender_id = getattr(message.from_id, 'user_id', None)

            # Debug: log every message in Baudoku group
            has_media = _has_processable_media(message)
            print(f"\n📋 [Baudoku] sender={sender_id} | owner={owner_user_id} | media={has_media} | out={event.out} | text={bool(message.text)}")

            # ── Only process Lothar's messages ────────────────────────
            if not sender_id:
                print(f"   ⏭️ No sender_id, skipping")
                return
            if abs(sender_id) != abs(owner_user_id):
                print(f"   ⏭️ Not owner ({sender_id} != {owner_user_id}), skipping")
                return

            # ── Check for processable media ───────────────────────────
            if not has_media:
                await _handle_text_reply(event, user_client, owner_user_id, dashboard_url)
                return

            media_type = _get_media_type(message)
            print(f"\n{'='*70}")
            print(f"🎬 CONSTRUCTION VIDEO DETECTED — KI Bautagebuch")
            print(f"{'='*70}")
            print(f"   Type: {media_type}")
            print(f"   Sender ID: {sender_id}")
            print(f"   Group: {getattr(chat, 'title', 'KI Bautagebuch')}")
            print(f"   Outgoing: {event.out}")

            # ── Send processing notification in the group ─────────────
            status_msg = await user_client.send_message(
                chat,
                format_processing_start_message(media_type)
            )

            # ── Run pipeline in background ────────────────────────────
            asyncio.create_task(
                _run_pipeline_and_notify(
                    user_client, message, chat, status_msg,
                    owner_user_id, dashboard_url, media_type,
                )
            )

        except Exception as e:
            print(f"❌ Construction handler error: {e}")
            import traceback; traceback.print_exc()

    print(f"✅ Construction video handler registered (group ID: {baudoku_group_id})")
    print("   📹 Supported: video, voice, video_note, audio documents")


async def _run_pipeline_and_notify(
    client, message, chat, status_msg,
    owner_user_id, dashboard_url, media_type,
):
    """
    Background task: runs the full pipeline and sends result notification.
    """
    try:
        # ── Update status: downloading ────────────────────────────────
        await client.edit_message(
            chat, status_msg,
            f"🎬 **Baudokumentation wird erstellt...**\n\n"
            f"✅ Video erkannt ({media_type})\n"
            f"⏳ Download läuft...\n"
            f"⏳ Audio wird extrahiert\n"
            f"⏳ Transkription läuft\n"
            f"⏳ Beschreibung wird generiert"
        )

        # ── Run pipeline ──────────────────────────────────────────────
        result = await process_construction_video(
            client=client,
            message=message,
            owner_user_id=owner_user_id,
            dashboard_url=dashboard_url,
        )

        # ── Send result ───────────────────────────────────────────────
        if result['success']:
            # Update status message with final result
            try:
                await client.edit_message(
                    chat, status_msg,
                    f"✅ **Verarbeitung abgeschlossen!**\n\n"
                    f"📝 Transkription: {len(result.get('transcript', ''))} Zeichen\n"
                    f"🌍 Sprache: {result.get('transcript_language', '?')}\n"
                    f"📋 Beschreibung generiert"
                )
            except Exception:
                pass

            # ── Send description ──────────────────────────────────────
            result_msg = format_result_message(result)
            await client.send_message(chat, result_msg)

            # ── Customer matching ─────────────────────────────────────
            if result.get('matched_via') in ('crm_auto', 'calendar_auto') and result.get('customer_name'):
                # Auto-matched — ask for confirmation
                record_info = f"Record #{result['crm_record_id']}" if result.get('crm_record_id') else ""
                await client.send_message(
                    chat,
                    f"👆 Automatisch zugeordnet zu **{result['customer_name']}**\n"
                    f"{record_info}\n\n"
                    f"⚠️ **Beschreibung wird ERST nach Bestätigung im CRM gespeichert.**\n\n"
                    f"Antwort:\n"
                    f"  ✅ `ja` — Bestätigen und ins CRM speichern\n"
                    f"  ✏️ `edit` — Im Dashboard bearbeiten\n"
                    f"  🔄 `andere` — Andere Baustelle wählen\n"
                    f"  🗑️ `nein` — Verwerfen"
                )
                # Store session
                _active_sessions[owner_user_id] = {
                    'token': result['token'],
                    'state': 'awaiting_confirmation',
                    'customer_name': result.get('customer_name', ''),
                    'crm_record_id': result.get('crm_record_id', ''),
                    'description': result.get('description', ''),
                    'sites': result.get('appointments', []),
                    'dashboard_url': result.get('dashboard_url', ''),
                }
            else:
                # No auto-match — show site list or ask
                sites = result.get('appointments', [])
                if sites:
                    await client.send_message(
                        chat,
                        format_sites_as_buttons(sites)
                    )
                    _active_sessions[owner_user_id] = {
                        'token': result['token'],
                        'state': 'awaiting_customer_selection',
                        'description': result.get('description', ''),
                        'sites': sites,
                        'dashboard_url': result.get('dashboard_url', ''),
                    }
                else:
                    await client.send_message(
                        chat,
                        f"⚠️ Keine aktiven Baustellen für heute gefunden.\n\n"
                        f"Bitte im Dashboard zuordnen:\n"
                        f"🔗 {result.get('dashboard_url', dashboard_url)}\n\n"
                        f"Oder Kundenname hier eingeben:"
                    )
                    _active_sessions[owner_user_id] = {
                        'token': result['token'],
                        'state': 'awaiting_manual_customer',
                        'description': result.get('description', ''),
                        'dashboard_url': result.get('dashboard_url', ''),
                    }
        else:
            # ── Error ─────────────────────────────────────────────────
            try:
                await client.edit_message(
                    chat, status_msg,
                    f"❌ **Verarbeitung fehlgeschlagen**"
                )
            except Exception:
                pass

            await client.send_message(
                chat,
                format_result_message(result)
            )

    except Exception as e:
        print(f"❌ Pipeline notify error: {e}")
        import traceback; traceback.print_exc()
        try:
            await client.send_message(
                chat,
                f"❌ Fehler bei der Verarbeitung:\n{str(e)[:300]}\n\nBitte erneut versuchen."
            )
        except Exception:
            pass


async def _handle_text_reply(event, client, owner_user_id, dashboard_url):
    """
    Handle text replies during an active construction description session.
    (Appointment selection, confirmation, etc.)
    """
    session = _active_sessions.get(owner_user_id)
    if not session:
        return  # No active session

    message = event.message
    if not message or not message.text:
        return

    text = message.text.strip().lower()
    token = session['token']
    state = session['state']
    chat = await event.get_chat()

    # ── Don't intercept normal commands ───────────────────────────────
    if text.startswith('/'):
        return

    try:
        # ── State: Awaiting confirmation (auto-matched) ──────────────
        if state == 'awaiting_confirmation':
            if text in ('ja', 'yes', 'ok', '✅', 'j', 'y', '1'):
                # Step 1: Approve locally
                success = approve_construction_description(token)
                if not success:
                    await client.send_message(chat, "❌ Fehler beim lokalen Speichern.")
                    del _active_sessions[owner_user_id]
                    return

                # Step 2: Push to CRM
                record_id = session.get('crm_record_id', '')
                description = session.get('description', '')
                if record_id and description:
                    crm_result = crm_update_description(int(record_id), description)
                    if crm_result.get('success'):
                        await client.send_message(
                            chat,
                            f"✅ **Baubeschreibung im CRM gespeichert!**\n"
                            f"Kunde: {session.get('customer_name', 'Unbekannt')}\n"
                            f"Record: #{record_id}\n"
                            f"Feld 137 aktualisiert ✅"
                        )
                    else:
                        await client.send_message(
                            chat,
                            f"⚠️ Lokal gespeichert, aber CRM-Update fehlgeschlagen:\n"
                            f"{crm_result.get('error', 'Unbekannter Fehler')}\n\n"
                            f"Bitte manuell im Dashboard prüfen:\n"
                            f"🔗 {session.get('dashboard_url', dashboard_url)}"
                        )
                else:
                    await client.send_message(
                        chat,
                        f"✅ **Lokal gespeichert!**\n"
                        f"Kunde: {session.get('customer_name', 'Unbekannt')}\n\n"
                        f"⚠️ Keine CRM Record-ID — bitte manuell zuordnen:\n"
                        f"🔗 {session.get('dashboard_url', dashboard_url)}"
                    )
                del _active_sessions[owner_user_id]

            elif text in ('nein', 'no', 'n', '🗑️', 'verwerfen', 'discard'):
                discard_construction_description(token)
                await client.send_message(chat, "🗑️ Baubeschreibung verworfen.")
                del _active_sessions[owner_user_id]

            elif text in ('edit', 'bearbeiten', '✏️', 'e'):
                await client.send_message(
                    chat,
                    f"✏️ Im Dashboard bearbeiten:\n🔗 {session.get('dashboard_url', dashboard_url)}"
                )
                del _active_sessions[owner_user_id]

            elif text in ('andere', 'other', 'wechseln', '🔄'):
                # Show site list
                sites = session.get('sites', [])
                if sites:
                    await client.send_message(chat, format_sites_as_buttons(sites))
                    session['state'] = 'awaiting_customer_selection'
                else:
                    await client.send_message(chat, "Kundenname eingeben:")
                    session['state'] = 'awaiting_manual_customer'

        # ── State: Awaiting customer selection (number) ───────────────
        elif state == 'awaiting_customer_selection':
            sites = session.get('sites', [])

            if text.isdigit():
                idx = int(text) - 1
                if 0 <= idx < len(sites):
                    site = sites[idx]
                    record_id = str(site.get('record_id', ''))
                    customer_name = site.get('customer_name', site.get('site_name', ''))

                    update_customer_match(
                        token=token,
                        crm_record_id=record_id,
                        customer_name=customer_name,
                        customer_address=site.get('address', ''),
                    )
                    success = approve_construction_description(
                        token=token,
                        customer_name=customer_name,
                        crm_record_id=record_id,
                    )

                    if success and record_id:
                        # Push to CRM
                        description = session.get('description', '')
                        crm_result = crm_update_description(int(record_id), description)
                        if crm_result.get('success'):
                            await client.send_message(
                                chat,
                                f"✅ **Baubeschreibung im CRM gespeichert!**\n"
                                f"Kunde: {customer_name}\n"
                                f"Record: #{record_id} ✅"
                            )
                        else:
                            await client.send_message(
                                chat,
                                f"⚠️ Lokal gespeichert, CRM-Update fehlgeschlagen:\n"
                                f"{crm_result.get('error', '')}\n"
                                f"🔗 {session.get('dashboard_url', dashboard_url)}"
                            )
                    elif success:
                        await client.send_message(
                            chat,
                            f"✅ **Lokal gespeichert!**\nKunde: {customer_name}\n"
                            f"⚠️ Keine CRM Record-ID für diese Baustelle"
                        )
                    else:
                        await client.send_message(chat, "❌ Fehler beim Speichern.")
                    del _active_sessions[owner_user_id]
                else:
                    await client.send_message(
                        chat,
                        f"❌ Ungültige Nummer. Bitte 1-{len(sites)} eingeben."
                    )
            elif text in ('nein', 'no', 'cancel', 'abbrechen'):
                discard_construction_description(token)
                await client.send_message(chat, "🗑️ Baubeschreibung verworfen.")
                del _active_sessions[owner_user_id]

        # ── State: Awaiting manual customer name ──────────────────────
        elif state == 'awaiting_manual_customer':
            if text in ('nein', 'no', 'cancel', 'abbrechen'):
                discard_construction_description(token)
                await client.send_message(chat, "🗑️ Baubeschreibung verworfen.")
                del _active_sessions[owner_user_id]
            else:
                # Treat the text as a customer name
                customer_name = message.text.strip()
                update_customer_match(
                    token=token,
                    crm_record_id='',
                    customer_name=customer_name,
                    customer_address='',
                )
                success = approve_construction_description(
                    token=token,
                    customer_name=customer_name,
                )
                if success:
                    await client.send_message(
                        chat,
                        f"✅ **Lokal gespeichert!**\n"
                        f"Kunde: {customer_name}\n\n"
                        f"⚠️ Manueller Name — bitte CRM-Zuordnung im Dashboard prüfen:\n"
                        f"🔗 {session.get('dashboard_url', dashboard_url)}"
                    )
                else:
                    await client.send_message(chat, "❌ Fehler beim Speichern.")
                del _active_sessions[owner_user_id]

    except Exception as e:
        print(f"❌ Text reply handler error: {e}")
        import traceback; traceback.print_exc()