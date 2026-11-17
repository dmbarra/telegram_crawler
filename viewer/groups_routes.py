"""Viewer tabs for automatic groups: by look (CLIP) and by face."""
import math

from flask import Blueprint, abort, current_app, jsonify, render_template, request

from src.groups import KINDS, LEVELS
from viewer import config
from viewer.download_routes import _json_body
from viewer.helpers import file_item, group_card, page_window
from viewer.routes import _per_page

bp = Blueprint("groups", __name__)

TITLES = {"look": "By look", "face": "By face"}
LEVEL_LABELS = ("Fewer, bigger groups", "Bigger groups", "Normal", "Smaller groups",
                "More, smaller groups")
MIN_IMAGES = 3


def _tg():
    return current_app.extensions["telegram"]


def _kind(kind):
    if kind not in KINDS:
        abort(404)
    return kind


@bp.route("/viewer/groups/<kind>")
def groups_page(kind):
    lib, grouper = _tg().library, _tg().grouper
    show_all = request.args.get("all") == "1"
    rows = lib.group_cards(_kind(kind), 1)
    shown = [r for r in rows if show_all or r["images"] >= MIN_IMAGES]
    return render_template(
        "groups.html", kind=kind, titles=TITLES, cards=[group_card(r, kind) for r in shown],
        hidden=len(rows) - len(shown), show_all=show_all, min_images=MIN_IMAGES,
        level=grouper.level(kind), levels=list(zip(range(1, 6), LEVEL_LABELS, LEVELS[kind])))


@bp.route("/viewer/groups/<kind>/<int:gid>")
def group_page(kind, gid):
    lib = _tg().library
    matches = lib.group_paths(_kind(kind), gid)
    if not matches:
        abort(404)
    per = _per_page()
    total = len(matches)
    pages = max(1, math.ceil(total / per))
    page = min(max(request.args.get("page", 1, type=int), 1), pages)
    page_matches = matches[(page - 1) * per:page * per]
    scores = dict(page_matches)
    files = [file_item(r) for r in lib.kept_rows([p for p, _ in page_matches])]
    for f in files:
        f["score"] = scores.get(f["rel"])
    return render_template(
        "group.html", kind=kind, gid=gid, titles=TITLES, files=files, total=total,
        page=page, pages=pages, window=page_window(page, pages), first_index=(page - 1) * per + 1,
        pager_endpoint="groups.group_page", pager_args={"kind": kind, "gid": gid},
        empty_text="This group has no files left.", tile_size=config.TILE_SIZE)


@bp.get("/api/groups/status")
def status():
    return jsonify(_tg().grouper.snapshot())


@bp.post("/api/groups/start")
def start():
    rebuild = _json_body().get("rebuild") is True
    err = _tg().grouper.start(rebuild)
    if err:
        return jsonify(error=err), 409
    return jsonify(ok=True)


@bp.post("/api/groups/tightness")
def tightness():
    body = _json_body()
    kind, level = body.get("kind"), body.get("level")
    if kind not in KINDS or isinstance(level, bool) or level not in range(1, 6):
        abort(400)
    _tg().grouper.set_level(kind, level)
    return jsonify(kind=kind, level=_tg().grouper.level(kind))
