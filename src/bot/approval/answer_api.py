"""
ANSWER API — receives answers from the CRM form (approval_answer.php)

The "✍️ Antwort eingeben" button opens approval_answer.php in the CRM. When Lothar
presses "Antwort senden" there, the CRM POSTs the answer to the bot on port 5002.
This module is that listener: it hands the answer to ApprovalHandler.deliver_answer(),
which posts it in the customer group, translates it, closes the card and saves to KB.

Runs a small Flask server in a background thread inside the bot process, because
deliver_answer() needs the bot's own Telethon clients and event loop.

Accepts form data or JSON, on any path:
    bot_secret / secret (or header X-Bot-Secret)  must equal APPROVAL_BOT_SECRET
    token                                          approval token
    answer / answer_text / text / antwort          the answer
    by (optional)                                  who answered
"""
import asyncio
import os
import threading

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from bot.approval.approval_handler import APPROVAL_BOT_SECRET

ANSWER_API_PORT = int(os.getenv("APPROVAL_API_PORT", "5002"))

_TOKEN_KEYS = ("token",)
_ANSWER_KEYS = ("answer", "answer_text", "text", "antwort")
_SECRET_KEYS = ("bot_secret", "secret")


def _first(data, keys):
    for k in keys:
        v = data.get(k)
        if v:
            return str(v)
    return ""


def create_app(approval_handler, loop):
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify(ok=True)

    @app.route("/", defaults={"path": ""}, methods=["POST"])
    @app.route("/<path:path>", methods=["POST"])
    def receive_answer(path):
        data = dict(request.form) if request.form else (request.get_json(silent=True) or {})

        secret = _first(data, _SECRET_KEYS) or request.headers.get("X-Bot-Secret", "")
        if secret != APPROVAL_BOT_SECRET:
            print(f"⚠️ Answer API: wrong/missing secret on /{path} from {request.remote_addr}")
            return jsonify(ok=False, success=False, error="forbidden"), 403

        token = _first(data, _TOKEN_KEYS)
        answer = _first(data, _ANSWER_KEYS)
        if not token or not answer:
            # Log only the field names, never the content.
            print(f"⚠️ Answer API: missing token/answer on /{path} — fields received: {sorted(data.keys())}")
            return jsonify(ok=False, success=False, error="token and answer required"), 400

        by = _first(data, ("by", "approved_by")) or "lothar_form"
        try:
            fut = asyncio.run_coroutine_threadsafe(
                approval_handler.deliver_answer(token, answer, by=by), loop
            )
            result = fut.result(timeout=90)
        except Exception as e:
            print(f"❌ Answer API error: {e}")
            return jsonify(ok=False, success=False, error=str(e)[:200]), 500

        ok = bool(result.get("ok"))
        print(f"{'✅' if ok else '⚠️'} Answer API: token {token[:8]}... → {result}")
        return jsonify(ok=ok, success=ok, error=result.get("error", "")), (200 if ok else 409)

    return app


def start_answer_api(approval_handler, loop=None, port: int = ANSWER_API_PORT):
    """Start the listener in a daemon thread. Call from inside the bot's running event loop."""
    loop = loop or asyncio.get_running_loop()
    app = create_app(approval_handler, loop)
    server = make_server("0.0.0.0", port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True, name="answer-api").start()
    print(f"✅ Answer API listening on :{port} (CRM form → Telegram)")
    return server
