-- ============================================================
-- WORKER SITE GROUPS — VPS SQLite Migration
-- Run this on your VPS inside the telegram_bot container
--
-- Command:
--   docker exec -it telegram_bot sqlite3 /app/bot_data.db < vps_migration.sql
--
-- Or manually:
--   docker exec -it telegram_bot bash
--   sqlite3 bot_data.db
--   (paste these queries)
-- ============================================================

-- ─────────────────────────────────────────────────────────────
-- TABLE: group_pair_mappings
-- Synced from CRM MySQL → VPS SQLite
-- Bot reads this at runtime for dynamic source↔dest routing
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS group_pair_mappings (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    source_chat_id        INTEGER NOT NULL,       -- Worker group Telegram chat_id
    destination_chat_id   INTEGER NOT NULL,       -- Baustart group Telegram chat_id
    source_group_name     TEXT    DEFAULT NULL,    -- e.g. "Wojtek at Schulz"
    source_topic_id       INTEGER DEFAULT NULL,
    destination_topic_id  INTEGER DEFAULT NULL,
    is_active             INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT    DEFAULT (datetime('now')),
    updated_at            TEXT    DEFAULT (datetime('now')),

    UNIQUE(source_chat_id, destination_chat_id)
);

-- Index for fast lookups by source or destination
CREATE INDEX IF NOT EXISTS idx_gpm_source      ON group_pair_mappings(source_chat_id);
CREATE INDEX IF NOT EXISTS idx_gpm_destination  ON group_pair_mappings(destination_chat_id);
CREATE INDEX IF NOT EXISTS idx_gpm_active       ON group_pair_mappings(is_active);


-- ─────────────────────────────────────────────────────────────
-- VERIFICATION
-- ─────────────────────────────────────────────────────────────
-- Check existing tables
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;

-- Check group_pair_mappings structure
PRAGMA table_info(group_pair_mappings);
