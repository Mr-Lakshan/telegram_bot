"""
KNOWLEDGE BASE — Learn from Approved Answers
==============================================
Every approved/edited answer from Lothar gets saved in KB.
Next time similar question comes → KB match → instant answer (free, no AI cost).

Two types:
  - Customer-specific: "Wie wird der Boden gemacht?" → answer specific to Strozycki
  - Global: "Was ist DIN 18534?" → same answer for all groups

Uses OpenAI text-embedding-3-small for similarity matching (cheapest embedding model).
Fallback: keyword-based matching if embeddings unavailable.

Usage:
    kb = KnowledgeBase(db_path, openai_api_key)
    
    # Save approved answer
    kb.save_answer(question, answer, topic, chat_id, customer_name)
    
    # Search for similar question
    match = kb.find_answer(question, chat_id)
    if match:
        print(match['answer'])  # Reuse approved answer
"""

import os
import json
import sqlite3
import time
import hashlib
from typing import Dict, Optional, List
from datetime import datetime

# Centralized DB writer — permanent fix for "database is locked"
try:
    from bot.core.db_writer import db_writer
except ImportError:
    db_writer = None

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

# ── Config ──
EMBEDDING_MODEL = "text-embedding-3-small"  # Cheapest: $0.02/1M tokens
SIMILARITY_THRESHOLD_HIGH = 0.88   # Very similar → auto-use
SIMILARITY_THRESHOLD_LOW = 0.75    # Somewhat similar → use as context
MAX_KB_RESULTS = 5


class KnowledgeBase:
    """
    Stores approved answers and finds similar questions using embeddings.
    """

    def __init__(self, db_path: str = "bot_data.db", openai_api_key: str = ""):
        self.db_path = db_path
        self.api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.client = OpenAI(api_key=self.api_key) if HAS_OPENAI and self.api_key else None

        self._init_db()
        self._stats = {
            'saved': 0, 'matched': 0, 'no_match': 0,
            'auto_answered': 0, 'used_as_context': 0,
        }

        count = self._get_entry_count()
        print(f"✅ KnowledgeBase initialized ({count} entries)")

    def _get_db(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")  # 10 seconds wait on lock
        return conn

    def _init_db(self):
        """Create KB tables."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        c = conn.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            
            -- Question & Answer
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            
            -- Context
            topic TEXT DEFAULT '',
            intent TEXT DEFAULT '',
            classification_type TEXT DEFAULT '',
            
            -- Scope: customer-specific or global
            scope TEXT DEFAULT 'global',
            chat_id INTEGER DEFAULT 0,
            customer_name TEXT DEFAULT '',
            
            -- Source tracking
            source TEXT DEFAULT 'approval',
            approval_token TEXT DEFAULT '',
            approved_by TEXT DEFAULT 'lothar',
            
            -- Embedding for similarity search
            embedding TEXT DEFAULT '',
            
            -- Usage stats
            use_count INTEGER DEFAULT 0,
            last_used_at DATETIME,
            
            -- Timestamps
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            
            -- Active/deleted flag
            is_active INTEGER DEFAULT 1
        )
        """)

        c.execute("CREATE INDEX IF NOT EXISTS idx_kb_scope ON knowledge_base(scope, is_active)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_kb_chat ON knowledge_base(chat_id, is_active)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_kb_topic ON knowledge_base(topic, is_active)")

        conn.commit()
        conn.close()

    def _get_entry_count(self) -> int:
        try:
            conn = self._get_db()
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM knowledge_base WHERE is_active = 1")
            count = c.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    # ══════════════════════════════════════════════════════════════════════
    #  SAVE: Store approved answer in KB
    # ══════════════════════════════════════════════════════════════════════

    def save_answer(
        self,
        question: str,
        answer: str,
        topic: str = "",
        intent: str = "",
        classification_type: str = "",
        chat_id: int = 0,
        customer_name: str = "",
        approval_token: str = "",
        approved_by: str = "lothar",
    ) -> bool:
        """
        Save an approved answer to KB.
        Determines scope automatically:
          - Dynamic questions (address, phone, dates, drive) → customer-specific
          - Static questions (material, technique, process) → global
        """
        if not question or not answer:
            return False

        # Determine scope
        dynamic_intents = {
            'customer_address', 'customer_phone', 'customer_name',
            'customer_email', 'construction_date', 'construction_status',
            'drive_files', 'documents',
        }

        if classification_type == 'dynamic' or intent in dynamic_intents:
            scope = 'customer'
        else:
            scope = 'global'

        # Generate embedding
        embedding = self._get_embedding(question)
        embedding_json = json.dumps(embedding) if embedding else ''

        try:
            for attempt in range(5):
                try:
                    conn = self._get_db()
                    c = conn.cursor()

                    # Check for duplicate (same question + same scope/chat) — READ only
                    if scope == 'customer':
                        c.execute("""
                            SELECT id FROM knowledge_base
                            WHERE chat_id = ? AND question = ? AND is_active = 1
                        """, (chat_id, question))
                    else:
                        c.execute("""
                            SELECT id FROM knowledge_base
                            WHERE scope = 'global' AND question = ? AND is_active = 1
                        """, (question,))

                    existing = c.fetchone()
                    conn.close()  # Close read connection before write

                    if db_writer is not None:
                        # Use centralized writer (fire-and-forget) — no lock conflict
                        if existing:
                            db_writer.execute_nowait("""
                                UPDATE knowledge_base
                                SET answer = ?, embedding = ?, updated_at = ?,
                                    topic = ?, intent = ?, approved_by = ?
                                WHERE id = ?
                            """, (answer, embedding_json, datetime.now(),
                                  topic, intent, approved_by, existing['id']))
                            print(f"   📝 KB: Updated existing entry #{existing['id']}")
                        else:
                            db_writer.execute_nowait("""
                                INSERT INTO knowledge_base
                                (question, answer, topic, intent, classification_type, scope,
                                 chat_id, customer_name, approval_token, approved_by, embedding)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                question, answer, topic, intent, classification_type, scope,
                                chat_id, customer_name, approval_token, approved_by, embedding_json,
                            ))
                            print(f"   📝 KB: New {scope} entry saved (topic={topic})")
                        self._stats['saved'] += 1
                        return True
                    else:
                        # Fallback: own connection with retry
                        conn = self._get_db()
                        c = conn.cursor()
                        if existing:
                            c.execute("""
                                UPDATE knowledge_base
                                SET answer = ?, embedding = ?, updated_at = ?,
                                    topic = ?, intent = ?, approved_by = ?
                                WHERE id = ?
                            """, (answer, embedding_json, datetime.now(),
                                  topic, intent, approved_by, existing['id']))
                        else:
                            c.execute("""
                                INSERT INTO knowledge_base
                                (question, answer, topic, intent, classification_type, scope,
                                 chat_id, customer_name, approval_token, approved_by, embedding)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                question, answer, topic, intent, classification_type, scope,
                                chat_id, customer_name, approval_token, approved_by, embedding_json,
                            ))
                        conn.commit()
                        conn.close()
                        self._stats['saved'] += 1
                        return True

                except sqlite3.OperationalError as e:
                    if 'locked' in str(e) and attempt < 4:
                        import time
                        time.sleep(0.3 * (attempt + 1))
                        continue
                    raise

        except Exception as e:
            print(f"   ⚠️ KB save error: {e}")
            return False

    # ══════════════════════════════════════════════════════════════════════
    #  SEARCH: Find similar question in KB
    # ══════════════════════════════════════════════════════════════════════

    def find_answer(
        self,
        question: str,
        chat_id: int = 0,
        intent: str = "",
    ) -> Optional[Dict]:
        """
        Search KB for a similar question.
        
        Returns:
            {
                'answer': str,
                'similarity': float (0-1),
                'source_question': str,
                'scope': 'global' or 'customer',
                'use_count': int,
                'match_type': 'exact' | 'high' | 'low',
            }
            or None if no match
        """
        try:
            conn = self._get_db()
            c = conn.cursor()

            # Step 1: Try exact match first (free, no embedding needed)
            exact = self._find_exact(c, question, chat_id)
            if exact:
                self._increment_usage(conn, exact['id'])
                conn.close()
                self._stats['matched'] += 1
                self._stats['auto_answered'] += 1
                return exact

            # Step 2: Try embedding similarity search
            query_embedding = self._get_embedding(question)
            if query_embedding:
                similar = self._find_by_embedding(c, query_embedding, chat_id)
                if similar:
                    self._increment_usage(conn, similar['id'])
                    conn.close()
                    self._stats['matched'] += 1
                    if similar['similarity'] >= SIMILARITY_THRESHOLD_HIGH:
                        self._stats['auto_answered'] += 1
                    else:
                        self._stats['used_as_context'] += 1
                    return similar

            # Step 3: Keyword fallback
            keyword = self._find_by_keywords(c, question, chat_id)
            if keyword:
                self._increment_usage(conn, keyword['id'])
                conn.close()
                self._stats['matched'] += 1
                self._stats['used_as_context'] += 1
                return keyword

            conn.close()
            self._stats['no_match'] += 1
            return None

        except Exception as e:
            print(f"   ⚠️ KB search error: {e}")
            return None

    # ══════════════════════════════════════════════════════════════════════
    #  SEARCH METHODS
    # ══════════════════════════════════════════════════════════════════════

    def _find_exact(self, cursor, question: str, chat_id: int) -> Optional[Dict]:
        """Exact question match (case-insensitive)."""
        q_lower = question.strip().lower()

        # Customer-specific first
        if chat_id:
            cursor.execute("""
                SELECT * FROM knowledge_base
                WHERE LOWER(question) = ? AND chat_id = ? AND is_active = 1
            """, (q_lower, chat_id))
            row = cursor.fetchone()
            if row:
                return self._row_to_result(row, 1.0, 'exact')

        # Global fallback
        cursor.execute("""
            SELECT * FROM knowledge_base
            WHERE LOWER(question) = ? AND scope = 'global' AND is_active = 1
        """, (q_lower,))
        row = cursor.fetchone()
        if row:
            return self._row_to_result(row, 1.0, 'exact')

        return None

    def _find_by_embedding(self, cursor, query_embedding: list, chat_id: int) -> Optional[Dict]:
        """Cosine similarity search using embeddings."""

        # Get all active entries with embeddings
        cursor.execute("""
            SELECT * FROM knowledge_base
            WHERE is_active = 1 AND embedding != ''
            AND (scope = 'global' OR chat_id = ?)
        """, (chat_id,))

        best_match = None
        best_similarity = 0

        for row in cursor.fetchall():
            try:
                stored_embedding = json.loads(row['embedding'])
                similarity = self._cosine_similarity(query_embedding, stored_embedding)

                if similarity > best_similarity and similarity >= SIMILARITY_THRESHOLD_LOW:
                    best_similarity = similarity
                    best_match = row
            except (json.JSONDecodeError, TypeError):
                continue

        if best_match and best_similarity >= SIMILARITY_THRESHOLD_LOW:
            match_type = 'high' if best_similarity >= SIMILARITY_THRESHOLD_HIGH else 'low'
            return self._row_to_result(best_match, best_similarity, match_type)

        return None

    def _find_by_keywords(self, cursor, question: str, chat_id: int) -> Optional[Dict]:
        """Simple keyword overlap matching as fallback."""
        # Extract keywords (3+ char words)
        words = set(w.lower() for w in question.split() if len(w) >= 3)
        if not words:
            return None

        cursor.execute("""
            SELECT * FROM knowledge_base
            WHERE is_active = 1
            AND (scope = 'global' OR chat_id = ?)
        """, (chat_id,))

        best_match = None
        best_overlap = 0

        for row in cursor.fetchall():
            stored_words = set(w.lower() for w in row['question'].split() if len(w) >= 3)
            if not stored_words:
                continue

            overlap = len(words & stored_words) / max(len(words | stored_words), 1)
            if overlap > best_overlap and overlap >= 0.5:
                best_overlap = overlap
                best_match = row

        if best_match and best_overlap >= 0.5:
            return self._row_to_result(best_match, best_overlap, 'low')

        return None

    # ══════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════════

    def _row_to_result(self, row, similarity: float, match_type: str) -> Dict:
        return {
            'id': row['id'],
            'answer': row['answer'],
            'similarity': round(similarity, 3),
            'source_question': row['question'],
            'scope': row['scope'],
            'topic': row['topic'],
            'customer_name': row['customer_name'],
            'use_count': row['use_count'],
            'match_type': match_type,
        }

    def _increment_usage(self, conn, entry_id: int):
        try:
            if db_writer is not None:
                db_writer.execute_nowait("""
                    UPDATE knowledge_base
                    SET use_count = use_count + 1, last_used_at = ?
                    WHERE id = ?
                """, (datetime.now(), entry_id))
            else:
                conn.execute("""
                    UPDATE knowledge_base
                    SET use_count = use_count + 1, last_used_at = ?
                    WHERE id = ?
                """, (datetime.now(), entry_id))
                conn.commit()
        except Exception:
            pass

    def _get_embedding(self, text: str) -> Optional[list]:
        """Get embedding vector from OpenAI."""
        if not self.client:
            return None
        try:
            resp = self.client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=text.strip()[:1000],  # Truncate long texts
            )
            return resp.data[0].embedding
        except Exception as e:
            print(f"   ⚠️ Embedding error: {e}")
            return None

    def _cosine_similarity(self, a: list, b: list) -> float:
        """Calculate cosine similarity between two vectors."""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # ══════════════════════════════════════════════════════════════════════
    #  STATS
    # ══════════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        stats = self._stats.copy()
        stats['total_entries'] = self._get_entry_count()
        return stats

    def reset_stats(self):
        for key in self._stats:
            if key != 'total_entries':
                self._stats[key] = 0