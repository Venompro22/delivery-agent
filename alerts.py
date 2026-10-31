"""🚨 Security alerts: always written to a file, optionally pushed to Telegram.

The file is the part that must never fail — it works offline, needs no account,
and survives a blocked network. Telegram is a best-effort extra on top.
"""
from __future__ import annotations

import os
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ALERT_LOG = os.environ.get("ALERT_LOG", os.path.join(HERE, "alerts.log"))

# Events worth waking up for. Anything else stays in the normal audit log.
URGENT = {
    "login_owner",        # someone signed in as owner — that should only be you
    "2fa_failed",
    "2fa_recovery_used",
    "2fa_disabled",
    "login_blocked",      # rate limiter kicked in: brute force in progress
    "logout_all",
    "panic_readonly",
    "panic_lockdown",
    "panic_off",
    "panic_normal",
}
IMPORTANT = {
    "signup",             # a new driver asked for an account
    "password_reset",
    "password_changed",
}


def _write(line: str) -> None:
    """Append to the alert file. Never raises: an alert must not break a request."""
    try:
        with open(ALERT_LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _telegram(text: str) -> None:
    """Best-effort push. Silently skipped if unset or unreachable (blocked, offline)."""
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text}, timeout=5)
    except Exception:
        _write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  [!] telegram send failed")


def notify(event: str, ip: str = "", who: str = "", detail: str = "") -> None:
    """Called for every audited event; only urgent/important ones raise an alert."""
    if event in URGENT:
        level = "URGENT"
    elif event in IMPORTANT:
        level = "INFO"
    else:
        return                       # everyday activity: audit log only

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp}  [{level}] {event}  ip={ip or '?'}  who={who or '?'}"
    if detail:
        line += f"  {detail}"
    _write(line)

    if level == "URGENT":
        _telegram(f"🔴 {event}\n{stamp}\nip: {ip or '?'}\nwho: {who or '?'}"
                  + (f"\n{detail}" if detail else ""))


def recent(limit: int = 30) -> list:
    """Last alerts, newest first — for a quick look from the dashboard."""
    try:
        with open(ALERT_LOG) as f:
            return [l.rstrip() for l in f.readlines()[-limit:]][::-1]
    except OSError:
        return []
