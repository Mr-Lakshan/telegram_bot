"""
KB SCORER — Filter system for knowledge base candidates
=========================================================
Lothar's spec: score each message 0-100, only score > 60 = KB candidate.

Scoring rules:
  +30  text contains "?"  (real question)
  +20  2-3+ work-related sentences (substance)
  +20  reusable/general (not customer-specific)
  -40  addressed to a person ("Hello Rohit", "@name")
  -30  pure chat phrase ("guten Morgen", "bis bald")
  -20  time/coordination question ("wann hast du Zeit")

Result: instead of 20 popups/day → only 2-3 real KB candidates.

Usage:
  from bot.knowledge.kb_scorer import KBScorer
  scorer = KBScorer()
  result = scorer.score("Wie wird der Estrich verlegt?")
  # → {'score': 80, 'is_candidate': True, 'reasons': [...]}
"""

import re

THRESHOLD = 60  # Only messages above this become KB candidates


class KBScorer:
    def __init__(self, threshold: int = THRESHOLD):
        self.threshold = threshold

        # Names that indicate a message is addressed to a person
        self.PERSON_NAMES = [
            'rohit', 'lothar', 'paulina', 'manisha', 'wojtek', 'preet',
            'mansha', 'profi', 'mr ', 'herr ', 'frau ',
        ]

        # Greeting / goodbye / chat phrases (DE + EN + PL)
        self.CHAT_PHRASES = [
            # German
            'guten morgen', 'guten tag', 'guten abend', 'gute nacht',
            'hallo', 'hi ', 'hey', 'servus', 'moin', 'tschüss', 'tschüs',
            'bis bald', 'bis später', 'bis dann', 'bis morgen',
            'danke', 'vielen dank', 'dankeschön', 'bitte schön',
            'schönen tag', 'schönes wochenende', 'gute fahrt', 'gute reise',
            'alles gute', 'viel erfolg', 'mach es gut', 'pass auf dich auf',
            'ok', 'okay', 'super', 'prima', 'perfekt', 'alles klar',
            'verstanden', 'in ordnung', 'gerne', 'kein problem',
            # English
            'good morning', 'good evening', 'good night', 'good day',
            'hello', 'hey there', 'see you', 'see you soon', 'bye',
            'goodbye', 'thanks', 'thank you', 'cheers', 'take care',
            'have a good', 'safe travels', 'best regards', 'regards',
            'sounds good', 'got it', 'understood', 'no problem',
            # Polish
            'dzień dobry', 'cześć', 'dziękuję', 'do widzenia', 'na razie',
            'dobranoc', 'pozdrawiam',
        ]

        # Time / coordination phrases
        self.TIME_COORD = [
            # German
            'wann hast du zeit', 'wann kannst du', 'wann bist du',
            'hast du zeit', 'wann treffen', 'wann machen wir',
            'um wie viel uhr', 'wie spät', 'welche uhrzeit', 'wann ist',
            'können wir uns', 'lass uns treffen', 'wann passt',
            # English
            'what time', 'when are you', 'when can you', 'when do you',
            'do you have time', 'are you free', 'let us meet', 'when shall',
            'when will you', 'when is the meeting',
            # Polish
            'kiedy masz czas', 'o której',
        ]

        # Work-related keywords (construction + business)
        self.WORK_KEYWORDS = [
            # Construction
            'fliesen', 'estrich', 'boden', 'dusche', 'bad', 'wand', 'rohr',
            'abdichtung', 'silikon', 'fuge', 'montage', 'installieren',
            'material', 'werkzeug', 'maschine', 'baustelle', 'kunde',
            'angebot', 'rechnung', 'aufmaß', 'gefälle', 'abfluss',
            # Process / business
            'prozess', 'regel', 'anleitung', 'schritt', 'methode', 'system',
            'anzeige', 'stellenanzeige', 'marketing', 'video', 'schnitt',
            'wie macht man', 'wie wird', 'wie funktioniert', 'wie erstellt',
            # English
            'how to', 'how do', 'how is', 'process', 'method', 'install',
            'material', 'tool', 'machine', 'tiles', 'floor', 'shower',
        ]

        # Customer-specific markers (makes it NOT reusable)
        self.CUSTOMER_MARKERS = [
            'bei kunde', 'beim kunden', 'kunde ', 'kundin ',
            'baustelle ', 'herr ', 'frau ', 'familie ',
            'diese baustelle', 'dieser kunde', 'auftrag nr', 'auftrag #',
            'at customer', 'this customer', 'this site',
        ]

    def score(self, text: str, sender_name: str = None) -> dict:
        """Score a message 0-100. Returns dict with score, is_candidate, reasons."""
        if not text or not text.strip():
            return {'score': 0, 'is_candidate': False, 'reasons': ['empty']}

        t = text.lower().strip()
        score = 0
        reasons = []

        # +30 — contains question mark
        if '?' in text:
            score += 30
            reasons.append('+30 has "?"')

        # +20 — 2-3+ work-related sentences (substance)
        sentences = [s for s in re.split(r'[.!?]+', text) if len(s.strip()) > 10]
        work_hits = sum(1 for kw in self.WORK_KEYWORDS if kw in t)
        if len(sentences) >= 2 and work_hits >= 1:
            score += 20
            reasons.append(f'+20 work content ({len(sentences)} sentences, {work_hits} keywords)')
        elif work_hits >= 2:
            score += 20
            reasons.append(f'+20 work content ({work_hits} keywords)')

        # +20 — reusable / general (not customer-specific)
        is_customer_specific = any(m in t for m in self.CUSTOMER_MARKERS)
        if not is_customer_specific and work_hits >= 1:
            score += 20
            reasons.append('+20 reusable/general')

        # -40 — addressed to a person
        addressed = False
        # Starts with greeting + name, or contains @, or names a person directly
        if '@' in text:
            addressed = True
        else:
            first_words = t[:25]
            for name in self.PERSON_NAMES:
                if name in first_words:
                    addressed = True
                    break
        if addressed:
            score -= 40
            reasons.append('-40 addressed to person')

        # -30 — pure chat phrase
        is_chat = False
        # If the message is short AND matches a chat phrase, or starts with one
        for phrase in self.CHAT_PHRASES:
            if t == phrase or t.startswith(phrase) or (len(t) < 40 and phrase in t):
                is_chat = True
                break
        if is_chat:
            score -= 30
            reasons.append('-30 chat phrase')

        # -20 — time/coordination question
        if any(p in t for p in self.TIME_COORD):
            score -= 20
            reasons.append('-20 time/coordination')

        # Clamp 0-100
        score = max(0, min(100, score))

        return {
            'score': score,
            'is_candidate': score > self.threshold,
            'reasons': reasons,
        }