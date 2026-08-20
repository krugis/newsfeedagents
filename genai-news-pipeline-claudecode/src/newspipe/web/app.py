"""Flask app factory for the newspipe web UI.

Three pages: a public `/` front page (today's top news, no login), and two
pages behind one login — `/admin` (configurable values + fetch/dedup/label
triggers) and `/news` (a paginated, DB-dump-style view of `arrivals`).
"""

from __future__ import annotations

import math

from flask import Blueprint, Flask, flash, redirect, render_template, request, url_for
from pydantic import ValidationError

from newspipe.config import get_settings
from newspipe.db.arrivals import count_arrivals, select_arrivals_page
from newspipe.db.engine import connect
from newspipe.web import settings_editor
from newspipe.web.actions import ActionBusyError, run_action
from newspipe.web.auth import bp as auth_bp
from newspipe.web.auth import login_required
from newspipe.web.frontpage import bp as frontpage_bp

admin_bp = Blueprint("admin", __name__)

TITLE_TRUNCATE = 100
PAGE_SIZES = (10, 20)
DEFAULT_PAGE_SIZE = 20

ACTION_LABELS = {
    "fetch": "Start Digest (Collect News)",
    "dedup": "Prepare News",
    "label": "Start Labeling",
}


def _summarize(name: str, stats: dict) -> str:
    duration = stats.get("duration_s")
    if name == "fetch":
        per_source = stats.get("per_source", {})
        new_total = sum(r.get("new", 0) for r in per_source.values() if r.get("status") == "ok")
        failed = sum(1 for r in per_source.values() if r.get("status") != "ok")
        return f"{len(per_source)} sources, {new_total} new arrivals, {failed} failed ({duration}s)"
    if name == "dedup":
        return (
            f"{stats.get('arrivals_processed', 0)} arrivals processed, "
            f"{stats.get('stories_created', 0)} stories created, "
            f"{stats.get('arrivals_attached', 0)} attached ({duration}s)"
        )
    if name == "label":
        return (
            f"{stats.get('stories_attempted', 0)} attempted, "
            f"{stats.get('labels_created', 0)} labeled, "
            f"{stats.get('failed', 0)} failed ({duration}s)"
        )
    return str(stats)  # pragma: no cover - unreachable, name is URL-validated upstream


@admin_bp.route("/admin", methods=["GET"])
@login_required
def admin_page():
    settings = get_settings()
    fields = [
        (field, settings_editor.field_display_value(settings, field))
        for field in settings_editor.EDITABLE_SETTINGS
    ]
    return render_template("admin.html", fields=fields, action_labels=ACTION_LABELS)


@admin_bp.route("/admin/settings", methods=["POST"])
@login_required
def save_settings():
    try:
        candidate = settings_editor.build_candidate(request.form)
    except (ValidationError, ValueError) as exc:
        flash(f"Not saved: {exc}", "error")
        return redirect(url_for("admin.admin_page"))
    settings_editor.save(candidate)
    flash("Settings saved.", "success")
    return redirect(url_for("admin.admin_page"))


@admin_bp.route("/admin/actions/<name>", methods=["POST"])
@login_required
def trigger_action(name):
    if name not in ACTION_LABELS:
        flash(f"Unknown action: {name}", "error")
        return redirect(url_for("admin.admin_page"))
    try:
        stats = run_action(name)
    except ActionBusyError as exc:
        flash(str(exc), "error")
    else:
        flash(f"{ACTION_LABELS[name]}: {_summarize(name, stats)}", "success")
    return redirect(url_for("admin.admin_page"))


@admin_bp.route("/news", methods=["GET"])
@login_required
def news_page():
    page_size = request.args.get("page_size", type=int, default=DEFAULT_PAGE_SIZE)
    if page_size not in PAGE_SIZES:
        page_size = DEFAULT_PAGE_SIZE
    with connect() as conn:
        total = count_arrivals(conn)
        total_pages = max(1, math.ceil(total / page_size))
        page = request.args.get("page", type=int, default=1)
        page = min(max(page, 1), total_pages)
        rows = select_arrivals_page(conn, limit=page_size, offset=(page - 1) * page_size)
    items = [
        {
            "fetched_at": row["fetched_at"],
            "source": row["source"],
            "title": (
                row["title"]
                if len(row["title"]) <= TITLE_TRUNCATE
                else row["title"][:TITLE_TRUNCATE] + "…"
            ),
        }
        for row in rows
    ]
    return render_template(
        "news.html",
        items=items,
        page=page,
        total_pages=total_pages,
        page_size=page_size,
        page_sizes=PAGE_SIZES,
        total=total,
    )


def create_app() -> Flask:
    """Build the Flask app: session secret, blueprints, login gate."""
    settings = get_settings()
    app = Flask(__name__)
    app.secret_key = settings.web_session_secret
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(frontpage_bp)
    return app
