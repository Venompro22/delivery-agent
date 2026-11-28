"""🚨 Server errors reach the owner on Telegram, so a bug is known before a customer reports it.

Privacy: the alert carries only the route template (no ids, no query string), the
exception type and the code location. Never the exception text or the request body:
those can contain phone numbers, addresses or even tokens.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback

from flask import Flask, got_request_exception, request

REPEAT_WINDOW = 600       # the same error is reported once per 10 minutes
MAX_PER_HOUR = 20         # hard cap, so a crash loop can't flood the chat

_lock = threading.Lock()
_last_sent: dict = {}     # error key -> time it was last reported
_suppressed: dict = {}    # error key -> repeats not reported since
_sent_times: list = []    # times of every alert in the last hour


def _location(exc: BaseException) -> str:
    """Deepest frame inside this project: 'file.py:123 in func'."""
    here = os.path.dirname(os.path.abspath(__file__))
    best = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if os.path.abspath(frame.filename).startswith(here) and "site-packages" not in frame.filename:
            best = frame
    if best is None:
        return "unknown location"
    return f"{os.path.relpath(best.filename, here)}:{best.lineno} in {best.name}"


def build_message(method: str, rule: str, exc: BaseException, repeats: int = 0) -> str:
    text = (f"🚨 智送管家 server error\n"
            f"{method} {rule}\n"
            f"{type(exc).__name__} at {_location(exc)}")
    if repeats:
        text += f"\n(+{repeats} more of the same since the last alert)"
    return text


def should_send(key: str, now: float) -> tuple:
    """Returns (send?, repeats folded into this alert). Thread-safe."""
    with _lock:
        _sent_times[:] = [t for t in _sent_times if now - t < 3600]
        last = _last_sent.get(key)
        if (last is not None and now - last < REPEAT_WINDOW) or len(_sent_times) >= MAX_PER_HOUR:
            _suppressed[key] = _suppressed.get(key, 0) + 1
            return False, 0
        _last_sent[key] = now
        _sent_times.append(now)
        return True, _suppressed.pop(key, 0)


def reset() -> None:
    """Forget the throttle state (used by tests)."""
    with _lock:
        _last_sent.clear()
        _suppressed.clear()
        _sent_times.clear()


def _enabled() -> bool:
    if os.environ.get("ERROR_ALERTS") == "0":
        return False
    return "pytest" not in sys.modules or os.environ.get("ERROR_ALERTS") == "1"


def install(app: Flask, send) -> None:
    """Report every unhandled exception in a request. `send(text)` must never raise."""

    def on_exception(sender, exception, **extra):
        if not _enabled():
            return
        try:
            rule = request.url_rule.rule if request.url_rule else "(unknown route)"
            method = request.method
        except RuntimeError:
            rule, method = "(no request)", "?"
        key = f"{method} {rule} {type(exception).__name__} {_location(exception)}"
        ok, repeats = should_send(key, time.time())
        if ok:
            text = build_message(method, rule, exception, repeats)
            threading.Thread(target=send, args=(text,), daemon=True).start()

    got_request_exception.connect(on_exception, app, weak=False)
