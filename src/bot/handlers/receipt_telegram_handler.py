#!/usr/bin/env python3
"""
RECEIPT PHOTO — TELEGRAM INTEGRATION (v2 with Album Support)
=============================================================
Supports:
- Single photo + "Bon" caption → scan immediately
- Album (multiple photos) + "Bon" on first photo → collect all, merge via CRM
"""

import asyncio
import os
import base64
import time
import requests
from telethon import events

CRM_BASE_URL = os.getenv("CRM_API_URL", "https://teampflegeinfo.de/crm/api/bot_construction.php")
CRM_API_KEY = os.getenv("CRM_BOT_API_KEY", "")
CRM_RECEIPT_URL = CRM_BASE_URL.replace("bot_construction.php", "telegram_receipt.php")
RECEIPT_KEYWORDS = ['bon', 'kassenbon', 'quittung', 'rechnung', 'receipt', '\U0001f9fe', 'einkauf', 'beleg']
ALBUM_WAIT_SECONDS = 3


def register_receipt_handler(user_client, bot_client=None):
    if not CRM_API_KEY:
        print("   \u26a0\ufe0f CRM_BOT_API_KEY not set \u2014 receipt handler DISABLED")
        return

    _processed = set()
    _lock = set()
    _albums = {}

    async def _send_single(chat, chat_id, message, sender_name, sender_id, photo_bytes):
        b64 = base64.b64encode(photo_bytes).decode('utf-8')
        full_id = chat_id if chat_id < 0 else int(f"-100{chat_id}")
        payload = {
            'api_key': CRM_API_KEY, 'chat_id': full_id,
            'image_base64': b64, 'sender_id': sender_id,
            'sender_name': sender_name or 'Telegram', 'message_id': message.id,
        }
        print(f"   \U0001f310 Sending to CRM: {CRM_RECEIPT_URL}")
        resp = requests.post(CRM_RECEIPT_URL, json=payload, timeout=90, headers={'X-Bot-Api-Key': CRM_API_KEY})
        data = resp.json()
        print(f"   \U0001f4e8 CRM response: success={data.get('success')}")
        return data, full_id

    async def _handle_reply(data, chat, msg_id, client, full_id, bare_id):
        c = client
        if data.get('success'):
            txt = data.get('reply_message', '\u2705 Kassenbon gespeichert!')
            try: await c.send_message(chat, txt, reply_to=msg_id)
            except:
                try: await user_client.send_message(chat, txt, reply_to=msg_id)
                except: pass
        elif 'duplicate' in str(data.get('error','')):
            try: await c.send_message(chat, f"\u26a0\ufe0f {data.get('message','Duplikat')}", reply_to=msg_id)
            except: pass
        elif 'No construction site' in str(data.get('error','')):
            print(f"   \u23ed\ufe0f Group not linked to site")
        else:
            print(f"   \u274c CRM error: {data.get('error','unknown')}")

    async def _process_album(gid):
        album = _albums.pop(gid, None)
        if not album or not album['photos']:
            return
        chat = album['chat']
        chat_id = album['chat_id']
        photos = album['photos']
        client = bot_client or user_client
        title = getattr(chat, 'title', '?')
        print(f"\n\U0001f4f8 [Album] Processing {len(photos)} photos from {title}")

        if len(photos) == 1:
            try:
                data, fid = await _send_single(chat, chat_id, album['first_msg'], album['sender_name'], album['sender_id'], photos[0])
                await _handle_reply(data, chat, album['first_msg_id'], client, fid, chat_id)
            except Exception as e:
                print(f"   \u274c Single: {e}")
            return

        # Notify
        try: status = await client.send_message(chat, f"\U0001f4f8 {len(photos)} Fotos empfangen. Scanne...", reply_to=album['first_msg_id'])
        except: status = None

        full_id = chat_id if chat_id < 0 else int(f"-100{chat_id}")
        all_items = []
        info = {'store_name':'','date':'','total':0,'tax':0,'payment_method':'','currency':'EUR'}
        site_name = ''
        rid = None
        errors = []

        for i, pb in enumerate(photos):
            print(f"   \U0001f4f7 Photo {i+1}/{len(photos)}...")
            try:
                b64 = base64.b64encode(pb).decode('utf-8')
                payload = {
                    'api_key': CRM_API_KEY, 'chat_id': full_id,
                    'image_base64': b64, 'sender_id': album['sender_id'],
                    'sender_name': album['sender_name'], 'message_id': album['first_msg_id'],
                    'multi_photo': True, 'photo_index': i, 'total_photos': len(photos),
                    'force': True if i > 0 else False,
                }
                r = requests.post(CRM_RECEIPT_URL, json=payload, timeout=90, headers={'X-Bot-Api-Key': CRM_API_KEY})
                d = r.json()
                if d.get('success'):
                    sd = d.get('scan_data', d.get('data', {}))
                    if not info['store_name'] and sd.get('store_name'): info['store_name'] = sd['store_name']
                    if not info['date'] and sd.get('date'): info['date'] = sd['date']
                    if sd.get('total',0) > info['total']: info['total'] = sd['total']; info['tax'] = sd.get('tax',0)
                    if sd.get('payment_method'): info['payment_method'] = sd['payment_method']
                    all_items.extend(sd.get('items', []))
                    if not site_name and d.get('site_name'): site_name = d['site_name']
                    if not rid and d.get('receipt_id'): rid = d['receipt_id']
                    print(f"   \u2705 Photo {i+1}: {len(sd.get('items',[]))} items")
                else:
                    errors.append(f"Foto {i+1}")
                    print(f"   \u26a0\ufe0f Photo {i+1}: {d.get('error','?')}")
            except Exception as e:
                errors.append(f"Foto {i+1}")
                print(f"   \u274c Photo {i+1}: {e}")

        # Dedup
        unique = []
        seen = set()
        for it in all_items:
            k = (it.get('name','').lower().strip(), f"{float(it.get('price',0)):.2f}")
            if k not in seen: seen.add(k); unique.append(it)

        t = info['total']
        ts = f"{t:,.2f}".replace(',','X').replace('.',',').replace('X','.') + '\u20ac'
        reply = f"\u2705 Kassenbon gespeichert! ({len(photos)} Fotos)\n\n"
        reply += f"\U0001f3ea {info['store_name'] or '?'}\n"
        if info['date']: reply += f"\U0001f4c5 {info['date']}\n"
        reply += f"\U0001f4b0 {ts}\n"
        if site_name: reply += f"\U0001f3d7 Baustelle: {site_name}\n"
        if unique:
            reply += f"\n\U0001f4e6 {len(unique)} Artikel"
            if len(all_items) > len(unique): reply += f" ({len(all_items)-len(unique)} Duplikate entfernt)"
            reply += "\n"
            for it in unique[:12]:
                n = it.get('name','?'); nd = it.get('name_de','')
                q = it.get('quantity',1); p = float(it.get('price',0))
                ps = f"{p:,.2f}".replace(',','X').replace('.',',').replace('X','.')
                line = f"  \u2022 {n}"
                if nd and nd != n: line += f" (\U0001f1e9\U0001f1ea {nd})"
                if q and q > 1: line += f" \u00d7{q}"
                line += f" \u2014 {ps}\u20ac"
                reply += line + "\n"
            if len(unique) > 12: reply += f"  ... +{len(unique)-12} weitere\n"
        if errors: reply += f"\n\u26a0\ufe0f {len(errors)} Foto(s) mit Problemen"
        if rid: reply += f"\n\U0001f517 ID: #{rid}"

        try:
            if status: await status.delete()
        except: pass
        try: await client.send_message(chat, reply, reply_to=album['first_msg_id'])
        except:
            try: await user_client.send_message(chat, reply, reply_to=album['first_msg_id'])
            except: pass

    @user_client.on(events.NewMessage())
    async def handler(event):
        try:
            msg = event.message
            if not event.is_group or not msg.photo:
                return

            caption = (msg.message or '').strip().lower()
            has_kw = any(kw in caption for kw in RECEIPT_KEYWORDS)
            gid = msg.grouped_id

            # ── Album photo ──
            if gid:
                if gid in _albums:
                    # Additional album photo — add to collection
                    try:
                        pb = await user_client.download_media(msg, bytes)
                        if pb and len(pb) >= 1000:
                            _albums[gid]['photos'].append(pb)
                            print(f"   \U0001f4f7 Album +photo ({len(_albums[gid]['photos'])} total)")
                    except: pass
                    return

                if not has_kw:
                    return

                # First album photo with keyword
                key = (event.chat_id, gid)
                if key in _processed: return
                _processed.add(key)

                chat = await event.get_chat()
                sender = await event.get_sender()
                sn = ((getattr(sender,'first_name','') or '') + ' ' + (getattr(sender,'last_name','') or '')).strip()

                try:
                    pb = await user_client.download_media(msg, bytes)
                    if not pb or len(pb) < 1000: return
                except: return

                print(f"\n\U0001f4f8 [Album] Start in {getattr(chat,'title','?')} (gid={gid})")
                _albums[gid] = {
                    'photos': [pb], 'chat_id': event.chat_id, 'chat': chat,
                    'sender_name': sn, 'sender_id': getattr(sender,'id',None),
                    'first_msg_id': msg.id, 'first_msg': msg,
                }

                async def timer():
                    await asyncio.sleep(ALBUM_WAIT_SECONDS)
                    if gid in _albums: await _process_album(gid)
                asyncio.ensure_future(timer())
                return

            # ── Single photo ──
            if not has_kw: return

            key = (event.chat_id, msg.id)
            if key in _processed or key in _lock: return
            if len(_processed) > 5000: _processed.clear()
            _lock.add(key); _processed.add(key)

            chat = await event.get_chat()
            sender = await event.get_sender()
            sn = ((getattr(sender,'first_name','') or '') + ' ' + (getattr(sender,'last_name','') or '')).strip()
            sid = getattr(sender,'id',None)

            print(f"\n\U0001f4f8 [Single] Receipt in {getattr(chat,'title','?')}")
            try:
                pb = await user_client.download_media(msg, bytes)
                if not pb or len(pb) < 1000: return
            except: return

            client = bot_client or user_client
            try:
                data, fid = await _send_single(chat, event.chat_id, msg, sn, sid, pb)
                await _handle_reply(data, chat, msg.id, client, fid, event.chat_id)
            except requests.exceptions.Timeout:
                await client.send_message(chat, "\u23f0 Timeout. Bitte im CRM-Scanner hochladen.", reply_to=msg.id)
            except Exception as e:
                print(f"   \u274c {e}")
            _lock.discard(key)

        except Exception as e:
            print(f"   \u274c Handler error: {e}")
            import traceback; traceback.print_exc()

    print(f"\u2705 Receipt handler registered (v2 with album support)")
    print(f"   \U0001f4f8 Single: 'Bon' caption \u2192 scan")
    print(f"   \U0001f4f7 Album: 'Bon' on first photo \u2192 collect {ALBUM_WAIT_SECONDS}s \u2192 merge")
    print(f"   \U0001f310 Endpoint: {CRM_RECEIPT_URL}")