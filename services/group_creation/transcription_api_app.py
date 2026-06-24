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
    POST /api/transcribe-analyze   (multipart: file=<audio/video>, Header: X-API-Key)
    → { "transcript": "...", "analysis": "...", "language": "de" }
"""

import os
import tempfile
from flask import Flask, request, jsonify
from openai import OpenAI

# Bestehende Bausteine wiederverwenden
from bot.handlers.construction_video_handler import extract_audio, transcribe_audio
from bot.reports.call_analysis import CALL_PROMPT, CALL_ANALYSIS_MODEL

app = Flask(__name__)

API_KEY   = os.getenv("TRANSCRIBE_API_KEY", "")
MAX_MB    = int(os.getenv("TRANSCRIBE_MAX_MB", "50"))
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))


@app.route("/api/health")
def health():
    return jsonify(ok=True, service="transcription_api")


@app.route("/api/transcribe-analyze", methods=["POST"])
def transcribe_analyze():
    # ── Auth ──
    if API_KEY and request.headers.get("X-API-Key", "") != API_KEY:
        return jsonify(error="unauthorized"), 401

    if "file" not in request.files:
        return jsonify(error="Keine Datei empfangen (Feld 'file')."), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify(error="Leere Datei."), 400

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

        # 1) Audio extrahieren (FFmpeg) + 2) Whisper (wiederverwendet)
        audio_path = extract_audio(media_path, media_type)
        tr = transcribe_audio(audio_path, language_hint="de")
        transcript = (tr.get("text") or "").strip()
        language   = tr.get("language", "de")

        if len(transcript) < 5:
            return jsonify(transcript="", analysis="", language=language,
                           error="Keine verständliche Sprache erkannt."), 200

        # 3) Analyse (GPT — gleicher Prompt wie im Telegram-Feature)
        resp = openai_client.chat.completions.create(
            model=CALL_ANALYSIS_MODEL,
            max_tokens=1400,
            temperature=0.4,
            messages=[
                {"role": "system", "content": "Du bist ein erfahrener, ehrlicher Vertriebs-Coach."},
                {"role": "user", "content": CALL_PROMPT + transcript},
            ],
        )
        analysis = (resp.choices[0].message.content or "").strip()

        return jsonify(transcript=transcript, analysis=analysis, language=language), 200

    except ValueError as ve:        # z. B. > 25MB für Whisper
        return jsonify(error=str(ve)), 413
    except Exception as e:
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
