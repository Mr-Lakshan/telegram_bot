# """
# Telegram - Username se User ID nikalne ka Program
# =================================================
# Requirements:
#     pip install pyrogram tgcrypto

# Setup:
#     1. https://my.telegram.org pe jao
#     2. "API Development Tools" mein apni app banao
#     3. api_id aur api_hash copy karo
#     4. Neeche config mein paste karo
# """

# import asyncio
# import json
# import csv
# import os
# from datetime import datetime
# from pyrogram import Client
# from pyrogram.errors import UsernameNotOccupied, UsernameInvalid, FloodWait, PeerIdInvalid

# # ============================================================
# #                     CONFIG - YAHAN BHARO
# # ============================================================
# API_ID = 30719474               # apna api_id
# API_HASH = "97b9cb6a8ffe87f9d848a8a4d765bcfc"    # apna api_hash
# SESSION_NAME = "my_session"   # session file ka naam (kuch bhi)

# # Usernames list - jitne chahein add karo (without @)
# USERNAMES = [
#     "jane_ko_va"
#     # ... aur add karo
# ]

# # Output files
# OUTPUT_JSON = "users_data.json"
# OUTPUT_CSV  = "users_data.csv"

# # ============================================================


# async def get_user_info(app: Client, username: str) -> dict:
#     """Single username se user info fetch karo"""
#     try:
#         user = await app.get_users(username)
#         return {
#             "username":   username,
#             "user_id":    user.id,
#             "first_name": user.first_name or "",
#             "last_name":  user.last_name  or "",
#             "phone":      user.phone_number or "N/A",
#             "is_bot":     user.is_bot,
#             "is_verified":user.is_verified,
#             "status":     "found"
#         }

#     except UsernameNotOccupied:
#         print(f"  ❌ @{username} — username exist nahi karta")
#         return {"username": username, "user_id": None, "status": "not_found"}

#     except UsernameInvalid:
#         print(f"  ❌ @{username} — invalid username")
#         return {"username": username, "user_id": None, "status": "invalid"}

#     except PeerIdInvalid:
#         print(f"  ❌ @{username} — peer ID invalid")
#         return {"username": username, "user_id": None, "status": "peer_invalid"}

#     except FloodWait as e:
#         print(f"  ⏳ Flood wait! {e.value} seconds ruko...")
#         await asyncio.sleep(e.value)
#         return await get_user_info(app, username)  # retry

#     except Exception as e:
#         print(f"  ⚠️  @{username} — Error: {e}")
#         return {"username": username, "user_id": None, "status": f"error: {e}"}


# async def main():
#     print("=" * 50)
#     print("  Telegram Username → User ID Fetcher")
#     print("=" * 50)
#     print(f"  Total usernames: {len(USERNAMES)}")
#     print(f"  Started at: {datetime.now().strftime('%H:%M:%S')}")
#     print("=" * 50)

#     results = []
#     found = 0
#     not_found = 0

#     async with Client(SESSION_NAME, API_ID, API_HASH) as app:
#         print("\n✅ Telegram se connect ho gaya!\n")

#         for i, username in enumerate(USERNAMES, 1):
#             print(f"[{i}/{len(USERNAMES)}] Fetching @{username}...")

#             info = await get_user_info(app, username)
#             results.append(info)

#             if info["status"] == "found":
#                 found += 1
#                 print(f"  ✅ Found → ID: {info['user_id']} | Name: {info['first_name']} {info['last_name']}")
#             else:
#                 not_found += 1

#             # Flood se bachne ke liye thodi delay
#             await asyncio.sleep(0.5)

#     # ── Save to JSON ──────────────────────────────────────
#     with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
#         json.dump(results, f, indent=2, ensure_ascii=False)
#     print(f"\n💾 JSON saved: {OUTPUT_JSON}")

#     # ── Save to CSV ───────────────────────────────────────
#     with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
#         fieldnames = ["username", "user_id", "first_name", "last_name", "phone", "is_bot", "is_verified", "status"]
#         writer = csv.DictWriter(f, fieldnames=fieldnames)
#         writer.writeheader()
#         for row in results:
#             writer.writerow({k: row.get(k, "") for k in fieldnames})
#     print(f"💾 CSV saved:  {OUTPUT_CSV}")

#     # ── Summary ───────────────────────────────────────────
#     print("\n" + "=" * 50)
#     print(f"  ✅ Found:     {found}")
#     print(f"  ❌ Not Found: {not_found}")
#     print(f"  📊 Total:     {len(USERNAMES)}")
#     print("=" * 50)

#     # Sirf found users ki IDs print karo
#     print("\n📋 Found User IDs (copy ke liye):")
#     found_ids = [r["user_id"] for r in results if r["user_id"]]
#     print(found_ids)

#     return results


# if __name__ == "__main__":
#     asyncio.run(main())
# check_id.py - ek baar run karo
from telethon.sync import TelegramClient
from bot.config import YOUR_API_ID, YOUR_API_HASH, SESSION_STRING
from telethon.sessions import StringSession

with TelegramClient(StringSession(SESSION_STRING), YOUR_API_ID, YOUR_API_HASH) as client:
    async def main():
        async for dialog in client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                print(f"{dialog.name}: {dialog.id}")
    client.loop.run_until_complete(main())