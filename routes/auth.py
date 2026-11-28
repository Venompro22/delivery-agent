"""Owner and driver sign-in: login, signup, account, 2FA setup, logout, panic switch, security log."""
from __future__ import annotations

from flask import request, jsonify, render_template, session, redirect, url_for
from users import public
from werkzeug.security import generate_password_hash
import base64
import filesync
import hmac
import os
import pyotp
import secrets

import fileapp as core


@core.app.route("/login", methods=["GET", "POST"])
def login():
    if core.is_logged_in():
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        ip = core._visitor_ip()
        phone = request.form.get("phone", "").strip()
        if core.limiter.blocked(ip, phone):
            error = "Too many attempts. Wait 10 minutes."
            core.audit.add("login_blocked", ip, phone or "owner")
        else:
            given = request.form.get("password", "")
            role, uid = None, None
            if phone:                                 # 👤 driver account
                user = core.users.check_login(phone, given)
                if user and user["status"] == "approved":
                    role, uid = user["role"], user["id"]
                elif user and user["status"] == "pending":
                    error = "Your account is waiting for approval."
                elif user:
                    error = "This account is blocked."
            elif hmac.compare_digest(given.encode(), core.DASHBOARD_PASSWORD.encode()):
                # 🔐 Right password, but the owner may still need a 2FA code.
                if core.twofa_enabled() and not core.twofa_check(request.form.get("code", "")):
                    core.limiter.fail(ip, phone)
                    core.audit.add("2fa_failed", ip, "owner")
                    return render_template("login.html",
                                           error="Enter the 6-digit code from your authenticator.",
                                           need_code=True)
                role = "owner"
            if role:
                core.limiter.success(phone)
                session.clear()
                session["auth"] = True
                session["role"] = role
                session["v"] = core.session_version()
                if uid:
                    session["uid"] = uid
                    session["pv"] = core.users.get(uid)["pw_version"]
                session.permanent = True
                print(f"Login from {ip} as {role}", flush=True)
                # Owner logins are alert-worthy; driver logins are everyday noise.
                core.audit.add("login_owner" if role == "owner" else "login",
                          ip, phone or "owner", role)
                return redirect(url_for("home") if role == "owner" else url_for("my_page") if role == "customer" else url_for("driver_page"))
            if error is None:                  # pending/blocked = right password, not a guess
                core.limiter.fail(ip, phone)
                core.audit.add("login_failed", ip, phone or "owner")
                print(f"Wrong password from {ip}" + (f" for {phone}" if phone else ""), flush=True)
            error = error or "Wrong password."
    return render_template("login.html", error=error)


@core.app.route("/signup", methods=["GET", "POST"])
def signup():
    """👤 Drivers create an account; it waits until the owner approves it."""
    error = done = None
    if request.method == "POST":
        ip = core._visitor_ip()
        if core.limiter.signup_blocked(ip):
            error = "Too many new accounts from this network today. Try again tomorrow."
            core.audit.add("signup_blocked", ip)
        else:
            try:
                user = core.users.create(request.form.get("phone", ""), request.form.get("name", ""),
                                    request.form.get("password", ""),
                                    role=request.form.get("role", "driver"))
                core.limiter.signup_done(ip)
                core.audit.add("signup", ip, user["phone"], user["name"])
                print(f"👤 New driver signup: {user['name']} ({user['phone']}) from {ip} — pending",
                      flush=True)
                done = True
            except ValueError as e:
                error = str(e)
    return render_template("signup.html", error=error, done=done, role=request.form.get("role", "driver"))


@core.app.route("/account", methods=["GET", "POST"])
@core.login_required
def account():
    """🔑 A driver changes their own password."""
    uid = session.get("uid")
    if not uid:                                   # the owner has no account here
        return redirect(url_for("home"))
    user = core.users.get(uid)
    ip = core.client_ip()
    error = done = None
    if request.method == "POST":
        if core.limiter.blocked(ip, user["phone"]):
            error = "Too many attempts. Wait 10 minutes."
        elif not core.users.check_login(user["phone"], request.form.get("current", "")):
            core.limiter.fail(ip, user["phone"])
            core.audit.add("password_change_failed", ip, user["phone"])
            error = "Current password is wrong."
        else:
            try:
                core.users.set_password(uid, request.form.get("new", ""))
                session["pv"] = core.users.get(uid)["pw_version"]      # this device stays logged in
                core.audit.add("password_changed", ip, user["phone"])
                done = True
            except ValueError as e:
                error = str(e)
    user = core.users.get(uid)
    return render_template("account.html", user=public(user), error=error, done=done,
                           must_change=bool(user["must_change"]))


@core.app.route("/api/security-log")
@core.owner_required
def security_log():
    """📜 The last 50 security events, newest first."""
    return jsonify(core.audit.recent(50))


@core.app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@core.app.route("/api/logout-all", methods=["POST"])
@core.owner_required
def logout_all():
    """🚪 Log out everyone (owner + all drivers) right now."""
    core.audit.add("logout_all", core.client_ip(), "owner")
    new = core.session_version() + 1
    with open(core.SESSION_VERSION_PATH, "w") as f:
        f.write(str(new))
    filesync.persist(core.SESSION_VERSION_PATH)
    session.clear()
    print(f"🚪 Logged everyone out (session version {new})", flush=True)
    return jsonify({"ok": True})


@core.app.route("/security/2fa", methods=["GET", "POST"])
@core.owner_required
def setup_2fa():
    """🔐 Turn two-factor on or off for the owner account."""
    if request.method == "POST" and request.form.get("action") == "disable":
        if os.path.exists(core.TWOFA_PATH):
            os.remove(core.TWOFA_PATH)
            filesync.persist(core.TWOFA_PATH)
        core.audit.add("2fa_disabled", core.client_ip(), "owner")
        return render_template("twofa.html", enabled=False, message="Two-factor is now off.")

    if request.method == "POST" and request.form.get("action") == "enable":
        secret = session.get("pending_2fa_secret")
        if not secret:
            return redirect(url_for("setup_2fa"))
        if not pyotp.TOTP(secret).verify(request.form.get("code", "").strip(), valid_window=1):
            return render_template("twofa.html", enabled=False, secret=secret,
                                   qr=_twofa_qr(secret),
                                   message="That code didn't match — try the current one.")
        # Eight one-time recovery codes, shown once and stored only as hashes.
        plain = [secrets.token_hex(4) for _ in range(8)]
        core.twofa_save({"secret": secret,
                    "recovery": [generate_password_hash(c) for c in plain]})
        session.pop("pending_2fa_secret", None)
        core.audit.add("2fa_enabled", core.client_ip(), "owner")
        return render_template("twofa.html", enabled=True, recovery=plain,
                               message="Two-factor is on. Save these recovery codes now.")

    if core.twofa_enabled():
        return render_template("twofa.html", enabled=True)

    # Fresh secret, held in the session until the first code proves it works.
    secret = pyotp.random_base32()
    session["pending_2fa_secret"] = secret
    return render_template("twofa.html", enabled=False, secret=secret, qr=_twofa_qr(secret))


def _twofa_qr(secret: str) -> str:
    """The setup QR as an inline data URI, so no image file is ever written."""
    import io
    import qrcode
    uri = pyotp.TOTP(secret).provisioning_uri(name="owner", issuer_name="智送管家")
    buf = io.BytesIO()
    qrcode.make(uri).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@core.app.route("/panic/<mode>")
def panic_switch(mode):
    """🔴 Emergency switch, opened from a signed bookmark on your phone.

    No login: a stolen session must never be able to undo a lockdown, and you
    must be able to trigger it from any device. The HMAC key is the only proof.
    """
    if mode not in core.PANIC_MODES:
        return jsonify({"ok": False, "error": "unknown mode"}), 404
    given = request.args.get("k", "").encode()
    if not hmac.compare_digest(given, core.panic_key(mode).encode()):
        return jsonify({"ok": False, "error": "not found"}), 404   # same answer: no hints

    core.set_panic_mode(mode)
    core.audit.add("panic_" + mode, core.client_ip(), "owner")
    if mode != "normal":
        # Kick every existing session out as well.
        new = core.session_version() + 1
        with open(core.SESSION_VERSION_PATH, "w") as f:
            f.write(str(new))
        filesync.persist(core.SESSION_VERSION_PATH)
    print(f"\U0001f534 PANIC MODE: {mode}", flush=True)
    return f"<h1>Mode: {mode}</h1><p>Saved.</p>"
