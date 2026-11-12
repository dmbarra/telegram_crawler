"""Discover page (find + join groups) and the duplicates page, with their JSON APIs."""
import concurrent.futures

from flask import Blueprint, abort, current_app, jsonify, render_template, request

from src.config import JOIN_DAILY_LIMIT, JOIN_MIN_INTERVAL
from src.discover import INVITE_HASH_RE, DiscoverError
from viewer.download_routes import _json_body
from viewer.helpers import fmt_size

bp = Blueprint("discover", __name__)


def _tg():
    return current_app.extensions["telegram"]


def _api(fn):
    """Run fn() and turn the result or the problem into JSON for the page."""
    try:
        return jsonify(fn())
    except DiscoverError as e:
        return jsonify(error=str(e)), 409
    except concurrent.futures.TimeoutError:
        return jsonify(error="Telegram did not answer in time. Try again."), 504
    except Exception as e:  # Telegram errors (RPCError and friends)
        return jsonify(error=f"Telegram error: {e}"), 502


def _int(body, key) -> int:
    try:
        return int(body.get(key))
    except (TypeError, ValueError):
        abort(400)


def _hash(body) -> str:
    h = body.get("hash")
    if not isinstance(h, str) or not INVITE_HASH_RE.match(h):
        abort(400)
    return h


def _text(body, key, max_len) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        abort(400)
    return value.strip()


# ─── pages ──────────────────────────────────────────────────────
@bp.route("/discover")
def discover_page():
    return render_template("discover.html", join_limit=JOIN_DAILY_LIMIT, join_interval=JOIN_MIN_INTERVAL)


@bp.route("/duplicates")
def duplicates_page():
    hashes = _tg().hashes
    groups = hashes.find_duplicates()
    copies = sum(len(g["dups"]) for g in groups)
    return render_template(
        "duplicates.html",
        groups=groups,
        copies=copies,
        wasted=fmt_size(sum(g["size"] * len(g["dups"]) for g in groups)),
        scanning=hashes.scanning,
        scanned=hashes.scanned,
    )


# ─── discover API ───────────────────────────────────────────────
@bp.route("/api/discover/state")
def state():
    return jsonify(_tg().discovery.snapshot())


@bp.post("/api/discover/search")
def search():
    q = _text(_json_body(), "q", 100)
    return _api(lambda: {"results": _tg().discovery.search(q)})


@bp.post("/api/discover/similar")
def similar():
    chat_id = _int(_json_body(), "id")
    return _api(lambda: {"results": _tg().discovery.similar(chat_id)})


@bp.post("/api/discover/details")
def details():
    chat_id = _int(_json_body(), "id")
    return _api(lambda: _tg().discovery.details(chat_id))


@bp.post("/api/discover/add")
def add_public():
    chat_id = _int(_json_body(), "id")
    return _api(lambda: _tg().discovery.add_public(chat_id))


@bp.post("/api/discover/join")
def join_public():
    chat_id = _int(_json_body(), "id")
    return _api(lambda: _tg().discovery.join_public(chat_id))


@bp.post("/api/discover/invites/add")
def add_links():
    text = _text(_json_body(), "text", 5000)
    return _api(lambda: _tg().discovery.add_links(text))


@bp.post("/api/discover/invites/preview")
def preview_invite():
    h = _hash(_json_body())
    return _api(lambda: _tg().discovery.preview_invite(h))


@bp.post("/api/discover/invites/join")
def join_invite():
    h = _hash(_json_body())
    return _api(lambda: _tg().discovery.join_invite(h))


@bp.post("/api/discover/invites/ignore")
def ignore_invite():
    body = _json_body()
    h = _hash(body)
    return _api(lambda: _tg().discovery.set_ignored(h, bool(body.get("ignored", True))))


@bp.post("/api/discover/scan")
def start_scan():
    body = _json_body()
    ids = body.get("channel_ids")
    keywords = body.get("keywords", "")
    if not isinstance(ids, list) or not isinstance(keywords, str):
        abort(400)
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        abort(400)
    limit = _int(body, "limit")
    return _api(lambda: _tg().discovery.start_scan(ids, limit, keywords.split(",")) or {"ok": True})


@bp.post("/api/discover/scan/stop")
def stop_scan():
    _json_body()
    _tg().discovery.stop_scan()
    return jsonify(ok=True)


# ─── duplicates API ─────────────────────────────────────────────
@bp.post("/api/duplicates/remove")
def remove_duplicates():
    paths = _json_body().get("paths")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        abort(400)
    summary, err = _tg().remove_duplicates(paths)
    if err:
        return jsonify(error=err), 409
    return jsonify(removed=summary["removed"], freed=fmt_size(summary["freed"]))
