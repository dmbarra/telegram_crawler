"""All HTTP endpoints. Blueprint pattern → import once, register anywhere."""
import mimetypes
from flask import (
    Blueprint, abort, jsonify, make_response, render_template, request,
    send_file, send_from_directory,
)
from viewer import config
from viewer.scanner import scan_media
from viewer.helpers import (
    apply_filters, sort_files, SORT_OPTIONS, DEFAULT_SORT,
)
from viewer.thumbnails import get_thumbnail

bp = Blueprint("viewer", __name__)


def _normalize_sort(raw: str) -> str:
    return raw if raw in SORT_OPTIONS else DEFAULT_SORT


# ─── GET /viewer ────────────────────────────────────────────────
@bp.route("/viewer")
def index():
    force = request.args.get("refresh") == "1"
    channel = request.args.get("channel", "")
    kind = request.args.get("type", "")
    q = request.args.get("q", "").strip().lower()
    sort_key = _normalize_sort(request.args.get("sort", DEFAULT_SORT))

    data = scan_media(force=force)

    paged = {}
    for ch, files in data.items():
        if channel and ch != channel:
            continue
        ff = apply_filters(files, kind, q)
        if not ff:
            continue
        ff = sort_files(ff, sort_key)
        paged[ch] = {
            "files": ff[: config.PAGE_SIZE],
            "total": len(ff),
            "has_more": len(ff) > config.PAGE_SIZE,
        }

    return render_template(
        "index.html",
        data=paged,
        channels=sorted(data.keys()),
        current_channel=channel,
        current_type=kind,
        current_sort=sort_key,
        sort_options=SORT_OPTIONS,
        query=q,
        total_matching=sum(i["total"] for i in paged.values()),
        page_size=config.PAGE_SIZE,
        tile_size=config.TILE_SIZE,
    )


# ─── GET /api/files ─────────────────────────────────────────────
@bp.route("/api/files")
def api_files():
    channel = request.args.get("channel", "")
    try:
        offset = max(0, int(request.args.get("offset", "0")))
        limit = max(1, min(int(request.args.get("limit", str(config.PAGE_SIZE))), 200))
    except ValueError:
        abort(400)
    kind = request.args.get("type", "")
    q = request.args.get("q", "").strip().lower()
    sort_key = _normalize_sort(request.args.get("sort", DEFAULT_SORT))

    data = scan_media(force=False)
    if channel not in data:
        return jsonify({"files": [], "has_more": False, "next_offset": offset, "total": 0})

    files = apply_filters(data[channel], kind, q)
    files = sort_files(files, sort_key)
    page = files[offset:offset + limit]
    next_off = offset + len(page)
    return jsonify({
        "files": page,
        "has_more": next_off < len(files),
        "next_offset": next_off,
        "total": len(files),
    })


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