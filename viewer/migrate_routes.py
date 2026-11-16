"""Migration page: move old title folders to the new layout, one folder at a time.

Temporary — see src/migrate.py for how to remove it.
"""
import concurrent.futures

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
        return jsonify(error="Telegram did not answer in time. Try again."), 504
    except Exception as e:  # Telegram errors (RPCError and friends)
        return jsonify(error=f"Error: {e}"), 502


def _target():
    body = _json_body()
    folder = body.get("folder")
    if not isinstance(folder, str) or not folder:
        abort(400)
    try:
        channel_id = int(body.get("channel_id"))
    except (TypeError, ValueError):
        abort(400)
    return folder, channel_id


@bp.route("/migrate")
def migrate_page():
    tg = _tg()
    folders = migrate.old_folders(tg)
    for f in folders:
        f["size_str"] = fmt_size(f["size"])
    return render_template(
        "migrate.html",
        folders=folders,
        channels=sorted(tg.channels, key=lambda c: (c["title"] or "").lower()),
        ready=tg.status == "ready",
        has_old_files=any(p.exists() for p in (migrate.STATE_PATH, migrate.HASHES_PATH,
                                               migrate.CHANNELS_TXT)),
    )


@bp.post("/api/migrate/import")
def import_old():
    _json_body()
    return _api(lambda: migrate.backup_and_import(_tg()))


@bp.post("/api/migrate/preview")
def preview():
    folder, channel_id = _target()
    return _api(lambda: migrate.preview(_tg(), folder, channel_id))


@bp.post("/api/migrate/run")
def run():
    folder, channel_id = _target()
    return _api(lambda: migrate.run(_tg(), folder, channel_id))
