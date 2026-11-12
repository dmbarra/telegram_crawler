"""Viewer endpoints: channel index, one channel with pages, media + thumbnails."""
import math
import mimetypes
from flask import (
    Blueprint, abort, make_response, render_template, request,
    send_file, send_from_directory,
)
from viewer import config
from viewer.scanner import scan_media
from viewer.helpers import (
    apply_filters, sort_files, SORT_OPTIONS, DEFAULT_SORT,
    CHANNEL_SORTS, DEFAULT_CHANNEL_SORT, channel_summary, sort_channels, page_window,
)
from viewer.thumbnails import get_thumbnail

bp = Blueprint("viewer", __name__)

PER_PAGE_OPTIONS = sorted({20, 50, 100, config.PAGE_SIZE})
TYPES = ("", "image", "video", "audio")


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
    data = scan_media(force=request.args.get("refresh") == "1")
    q = request.args.get("q", "").strip().lower()
    sort_key = request.args.get("sort", DEFAULT_CHANNEL_SORT)
    if sort_key not in CHANNEL_SORTS:
        sort_key = DEFAULT_CHANNEL_SORT

    cards = [channel_summary(name, files) for name, files in data.items()
             if not q or q in name.lower()]
    return render_template(
        "index.html",
        cards=sort_channels(cards, sort_key),
        total_channels=len(data),
        total_files=sum(len(f) for f in data.values()),
        query=q,
        current_sort=sort_key,
        sort_options=CHANNEL_SORTS,
    )


# ─── GET /viewer/<channel> — one channel, paged ─────────────────
@bp.route("/viewer/<channel>")
def channel_page(channel):
    data = scan_media(force=request.args.get("refresh") == "1")
    if channel not in data:
        abort(404)

    kind = request.args.get("type", "")
    if kind not in TYPES:
        kind = ""
    q = request.args.get("q", "").strip().lower()
    sort_key = _normalize_sort(request.args.get("sort", DEFAULT_SORT))
    per = _per_page()

    files = sort_files(apply_filters(data[channel], kind, q), sort_key)
    pages = max(1, math.ceil(len(files) / per))
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    page = min(max(page, 1), pages)

    resp = make_response(render_template(
        "channel.html",
        channel=channel,
        files=files[(page - 1) * per: page * per],
        total=len(files),
        channel_total=len(data[channel]),
        page=page,
        pages=pages,
        window=page_window(page, pages),
        first_index=(page - 1) * per + 1,
        url_args={k: v for k, v in {"type": kind, "sort": sort_key, "q": q}.items() if v},
        current_type=kind,
        current_sort=sort_key,
        sort_options=SORT_OPTIONS,
        query=q,
        per=per,
        per_options=PER_PAGE_OPTIONS,
        tile_size=config.TILE_SIZE,
    ))
    if "per" in request.args:
        resp.set_cookie("per_page", str(per), max_age=365 * 24 * 3600, samesite="Lax")
    return resp


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