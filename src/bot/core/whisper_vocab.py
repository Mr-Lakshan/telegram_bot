"""
WHISPER VOCABULARY HINTS
========================
Whisper akzeptiert einen `prompt` — einen kurzen Text, der zeigt, welche Woerter
in der Aufnahme zu erwarten sind. Ohne ihn raet das Modell bei Fachbegriffen und
Eigennamen, und zwar konsequent falsch: "Wannentuer" wird zu "Wanne. Tuer",
"Pflegegrad" zu "Pflege. Grad", "Premiobad" zu irgendetwas.

Das ist der billigste Genauigkeitsgewinn, den es hier gibt — ein Parameter,
keine zusaetzlichen Kosten, keine zusaetzliche Laufzeit.

WICHTIG — die 224-Token-Grenze
------------------------------
Whisper schneidet den Prompt bei 224 Tokens ab. Die Listen unten liegen bei rund
180 und haben damit etwas Luft. Wer etwas hinzufuegt, sollte etwas anderes
streichen, sonst faellt das Ende stillschweigend weg.

WICHTIG — warum der Prompt sprachabhaengig ist
----------------------------------------------
Der Prompt lenkt nicht nur das Vokabular, sondern auch die Sprache. Ein deutscher
Prompt auf einer polnischen Aufnahme schiebt Whisper Richtung Deutsch — also
genau der Fehler, den wir gerade abstellen. Deshalb:

    - Sprache explizit gewaehlt  -> die Liste dieser Sprache
    - "automatisch"              -> NEUTRAL, nur Eigennamen und die Begriffe,
                                    die auf der Baustelle ohnehin in jeder
                                    Sprache deutsch gesagt werden

Diese Datei liegt bewusst in BEIDEN Repos (telegram_bot_v2 und video-translate).
Sie hat keine Imports und keinen Zustand — eine Kopie ist hier billiger als eine
Abhaengigkeit zwischen zwei getrennt deployten Diensten. Wer sie aendert, aendert
sie an beiden Stellen.
"""

# ── Deutsch — die volle Liste ────────────────────────────────────────────────
# Zusammengetragen aus dem, was das CRM tatsaechlich enthaelt:
# lib/WerkvertragWorkSections.php, install/angebot_catalog_seed.json und den
# Status-Bezeichnungen in config/status_config.php.
_DE = (
    "Gespraech bei einem Badumbau-Betrieb. "
    "Badumbau, Badsanierung, bodengleiche Dusche, Duschwanne, Wannentuer, "
    "Waschtisch, Armaturen, Handbrause, Haltegriff, Fliesen, Abdichtung, "
    "Estrich, Silikon, Rueckbau, Montage, Aufmass, Baustelle, Bautermin, "
    "Nacharbeit. "
    "Angebot, Werkvertrag, Rechnung, Anzahlung, Zahlungsplan, Reklamation, "
    "Wiedervorlage, Besichtigungstermin. "
    "Pflegekasse, Krankenkasse, Pflegegrad, Antrag, Bewilligung, Zuschuss, "
    "Paragraf 40 SGB XI, Vermieter. "
    "Firma: Premiobad."
)

# ── Polnisch — deutsche Fachbegriffe im polnischen Satz ──────────────────────
# Die Monteure sprechen Polnisch, benennen die Sache am Bau aber deutsch. Der
# Prompt ist deshalb polnisch geschrieben, traegt die deutschen Begriffe aber
# unveraendert mit — sonst schreibt Whisper sie phonetisch polnisch.
_PL = (
    "Rozmowa w firmie remontujacej lazienki. "
    "Terminy niemieckie uzywane na budowie: Badumbau, Badsanierung, "
    "bodengleiche Dusche, Duschwanne, Duschkabine, Badewanne, Wannentuer, "
    "Waschbecken, Waschtisch, Armaturen, Handbrause, Haltegriff, Fliesen, "
    "Abdichtung, Estrich, Silikon, Rueckbau, Montage, Aufmass, Baustelle, "
    "Bautermin, Angebot, Werkvertrag, Rechnung, Anzahlung, Reklamation, "
    "Wiedervorlage, Pflegekasse, Krankenkasse, Pflegegrad, Antrag, Zuschuss. "
    "Firma: Premiobad."
)

# ── Neutral — fuer die automatische Spracherkennung ──────────────────────────
# Nur Eigennamen und Begriffe, die in jeder Sprache deutsch ausgesprochen
# werden. Kurz gehalten, damit er die Spracherkennung nicht kippt.
_NEUTRAL = (
    "Premiobad. Badumbau, Duschwanne, Wannentuer, Haltegriff, Fliesen, "
    "Aufmass, Baustelle, Bautermin, Angebot, Werkvertrag, Rechnung, "
    "Anzahlung, Reklamation, Wiedervorlage, Pflegekasse, Krankenkasse, "
    "Pflegegrad, Antrag, Zuschuss, SGB XI."
)

VOCAB = {
    "de": _DE,
    "pl": _PL,
}


def vocab_prompt(language: str | None = None) -> str:
    """
    Der passende Prompt fuer die gewaehlte Sprache.

    None / "" / "auto" -> die neutrale Liste. Fuer jede Sprache ohne eigene
    Liste ebenfalls die neutrale: eine falsche Sprachliste waere schlechter als
    gar keine, weil sie die Erkennung in die falsche Richtung zieht.
    """
    if not language or language == "auto":
        return _NEUTRAL
    return VOCAB.get(language.lower(), _NEUTRAL)


# ── Schutz gegen stilles Abschneiden ─────────────────────────────────────────
# Whisper schneidet bei 224 Tokens ab, ohne es zu melden — das Ende der Liste
# waere dann einfach wirkungslos, und niemand wuerde es bemerken. Deutsche
# Komposita kommen im schlechtesten Fall auf rund 2,2 Zeichen pro Token, also
# sind 480 Zeichen die praktische Obergrenze. Die Pruefung laeuft beim Import:
# wer eine Liste erweitert, sieht die Warnung sofort im Log und nicht erst an
# unerklaerlich schlechten Transkripten.
_MAX_CHARS = 480

for _name, _text in list(VOCAB.items()) + [("neutral", _NEUTRAL)]:
    if len(_text) > _MAX_CHARS:
        import warnings
        warnings.warn(
            f"whisper_vocab[{_name}] ist {len(_text)} Zeichen lang "
            f"(Grenze {_MAX_CHARS}). Whisper schneidet den Prompt bei 224 "
            f"Tokens ab — bitte etwas streichen, statt anzuhaengen.",
            stacklevel=2,
        )


# Sprachen, die die Oberflaeche anbieten darf. Deckungsgleich mit
# video-translate/app/config.py LANG_NAMES, damit beide Dienste dieselbe
# Auswahl kennen.
SUPPORTED = {
    "de": "Deutsch",
    "pl": "Polnisch",
    "en": "Englisch",
    "ru": "Russisch",
    "uk": "Ukrainisch",
    "ro": "Rumaenisch",
    "hr": "Kroatisch",
    "tr": "Tuerkisch",
    "it": "Italienisch",
    "es": "Spanisch",
    "fr": "Franzoesisch",
    "nl": "Niederlaendisch",
    "pt": "Portugiesisch",
    "hi": "Hindi",
}


def normalise(language: str | None) -> str | None:
    """
    Eingabe der Oberflaeche in etwas verwandeln, das Whisper akzeptiert.

    Gibt None zurueck, wenn automatisch erkannt werden soll — der Aufrufer darf
    `language` dann gar nicht erst an die API uebergeben. Ein leerer String waere
    dort ein Fehler, kein "egal".
    """
    if not language:
        return None
    lang = language.strip().lower()
    if lang in ("", "auto", "automatisch"):
        return None
    return lang if lang in SUPPORTED else None
