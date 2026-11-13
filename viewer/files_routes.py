"""Files manager: browse the downloads folder and delete files or folders."""
import os
import shutil
import time

from flask import Blueprint, abort, current_app, jsonify, render_template, request

from src.config import DOWNLOADS_DIR
from viewer.download_routes import _json_body
from viewer.helpers import fmt_size

bp = Blueprint("files", __name__)

FILE_SORTS = {"name": "Name", "size": "Size", "date": "Date"}


def _dir_size(path) -> int:
    total = 0
    for root, _, names in os.walk(path):
        for n in names:
            try:
                total += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return total


@bp.route("/files")
def files_page():
    rel = request.args.get("p", "").strip("/")
    root = DOWNLOADS_DIR.resolve()
    try:
        folder = (DOWNLOADS_DIR / rel).resolve()
        folder.relative_to(root)
    except (ValueError, OSError):
        abort(403)
    if not folder.is_dir():
        abort(404)

    entries = []
    for e in os.scandir(folder):
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        is_dir = e.is_dir(follow_symlinks=False)
        size = _dir_size(e.path) if is_dir else st.st_size
        entries.append({
            "name": e.name,
            "rel": f"{rel}/{e.name}" if rel else e.name,
            "is_dir": is_dir,
            "size": size,
            "size_str": fmt_size(size),
            "mtime": st.st_mtime,
            "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
        })

    sort_key = request.args.get("sort", "name")
    if sort_key not in FILE_SORTS:
        sort_key = "name"
    if sort_key == "size":
        entries.sort(key=lambda x: x["size"], reverse=True)
    elif sort_key == "date":
        entries.sort(key=lambda x: x["mtime"], reverse=True)
    else:
        entries.sort(key=lambda x: x["name"].lower())
    entries.sort(key=lambda x: not x["is_dir"])

    parts = rel.split("/") if rel else []
    crumbs = [{"name": p, "rel": "/".join(parts[: i + 1])} for i, p in enumerate(parts)]
    disk = shutil.disk_usage(root)
    return render_template(
        "files.html",
        entries=entries,
        rel=rel,
        crumbs=crumbs,
        total_str=fmt_size(sum(e["size"] for e in entries)),
        free_str=fmt_size(disk.free),
        disk_str=fmt_size(disk.total),
        current_sort=sort_key,
        sort_options=FILE_SORTS,
    )


@bp.post("/api/files/delete")
def delete_files():
    paths = _json_body().get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        abort(400)
    summary, err = current_app.extensions["telegram"].delete_paths(paths)
    if err:
        return jsonify(error=err), 409
    return jsonify(removed=summary["removed"], freed=fmt_size(summary["freed"]))
