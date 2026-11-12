"""Home page + downloader page and its JSON API."""
from flask import Blueprint, abort, current_app, jsonify, render_template, request

from src.config import BATCH_SIZE, MAX_MESSAGES
from src.runner import MEDIA_LABELS

bp = Blueprint("downloader", __name__)


def _tg():
    return current_app.extensions["telegram"]


def _json_body() -> dict:
    # JSON-only POSTs: a foreign site can't send these without a CORS preflight, which we never allow.
    if not request.is_json:
        abort(415)
    return request.get_json(silent=True) or {}


def _result(err):
    return (jsonify(error=err), 409) if err else jsonify(ok=True)


# ─── pages ──────────────────────────────────────────────────────
@bp.route("/")
def home():
    return render_template("home.html")


@bp.route("/download")
def download_page():
    return render_template(
        "download.html",
        media_labels=MEDIA_LABELS,
        default_batch=BATCH_SIZE,
        default_max=MAX_MESSAGES,
    )


# ─── API ────────────────────────────────────────────────────────
@bp.route("/api/tg/status")
def status():
    try:
        since = int(request.args.get("since", "0"))
    except ValueError:
        abort(400)
    return jsonify(_tg().snapshot(since))


@bp.route("/api/tg/channels")
def channels():
    tg = _tg()
    return jsonify({
        "channels": [
            {
                "id": c["id"],
                "title": c["title"],
                "username": c["username"],
                "kind": "channel" if c["is_channel"] else "group",
                "extra": c["extra"],
                "extra_source": c["extra_source"],
            }
            for c in tg.channels
        ],
        "failed": tg.failed_extras,
    })


@bp.post("/api/tg/channels/refresh")
def refresh_channels():
    _json_body()
    return _result(_tg().refresh_channels())


def _ident(body) -> str:
    ident = body.get("ident")
    if not isinstance(ident, str):
        abort(400)
    ident = ident.strip()
    if not ident or len(ident) > 200 or "\n" in ident or "\r" in ident:
        abort(400)
    return ident


@bp.post("/api/tg/channels/add")
def add_channel():
    channel_id, err = _tg().add_channel(_ident(_json_body()))
    if channel_id is None:
        return jsonify(error=err), 409
    return jsonify(ok=True, id=channel_id, note=err)


@bp.post("/api/tg/channels/remove")
def remove_channel():
    return _result(_tg().remove_channel(_ident(_json_body())))


@bp.post("/api/tg/start")
def start():
    body = _json_body()
    ids = body.get("channel_ids")
    if not isinstance(ids, list):
        abort(400)
    try:
        ids = [int(i) for i in ids]
        batch_size = int(body.get("batch_size", BATCH_SIZE))
        max_messages = int(body.get("max_messages", MAX_MESSAGES))
    except (TypeError, ValueError):
        abort(400)
    return _result(_tg().start_job(ids, str(body.get("media", "")), batch_size, max_messages))


@bp.post("/api/tg/stop")
def stop():
    _json_body()
    _tg().stop_job()
    return jsonify(ok=True)
