#!/usr/bin/env python3
"""
TEST SCRIPT — Phase B Evening Suggestions
==========================================
Sends 2-3 sample KB suggestions to KI Freigaben with buttons.
The RUNNING bot handles the button taps (OK/NotOK/Revise/Explain).

Run INSIDE the container:
  docker cp test_phase_b.py telegram_bot:/app/
  docker exec -it telegram_bot python3 /app/test_phase_b.py

Two modes:
  1. Sample suggestions (default) — sends 3 fixed test suggestions
  2. Real data — pass 'real' to run the actual evening flow on today's chats
       docker exec -it telegram_bot python3 /app/test_phase_b.py real
"""

import asyncio
import os
import sys

from telethon import TelegramClient
from bot.config import (
    YOUR_API_ID, YOUR_API_HASH, BOT_TOKEN, OPENAI_API_KEY,
)

DB = "bot_data.db"
APPROVAL_CHAT_ID = int(os.getenv("APPROVAL_CHAT_ID", "0"))

from bot.knowledge.knowledge_base import KnowledgeBase
from bot.knowledge.kb_suggestion_manager import KBSuggestionManager


SAMPLE_LEARNINGS = [
    {
        'type': 'rule',
        'title': 'Estrich Trocknungszeit',
        'content': 'Estrich muss mindestens 28 Tage trocknen, bevor gefliest werden darf. Bei Schnellestrich kann diese Zeit kürzer sein.',
        'scope': 'global',
        'chat': 'Baustelle Test',
    },
    {
        'type': 'process',
        'title': 'Videoschnitt Ablauf',
        'content': 'Erst Rohschnitt erstellen, dann Feinschnitt mit Übergängen, danach Farbkorrektur, zum Schluss Export in 1080p.',
        'scope': 'global',
        'chat': 'Lothar & Manisha',
    },
    {
        'type': 'idea',
        'title': 'Avatar für Schulungsvideos',
        'content': 'Einen KI-Avatar erstellen, der Schulungsvideos für neue Mitarbeiter spricht — spart Zeit bei wiederkehrenden Erklärungen.',
        'scope': 'global',
        'chat': 'Lothar & Rohit',
    },
]


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'sample'

    print("🔌 Connecting bot client...")
    bot = TelegramClient("test_bot_session", YOUR_API_ID, YOUR_API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    print("✅ Bot connected")

    kb = KnowledgeBase(db_path=DB, openai_api_key=OPENAI_API_KEY)
    sm = KBSuggestionManager(
        db_path=DB, bot_client=bot, kb=kb, approval_chat_id=APPROVAL_CHAT_ID
    )

    if mode == 'real':
        # Run the ACTUAL evening flow on today's chats
        print("\n📊 Running REAL evening flow on today's conversations...")
        from bot.knowledge.conversation_learner import ConversationLearner
        cl = ConversationLearner(
            db_path=DB,
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            kb=kb,
            suggestion_manager=sm,
        )
        result = await cl.suggest_daily_learnings()
        print(f"\n✅ Result: {result.get('suggested', 0)} suggestions sent to KI Freigaben")
    else:
        # Send sample suggestions
        print(f"\n💡 Sending {len(SAMPLE_LEARNINGS)} sample suggestions to KI Freigaben...")
        sent = await sm.send_suggestions(SAMPLE_LEARNINGS)
        print(f"\n✅ {sent} suggestions sent!")
        print("👉 Open KI Freigaben in Telegram and tap the buttons to test:")
        print("   ✅ OK → saves to knowledge base")
        print("   ❌ Nicht OK → discards")
        print("   ✏️ Überarbeiten → marks for revision")
        print("   ❓ → shows detailed explanation")

    await asyncio.sleep(2)
    await bot.disconnect()
    print("\n✅ Test done. The RUNNING bot will handle your button taps.")


if __name__ == "__main__":
    asyncio.run(main())
