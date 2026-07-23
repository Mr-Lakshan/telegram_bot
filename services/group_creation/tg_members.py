"""
tg_members.py — Robustes Hinzufügen/Entfernen von Gruppenmitgliedern
============================================================================
Ablage: telegram_bot_v2/services/group_creation/tg_members.py

WARUM DIESE DATEI EXISTIERT
---------------------------
Bisher wurden Mitglieder in EINEM einzigen InviteToChannelRequest hinzugefügt:

    await client(functions.channels.InviteToChannelRequest(channel=sg, users=user_entities))

Problem: schlägt EIN Nutzer fehl (z. B. UserPrivacyRestrictedError), scheitert
der GESAMTE Call — es wird NIEMAND hinzugefügt. Der Fehler wurde nur geloggt,
die Gruppe galt trotzdem als erfolgreich erstellt. Genau daher rührt der Zustand
"Nutzer ist in der Construction-Gruppe, aber nicht in der Baustart-Gruppe":
beide Gruppen bekommen unterschiedliche Nutzerlisten, also kann die eine
scheitern während die andere durchgeht.

Dieses Modul fügt jeden Nutzer EINZELN hinzu, fängt Fehler pro Nutzer ab,
respektiert FloodWait und meldet ein detailliertes Ergebnis zurück.

VERWENDUNG
----------
    import tg_members

    result = await tg_members.invite_users(client, chat_entity, ['@max', 123456789])
    # -> {'added': ['@max'], 'skipped': [], 'failed': [{'user':'123456789','error':'...','code':'CANNOT_RESOLVE'}]}

Rückgabe ist JSON-serialisierbar und kann direkt in die API-Antwort.
"""

import asyncio

from telethon import functions, types
from telethon import errors as tg_errors


# ── Konfiguration ────────────────────────────────────────────────────────
DEFAULT_DELAY   = 1.5    # Sekunden zwischen zwei Telegram-Calls
MAX_FLOOD_WAIT  = 120    # Länger als das warten wir nicht — Rest wird gemeldet


def _err_code(ex: Exception) -> str:
    """Kurzer, stabiler Fehlercode für das CRM-Log."""
    name = type(ex).__name__
    text = str(ex).upper()

    if isinstance(ex, tg_errors.FloodWaitError):        return 'FLOOD_WAIT'
    if 'PEERFLOOD' in name.upper() or 'PEER_FLOOD' in text: return 'PEER_FLOOD'
    if 'PRIVACY' in name.upper() or 'PRIVACY' in text:  return 'USER_PRIVACY_RESTRICTED'
    if 'NOTMUTUAL' in name.upper():                     return 'USER_NOT_MUTUAL_CONTACT'
    if 'ALREADYPARTICIPANT' in name.upper() or 'ALREADY' in text: return 'USER_ALREADY_PARTICIPANT'
    if 'NOTPARTICIPANT' in name.upper() or 'NOT_PARTICIPANT' in text: return 'USER_NOT_PARTICIPANT'
    if 'ADMINREQUIRED' in name.upper():                 return 'CHAT_ADMIN_REQUIRED'
    if 'CHANNELINVALID' in name.upper() or 'CHATINVALID' in name.upper(): return 'CHANNEL_INVALID'
    if 'TOOMUCH' in name.upper():                       return 'USER_CHANNELS_TOO_MUCH'
    if 'CANNOT FIND' in text or 'NO USER HAS' in text or 'CANNOT GET ENTITY' in text:
        return 'CANNOT_RESOLVE'
    return name


async def resolve_user(client, ident):
    """
    Einen Identifier (@username | numerische ID | +Telefonnummer) zu einer
    Telegram-Entity auflösen. Wirft bei Misserfolg.
    """
    s = str(ident).strip()
    if not s:
        raise ValueError('empty identifier')

    if s.lstrip('-').isdigit():
        # Numerische ID — funktioniert nur, wenn der Userbot den Nutzer "kennt"
        return await client.get_entity(types.PeerUser(int(s)))

    return await client.get_entity(s)


async def warm_entity_cache(client, limit: int = 400) -> None:
    """
    Dialoge laden, damit numerische User-IDs auflösbar werden.
    Fehler sind unkritisch — nur Trefferquote sinkt.
    """
    try:
        await client.get_dialogs(limit=limit)
    except Exception as ex:
        print(f"[WARN] entity cache warm-up failed: {ex}")


async def invite_entities(client, chat_entity, pairs, delay: float = DEFAULT_DELAY) -> dict:
    """
    Wie invite_users, aber für BEREITS aufgelöste Entities.

    Args:
        pairs: Liste von (label, entity) — label ist der ursprüngliche
               Identifier und wird nur fürs Logging/Reporting benutzt.

    Returns: {'added': [...], 'skipped': [...], 'failed': [...], 'flood_wait': int|None}
    """
    added, skipped, failed = [], [], []
    flood_wait = None
    pairs = list(pairs or [])

    for i, (label, entity) in enumerate(pairs):
        u = str(label)
        try:
            await client(functions.channels.InviteToChannelRequest(
                channel=chat_entity, users=[entity]
            ))
            print(f"[INFO] Added '{u}'")
            added.append(u)

        except tg_errors.FloodWaitError as ex:
            wait = int(getattr(ex, 'seconds', 0) or 0)
            print(f"[WARN] FloodWait {wait}s while adding '{u}'")
            if wait <= MAX_FLOOD_WAIT:
                await asyncio.sleep(wait + 1)
                try:
                    await client(functions.channels.InviteToChannelRequest(
                        channel=chat_entity, users=[entity]
                    ))
                    added.append(u)
                    continue
                except Exception as ex2:
                    failed.append({'user': u, 'error': str(ex2), 'code': _err_code(ex2)})
                    continue
            flood_wait = wait
            for label2, _ in pairs[i:]:
                failed.append({'user': str(label2), 'error': f'FloodWait {wait}s', 'code': 'FLOOD_WAIT'})
            break

        except Exception as ex:
            code = _err_code(ex)
            if code == 'USER_ALREADY_PARTICIPANT':
                skipped.append({'user': u, 'code': code})
            else:
                print(f"[WARN] Could not add '{u}': {ex}")
                failed.append({'user': u, 'error': str(ex), 'code': code})

        if delay:
            await asyncio.sleep(delay)

    return {'added': added, 'skipped': skipped, 'failed': failed, 'flood_wait': flood_wait}


async def invite_users(client, chat_entity, idents, delay: float = DEFAULT_DELAY) -> dict:
    """
    Fügt jeden Nutzer EINZELN hinzu.

    Returns:
        {
          'added':   ['@max', ...],
          'skipped': [{'user': ..., 'code': 'USER_ALREADY_PARTICIPANT'}],
          'failed':  [{'user': ..., 'error': '...', 'code': '...'}],
          'flood_wait': int | None    # Sekunden, falls abgebrochen
        }
    """
    added, skipped, failed = [], [], []
    flood_wait = None

    for i, ident in enumerate(idents or []):
        u = str(ident)
        try:
            entity = await resolve_user(client, ident)
        except Exception as ex:
            print(f"[WARN] Cannot resolve '{u}': {ex}")
            failed.append({'user': u, 'error': str(ex), 'code': _err_code(ex)})
            continue

        try:
            await client(functions.channels.InviteToChannelRequest(
                channel=chat_entity, users=[entity]
            ))
            print(f"[INFO] Added '{u}'")
            added.append(u)

        except tg_errors.FloodWaitError as ex:
            wait = int(getattr(ex, 'seconds', 0) or 0)
            print(f"[WARN] FloodWait {wait}s while adding '{u}'")
            if wait <= MAX_FLOOD_WAIT:
                await asyncio.sleep(wait + 1)
                try:
                    await client(functions.channels.InviteToChannelRequest(
                        channel=chat_entity, users=[entity]
                    ))
                    added.append(u)
                    continue
                except Exception as ex2:
                    failed.append({'user': u, 'error': str(ex2), 'code': _err_code(ex2)})
                    continue
            # Zu lange — Rest gar nicht erst versuchen
            flood_wait = wait
            failed.append({'user': u, 'error': f'FloodWait {wait}s', 'code': 'FLOOD_WAIT'})
            for rest in list(idents)[i + 1:]:
                failed.append({'user': str(rest), 'error': f'Übersprungen wegen FloodWait {wait}s',
                               'code': 'FLOOD_WAIT'})
            break

        except Exception as ex:
            code = _err_code(ex)
            if code == 'USER_ALREADY_PARTICIPANT':
                skipped.append({'user': u, 'code': code})
            else:
                print(f"[WARN] Could not add '{u}': {ex}")
                failed.append({'user': u, 'error': str(ex), 'code': code})

        if delay:
            await asyncio.sleep(delay)

    return {'added': added, 'skipped': skipped, 'failed': failed, 'flood_wait': flood_wait}


async def remove_users(client, chat_entity, idents, delay: float = DEFAULT_DELAY) -> dict:
    """Wie invite_users, nur entfernend (Kick + Unban, damit der Nutzer neu beitreten kann)."""
    removed, skipped, failed = [], [], []
    flood_wait = None

    for i, ident in enumerate(idents or []):
        u = str(ident)
        try:
            entity = await resolve_user(client, ident)
        except Exception as ex:
            failed.append({'user': u, 'error': str(ex), 'code': _err_code(ex)})
            continue

        try:
            await client(functions.channels.EditBannedRequest(
                channel=chat_entity,
                participant=entity,
                banned_rights=types.ChatBannedRights(until_date=None, view_messages=True)
            ))
            # Sofort entbannen -> Nutzer kann später wieder hinzugefügt werden
            try:
                await client(functions.channels.EditBannedRequest(
                    channel=chat_entity,
                    participant=entity,
                    banned_rights=types.ChatBannedRights(until_date=None, view_messages=False)
                ))
            except Exception:
                pass

            print(f"[INFO] Removed '{u}'")
            removed.append(u)

        except tg_errors.FloodWaitError as ex:
            wait = int(getattr(ex, 'seconds', 0) or 0)
            if wait <= MAX_FLOOD_WAIT:
                await asyncio.sleep(wait + 1)
                continue
            flood_wait = wait
            failed.append({'user': u, 'error': f'FloodWait {wait}s', 'code': 'FLOOD_WAIT'})
            for rest in list(idents)[i + 1:]:
                failed.append({'user': str(rest), 'error': f'Übersprungen wegen FloodWait {wait}s',
                               'code': 'FLOOD_WAIT'})
            break

        except Exception as ex:
            code = _err_code(ex)
            if code == 'USER_NOT_PARTICIPANT':
                skipped.append({'user': u, 'code': code})
            else:
                failed.append({'user': u, 'error': str(ex), 'code': code})

        if delay:
            await asyncio.sleep(delay)

    return {'removed': removed, 'skipped': skipped, 'failed': failed, 'flood_wait': flood_wait}
