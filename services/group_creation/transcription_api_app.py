"""
TRANSCRIPTION API — kleiner HTTP-Dienst für das CRM-Dashboard
==============================================================
Das CRM lädt eine Audio-/Video-Datei hoch (per Drag & Drop) → dieser Dienst
transkribiert (Whisper) und analysiert das Gespräch (GPT) und gibt JSON zurück.

Wiederverwendung der bestehenden Logik (KEIN neuer Transkriptions-Code):
    bot.handlers.construction_video_handler.extract_audio / transcribe_audio
    bot.reports.call_analysis.CALL_PROMPT / CALL_ANALYSIS_MODEL

Läuft im Bot-Image (ffmpeg + Code + OPENAI_API_KEY bereits vorhanden), Port 5003.

Endpoint:
    POST /api/transcribe-analyze
        multipart: file=<audio/video>, language=<de|pl|...|auto>
        Header:    X-API-Key
    → { "transcript": "...", "analysis": "...", "language": "de" }

─── Änderungen 04.09.2026 ────────────────────────────────────────────────────
1. `language` ist jetzt ein Feld der Anfrage statt fest "de". Vorher wurde jede
   Aufnahme als Deutsch transkribiert — bei einer polnisch sprechenden Kraft
   hat Whisper die Laute in deutsche Wörter gepresst und unbrauchbaren Text
   geliefert. Ohne Angabe wird die Sprache jetzt erkannt.
2. Whisper bekommt eine Vokabelliste (whisper_vocab). Fachbegriffe und
   Eigennamen kamen bis jetzt zuverlässig falsch heraus.
3. Der API-Key wird erzwungen. Vorher hieß es `if API_KEY and ...` — bei nicht
   gesetztem Key war die Prüfung damit komplett aus, und der Port ist in
   docker-compose auf 0.0.0.0 veröffentlicht.
4. max_tokens der Analyse angehoben: 1400 hat lange Gespräche mitten im Satz
   abgeschnitten.
"""

import logging
import os
import tempfile

from flask import Flask, request, jsonify
from openai import OpenAI

# Bestehende Bausteine wiederverwenden
from bot.handlers.construction_video_handler import extract_audio, transcribe_audio
from bot.reports.call_analysis import CALL_PROMPT, CALL_ANALYSIS_MODEL
from bot.core.whisper_vocab import normalise, SUPPORTED

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("transcription_api")

app = Flask(__name__)

API_KEY = os.getenv("TRANSCRIBE_API_KEY", "")
MAX_MB = int(os.getenv("TRANSCRIBE_MAX_MB", "50"))
ANALYSIS_MAX_TOKENS = int(os.getenv("TRANSCRIBE_ANALYSIS_TOKENS", "4000"))

# Flask soll den Upload abweisen, bevor er komplett im Speicher landet.
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))

if not API_KEY:
    log.error(
        "TRANSCRIBE_API_KEY ist nicht gesetzt. Der Dienst nimmt keine Anfragen "
        "an. Setze den Wert in der .env — denselben, den das CRM verwendet."
    )


@app.route("/api/health")
def health():
    """Ohne Auth, damit Monitoring drankommt. Verrät nichts über den Key."""
    return jsonify(ok=True, service="transcription_api",
                   configured=bool(API_KEY))


@app.route("/api/languages")
def languages():
    """Womit die Oberfläche ihr Auswahlfeld füllt."""
    return jsonify(languages=SUPPORTED)


@app.route("/api/transcribe-analyze", methods=["POST"])
def transcribe_analyze():
    # ── Auth ──
    # Fail closed. Ohne konfigurierten Key wird nichts verarbeitet, statt die
    # Prüfung stillschweigend zu überspringen.
    if not API_KEY:
        return jsonify(error="Dienst nicht konfiguriert (TRANSCRIBE_API_KEY fehlt)."), 503

    import hmac
    if not hmac.compare_digest(request.headers.get("X-API-Key", ""), API_KEY):
        return jsonify(error="unauthorized"), 401

    if "file" not in request.files:
        return jsonify(error="Keine Datei empfangen (Feld 'file')."), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify(error="Leere Datei."), 400

    # ── Sprache ──
    # Kommt aus dem Auswahlfeld der Seite. Leer oder "auto" heißt: Whisper
    # erkennt sie selbst. normalise() gibt dafür None zurück, und None wird
    # weiter unten gar nicht erst an die API übergeben.
    requested = request.form.get("language", "").strip()
    lang_hint = normalise(requested)
    if requested and requested not in ("auto", "") and lang_hint is None:
        log.warning("Unbekannte Sprache %r — es wird automatisch erkannt", requested)

    suffix = os.path.splitext(f.filename)[1] or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    media_path = tmp.name
    tmp.close()
    f.save(media_path)

    audio_path = None
    try:
        size_mb = os.path.getsize(media_path) / (1024 * 1024)
        if size_mb > MAX_MB:
            return jsonify(error=f"Datei zu groß: {size_mb:.1f}MB (max {MAX_MB}MB)"), 413

        mime = (f.mimetype or "").lower()
        media_type = "video" if mime.startswith("video/") else "voice"

        log.info("Auftrag: %s (%.1f MB, Sprache: %s)",
                 f.filename, size_mb, lang_hint or "automatisch")

        # 1) Audio extrahieren (FFmpeg) + 2) Whisper (wiederverwendet)
        audio_path = extract_audio(media_path, media_type)
        tr = transcribe_audio(audio_path, language_hint=lang_hint)
        transcript = (tr.get("text") or "").strip()
        language = tr.get("language", lang_hint or "unbekannt")

        if len(transcript) < 5:
            return jsonify(transcript="", analysis="", language=language,
                           error="Keine verständliche Sprache erkannt."), 200

        # 3) Analyse (GPT — gleicher Prompt wie im Telegram-Feature)
        #
        # Läuft weiterhin auf Deutsch, auch wenn das Gespräch polnisch war:
        # die Auswertung liest Lothar, nicht der Monteur.
        resp = openai_client.chat.completions.create(
            model=CALL_ANALYSIS_MODEL,
            max_tokens=ANALYSIS_MAX_TOKENS,
            temperature=0.4,
            messages=[
                {"role": "system", "content": "Du bist ein erfahrener, ehrlicher Vertriebs-Coach."},
                {"role": "user", "content": CALL_PROMPT + transcript},
            ],
        )
        analysis = (resp.choices[0].message.content or "").strip()

        log.info("Fertig: %d Zeichen Transkript, Sprache %s", len(transcript), language)
        return jsonify(transcript=transcript, analysis=analysis,
                       language=language), 200

    except ValueError as ve:        # z. B. > 25MB für Whisper
        log.warning("Abgelehnt: %s", ve)
        return jsonify(error=str(ve)), 413
    except Exception as e:
        log.exception("Verarbeitungsfehler")
        return jsonify(error=f"Verarbeitungsfehler: {str(e)[:300]}"), 500
    finally:
        for p in (media_path, audio_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


if __name__ == "__main__":
    print("✅ Transcription API startet auf :5003")
    app.run(host="0.0.0.0", port=5003, threaded=True)