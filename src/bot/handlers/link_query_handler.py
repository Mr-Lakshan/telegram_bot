"""
LINK QUERY HANDLER — Answer "Was ist der Registrierungs-Link?" type questions
==============================================================================
Companion to link_extractor.py. While LinkExtractor *captures* knowledge,
this module *detects retrieval requests* and pulls the matching answer.

It sits early in the AI pipeline (before the paid classifier/AI call) so that
a known question is answered instantly and for free.

Flow:
    handler = LinkQueryHandler(extractor)
    hit = handler.try_answer(message_text)
    if hit:
        # hit['answer'] is a ready-to-post string
        # send to group, then handler.confirm_used(hit)
        ...

Detection is intentionally conservative: it only fires when the message both
(a) looks like a question / request, and (b) names something we actually have.
This avoids hijacking normal conversation.
"""

import re
from typing import Dict, Optional


class LinkQueryHandler:
    def __init__(self, extractor):
        self.ext = extractor

        # Request signals — message must contain at least one to be a retrieval.
        # German + English. Kept broad but paired with a topic match below.
        self._request_signals = [
            # interrogatives
            'was ist', 'wo ist', 'wie lautet', 'wie ist', 'hast du', 'gibt es',
            'kannst du', 'wo finde', 'wo kann ich', 'welche', 'welcher', 'welches',
            'what is', 'where is', 'do you have', 'can you', 'where can', 'how do i',
            'how can i', 'send me', 'share the', 'whats the', "what's the",
            # nouns that imply "the link/the steps for X"
            'link', 'url', 'adresse', 'seite', 'portal',
            'schritte', 'ablauf', 'anleitung', 'prozess', 'steps', 'process',
            '?',  # any question mark
        ]

        # Topic anchors → map a question to a link_type so we can do a direct lookup.
        # Use short stems ('registr' matches register/registration/Registrierung).
        self._topic_to_type = [
            ('registration', ['regist', 'signup', 'sign up', 'anmeldung',
                              'konto erstell', 'neu anmeld', 'onboard', 'sign-up']),
            ('login',        ['login', 'log in', 'sign in', 'sign-in', 'einlogg', 'einloggen',
                              'logge', 'log dich', 'log mich', 'eingelogg',
                              'passwort', 'password', 'zugang', 'anmelden']),
            ('documentation',['doku', 'documentation', 'anleitung', 'guide', 'handbuch',
                              'manual', 'tutorial', 'wiki', 'how to']),
            ('invoice',      ['rechnung', 'invoice', 'zahlung', 'payment', 'beleg',
                              'billing']),
            ('form',         ['formular', 'form', 'antrag', 'apply', 'application']),
        ]

    # ════════════════════════════════════════════════════════════════════

    def _looks_like_request(self, text: str) -> bool:
        low = text.lower()
        return any(sig in low for sig in self._request_signals)

    def _topic_type(self, text: str) -> Optional[str]:
        low = text.lower()
        for link_type, kws in self._topic_to_type:
            if any(kw in low for kw in kws):
                return link_type
        return None

    # ════════════════════════════════════════════════════════════════════
    #  MAIN
    # ════════════════════════════════════════════════════════════════════

    def try_answer(self, message_text: str) -> Optional[Dict]:
        """
        If `message_text` is a request for a stored link/procedure/guideline,
        return a dict ready to post; otherwise None.

        Returns:
            {
              'answer'    : str,        # formatted, ready to send
              'matched'   : 'link'|'info',
              'link_type' : str|None,
              'url'       : str|None,   # for usage tracking
              'info_id'   : int|None,   # for usage tracking
              'confidence': 'high'|'medium',
            }
        """
        if not message_text or not message_text.strip():
            return None
        if not self._looks_like_request(message_text):
            return None

        # 1) Direct type lookup (strongest): "Registrierungs-Link?" → registration
        link_type = self._topic_type(message_text)
        if link_type:
            links = self.ext.by_category(link_type, limit=1)
            if links:
                top = links[0]
                answer = self.ext.format_answer({"links": [
                    {"url": top["url"], "type": link_type,
                     "description": top.get("description", ""),
                     "importance": top.get("importance", 5)}
                ], "info": [], "found": 1})
                if answer:
                    return {
                        "answer": answer, "matched": "link",
                        "link_type": link_type, "url": top["url"],
                        "info_id": None, "confidence": "high",
                    }

        # 2) Keyword search fallback (covers procedures + guidelines + general links)
        hits = self.ext.search(message_text, limit=3)
        if hits["found"] > 0:
            answer = self.ext.format_answer(hits)
            if answer:
                # pick first identifier for usage tracking
                url = hits["links"][0]["url"] if hits["links"] else None
                info_id = hits["info"][0]["id"] if hits["info"] else None
                return {
                    "answer": answer, "matched": "link" if url else "info",
                    "link_type": link_type, "url": url,
                    "info_id": info_id, "confidence": "medium",
                }

        return None

    def confirm_used(self, hit: Dict):
        """Bump usage counters after an answer is actually sent."""
        if not hit:
            return
        if hit.get("url"):
            self.ext.mark_used(url=hit["url"])
        elif hit.get("info_id") is not None:
            self.ext.mark_used(info_id=hit["info_id"])
