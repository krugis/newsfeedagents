"""Session-based login for the web UI (a single admin account, no user table)."""

from __future__ import annotations

import secrets
from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from newspipe.config import get_settings

bp = Blueprint("auth", __name__)


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


@bp.route("/login", methods=["GET", "POST"])
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


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
