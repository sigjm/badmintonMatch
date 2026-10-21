from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from . import get_conn
from .groups import get_group, list_groups
from .meetups import board, today

bp = Blueprint("public", __name__)


@bp.get("/")
def index():
    return redirect(url_for("public.board_view"))


def _started(conn, group_id=None):
    """오늘(정모일 기준) 시작된 정모, 없으면 가장 최근에 시작된 정모."""
    where, args = ("AND group_id = ?", (group_id,)) if group_id else ("", ())
    return (conn.execute(f"SELECT * FROM meetups WHERE started = 1 AND date = ? {where} ORDER BY id DESC",
                         (today(), *args)).fetchone()
            or conn.execute(f"SELECT * FROM meetups WHERE started = 1 {where} ORDER BY date DESC, id DESC", args).fetchone())


@bp.get("/board")
def board_view():
    conn = get_conn()
    group = get_group(conn, request.args.get("g", type=int))
    if group is None:  # 모임을 안 고르면 가장 최근에 진행한 정모의 모임
        latest = _started(conn)
        group = get_group(conn, latest["group_id"]) if latest else None
        group = group or list_groups(conn)[0]
    m = _started(conn, group["id"])
    return render_template("board.html", m=m, board=board(conn, m["id"]) if m else None,
                           group=group, groups=list_groups(conn))


@bp.get("/board/updates")
def board_updates():
    conn = get_conn()
    group = get_group(conn, request.args.get("g", type=int))
    if group is None:
        latest = _started(conn)
        group = get_group(conn, latest["group_id"]) if latest else None
        group = group or list_groups(conn)[0]
    m = _started(conn, group["id"])
    current_board = board(conn, m["id"]) if m else None
    response = jsonify(
        meetup_id=m["id"] if m else None,
        court_board=render_template("_court_board_content.html", m=m, board=current_board, admin=False) if m else "",
        queue=render_template("queue_list.html", board=current_board) if m else "",
        reservations=current_board.get("reservations", []) if current_board else [],
        active_courts=sum(bool(c["game_id"]) for c in current_board["courts"]) if m else 0,
        queue_count=len(current_board["queue"]) if m else 0,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/me")
def me():
    return render_template("me.html")
