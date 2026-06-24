"""
MESSAGE PRE-FILTER — Zero-Cost Local Message Filter
=====================================================
Filters out messages that don't need AI processing:
  - Smalltalk (ok, danke, 👍, greetings)
  - Too short / emoji-only / sticker messages
  - Duplicate messages (same text within cooldown)
  - Media without text
  - Bot commands (already handled elsewhere)

Returns: True = process this message, False = skip it

IMPORTANT: This module only FILTERS. It does NOT modify any
existing message flow. If filter says "skip", the message is
still stored in DB by the existing system — just AI analysis skipped.

Usage in telegram_bot_groups.py:
    from bot.ai.message_prefilter import MessagePreFilter
    prefilter = MessagePreFilter()
    
    # In handle_incoming_message, before AI handler:
    if not prefilter.should_process(text, sender_id, chat_id):
        print("⏭️  Pre-filter: skipped (no AI needed)")
        return
"""

import re
import time
from typing import Optional


class MessagePreFilter:
    """
    Zero-cost local filter. No API calls, no DB calls.
    Pure Python logic to skip irrelevant messages.
    """

    def __init__(self, duplicate_cooldown: int = 300):
        """
        Args:
            duplicate_cooldown: Seconds within which same text = duplicate (default 5 min)
        """
        self.duplicate_cooldown = duplicate_cooldown
        self._recent_messages = {}  # key: (chat_id, text_hash) → timestamp
        self._stats = {
            'total': 0, 'passed': 0, 'filtered': 0,
            'reason_smalltalk': 0, 'reason_too_short': 0,
            'reason_emoji_only': 0, 'reason_duplicate': 0,
            'reason_greeting': 0, 'reason_command': 0,
            'reason_media_no_text': 0,
        }

        # ── Smalltalk words (exact match, lowercased) ──
        self.SMALLTALK = {
            # German
            'ok', 'okay', 'ja', 'nein', 'ne', 'jo', 'jap', 'nö',
            'danke', 'dankeschön', 'vielen dank', 'bitte', 'alles klar',
            'super', 'toll', 'prima', 'perfekt', 'genau', 'richtig',
            'gut', 'sehr gut', 'passt', 'alles gut', 'klar', 'stimmt',
            'achso', 'ach so', 'aha', 'hmm', 'hm', 'mhm',
            'top', 'nice', 'mega', 'läuft', 'geht klar', 'mach ich',
            'wird gemacht', 'erledigt', 'fertig', 'bin da', 'komme',
            'bin unterwegs', 'moment', 'gleich', 'sofort',
            # English
            'yes', 'no', 'yeah', 'yep', 'nope', 'sure', 'fine',
            'thanks', 'thank you', 'thx', 'ty', 'cool', 'nice',
            'great', 'awesome', 'alright', 'right', 'exactly',
            'got it', 'understood', 'noted', 'done', 'ok thanks',
            'sounds good', 'perfect', 'agreed', 'roger', 'copy',
            # Polish
            'tak', 'nie', 'dobrze', 'dzięki', 'dziękuję', 'jasne',
            'okej', 'spoko', 'dobra', 'rozumiem', 'zrobione', 'git',
            # Hindi
            'haan', 'nahi', 'theek', 'theek hai', 'accha', 'sahi',
            'ok hai', 'done', 'ho gaya', 'chal', 'acha',
        }

        # ── Greetings (start of message match) ──
        self.GREETING_PATTERNS = [
            # German
            r'^guten\s*(morgen|tag|abend|nacht)',
            r'^(hallo|hi|hey|moin|servus|grüß\s*gott|tschüss|tschüs|ciao|bis\s*(bald|morgen|dann))\b',
            r'^(schönen|guten)\s*(feierabend|abend|tag)',
            r'^(gute\s*nacht|schlaf\s*gut)',
            # English
            r'^(good\s*(morning|evening|night|afternoon)|hello|hi|hey|bye|goodbye|see\s*you)',
            # Polish
            r'^(cześć|hej|dzień\s*dobry|dobranoc|pa|do\s*widzenia)',
            # Hindi
            r'^(namaste|namaskar|good\s*morning|good\s*night)',
        ]
        self._greeting_re = re.compile(
            '|'.join(self.GREETING_PATTERNS), re.IGNORECASE
        )

        # ── Emoji-only pattern ──
        # Matches strings that contain ONLY emojis, spaces, and variation selectors
        self._emoji_only_re = re.compile(
            r'^[\U0001F600-\U0001F64F'   # emoticons
            r'\U0001F300-\U0001F5FF'      # symbols & pictographs
            r'\U0001F680-\U0001F6FF'      # transport & map
            r'\U0001F1E0-\U0001F1FF'      # flags
            r'\U0001F900-\U0001F9FF'      # supplemental symbols
            r'\U0001FA00-\U0001FA6F'      # chess symbols
            r'\U0001FA70-\U0001FAFF'      # symbols extended-A
            r'\U00002702-\U000027B0'      # dingbats
            r'\U0000FE00-\U0000FE0F'      # variation selectors
            r'\U0000200D'                  # zero width joiner
            r'\U00002600-\U000026FF'      # misc symbols
            r'\U0000231A-\U0000231B'      # watch, hourglass
            r'\U00002300-\U000023FF'      # misc technical
            r'\U0000203C-\U00003299'      # CJK symbols + enclosed
            r'\U0001F000-\U0001F02F'      # mahjong
            r'\U0001F0A0-\U0001F0FF'      # playing cards
            r'\s]+$'
        )

        print("✅ MessagePreFilter initialized")

    # ══════════════════════════════════════════════════════════════════════
    #  MAIN: Should this message be processed by AI?
    # ══════════════════════════════════════════════════════════════════════

    def should_process(
        self,
        text: Optional[str],
        sender_id: int = 0,
        chat_id: int = 0,
        is_group: bool = True,
    ) -> bool:
        """
        Returns True if message should go to AI pipeline.
        Returns False if it can be safely skipped.
        
        NOTE: This does NOT prevent the message from being stored
        in the database. Only AI analysis is skipped.
        """
        self._stats['total'] += 1

        # ── No text at all (media without caption) ──
        if not text or not text.strip():
            self._stats['filtered'] += 1
            self._stats['reason_media_no_text'] += 1
            return False

        clean = text.strip()

        # ── Bot commands (handled by their own handlers) ──
        if clean.startswith('/'):
            self._stats['filtered'] += 1
            self._stats['reason_command'] += 1
            return False

        # ── Too short (1-2 chars, unlikely a real question) ──
        if len(clean) <= 2:
            self._stats['filtered'] += 1
            self._stats['reason_too_short'] += 1
            return False

        # ── Emoji-only messages ──
        if self._emoji_only_re.match(clean):
            self._stats['filtered'] += 1
            self._stats['reason_emoji_only'] += 1
            return False

        # ── Exact smalltalk match ──
        lower = clean.lower().rstrip('.!,')
        if lower in self.SMALLTALK:
            self._stats['filtered'] += 1
            self._stats['reason_smalltalk'] += 1
            return False

        # ── Greeting-only messages ──
        # Only filter if message is SHORT (< 30 chars) — longer messages
        # starting with greeting might contain a real question
        if len(clean) < 30 and self._greeting_re.match(clean):
            self._stats['filtered'] += 1
            self._stats['reason_greeting'] += 1
            return False

        # ── Duplicate check (same sender + same text within cooldown) ──
        if chat_id and sender_id:
            msg_key = (chat_id, sender_id, hash(lower))
            now = time.time()

            # Clean old entries
            cutoff = now - self.duplicate_cooldown
            self._recent_messages = {
                k: v for k, v in self._recent_messages.items() if v > cutoff
            }

            if msg_key in self._recent_messages:
                self._stats['filtered'] += 1
                self._stats['reason_duplicate'] += 1
                return False

            self._recent_messages[msg_key] = now

        # ── Passed all filters → process this message ──
        self._stats['passed'] += 1
        return True

    # ══════════════════════════════════════════════════════════════════════
    #  STATS: For daily report
    # ══════════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        """Return filter statistics. Useful for daily report."""
        stats = self._stats.copy()
        if stats['total'] > 0:
            stats['filter_rate'] = round(stats['filtered'] / stats['total'] * 100, 1)
        else:
            stats['filter_rate'] = 0.0
        return stats

    def reset_stats(self):
        """Reset stats (call after daily report)."""
        for key in self._stats:
            self._stats[key] = 0