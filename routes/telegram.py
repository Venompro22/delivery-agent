"""Telegram: connect the bot (setWebhook) and the webhook that receives the owner's messages."""
from __future__ import annotations

from flask import request, jsonify
import hmac
import os
import owner_ai
import re

import fileapp as core


TELEGRAM_ENV = ("TELEGRAM_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_CHAT_ID")


@core.app.route("/api/telegram/register", methods=["POST"])
@core.login_required
def telegram_register():
    """Owner button "Connect Telegram bot": point the bot's webhook at this server.
    Only safe info comes back; the token and the secret never leave the server."""
    import alerts
    if core.current_role() != "owner":                     # viewers and drivers too
        return jsonify({"ok": False, "error": "owner only"}), 403
    # CSRF: the session cookie is SameSite=Lax, and a cross-site form cannot send a JSON body
    # (that needs a CORS preflight, which this server never allows).
    if not request.is_json:
        return core.bad_request("send JSON")
    missing = [k for k in TELEGRAM_ENV if not os.environ.get(k)]
    if missing:
        return core.bad_request("Set these env vars on the server first: " + ", ".join(missing))
    token, secret = os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_WEBHOOK_SECRET"]

    def safe(text) -> str:
        """Telegram's own text, with the token and the secret blanked out just in case."""
        return str(text or "").replace(token, "***").replace(secret, "***")[:300]

    host = request.host                               # behind Render: X-Forwarded-Host via ProxyFix
    if not re.fullmatch(r"[A-Za-z0-9.-]+(:\d{1,5})?", host):
        return core.bad_request("unexpected host name")
    url = f"https://{host}/api/telegram/webhook"
    r = alerts.telegram_call("setWebhook", {"url": url, "secret_token": secret,
                                            "allowed_updates": ["message", "callback_query"],
                                            "drop_pending_updates": True}, timeout=10)
    if not r:
        return jsonify({"ok": False, "error": "Telegram could not be reached"}), 502
    if not r.get("ok"):
        return jsonify({"ok": False, "error": "Telegram refused: " + safe(r.get("description"))}), 502
    info = (alerts.telegram_call("getWebhookInfo", {}) or {}).get("result") or {}
    me = (alerts.telegram_call("getMe", {}) or {}).get("result") or {}
    core.audit.add("telegram_webhook_registered", core.client_ip(), "owner")
    return jsonify({"ok": True,
                    "bot": safe(me.get("username")),
                    "webhook_url": safe(info.get("url")),
                    "pending_update_count": int(info.get("pending_update_count") or 0),
                    "last_error_message": safe(info.get("last_error_message"))})


@core.app.route("/api/telegram/webhook", methods=["POST"])
def telegram_webhook():
    """The owner asks and acts from Telegram. Two locks: Telegram's secret-token header
    (proves the update really comes from Telegram) and TELEGRAM_CHAT_ID (only the owner's chat)."""
    import alerts
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    owner_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not secret or not owner_chat:
        return jsonify({"ok": False}), 404                 # not set up: the route does not exist
    sent = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(sent.encode(), secret.encode()):
        return jsonify({"ok": False}), 403
    update = request.get_json(silent=True) or {}

    def send(text, buttons=None):
        payload = {"chat_id": owner_chat, "text": text[:4000]}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [buttons]}
        alerts.telegram_api("sendMessage", payload)

    # 🔘 A Confirm / Cancel button under a preview
    cb = update.get("callback_query")
    if isinstance(cb, dict):
        if str(((cb.get("message") or {}).get("chat") or {}).get("id", "")) != owner_chat:
            return jsonify({"ok": True})                   # someone else's chat: ignored
        alerts.telegram_api("answerCallbackQuery", {"callback_query_id": cb.get("id")})
        choice, _, token = str(cb.get("data", "")).partition(":")
        if choice == "ok":
            ok, message = core.confirm_owner_action(token, "telegram")
            send(message if ok else "⚠️ " + message)
        elif choice == "no":
            a = core.pending_actions().take(token)
            send(owner_ai.DROPPED.get((a or {}).get("lang"), owner_ai.DROPPED["en"]))   # old "darija" tokens → English
        return jsonify({"ok": True})

    # 💬 A text message: question → answer, command → preview with buttons, else help
    msg = update.get("message")
    if not isinstance(msg, dict) or str((msg.get("chat") or {}).get("id", "")) != owner_chat:
        return jsonify({"ok": True})
    text = str(msg.get("text") or "").strip()[:500]
    if not text:
        return jsonify({"ok": True})
    lang = owner_ai.reply_lang(text)
    question = owner_ai.answer(text, core.stats_data, core.late_now)
    if question:
        send(question["reply"])
        return jsonify({"ok": True})
    preview = core.owner_preview(text)
    if preview and preview.get("error"):
        send(f"⚠️ {preview['error']}\n\n{owner_ai.HELP[lang]}")
    elif preview:
        yes, no = owner_ai.BUTTONS[preview["lang"]]
        send(f"{preview['summary']}\n{preview['hint']}",
             [{"text": yes, "callback_data": f"ok:{preview['token']}"},
              {"text": no, "callback_data": f"no:{preview['token']}"}])
    else:
        send(owner_ai.HELP[lang])
    return jsonify({"ok": True})
