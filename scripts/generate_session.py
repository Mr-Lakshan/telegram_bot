#!/usr/bin/env python3
"""
STRING SESSION GENERATOR
Sirf ek baar run karo — session string copy karo config.py mein paste karo
Uske baad kabhi login code nahi maangega
"""

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

# Apna config.py se same values
from bot.config import YOUR_API_ID, YOUR_API_HASH, YOUR_PHONE

print("=" * 60)
print("📱 STRING SESSION GENERATOR")
print("=" * 60)
print("Ye sirf ek baar run karna hai!")
print("Phone number pe OTP aayega, enter karo\n")

with TelegramClient(StringSession(), YOUR_API_ID, YOUR_API_HASH) as client:
    client.start(phone=YOUR_PHONE)
    session_string = client.session.save()

print("\n" + "=" * 60)
print("✅ SESSION STRING GENERATED!")
print("=" * 60)
print("\nYe string copy karo aur config.py mein paste karo:\n")
print(f"SESSION_STRING = \"{session_string}\"")
print("\n" + "=" * 60)
print("⚠️  Ye string secret rakho — kisi ko mat batao!")
print("=" * 60)
