"""Files manager: channels and files from the library, delete files or whole channels."""
import math
import shutil

from flask import Blueprint, abort, current_app, jsonify, render_template, request

from src.config import DOWNLOADS_DIR
from src.migrate import old_folder_names
from viewer.download_routes import _json_body
from viewer.helpers import card, file_item, fmt_size, page_window

bp = Blueprint("files", __name__)

FILE_SORTS = {"newest": "Newest", "oldest": "Oldest", "size_desc": "Largest", "name_asc": "Name"}
CHANNEL_SORTS = {"name": "Name", "size": "Size", "files": "Files"}
PER_PAGE = 100


def _library():
    return current_app.extensions["telegram"].library


@bp.route("/files")
def files_page():
    lib = _library()
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(DOWNLOADS_DIR)
    common = {"free_str": fmt_size(disk.free), "disk_str": fmt_size(disk.total),
              "old_folders": len(old_folder_names(lib))}

    channel = request.args.get("c", type=int)
    if channel is None:
        sort_key = request.args.get("sort", "name")
        if sort_key not in CHANNEL_SORTS:
            sort_key = "name"
        channels = [card(r) for r in lib.channel_cards()]
        if sort_key == "size":
            channels.sort(key=lambda c: c["size"], reverse=True)
        elif sort_key == "files":
            channels.sort(key=lambda c: c["count"], reverse=True)
        else:
            channels.sort(key=lambda c: c["title"].lower())
        return render_template(
            "files.html", channel=None, channels=channels, current_sort=sort_key,
            sort_options=CHANNEL_SORTS,
            total_str=fmt_size(sum(c["size"] for c in channels)), **common)

    sort_key = request.args.get("sort", "newest")
    if sort_key not in FILE_SORTS:
        sort_key = "newest"
    _, total = lib.channel_files(channel, limit=0)
    if not total:
        abort(404)
    pages = max(1, math.ceil(total / PER_PAGE))
    page = min(max(request.args.get("page", 1, type=int), 1), pages)
    rows, _ = lib.channel_files(channel, sort=sort_key, limit=PER_PAGE, offset=(page - 1) * PER_PAGE)
    return render_template(
        "files.html", channel=channel,
        channel_title=lib.channel_titles().get(str(channel), f"Channel {channel}"),
        entries=[file_item(r) for r in rows], total=total, page=page, pages=pages,
        window=page_window(page, pages), current_sort=sort_key, sort_options=FILE_SORTS, **common)


@bp.post("/api/files/delete")
def delete_files():
    body = _json_body()
    paths = body.get("paths") or []
    channels = body.get("channels") or []
    if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
        abort(400)
    if not isinstance(channels, list) or not all(isinstance(c, int) for c in channels):
        abort(400)
    if not paths and not channels:
        abort(400)
    summary, err = current_app.extensions["telegram"].delete_files(paths, channels)
    if err:
        return jsonify(error=err), 409
    return jsonify(removed=summary["removed"], freed=fmt_size(summary["freed"]),
                   failed=summary["failed"])
