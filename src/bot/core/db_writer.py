"""
CENTRALIZED DATABASE WRITER — Permanent fix for "database is locked"
=====================================================================
Single async write queue processed by ONE worker.
ALL database writes go through this → no concurrent write conflicts.

How it works:
  - One background worker processes writes sequentially from a queue
  - Each write request returns a future that resolves when done
  - SQLite never sees concurrent writes → no locks

Usage:
  from bot.core.db_writer import db_writer
  db_writer.init('bot_data.db')
  db_writer.start()  # in main(), after event loop starts

  # Async write (returns lastrowid):
  row_id = await db_writer.execute("INSERT INTO ... VALUES (?)", (val,))

  # Fire-and-forget (no wait):
  db_writer.execute_nowait("UPDATE ...", (val,))
"""

import asyncio
import sqlite3
from datetime import datetime


class DBWriter:
    def __init__(self):
        self.db_path = None
        self.queue = None
        self._worker_task = None
        self._started = False

    def init(self, db_path):
        self.db_path = db_path

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def start(self):
        if self._started:
            return
        self.queue = asyncio.Queue()
        self._worker_task = asyncio.create_task(self._worker())
        self._started = True
        print("✅ DBWriter started (single-writer queue — no more lock conflicts)")

    async def _worker(self):
        """Single worker — processes all writes sequentially on ONE persistent connection."""
        conn = self._get_conn()  # persistent — no per-write connection churn
        while True:
            query, params, future, many = await self.queue.get()
            result = None
            error = None
            # Retry on lock (other readers may briefly hold the DB)
            for attempt in range(5):
                try:
                    if many:
                        conn.executemany(query, params)
                    else:
                        cur = conn.execute(query, params)
                        result = cur.lastrowid
                    conn.commit()
                    error = None
                    break
                except sqlite3.OperationalError as e:
                    error = e
                    if 'locked' in str(e) and attempt < 4:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        await asyncio.sleep(0.3 * (attempt + 1))
                        continue
                    break
                except Exception as e:
                    error = e
                    break
            if future and not future.done():
                if error:
                    future.set_exception(error)
                else:
                    future.set_result(result)
            elif error:
                print(f"⚠️ DBWriter error (nowait): {error}")
            self.queue.task_done()

    async def execute(self, query, params=()):
        """Async write — waits for completion, returns lastrowid."""
        if not self._started:
            # Fallback: direct write if worker not started
            conn = self._get_conn()
            try:
                cur = conn.execute(query, params)
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()
        future = asyncio.get_event_loop().create_future()
        await self.queue.put((query, params, future, False))
        return await future

    def execute_nowait(self, query, params=()):
        """Fire-and-forget write — no waiting."""
        if not self._started or not self.queue:
            try:
                conn = self._get_conn()
                conn.execute(query, params)
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"⚠️ DBWriter nowait fallback error: {e}")
            return
        try:
            self.queue.put_nowait((query, params, None, False))
        except Exception as e:
            print(f"⚠️ DBWriter queue full: {e}")

    async def executemany(self, query, params_list):
        """Async bulk write."""
        if not self._started:
            conn = self._get_conn()
            try:
                conn.executemany(query, params_list)
                conn.commit()
            finally:
                conn.close()
            return
        future = asyncio.get_event_loop().create_future()
        await self.queue.put((query, params_list, future, True))
        return await future


# Global singleton
db_writer = DBWriter()