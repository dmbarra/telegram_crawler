"""Viewer endpoints: channel index, one channel with pages, media + thumbnails.

Everything listed comes from the library (src/db.py), not from walking folders.
"""
import math
import mimetypes
from flask import (
    Blueprint, abort, current_app, jsonify, make_response, render_template, request,
    send_file, send_from_directory,
)
from src.db import RATINGS
from src.faces import MIN_SCORES, SAME_PERSON, same_person
from viewer import config
from viewer.download_routes import _json_body
from viewer.helpers import (
    SORT_OPTIONS, DEFAULT_SORT, CHANNEL_SORTS, DEFAULT_CHANNEL_SORT,
    card, file_item, sort_channels, page_window, paginate,
)
from viewer.thumbnails import get_thumbnail

bp = Blueprint("viewer", __name__)

PER_PAGE_OPTIONS = sorted({20, 50, 100, config.PAGE_SIZE})
VIEW_KINDS = ("images", "videos", "audio")
TYPES = {"": "", "image": "images", "video": "videos", "audio": "audio"}
RATING_FILTERS = {"": "All ratings", "good": "Good", "bad": "Not good",
                  "delete": "To delete", "none": "Not rated"}
FACE_FILTER_LABELS = {"": "All (faces)", "yes": "With faces", "no": "No faces",
                      "none": "Not analyzed"}


def _library():
    return current_app.extensions["telegram"].library


def _normalize_sort(raw: str) -> str:
    return raw if raw in SORT_OPTIONS else DEFAULT_SORT


def _per_page() -> int:
    for raw in (request.args.get("per"), request.cookies.get("per_page")):
        try:
            if int(raw) in PER_PAGE_OPTIONS:
                return int(raw)
        except (TypeError, ValueError):
            pass
    return config.PAGE_SIZE


# ─── GET /viewer — channel index ────────────────────────────────
@bp.route("/viewer")
def index():
    lib = _library()
    q = request.args.get("q", "").strip().lower()
    sort_key = request.args.get("sort", DEFAULT_CHANNEL_SORT)
    if sort_key not in CHANNEL_SORTS:
        sort_key = DEFAULT_CHANNEL_SORT

    all_cards = [card(r) for r in lib.channel_cards(VIEW_KINDS)]
    cards = [c for c in all_cards if not q or q in c["title"].lower()]
    paged = paginate(sort_channels(cards, sort_key), _per_page(), request.args.get("page", 1, type=int))
    return render_template(
        "index.html",
        cards=paged.pop("items"),
        **paged,
        matched=len(cards),
        pager_endpoint="viewer.index",
        pager_args={k: v for k, v in {"q": q, "sort": sort_key}.items() if v},
        total_channels=len(all_cards),
        total_files=sum(c["count"] for c in all_cards),
        query=q,
        current_sort=sort_key,
        sort_options=CHANNEL_SORTS,
    )


# ─── GET /viewer/<channel id> — one channel, paged ──────────────
@bp.route("/viewer/<int:channel>")
def channel_page(channel):
    lib = _library()
    _, channel_total = lib.channel_files(channel, VIEW_KINDS, limit=0)
    if not channel_total:
        abort(404)

    kind = request.args.get("type", "")
    if kind not in TYPES:
        kind = ""
    rating = request.args.get("rating", "")
    if rating not in RATING_FILTERS:
        rating = ""
    faces = request.args.get("faces", "")
    if faces not in FACE_FILTER_LABELS:
        faces = ""
    q = request.args.get("q", "").strip().lower()
    sort_key = _normalize_sort(request.args.get("sort", DEFAULT_SORT))
    per = _per_page()
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1

    # Same-person mode: every image with a face close to this one, best first.
    # The other filters do not apply in this mode.
    face_id = request.args.get("face", type=int)
    min_score = request.args.get("min", SAME_PERSON, type=float)
    if min_score not in MIN_SCORES:
        min_score = SAME_PERSON
    face_query = None
    scores = {}
    if face_id is not None:
        query = lib.face(face_id)
        if not query:
            abort(404)
        matches = same_person(query["vector"], lib.channel_faces(channel), min_score)
        total = len(matches)
        pages = max(1, math.ceil(total / per))
        page = min(max(page, 1), pages)
        page_matches = matches[(page - 1) * per:page * per]
        rows = lib.kept_rows([m[0] for m in page_matches])
        scores = {m[0]: m[1] for m in page_matches}
        face_query = {"id": face_id, "name": query["path"].rsplit("/", 1)[-1], "min": min_score}
    else:
        _, total = lib.channel_files(channel, VIEW_KINDS, TYPES[kind], q, limit=0,
                                     rating=rating, faces=faces)
        pages = max(1, math.ceil(total / per))
        page = min(max(page, 1), pages)
        rows, _ = lib.channel_files(channel, VIEW_KINDS, TYPES[kind], q, sort_key,
                                    limit=per, offset=(page - 1) * per, rating=rating, faces=faces)
    files = [file_item(r) for r in rows]
    for f in files:
        f["score"] = scores.get(f["rel"])
    url_args = {k: v for k, v in ({"face": face_id, "min": min_score} if face_query else
                                  {"type": kind, "sort": sort_key, "q": q, "rating": rating,
                                   "faces": faces}).items() if v}

    resp = make_response(render_template(
        "channel.html",
        channel=channel,
        channel_title=lib.channel_titles().get(str(channel), f"Channel {channel}"),
        files=files,
        total=total,
        channel_total=channel_total,
        page=page,
        pages=pages,
        window=page_window(page, pages),
        first_index=(page - 1) * per + 1,
        face_query=face_query,
        pager_endpoint="viewer.channel_page",
        pager_args={"channel": channel, **url_args},
        empty_text=("No image has a face this close. Try a lower score." if face_query
                    else "No files match your filters."),
        min_scores=MIN_SCORES,
        current_faces=faces,
        face_options=FACE_FILTER_LABELS,
        current_type=kind,
        current_rating=rating,
        rating_options=RATING_FILTERS,
        current_sort=sort_key,
        sort_options=SORT_OPTIONS,
        query=q,
        per=per,
        per_options=PER_PAGE_OPTIONS,
        tile_size=config.TILE_SIZE,
        study_source=lib.is_study_source(channel),
    ))
    if "per" in request.args:
        resp.set_cookie("per_page", str(per), max_age=365 * 24 * 3600, samesite="Lax")
    return resp


# ─── GET /api/faces?p=<path> — face boxes, in original pixels ───
@bp.get("/api/faces")
def face_boxes():
    path = request.args.get("p", "")
    if not path:
        abort(400)
    return jsonify(faces=[dict(r) for r in _library().face_boxes(path)])


# ─── POST /api/rate {path, rating} — rating null clears it ──────
@bp.post("/api/rate")
def rate():
    body = _json_body()
    path, rating = body.get("path"), body.get("rating")
    if not isinstance(path, str) or not path or (rating is not None and rating not in RATINGS):
        abort(400)
    lib = _library()
    if path not in lib.known_paths([path]):
        return jsonify(error="File is not in the database"), 404
    lib.set_rating(path, rating)
    return jsonify(path=path, rating=rating, marked=lib.marked_summary()["count"])


# ─── ML analysis (study-source channels only) ──────────────────
def _analyzer():
    return current_app.extensions["telegram"].analyzer


def _channel_arg(body):
    channel = body.get("channel")
    if isinstance(channel, bool) or not isinstance(channel, int):
        abort(400)
    return channel


@bp.get("/api/ml/status")
def ml_status():
    return jsonify(_analyzer().snapshot(request.args.get("channel", type=int)))


@bp.post("/api/ml/start")
def ml_start():
    err = _analyzer().start(_channel_arg(_json_body()))
    if err:
        return jsonify(error=err), 409
    return jsonify(ok=True)


@bp.post("/api/ml/stop")
def ml_stop():
    _json_body()
    _analyzer().stop()
    return jsonify(ok=True)


@bp.post("/api/study-source")
def study_source():
    body = _json_body()
    channel, on = _channel_arg(body), body.get("on")
    if not isinstance(on, bool):
        abort(400)
    _library().set_study_source(channel, on)
    if not on:
        _analyzer().stop_channel(channel)
    return jsonify(channel=channel, on=_library().is_study_source(channel))


# ─── GET /media?p=<relative-path>[&download=1] ──────────────────
@bp.route("/media")
def serve_media():
    filepath = request.args.get("p", "")
    as_attachment = request.args.get("download") == "1"
    if not filepath:
        abort(400)

    try:
        target = (config.DOWNLOADS_DIR / filepath).resolve()
        target.relative_to(config.DOWNLOADS_DIR.resolve())
    except (ValueError, OSError):
        abort(403)

    if not target.is_file():
        abort(404)

    resp = make_response(send_from_directory(
        str(config.DOWNLOADS_DIR.resolve()),
        filepath,
        mimetype=mimetypes.guess_type(str(target))[0],
        conditional=True,
        as_attachment=as_attachment,
    ))

    # Cache: browser can reuse the file (including Range requests) for 1 day.
    # conditional=True → ETag based on mtime+size → auto-invalidates on re-download.
    if not as_attachment:
        resp.headers["Cache-Control"] = "public, max-age=86400"
        resp.headers["Accept-Ranges"] = "bytes"

    return resp


# ─── GET /thumb?p=<relative-path> ──────────────────────────────
@bp.route("/thumb")
def serve_thumb():
    filepath = request.args.get("p", "")
    if not filepath:
        abort(400)

    try:
        target = (config.DOWNLOADS_DIR / filepath).resolve()
        target.relative_to(config.DOWNLOADS_DIR.resolve())
    except (ValueError, OSError):
        abort(403)

    if not target.is_file():
        abort(404)

    thumb = get_thumbnail(filepath, target)
    if not thumb:
        abort(404)

    return send_file(thumb, mimetype="image/jpeg", max_age=86400, conditional=True)