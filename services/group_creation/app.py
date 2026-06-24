"""
Flask Application - Telegram Group Manager (API Only)
Container: group_creator
Port: 5001
Session: user_session.session (mounted from host)

FIX: SESSION_PATH renamed to group_creator_session
     so it never conflicts with telegram_bot_groups.py's StringSession.
     app.py and bot now use COMPLETELY SEPARATE session sources.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from telethon import TelegramClient, functions, types, errors 
from telethon.tl.types import ChatBannedRights
from dotenv import load_dotenv
import asyncio
import os
import threading
import time

load_dotenv()

app = Flask(__name__)
CORS(app)

# ── Negative integer converter for Telegram chat IDs ──────
from werkzeug.routing import BaseConverter

class SignedIntConverter(BaseConverter):
    regex = r'-?\d+'
    def to_python(self, value): return int(value)
    def to_url(self, value):    return str(value)

app.url_map.converters['sint'] = SignedIntConverter

# ── Credentials ────────────────────────────────────────────
API_ID   = int(os.getenv('TELEGRAM_API_ID') or os.getenv('YOUR_API_ID', '0'))
API_HASH = os.getenv('TELEGRAM_API_HASH') or os.getenv('YOUR_API_HASH', '')
PHONE    = os.getenv('TELEGRAM_PHONE')    or os.getenv('YOUR_PHONE', '')
BOT_USERNAME = os.getenv('BOT_USERNAME', 'language_translator_lothar_bot')

if BOT_USERNAME and not BOT_USERNAME.startswith('@'):
    BOT_USERNAME = '@' + BOT_USERNAME

SESSION_PATH = os.getenv('SESSION_PATH', '/app/group_creator_session')

# ── Single persistent event loop ──────────────────────────
_loop   = asyncio.new_event_loop()
_client = None

def _start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

_bg_thread = threading.Thread(target=_start_loop, args=(_loop,), daemon=True)
_bg_thread.start()


def run_async(coro):
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=60)


async def _get_client():
    global _client
    if _client is None or not _client.is_connected():
        _client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
        await _client.connect()
        if not await _client.is_user_authorized():
            raise RuntimeError(
                "Not authorised. Session invalid — check group_creator_session.session mount."
            )
    return _client


# ══════════════════════════════════════════════════════════
#  HEALTH CHECK
# ══════════════════════════════════════════════════════════

@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'service': 'group_creator',
        'api_id': API_ID,
        'bot_username': BOT_USERNAME,
        'session': SESSION_PATH
    })


# ══════════════════════════════════════════════════════════
#  AUTH STATUS
# ══════════════════════════════════════════════════════════

@app.route('/api/auth/status')
def auth_status():
    async def _check():
        client = await _get_client()
        ok = await client.is_user_authorized()
        return ok
    try:
        ok = run_async(_check())
        return jsonify({'success': True, 'authenticated': ok})
    except Exception as e:
        return jsonify({'success': False, 'authenticated': False, 'error': str(e)})


# ══════════════════════════════════════════════════════════
#  CREATE GROUP
# ══════════════════════════════════════════════════════════

@app.route('/api/groups/create', methods=['POST'])
def create_group():
    data = request.json or {}

    title = data.get('title', '').strip()
    if not title:
        return jsonify({'success': False, 'error': 'title is required'}), 400

    about     = data.get('description', '').strip()
    raw_users = data.get('users', [])
    # Sprach-CODES (de/en/pl…) vom CRM — werden nach dem Erstellen per /setlang
    # in der Gruppe registriert (läuft im Bot-Prozess, kein Import nötig hier).
    raw_langs = data.get('languages', []) or []
    _setlangs = []
    for _l in (raw_langs if isinstance(raw_langs, list) else str(raw_langs).replace(';', ',').split(',')):
        _c = str(_l).strip().lower()
        if _c and _c not in _setlangs:
            _setlangs.append(_c)

    async def _create():
        client = await _get_client()

        # Warm the entity cache: lets numeric user-IDs of people already in the
        # userbot's existing dialogs/groups resolve. Cold/unknown IDs still fail.
        try:
            await client.get_dialogs(limit=200)
        except Exception as _wex:
            print(f"[WARN] get_dialogs warm-up failed: {_wex}")

        user_entities = []
        failed_users  = []
        for u in raw_users:
            try:
                # Integer ID — use InputPeerUser via get_input_entity
                if isinstance(u, int) or (isinstance(u, str) and u.lstrip('-').isdigit()):
                    uid = int(u)
                    entity = await client.get_entity(types.PeerUser(uid))
                else:
                    entity = await client.get_entity(str(u).strip())
                user_entities.append(entity)
            except Exception as ex:
                print(f"[WARN] Cannot resolve '{u}': {ex}")
                failed_users.append(str(u))

        if not user_entities:
            user_entities = [await client.get_me()]

        print(f"[INFO] Creating supergroup: '{title}'")
        result = await client(functions.channels.CreateChannelRequest(
            title=title,
            about=about,
            megagroup=True,
        ))

        chats_list = getattr(result, 'chats', None) or []
        if not chats_list:
            raise RuntimeError("CreateChannelRequest returned no chats.")

        sg = chats_list[0]
        print(f"[INFO] Supergroup created — id: {sg.id}")

        if user_entities:
            try:
                await client(functions.channels.InviteToChannelRequest(
                    channel=sg, users=user_entities
                ))
            except Exception as ex:
                print(f"[WARN] Could not add members: {ex}")

        try:
            print(f"[INFO] Adding bot {BOT_USERNAME}...")
            bot_user = await client.get_entity(BOT_USERNAME)
            try:
                await client(functions.channels.InviteToChannelRequest(channel=sg, users=[bot_user]))
            except Exception as inv_ex:
                print(f"[WARN] Bot invite: {inv_ex}")

            await asyncio.sleep(1)

            full_admin = types.ChatAdminRights(
                change_info=True, delete_messages=True, ban_users=True,
                invite_users=True, pin_messages=True, add_admins=True,
                manage_call=True, anonymous=True, manage_topics=True,
                post_messages=True, edit_messages=True, post_stories=True,
                edit_stories=True, delete_stories=True,
            )
            await client(functions.channels.EditAdminRequest(
                channel=sg, user_id=bot_user, admin_rights=full_admin, rank='Translation Bot'
            ))
            print("[INFO] Bot promoted to full admin.")
        except Exception as bot_ex:
            print(f"[WARN] Could not add/promote bot: {bot_ex}")

        # Sprachen per /setlang setzen: der Bot (admin in der Gruppe) verarbeitet
        # den Befehl in SEINEM Prozess und ruft add_special_group() auf — so wird
        # die Übersetzung registriert, ohne dass dieser Service special_group_setup
        # importieren muss. Mindestens 2 Sprachen nötig.
        if len(_setlangs) >= 2:
            try:
                await asyncio.sleep(2)  # Bot kurz Zeit geben, im Gruppen-Update aufzutauchen
                await client.send_message(sg, '/setlang ' + ' '.join(_setlangs))
                print(f"[INFO] Sent /setlang {' '.join(_setlangs)} to the group.")
            except Exception as sl_ex:
                print(f"[WARN] Could not send /setlang: {sl_ex}")

        invite_link = None
        try:
            link_result = await client(functions.messages.ExportChatInviteRequest(peer=sg))
            invite_link = link_result.link
        except Exception as link_ex:
            print(f"[WARN] Could not generate invite link: {link_ex}")

        return {
            'chat_id':     sg.id,
            'title':       title,
            'description': about,
            'invite_link': invite_link,
            'failed_users': failed_users,
        }

    try:
        result = run_async(_create())
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════
#  RENAME GROUP  ← NEW: called when baustart_datum changes
# ══════════════════════════════════════════════════════════

@app.route('/api/groups/rename', methods=['POST'])
def rename_group():
    data    = request.json or {}
    chat_id = data.get('chat_id')
    title   = (data.get('title') or '').strip()

    if not chat_id or not title:
        return jsonify({'success': False, 'error': 'chat_id and title are required'}), 400

    chat_id = int(chat_id)

    async def _rename():
        client = await _get_client()

        try:
            chat_entity = await client.get_entity(chat_id)
        except Exception:
            chat_entity = await client.get_entity(int(f"-100{abs(chat_id)}"))

        # title already same → skip, no ChatNotModifiedError
        if getattr(chat_entity, 'title', None) == title:
            print(f"[INFO] Group {chat_id} title already '{title}' — skip")
            return

        try:
            await client(functions.channels.EditTitleRequest(
                channel=chat_entity, title=title
            ))
        except errors.ChatNotModifiedError:
            print(f"[INFO] Group {chat_id} title unchanged — ok")
        print(f"[INFO] Group {chat_id} renamed to '{title}'")

    try:
        run_async(_rename())
        return jsonify({'success': True, 'data': {'chat_id': chat_id, 'title': title}})
    except Exception as e:
        print(f"[ERROR] rename_group: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ══════════════════════════════════════════════════════════
#  SEND & PIN MESSAGE
# ══════════════════════════════════════════════════════════

@app.route('/api/groups/send-pin-message', methods=['POST'])
def send_pin_message():
    data    = request.json or {}
    chat_id = data.get('chat_id')
    message = (data.get('message') or '').strip()

    if not chat_id or not message:
        return jsonify({'success': False, 'error': 'chat_id and message are required'}), 400

    chat_id = int(chat_id)

    async def _send_and_pin():
        client = await _get_client()

        # Resolve chat entity (try direct, then -100 prefix for supergroups)
        try:
            chat_entity = await client.get_entity(chat_id)
        except Exception:
            chat_entity = await client.get_entity(int(f"-100{abs(chat_id)}"))

        # Send the message
        sent = await client.send_message(chat_entity, message, link_preview=True)

        # Pin it (notify=False = silent pin, no notification)
        await client(functions.messages.UpdatePinnedMessageRequest(
            peer=chat_entity,
            id=sent.id,
            silent=False,   # sends a "message was pinned" notification
            unpin=False,
            pm_oneside=False,
        ))

        print(f"[INFO] Sent & pinned message (id={sent.id}) in chat {chat_id}")
        return sent.id

    try:
        msg_id = run_async(_send_and_pin())
        return jsonify({'success': True, 'data': {'chat_id': chat_id, 'message_id': msg_id}})
    except Exception as e:
        print(f"[ERROR] send_pin_message: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/groups/send-message', methods=['POST'])
def send_group_message():
    """Send a plain text message to a group/chat (NO pin). Body: {chat_id, message}"""
    data    = request.json or {}
    chat_id = data.get('chat_id')
    message = (data.get('message') or '').strip()
    if not chat_id or not message:
        return jsonify({'success': False, 'error': 'chat_id and message are required'}), 400
    chat_id = int(chat_id)

    async def _send():
        client = await _get_client()
        try:
            chat_entity = await client.get_entity(chat_id)
        except Exception:
            chat_entity = await client.get_entity(int(f"-100{abs(chat_id)}"))
        sent = await client.send_message(chat_entity, message, link_preview=False)
        print(f"[INFO] Sent message (id={sent.id}) to chat {chat_id}")
        return sent.id

    try:
        msg_id = run_async(_send())
        return jsonify({'success': True, 'data': {'chat_id': chat_id, 'message_id': msg_id}})
    except Exception as e:
        print(f"[ERROR] send_group_message: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500





@app.route('/api/groups/<sint:chat_id>/permissions', methods=['POST'])
def set_permissions(chat_id):
    data     = request.json or {}
    username = data.get('username', '').strip()
    perms    = data.get('permissions', {})

    if not username:
        return jsonify({'success': False, 'error': 'username is required'}), 400

    async def _set():
        client      = await _get_client()
        user        = await client.get_entity(username)
        chat_entity = await client.get_entity(chat_id)

        if isinstance(chat_entity, types.Chat):
            mg = await client(functions.messages.MigrateChatRequest(chat_id=chat_entity.id))
            chat_entity = next(
                (c for c in getattr(mg, 'chats', []) if getattr(c, 'megagroup', False)), None
            )
            if chat_entity is None:
                raise RuntimeError("Migration to supergroup failed.")

        rights = ChatBannedRights(
            until_date=None, view_messages=False,
            send_messages = not perms.get('send_messages', True),
            send_media    = not perms.get('send_media',    True),
            send_stickers = not perms.get('send_stickers', True),
            send_gifs     = not perms.get('send_stickers', True),
            send_games    = not perms.get('send_stickers', True),
            send_inline   = not perms.get('send_stickers', True),
            send_polls    = not perms.get('send_polls',    True),
            change_info   = not perms.get('change_info',   False),
            invite_users  = not perms.get('add_users',     False),
            pin_messages  = not perms.get('pin_messages',  False),
        )
        await client(functions.channels.EditBannedRequest(
            channel=chat_entity, participant=user, banned_rights=rights
        ))

    try:
        run_async(_set())
        return jsonify({'success': True, 'data': {'username': username, 'permissions': perms}})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════
#  PROMOTE ADMIN
# ══════════════════════════════════════════════════════════

@app.route('/api/groups/<sint:chat_id>/admins', methods=['POST'])
def promote_admin(chat_id):
    data     = request.json or {}
    username = data.get('username', '').strip()
    ar       = data.get('admin_rights', {})

    if not username:
        return jsonify({'success': False, 'error': 'username is required'}), 400

    async def _promote():
        client      = await _get_client()
        user        = await client.get_entity(username)
        chat_entity = await client.get_entity(chat_id)

        if isinstance(chat_entity, types.Chat):
            mg = await client(functions.messages.MigrateChatRequest(chat_id=chat_entity.id))
            chat_entity = next(
                (c for c in getattr(mg, 'chats', []) if getattr(c, 'megagroup', False)), None
            )
            if chat_entity is None:
                raise RuntimeError("Migration to supergroup failed.")

        rights = types.ChatAdminRights(
            change_info     = ar.get('change_info',     False),
            post_messages   = ar.get('post_messages',   False),
            edit_messages   = ar.get('edit_messages',   False),
            delete_messages = ar.get('delete_messages', False),
            ban_users       = ar.get('ban_users',       False),
            invite_users    = ar.get('invite_users',    False),
            pin_messages    = ar.get('pin_messages',    False),
            add_admins      = ar.get('add_admins',      False),
            manage_call     = ar.get('manage_call',     False),
        )
        await client(functions.channels.EditAdminRequest(
            channel=chat_entity, user_id=user, admin_rights=rights, rank='Admin'
        ))

    try:
        run_async(_promote())
        return jsonify({'success': True, 'data': {'username': username, 'admin_rights': ar}})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════
#  ADD MEMBER  (called from save_assignment.php)
# ══════════════════════════════════════════════════════════

async def _resolve_user_and_chat(client, user_raw, chat_id: int):
    """Resolve user entity (username or numeric ID) and chat entity."""
    user_str = str(user_raw).strip()
    try:
        if user_str.lstrip('-').isdigit():
            user_entity = await client.get_entity(types.PeerUser(int(user_str)))
        else:
            user_entity = await client.get_entity(user_str)
    except Exception as e:
        raise RuntimeError(f"Cannot resolve user '{user_raw}': {e}")

    try:
        chat_entity = await client.get_entity(chat_id)
    except Exception:
        chat_entity = await client.get_entity(int(f"-100{abs(chat_id)}"))

    return user_entity, chat_entity


@app.route('/api/groups/add-member', methods=['POST'])
@app.route('/api/groups/add-members', methods=['POST'])
def add_member():
    data    = request.json or {}
    chat_id = data.get('chat_id')
    # Support both single user (user/username) and array (users)
    users_raw = data.get('users') or ([data.get('user')] if data.get('user') else [])

    if not chat_id or not users_raw:
        return jsonify({'success': False, 'error': 'chat_id and user/users are required'}), 400

    chat_id = int(chat_id)

    async def _add():
        client = await _get_client()
        added, failed = [], []

        for u in users_raw:
            try:
                user_entity, chat_entity = await _resolve_user_and_chat(client, u, chat_id)
                await client(functions.channels.InviteToChannelRequest(
                    channel=chat_entity, users=[user_entity]
                ))
                print(f"[INFO] Added '{u}' to chat {chat_id}")
                added.append(str(u))
            except Exception as ex:
                print(f"[WARN] Could not add '{u}' to {chat_id}: {ex}")
                failed.append({'user': str(u), 'error': str(ex)})

        if failed and not added:
            raise RuntimeError('; '.join(f['error'] for f in failed))

        return {'added': added, 'failed': failed}

    try:
        result = run_async(_add())
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        print(f"[ERROR] add_member: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════
#  REMOVE MEMBER  (called from delete_assignment.php)
# ══════════════════════════════════════════════════════════

@app.route('/api/groups/remove-member', methods=['POST'])
@app.route('/api/groups/remove-members', methods=['POST'])
@app.route('/api/groups/kick', methods=['POST'])
def remove_member():
    data    = request.json or {}
    chat_id = data.get('chat_id')
    # Support user/users/username
    users_raw = data.get('users') or \
                ([data.get('user')]     if data.get('user')     else []) or \
                ([data.get('username')] if data.get('username') else [])

    if not chat_id or not users_raw:
        return jsonify({'success': False, 'error': 'chat_id and user/users are required'}), 400

    chat_id = int(chat_id)

    async def _remove():
        client = await _get_client()
        removed, failed = [], []

        for u in users_raw:
            try:
                user_entity, chat_entity = await _resolve_user_and_chat(client, u, chat_id)
                await client(functions.channels.EditBannedRequest(
                    channel=chat_entity,
                    participant=user_entity,
                    banned_rights=ChatBannedRights(until_date=None, view_messages=True)
                ))
                # Immediately unban so they can rejoin later if needed
                await client(functions.channels.EditBannedRequest(
                    channel=chat_entity,
                    participant=user_entity,
                    banned_rights=ChatBannedRights(until_date=None)
                ))
                print(f"[INFO] Removed '{u}' from chat {chat_id}")
                removed.append(str(u))
            except Exception as ex:
                print(f"[WARN] Could not remove '{u}' from {chat_id}: {ex}")
                failed.append({'user': str(u), 'error': str(ex)})

        if failed and not removed:
            raise RuntimeError('; '.join(f['error'] for f in failed))

        return {'removed': removed, 'failed': failed}

    try:
        result = run_async(_remove())
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        print(f"[ERROR] remove_member: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500



# ══════════════════════════════════════════════════════════
#  SEND DIRECT MESSAGE TO USER  (invite link delivery)
# ══════════════════════════════════════════════════════════

@app.route('/api/send-direct-message', methods=['POST'])
def send_direct_message():
    """
    Send a direct message to a Telegram user (by username or numeric ID).
    Body: { "user": "@username" or 123456789, "message": "text" }
    Uses the authenticated user session (not bot).
    """
    data    = request.json or {}
    user_raw = data.get('user')
    message  = (data.get('message') or '').strip()

    if not user_raw or not message:
        return jsonify({'success': False, 'error': 'user and message are required'}), 400

    async def _send():
        client = await _get_client()
        user_str = str(user_raw).strip()
        try:
            if user_str.lstrip('-').isdigit():
                entity = await client.get_entity(types.PeerUser(int(user_str)))
            else:
                entity = await client.get_entity(user_str)
        except Exception as e:
            raise RuntimeError(f"Cannot resolve user '{user_raw}': {e}")

        sent = await client.send_message(entity, message, link_preview=False)
        print(f"[INFO] Sent direct message to '{user_raw}' (msg_id={sent.id})")
        return {'message_id': sent.id}

    try:
        result = run_async(_send())
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        print(f"[ERROR] send_direct_message: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ══════════════════════════════════════════════════════════
#  DELETE GROUP
# ══════════════════════════════════════════════════════════

@app.route('/api/groups/delete', methods=['POST'])
def delete_group():
    """
    Permanently delete a Telegram supergroup/channel.
    Body: { "chat_id": -100xxxxxxxxxx }
    Uses the authenticated user session to call DeleteChannelRequest.
    """
    data    = request.json or {}
    chat_id = data.get('chat_id')

    if not chat_id:
        return jsonify({'success': False, 'error': 'chat_id is required'}), 400

    chat_id = int(chat_id)

    async def _delete():
        client = await _get_client()

        # Resolve entity — try direct, then with -100 prefix for supergroups
        try:
            chat_entity = await client.get_entity(chat_id)
        except Exception:
            chat_entity = await client.get_entity(int(f"-100{abs(chat_id)}"))

        await client(functions.channels.DeleteChannelRequest(channel=chat_entity))
        print(f"[INFO] Deleted Telegram group chat_id={chat_id}")

    try:
        run_async(_delete())
        return jsonify({'success': True, 'data': {'chat_id': chat_id, 'deleted': True}})
    except Exception as e:
        print(f"[ERROR] delete_group: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════
#  GET GROUP MEMBERS
# ══════════════════════════════════════════════════════════

@app.route('/api/groups/<sint:chat_id>/members', methods=['GET'])
def get_group_members(chat_id):
    """
    Fetch all participants of a Telegram supergroup/channel.
    Returns list of {user_id, first_name, last_name, username, phone, is_bot, is_admin}
    """
    async def _get_members():
        client = await _get_client()

        # Resolve entity — try direct, then with -100 prefix for supergroups
        try:
            chat_entity = await client.get_entity(chat_id)
        except Exception:
            chat_entity = await client.get_entity(int(f"-100{abs(chat_id)}"))

        participants = await client.get_participants(chat_entity)

        members = []
        for p in participants:
            members.append({
                'user_id':    p.id,
                'first_name': p.first_name or '',
                'last_name':  p.last_name  or '',
                'username':   p.username   or '',
                'phone':      getattr(p, 'phone', None) or '',
                'is_bot':     bool(p.bot),
                'is_admin':   bool(getattr(p, 'participant', None) and
                              hasattr(p.participant, 'admin_rights') and
                              p.participant.admin_rights is not None),
            })

        return {'members': members, 'total': len(members)}

    try:
        result = run_async(_get_members())
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        print(f"[ERROR] get_group_members: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════
#  SET GROUP PHOTO — upload existing file
# ══════════════════════════════════════════════════════════

@app.route('/api/groups/set-photo', methods=['POST'])
def set_group_photo():
    """
    Set a Telegram group's profile photo from an uploaded file.
    Accepts multipart form: chat_id (int) + photo (file).
    """
    chat_id = request.form.get('chat_id')
    photo   = request.files.get('photo')

    if not chat_id or not photo:
        return jsonify({'success': False, 'error': 'chat_id and photo file are required'}), 400

    chat_id = int(chat_id)

    import tempfile
    tmp_path = os.path.join(tempfile.gettempdir(), f'group_photo_{chat_id}.png')
    photo.save(tmp_path)

    try:
        _apply_group_photo(chat_id, tmp_path)
        return jsonify({'success': True, 'data': {'chat_id': chat_id, 'photo_set': True}})
    except Exception as e:
        print(f"[ERROR] set_group_photo: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        try: os.unlink(tmp_path)
        except Exception: pass


# ══════════════════════════════════════════════════════════
#  GENERATE & SET AVATAR ← NEW: Generate on VPS + set
# ══════════════════════════════════════════════════════════

@app.route('/api/groups/generate-avatar', methods=['POST'])
def generate_and_set_avatar():
    """
    Generate a professional worker group avatar and set it as group photo.
    Body JSON: { chat_id, initials, worker_names?, customer_name? }

    Called from CRM worker_groups.php after group creation.
    Avatar is generated server-side using Pillow (no PHP GD needed).
    """
    data = request.json or {}
    chat_id        = data.get('chat_id')
    initials       = data.get('initials', '')
    worker_names   = data.get('worker_names', [])
    customer_name  = data.get('customer_name', '')

    if not chat_id:
        return jsonify({'success': False, 'error': 'chat_id is required'}), 400

    chat_id = int(chat_id)

    # Build initials if not provided
    if not initials:
        from avatar_generator import build_initials
        initials = build_initials(worker_names, customer_name)

    # Generate avatar image
    try:
        from avatar_generator import generate_worker_avatar
        import tempfile
        tmp_path = os.path.join(tempfile.gettempdir(), f'wg_avatar_{chat_id}.png')
        # Use customer_name as color seed so same site = same color, different sites = different colors
        color_seed = customer_name or initials
        generate_worker_avatar(initials, tmp_path, color_seed=color_seed)
        print(f"[INFO] ✅ Avatar generated for chat_id={chat_id}: initials={initials}, color_seed={color_seed}")
    except Exception as e:
        print(f"[ERROR] Avatar generation failed: {e}")
        return jsonify({'success': False, 'error': f'Avatar generation failed: {e}'}), 500

    # Set as group photo
    try:
        _apply_group_photo(chat_id, tmp_path)
        return jsonify({
            'success': True,
            'data': {
                'chat_id': chat_id,
                'initials': initials,
                'photo_set': True,
            }
        })
    except Exception as e:
        print(f"[ERROR] Avatar set failed for chat_id={chat_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        try: os.unlink(tmp_path)
        except Exception: pass


def _apply_group_photo(chat_id: int, photo_path: str):
    """Upload a local image file and set it as a Telegram group's photo."""
    async def _set():
        client = await _get_client()
        try:
            chat_entity = await client.get_entity(chat_id)
        except Exception:
            chat_entity = await client.get_entity(int(f"-100{abs(chat_id)}"))

        uploaded = await client.upload_file(photo_path)
        await client(functions.channels.EditPhotoRequest(
            channel=chat_entity,
            photo=types.InputChatUploadedPhoto(file=uploaded)
        ))
        print(f"[INFO] Group photo applied for chat_id={chat_id}")

    run_async(_set())


# ══════════════════════════════════════════════════════════
#  MAPPINGS SYNC  ← NEW: CRM → VPS group pair sync
# ══════════════════════════════════════════════════════════

import sqlite3 as _sqlite3

_BOT_DB_PATH = os.getenv('DB_PATH', '/app/bot_data.db')

def _get_bot_db():
    """Connect to the bot's SQLite database."""
    conn = _sqlite3.connect(_BOT_DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _ensure_mappings_table(conn):
    """Create group_pair_mappings table if not exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_pair_mappings (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            source_chat_id        INTEGER NOT NULL,
            destination_chat_id   INTEGER NOT NULL,
            source_group_name     TEXT    DEFAULT NULL,
            source_topic_id       INTEGER DEFAULT NULL,
            destination_topic_id  INTEGER DEFAULT NULL,
            is_active             INTEGER NOT NULL DEFAULT 1,
            created_at            TEXT    DEFAULT NULL,
            updated_at            TEXT    DEFAULT NULL,
            UNIQUE(source_chat_id, destination_chat_id)
        )
    """)
    conn.commit()


@app.route('/api/mappings/sync', methods=['POST'])
def sync_mapping():
    """
    Receive a group pair mapping from CRM and save to SQLite.
    Body:
      action='add':    { source_chat_id, destination_chat_id, group_name, action:'add' }
      action='remove': { source_chat_id, action:'remove' }
    """
    data   = request.json or {}
    action = data.get('action', 'add')

    try:
        conn = _get_bot_db()
        _ensure_mappings_table(conn)

        if action == 'add':
            source_chat_id = data.get('source_chat_id')
            dest_chat_id   = data.get('destination_chat_id')
            group_name     = data.get('group_name', '')

            if not source_chat_id or not dest_chat_id:
                return jsonify({'success': False, 'error': 'source_chat_id and destination_chat_id required'}), 400

            conn.execute("""
                INSERT OR REPLACE INTO group_pair_mappings
                (source_chat_id, destination_chat_id, source_group_name, is_active, updated_at)
                VALUES (?, ?, ?, 1, datetime('now'))
            """, (int(source_chat_id), int(dest_chat_id), group_name))
            conn.commit()
            conn.close()

            print(f"[SYNC] ✅ Mapping added: {source_chat_id} → {dest_chat_id} ({group_name})")
            return jsonify({
                'success': True,
                'data': {
                    'action': 'add',
                    'source_chat_id': source_chat_id,
                    'destination_chat_id': dest_chat_id,
                    'group_name': group_name
                }
            })

        elif action == 'remove':
            source_chat_id = data.get('source_chat_id')
            if not source_chat_id:
                return jsonify({'success': False, 'error': 'source_chat_id required'}), 400

            conn.execute("""
                UPDATE group_pair_mappings
                SET is_active = 0, updated_at = datetime('now')
                WHERE source_chat_id = ?
            """, (int(source_chat_id),))
            conn.commit()
            conn.close()

            print(f"[SYNC] ✅ Mapping deactivated for source={source_chat_id}")
            return jsonify({'success': True, 'data': {'action': 'remove', 'source_chat_id': source_chat_id}})

        else:
            return jsonify({'success': False, 'error': f'Unknown action: {action}'}), 400

    except Exception as e:
        print(f"[ERROR] sync_mapping: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/mappings/list', methods=['GET'])
def list_mappings():
    """Return all active group pair mappings from SQLite."""
    try:
        conn = _get_bot_db()
        _ensure_mappings_table(conn)
        cursor = conn.execute("""
            SELECT source_chat_id, destination_chat_id, source_group_name,
                   source_topic_id, destination_topic_id, is_active, created_at
            FROM group_pair_mappings
            WHERE is_active = 1
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        conn.close()

        mappings = []
        for r in rows:
            mappings.append({
                'source_chat_id':      r[0],
                'destination_chat_id': r[1],
                'source_group_name':   r[2],
                'source_topic_id':     r[3],
                'destination_topic_id':r[4],
                'is_active':           r[5],
                'created_at':          r[6],
            })

        return jsonify({'success': True, 'data': mappings, 'count': len(mappings)})

    except Exception as e:
        print(f"[ERROR] list_mappings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════
#  SPECIAL GROUPS (translation languages)  ← NEW
#  CRM se call hota hai jab ek Gruppe N languages ke saath banti hai.
#  Registers chat_id → langs in bot_data.db, taaki translation bot ko
#  pata ho kin languages mein translate karna hai.
#  Reuses special_group_setup.py (same bot_data.db).
# ═══════════════════════════════════════════════════════════

def _load_sgs():
    """Import special_group_setup and point it at the SAME bot DB app.py uses.
    Tolerant of special_group_setup.py living next to app.py OR one level up
    (e.g. the parent telegram/ folder), so deployment layout doesn't matter."""
    import sys, os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    for _p in (_here, _os.path.dirname(_here)):
        if _p and _p not in sys.path:
            sys.path.insert(0, _p)
    import special_group_setup as sgs
    sgs.DB = _BOT_DB_PATH
    return sgs


def _norm_lang_list(langs):
    """Accept list or comma/semis string -> deduped lowercase code list."""
    if isinstance(langs, str):
        langs = [x for x in langs.replace(';', ',').split(',')]
    out = []
    for l in (langs or []):
        if l is None:
            continue
        l = str(l).strip().lower()
        if l and l not in out:
            out.append(l)
    return out


@app.route('/api/special-groups/register', methods=['POST'])
def register_special_group():
    """
    Body: { chat_id, langs:["de","en","pl"], name?, notes? }
    'languages' / 'group_name' aliases bhi accept karte hain.
    """
    data = request.json or {}
    chat_id = data.get('chat_id')
    langs   = data.get('langs', data.get('languages', []))
    name    = (data.get('name') or data.get('group_name') or '').strip()
    notes   = (data.get('notes') or '').strip()

    if chat_id is None:
        return jsonify({'success': False, 'error': 'chat_id is required'}), 400

    langs = _norm_lang_list(langs)
    if len(langs) < 2:
        return jsonify({'success': False,
                        'error': 'At least 2 different languages required for special mode'}), 400

    try:
        sgs = _load_sgs()
        bad = [l for l in langs if l not in sgs.VALID_LANGS]
        if bad:
            return jsonify({'success': False,
                            'error': f"Invalid language code(s): {', '.join(bad)}. "
                                     f"Valid: {', '.join(sgs.VALID_LANGS)}"}), 400

        ok = sgs.add_special_group(int(chat_id), langs, name=name, notes=notes)
        if not ok:
            return jsonify({'success': False, 'error': 'Could not register special group'}), 500

        print(f"[SPECIAL] ✅ Registered {chat_id} → {langs} ({name or 'Group ' + str(chat_id)})")
        return jsonify({'success': True, 'data': {
            'chat_id': int(chat_id),
            'langs': langs,
            'group_name': name or f'Group {chat_id}',
        }})
    except Exception as e:
        print(f"[ERROR] register_special_group: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/special-groups/remove', methods=['POST'])
def remove_special_group_ep():
    """Body: { chat_id }"""
    data = request.json or {}
    chat_id = data.get('chat_id')
    if chat_id is None:
        return jsonify({'success': False, 'error': 'chat_id is required'}), 400
    try:
        sgs = _load_sgs()
        sgs.remove_special_group(int(chat_id))
        return jsonify({'success': True, 'data': {'chat_id': int(chat_id)}})
    except Exception as e:
        print(f"[ERROR] remove_special_group_ep: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/special-groups/list', methods=['GET'])
def list_special_groups_ep():
    """Return all registered special groups (chat_id + langs)."""
    try:
        sgs = _load_sgs()
        sgs.init_special_groups_table()
        conn = sgs.get_db()
        c = conn.cursor()
        c.execute("SELECT chat_id, group_name, lang_1, lang_2, langs, created_at FROM special_groups")
        rows = c.fetchall()
        conn.close()
        out = []
        for chat_id, name, l1, l2, langs_json, created in rows:
            out.append({
                'chat_id': chat_id,
                'group_name': name,
                'langs': sgs._row_langs(l1, l2, langs_json),
                'created_at': created,
            })
        return jsonify({'success': True, 'data': out, 'count': len(out)})
    except Exception as e:
        print(f"[ERROR] list_special_groups_ep: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════
#  ERROR HANDLERS
# ══════════════════════════════════════════════════════════

@app.errorhandler(404)
def not_found(_):
    return jsonify({'success': False, 'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def server_error(_):
    return jsonify({'success': False, 'error': 'Internal server error'}), 500


# ══════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 55)
    print("🚀  Group Creator API")
    print("=" * 55)
    print(f"🔑  API_ID:   {API_ID}")
    print(f"🤖  Bot:      {BOT_USERNAME}")
    print(f"💾  Session:  {SESSION_PATH}   ← separate from bot!")
    print(f"📦  Bot DB:   {_BOT_DB_PATH}")
    print("─" * 55)
    print("📡  GET  /api/groups/<chat_id>/members")
    print("📡  POST /api/groups/create")
    print("📡  POST /api/groups/rename")
    print("📡  POST /api/groups/send-pin-message")
    print("📡  POST /api/groups/add-member (+ add-members)")
    print("📡  POST /api/send-direct-message")
    print("📡  POST /api/groups/remove-member (+ remove-members, kick)")
    print("📡  POST /api/groups/delete")
    print("📡  POST /api/groups/set-photo            ← NEW")
    print("📡  POST /api/groups/generate-avatar      ← NEW")
    print("📡  POST /api/mappings/sync               ← NEW")
    print("📡  GET  /api/mappings/list            ← NEW")
    print("❤️   GET  /api/health")
    print("🔐  GET  /api/auth/status")
    print("=" * 55)
    app.run(debug=False, host='0.0.0.0', port=5001, threaded=True)