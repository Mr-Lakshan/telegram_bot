#!/usr/bin/env python3
"""
SESSION MANAGER - DATABASE LOCKED FIX
=====================================
Problem: Multiple processes (telegram_bot_groups.py + app.py) open the
SAME user_session.session SQLite file simultaneously.
Telethon's SQLite session calls 'delete from sessions' on connect,
which causes OperationalError: database is locked.

Solution: Use StringSession for bot (no file) + separate session names
          for each process that needs a Telethon client.

HOW TO USE:
1. Run this script ONCE to export your session string:
   python session_manager.py export

2. Put the printed SESSION_STRING in your config.py or .env

3. Use StringSession in telegram_bot_groups.py (see fix below)
"""

import asyncio
import os
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession

# ── Load credentials from environment ──────────────────────
try:
    from bot.config import YOUR_API_ID, YOUR_API_HASH, YOUR_PHONE
except ImportError:
    YOUR_API_ID   = int(os.getenv('YOUR_API_ID', '0'))
    YOUR_API_HASH = os.getenv('YOUR_API_HASH', '')
    YOUR_PHONE    = os.getenv('YOUR_PHONE', '')


async def export_session_string():
    """
    Login once and export session as a string.
    After this, you never need the .session file again for the bot.
    """
    print("="*60)
    print("SESSION STRING EXPORTER")
    print("="*60)
    print("\nThis will login and give you a SESSION_STRING.")
    print("You only need to do this ONCE.\n")

    client = TelegramClient(StringSession(), YOUR_API_ID, YOUR_API_HASH)
    await client.start(phone=YOUR_PHONE)

    session_string = client.session.save()

    print("\n" + "="*60)
    print("✅ SUCCESS! Copy this line into your config.py or .env:")
    print("="*60)
    print(f"\nSESSION_STRING = \"{session_string}\"\n")
    print("="*60)
    print("\n⚠️  Keep this string SECRET — it gives full account access!")
    print("⚠️  Do NOT commit it to git!\n")

    await client.disconnect()
    return session_string


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "export":
        asyncio.run(export_session_string())
    else:
        print("Usage: python session_manager.py export")
