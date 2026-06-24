#!/usr/bin/env python3
"""
Alternative Solution: Direct Database Queue
Instead of subprocess, this adds messages to database queue
that telegram_bot.py's worker picks up automatically
"""
import sqlite3
from datetime import datetime

DB = "bot_data.db"

def get_db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def queue_message(user_id, message):
    """Add message to outgoing queue for telegram_bot.py worker to send"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            INSERT INTO outgoing_messages (user_id, message, created_at)
            VALUES (?, ?, ?)
        """, (user_id, message, datetime.now()))
        conn.commit()
        conn.close()
        print("✅ Message queued successfully")
        return True
    except Exception as e:
        print(f"❌ Database error: {e}")
        return False

if __name__ == '__main__':
    import sys
    if len(sys.argv) != 3:
        print("Usage: python telegram_sender_queue.py <user_id> <message>")
        sys.exit(1)
    
    user_id = sys.argv[1]
    message = sys.argv[2]
    
    success = queue_message(user_id, message)
    sys.exit(0 if success else 1)