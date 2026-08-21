"""Session-based login for the web UI (a single admin account, no user table).

The login route's path is `ADMIN_LOGIN_PATH` (default `/login`), not a fixed
`/login` decorator — see `build_auth_blueprint`. This lets it be moved to an
unguessable path per deployment without a code change, and kept out of
`base.html`'s nav entirely.
"""

from __future__ import annotations

import secrets
from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from newspipe.config import get_settings


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def _safe_next(candidate: str | None) -> str:
    """Only honor same-site relative redirects (no open redirect via ?next=)."""
    if candidate and candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return url_for("admin.admin_page")


def login():
    if request.method == "POST":
        settings = get_settings()
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if settings.admin_password is None:
            flash("ADMIN_PASSWORD is not set — login is disabled until it's configured.", "error")
        elif username == settings.admin_username and secrets.compare_digest(
            password, settings.admin_password
        ):
            session["authenticated"] = True
            return redirect(_safe_next(request.args.get("next")))
        else:
            flash("Invalid username or password.", "error")
    return render_template("login.html")


def logout():
    session.clear()
    return redirect(url_for("auth.login"))


def build_auth_blueprint(login_path: str) -> Blueprint:
    """Build the auth blueprint with the login form mounted at `login_path`.

    A factory rather than a module-level `Blueprint` + `@bp.route("/login")`
    decorator so the path can vary by deployment (`ADMIN_LOGIN_PATH`); both
    endpoints keep fixed names (`auth.login`, `auth.logout`) so every
    `url_for("auth.login", ...)` call elsewhere (redirects, templates) works
    unchanged regardless of the configured path.
    """
    bp = Blueprint("auth", __name__)
    bp.add_url_rule(login_path, "login", login, methods=["GET", "POST"])
    bp.add_url_rule("/logout", "logout", logout, methods=["POST"])
    return bp
