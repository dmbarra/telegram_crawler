"""Migration page: create the database, then move old folders one at a time.

Temporary — see src/migrate.py for how to remove it.
"""
import concurrent.futures
import json

from flask import Blueprint, abort, current_app, jsonify, render_template

from src import migrate
from viewer.download_routes import _json_body
from viewer.helpers import fmt_size

bp = Blueprint("migrate", __name__)


def _tg():
    return current_app.extensions["telegram"]


def _api(fn):
    try:
        return jsonify(fn())
    except migrate.MigrationError as e:
        return jsonify(error=str(e)), 409
    except concurrent.futures.TimeoutError:
        return jsonify(error="Telegram did not answer in time. Nothing was changed. Try again."), 504
    except Exception as e:  # Telegram errors (RPCError and friends), disk errors
        return jsonify(error=f"{type(e).__name__}: {e}"), 502


def _target():
    body = _json_body()
    folder = body.get("folder")
    if not isinstance(folder, str) or not folder:
        abort(400)
    try:
        channel_id = int(body.get("channel_id"))
    except (TypeError, ValueError):
        return None, None
    return folder, channel_id


def _need_channel():
    return jsonify(error="Pick the channel this folder belongs to"), 409


@bp.route("/migrate")
def migrate_page():
    tg = _tg()
    created = tg.library.is_created()
    folders = migrate.old_folders(tg) if created else []
    for f in folders:
        f["size_str"] = fmt_size(f["size"])
    return render_template(
        "migrate.html",
        created=created,
        ready=tg.status == "ready",
        tg_error=tg.error,
        folders=folders,
        done=tg.library.migrations() if created else [],
        channels=sorted(tg.channels, key=lambda c: (c["title"] or "").lower()),
    )


@bp.post("/api/migrate/create")
def create():
    _json_body()
    return _api(lambda: migrate.create_database(_tg()))


@bp.post("/api/migrate/preview")
def preview():
    folder, channel_id = _target()
    if channel_id is None:
        return _need_channel()
    return _api(lambda: migrate.preview(_tg(), folder, channel_id))


@bp.post("/api/migrate/run")
def run():
    folder, channel_id = _target()
    if channel_id is None:
        return _need_channel()
    return _api(lambda: migrate.run(_tg(), folder, channel_id))


@bp.route("/api/migrate/report/<int:migration_id>")
def report(migration_id):
    raw = _tg().library.migration_report(migration_id)
    if raw is None:
        abort(404)
    return jsonify(json.loads(raw))
