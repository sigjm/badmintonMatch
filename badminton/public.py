from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request, url_for

from . import announce, get_conn
from .groups import get_group, list_groups
from .meetups import board, fill_courts, today

bp = Blueprint("public", __name__)


@bp.get("/")
def index():
    return redirect(url_for("public.board_view"))


def _started(conn, group_id=None):
    """오늘(정모일 기준) 시작된 정모, 없으면 가장 최근에 시작된 정모."""
    where, args = ("AND group_id = ?", (group_id,)) if group_id else ("", ())
    return (conn.execute(f"SELECT * FROM meetups WHERE started = 1 AND archived_at IS NULL AND date = ? {where} ORDER BY id DESC",
                         (today(), *args)).fetchone()
            or conn.execute(f"SELECT * FROM meetups WHERE started = 1 AND archived_at IS NULL {where} ORDER BY date DESC, id DESC", args).fetchone())


@bp.get("/board")
def board_view():
    conn = get_conn()
    group = get_group(conn, request.args.get("g", type=int))
    if group is None:  # 모임을 안 고르면 가장 최근에 진행한 정모의 모임
        latest = _started(conn)
        group = get_group(conn, latest["group_id"]) if latest else None
        group = group or list_groups(conn)[0]
    m = _started(conn, group["id"])
    if m:
        fill_courts(conn, m["id"])
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
    if m:
        fill_courts(conn, m["id"])  # 쉬는 시간(15초)이 지난 코트 채우기
    current_board = board(conn, m["id"]) if m else None
    response = jsonify(
        meetup_id=m["id"] if m else None,
        court_board=render_template("_court_board_content.html", m=m, board=current_board, admin=False) if m else "",
        reservation_list=render_template("reservation_list.html", board=current_board) if m else "",
        queue=render_template("queue_list.html", board=current_board) if m else "",
        excluded=render_template("excluded_list.html", board=current_board) if m else "",
        reservations=current_board.get("reservations", []) if current_board else [],
        active_courts=sum(bool(c["game_id"]) for c in current_board["courts"]) if m else 0,
        queue_count=len(current_board["queue"]) if m else 0,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _mp3(text: str):
    try:
        audio = announce.speak(text)
    except Exception:  # 모델이 없거나 합성 실패 → 화면이 기기 내장 음성으로 대신 읽는다
        abort(503)
    return Response(audio, mimetype="audio/mpeg", headers={"Cache-Control": "public, max-age=3600"})


@bp.get("/announce/<int:gid>.mp3")
def announce_game(gid):
    conn = get_conn()
    g = conn.execute("SELECT court, a1, a2, b1, b2 FROM games WHERE id = ?", (gid,)).fetchone()
    if g is None:
        abort(404)
    ids = [g["a1"], g["a2"], g["b1"], g["b2"]]
    names = {r["id"]: r["nickname"] for r in conn.execute("SELECT id, nickname FROM members WHERE id IN (?, ?, ?, ?)", ids)}
    return _mp3(announce.announcement_text(g["court"], [names.get(i, "") for i in ids]))


@bp.get("/announce/hello.mp3")
def announce_hello():
    return _mp3("안내 방송을 시작합니다.")


@bp.get("/me")
def me():
    return render_template("me.html")
