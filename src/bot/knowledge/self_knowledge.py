"""
SELF KNOWLEDGE — der Bot erklärt seine EIGENEN Fähigkeiten
===========================================================
Wenn jemand in KI Freigaben fragt „Was kann der Bot? / Wie funktioniert <Feature>?",
antwortet der Bot aus einer kuratierten Funktionsliste (Registry).

Günstig:
  • Erkennung = reine Keyword-Prüfung (KEINE AI-Kosten)
  • Antwort   = gpt-4o-mini aus der Registry (sehr günstig), in der Sprache der Frage

Neues Feature? → einfach einen Eintrag in CAPABILITIES ergänzen.
"""

import os
from openai import OpenAI

SELF_KNOWLEDGE_MODEL = os.getenv("SELF_KNOWLEDGE_MODEL", "gpt-4o-mini")

# ── Kuratierte Funktionsliste (Quelle der Wahrheit) ──────────────────────────
CAPABILITIES = [
    {"name": "Übersetzung in Gruppen",
     "keys": ["übersetz", "translat", "sprache", "language", "polnisch", "polski"],
     "desc": "Übersetzt Gruppen-Nachrichten automatisch in mehrere Sprachen (z. B. Deutsch ↔ Polnisch) und postet die Übersetzung über den Bot. Mit /setlang kann eine Gruppe auf bestimmte Sprachen festgelegt werden."},

    {"name": "KI-Antworten mit Freigabe (Approval)",
     "keys": ["freigabe", "approval", "antwort vorschlag", "genehmig", "vorschlag"],
     "desc": "Mitarbeiterfragen in Gruppen werden erkannt, die KI erstellt einen Antwortvorschlag, der zur Freigabe in die KI-Freigaben-Gruppe geschickt wird. Lothar genehmigt/bearbeitet, dann wird die Antwort gesendet."},

    {"name": "Wissensdatenbank (lernend)",
     "keys": ["wissensdatenbank", "knowledge base", "kb", "lernt", "merkt"],
     "desc": "Genehmigte Antworten werden gespeichert und wiederverwendet. Bewährte Antworten (mehrfach bestätigt) werden automatisch direkt in der Gruppe beantwortet — spart Zeit und Kosten."},

    {"name": "SOP-Verwaltung",
     "keys": ["sop", "regel", "prozess", "checkliste", "anleitung", "ablauf"],
     "desc": "Mit Präfixen (Regel:, Idee:, Prozess: …) oder per Sprachnachricht in KI Freigaben können Standard-Abläufe gespeichert werden. Wenn jemand danach fragt, sendet der Bot die passende SOP automatisch."},

    {"name": "Link- & Info-Extraktion",
     "keys": ["link", "info extraktion", "gespeicherte info", "anleitung gespeichert"],
     "desc": "Aus den Dev-/Admin-Gruppen werden Links, nummerierte Abläufe und Hinweise automatisch gespeichert und später in KI Freigaben sofort (kostenlos) beantwortet."},

    {"name": "Baufortschritt (Foto-Analyse)",
     "keys": ["baufortschritt", "fortschritt", "foto analyse", "bilder analyse", "/fortschritt"],
     "desc": "Täglich um 19:05 analysiert die KI die Fotos der Baustellen-Gruppen und schreibt eine kurze deutsche Zusammenfassung (was heute gemacht wurde). Manuell per /fortschritt auslösbar."},

    {"name": "Gesprächsanalyse (Kundengespräche)",
     "keys": ["gesprächsanalyse", "anruf", "gespräch", "telefonat", "call", "verkaufsgespräch"],
     "desc": "Eine Audio-/Video-Aufnahme eines Kundengesprächs (in KI Freigaben hochladen → Button 'Gespräch analysieren', oder im CRM-Dashboard) wird transkribiert und analysiert: überflüssige Fragen, Kundenreaktionen, Verbesserungsvorschläge."},

    {"name": "Bautagebuch (Video/Sprache → Doku)",
     "keys": ["bautagebuch", "baudoku", "video", "sprachnachricht baustelle", "transkri"],
     "desc": "Video-/Sprachnachrichten in der Bautagebuch-Gruppe werden per Whisper transkribiert, zu einer strukturierten Baudokumentation verarbeitet, dem richtigen Kunden (über den Kalender) zugeordnet und nach Freigabe ins CRM gespeichert."},

    {"name": "Beleg-Scanner",
     "keys": ["beleg", "bon", "quittung", "receipt", "rechnung scannen"],
     "desc": "Ein Foto mit der Bildunterschrift 'Bon' wird gescannt, die Ausgabedaten erkannt und ins CRM übertragen (auch als Foto-Album)."},

    {"name": "Foto-Sync zu Google Drive",
     "keys": ["drive", "google drive", "foto sync", "bilder speichern"],
     "desc": "Fotos aus den Baustellen-Gruppen werden automatisch in den passenden Google-Drive-Ordner des Kunden synchronisiert."},

    {"name": "Täglicher Bericht",
     "keys": ["täglicher bericht", "daily report", "tagesbericht", "19:00", "abendbericht"],
     "desc": "Jeden Abend um 19:00 erstellt die KI eine Zusammenfassung des Tages (offene Fragen, Aktivität, Vorschläge) und postet sie in KI Freigaben."},

    {"name": "Ausgaben-Bericht",
     "keys": ["ausgaben", "ausgabenbericht", "kosten bericht", "spesen"],
     "desc": "Abends wird ein Ausgaben-/Kategorienbericht aus dem CRM in KI Freigaben gepostet."},

    {"name": "Lead-Quellen-Tracking",
     "keys": ["lead", "leadquelle", "lead source", "neue anfrage"],
     "desc": "Alle 30 Minuten werden neue Leads geprüft und deren Quelle erfasst/gemeldet."},

    {"name": "Antworten über Gruppen hinweg",
     "keys": ["cross group", "antwort weiterleiten", "quote reply", "zielgruppe"],
     "desc": "Eine Antwort in einer Zielgruppe wird automatisch (mit Übersetzung) in die ursprüngliche Quellgruppe zurückgeroutet."},

    {"name": "Vorlesen (Text-to-Speech)",
     "keys": ["vorlesen", "tts", "sprachausgabe", "audio antwort", "🔊"],
     "desc": "Über den 🔊-Button kann eine Nachricht/Übersetzung als Sprachausgabe vorgelesen werden (Stimme 'Fable')."},

    {"name": "Direkte KI-Fragen in KI Freigaben",
     "keys": ["frage stellen", "ki frage", "direkt fragen", "assistent fragen"],
     "desc": "Lothar kann in KI Freigaben direkt Fragen stellen — beantwortet aus der Wissensdatenbank oder per KI (ohne Freigabe)."},
]

# ── Erkennung (kostenlos, nur Keywords) ──────────────────────────────────────
_SELF_REF = ["bot", "system", "assistent", "assistant", "ki-system", "programm",
             "kannst du", "was kannst", "was machst", "what can you", "you do", "dieses tool", "das tool"]
_CAP_REF  = ["kann", "kannst", "fähig", "funktion", "funktionen", "feature", "features",
             "leist", "möglich", "able to", "do you do", "capabilit", "was tust", "was machst"]
_QWORD    = ["wie", "was", "welche", "erklär", "erkläre", "how", "what", "which", "explain", "zeig"]
_FEATURE_KEYS = [k for c in CAPABILITIES for k in c["keys"]]


def is_capability_question(text: str) -> bool:
    """True, wenn die Frage sich auf die Fähigkeiten des Bots/Systems bezieht."""
    if not text:
        return False
    t = text.lower()
    self_ref = any(k in t for k in _SELF_REF)
    cap_ref  = any(k in t for k in _CAP_REF)
    feat_ref = any(k in t for k in _FEATURE_KEYS)
    qword    = any(k in t for k in _QWORD)
    # (a) "Was kann der Bot?"  oder  (b) "Wie funktioniert <Feature>?"
    return (self_ref and cap_ref) or (feat_ref and qword)


class SelfKnowledge:
    def __init__(self, openai_api_key: str = ""):
        self.openai = OpenAI(api_key=openai_api_key or os.getenv("OPENAI_API_KEY", ""))
        self.model = SELF_KNOWLEDGE_MODEL
        print(f"✅ SelfKnowledge initialisiert ({len(CAPABILITIES)} Funktionen, Modell: {self.model})")

    def _context(self) -> str:
        return "\n".join(f"- {c['name']}: {c['desc']}" for c in CAPABILITIES)

    def answer(self, question: str) -> str:
        system = (
            "Du bist der KI-Assistent / Telegram-Bot dieser Firma (Badsanierung/Pflege) und "
            "beschreibst DEINE EIGENEN Fähigkeiten. Antworte in DERSELBEN Sprache wie die Frage. "
            "Nutze AUSSCHLIESSLICH die unten stehende Funktionsliste — erfinde nichts. "
            "Bei einer allgemeinen Frage (z. B. 'Was kannst du?') gib eine kurze Übersicht ALLER Funktionen "
            "als Stichpunkte (mit Emoji-Symbolen, je 1 Zeile). "
            "Bei einer spezifischen Frage (z. B. 'Wie funktioniert die Gesprächsanalyse?') erkläre NUR die "
            "relevante(n) Funktion(en) etwas genauer. Kurz, klar und freundlich."
        )
        user = f"FRAGE:\n{question}\n\nFUNKTIONSLISTE:\n{self._context()}"
        try:
            resp = self.openai.chat.completions.create(
                model=self.model,
                max_tokens=700,
                temperature=0.3,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            print(f"   ⚠️ SelfKnowledge Fehler: {e}")
            # Fallback: einfache Übersicht ohne AI
            return "🤖 Meine Funktionen:\n" + "\n".join(f"• {c['name']}" for c in CAPABILITIES)
