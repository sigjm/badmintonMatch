import re
from collections import Counter

from flask import Blueprint, redirect, render_template, request, url_for

from . import get_conn
from .groups import get_group, list_groups
from .meetups import board, on_info_changed, today
from .members import check_pin, list_members, set_info, set_pin

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


def _member_choices(conn):
    members = list_members(conn, guests=False)
    counts = Counter(r["nickname"] for r in members)
    return [(r["id"], f"{r['nickname']} (#{r['id']})" if counts[r["nickname"]] > 1 else r["nickname"]) for r in members]


@bp.route("/me", methods=["GET", "POST"])
def me():
    conn = get_conn()
    error = message = None
    if request.method == "POST":
        error, message = _save_me(conn)
    return render_template("me.html", choices=_member_choices(conn), error=error, message=message,
                           selected=request.form.get("member_id", type=int))


def _save_me(conn):
    member = conn.execute("SELECT * FROM members WHERE id = ? AND is_guest = 0",
                          (request.form.get("member_id", type=int),)).fetchone()
    pin = request.form.get("pin", "")
    if member is None:
        return "닉네임을 골라 주세요", None
    if not re.fullmatch(r"\d{4}", pin):
        return "PIN은 숫자 4자리로 입력해 주세요", None
    if member["pin_hash"] is None:
        set_pin(conn, member["id"], pin)
    elif not check_pin(conn, member["id"], pin):
        return "PIN이 맞지 않습니다. 잊었다면 운영진에게 초기화를 부탁해 주세요", None
    gender = request.form.get("gender") or member["gender"]
    grade = request.form.get("grade") or member["grade"]
    try:
        changed = set_info(conn, member["id"], gender, grade, by="self")
    except ValueError as e:
        return str(e), None
    on_info_changed(conn, member["id"], changed)
    return None, f"{member['nickname']}님 정보를 저장했습니다"
