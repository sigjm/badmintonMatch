import hmac
from functools import wraps

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from . import get_conn
from .db import get_setting, set_setting
from .meetups import (add_attendee, add_pasted, board, end_game, get_or_create_meetup, on_info_changed, redraw,
                      refill_today, set_status, start, sync_somoim, today)
from .members import changes, create_member, list_members, reset_pin, set_info
from .somoim import SomoimError, attendees, events_on, fetch_group

bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)
    return wrapped


def _blank_none(value):
    return value or None


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        expected = current_app.config["ADMIN_PASSWORD"]
        if expected and hmac.compare_digest(request.form.get("password", ""), expected):
            session["admin"] = True
            return redirect(url_for("admin.home"))
        error = "비밀번호가 맞지 않습니다"
    return render_template("admin_login.html", error=error)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


def _home(**ctx):
    meetups = get_conn().execute("SELECT * FROM meetups ORDER BY date DESC, id DESC LIMIT 10").fetchall()
    ctx.setdefault("date", today())
    return render_template("admin_home.html", meetups=meetups, **ctx)


@bp.get("")
@admin_required
def home():
    return _home()


def _import(date: str, eid: str | None = None, name: str = ""):
    """소모임에서 그 날짜 정모 참석자를 가져와 동기화한다. 실패하면 DB를 건드리지 않고 붙여넣기를 안내한다."""
    try:
        group = fetch_group(current_app.config["SOMOIM_GROUP_ID"])
    except SomoimError as e:
        return _home(date=date, name=name, error=f"소모임에서 명단을 가져오지 못했습니다 ({e}). 붙여넣기로 등록해 주세요.")
    events = [e for e in events_on(group, date) if eid is None or e.eid == eid]
    if not events:
        return _home(date=date, name=name, error=f"{date}에 열리는 정모를 소모임에서 찾지 못했습니다. 붙여넣기로 등록해 주세요.")
    if len(events) > 1:
        return _home(date=date, choices=events)
    ev = events[0]
    conn = get_conn()
    mid = get_or_create_meetup(conn, ev.date, ev.name, ev.eid)
    r = sync_somoim(conn, mid, attendees(group, ev))
    refill_today(conn)
    flash(f"불러오기 완료: 추가 {r['added']} · 취소 {r['canceled']} · 복귀 {r['restored']}")
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/import")
@admin_required
def import_date():
    return _import(request.form["date"], _blank_none(request.form.get("eid")))


def _meetup_row(mid: int):
    row = get_conn().execute("SELECT * FROM meetups WHERE id = ?", (mid,)).fetchone()
    if row is None:
        abort(404)
    return row


@bp.post("/m/<int:mid>/import")
@admin_required
def reimport(mid):
    m = _meetup_row(mid)
    if m["eid"] is None:
        flash("붙여넣기로 만든 정모는 소모임에서 다시 불러올 수 없습니다")
        return redirect(url_for("admin.meetup", mid=mid))
    return _import(m["date"], m["eid"], m["name"])


@bp.post("/paste")
@admin_required
def paste():
    conn = get_conn()
    mid = get_or_create_meetup(conn, request.form["date"], request.form.get("name") or "정모", None)
    r = add_pasted(conn, mid, request.form.get("text", ""))
    refill_today(conn)
    msg = f"붙여넣기: 등록 {len(r['added'])} · 새 회원 {len(r['created'])}"
    if r["ambiguous"]:
        msg += f" · 같은 닉네임이 여럿이라 건너뜀: {', '.join(r['ambiguous'])}"
    flash(msg)
    return redirect(url_for("admin.meetup", mid=mid))


@bp.get("/m/<int:mid>")
@admin_required
def meetup(mid):
    conn = get_conn()
    m = _meetup_row(mid)
    rows = conn.execute("""SELECT m.id, m.nickname, m.gender, m.grade, m.is_guest, a.status, a.games
                           FROM attendees a JOIN members m ON m.id = a.member_id
                           WHERE a.meetup_id = ? ORDER BY m.nickname""", (mid,)).fetchall()
    seated = {r["id"] for r in rows}
    guests = [g for g in list_members(conn, guests=True) if g["id"] not in seated]
    return render_template("admin_meetup.html", m=m, rows=rows, board=board(conn, mid), guests=guests)


@bp.post("/m/<int:mid>/status")
@admin_required
def status(mid):
    conn = get_conn()
    try:
        set_status(conn, mid, int(request.form["member_id"]), request.form["status"])
    except ValueError as e:
        flash(str(e))
    refill_today(conn)
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/info")
@admin_required
def info(mid):
    conn = get_conn()
    member_id = int(request.form["member_id"])
    try:
        set_info(conn, member_id, _blank_none(request.form.get("gender")), _blank_none(request.form.get("grade")), by="admin")
        on_info_changed(conn, member_id)
    except ValueError as e:
        flash(str(e))
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/guest")
@admin_required
def guest(mid):
    conn = get_conn()
    _meetup_row(mid)
    if request.form.get("member_id"):
        member_id = int(request.form["member_id"])
    else:
        nickname = request.form.get("nickname", "").strip()
        gender, grade = _blank_none(request.form.get("gender")), _blank_none(request.form.get("grade"))
        if not nickname or not gender or not grade:
            flash("게스트는 이름, 성별, 급수를 모두 입력해 주세요")
            return redirect(url_for("admin.meetup", mid=mid))
        try:
            member_id = create_member(conn, nickname, is_guest=True, gender=gender, grade=grade)
        except ValueError as e:
            flash(str(e))
            return redirect(url_for("admin.meetup", mid=mid))
    add_attendee(conn, mid, member_id, "guest")
    refill_today(conn)
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/start")
@admin_required
def start_meetup(mid):
    _meetup_row(mid)
    try:
        courts = int(request.form["courts"])
    except ValueError:
        courts = 0
    if not 1 <= courts <= 20:
        flash("코트 수는 1~20 사이로 입력해 주세요")
    else:
        start(get_conn(), mid, courts)
    return redirect(url_for("admin.meetup", mid=mid))


def _game_meetup(gid: int) -> int:
    row = get_conn().execute("SELECT meetup_id FROM games WHERE id = ?", (gid,)).fetchone()
    if row is None:
        abort(404)
    return row["meetup_id"]


@bp.post("/game/<int:gid>/end")
@admin_required
def end(gid):
    mid = _game_meetup(gid)
    end_game(get_conn(), gid)
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/game/<int:gid>/redraw")
@admin_required
def redraw_game(gid):
    conn = get_conn()
    row = conn.execute("SELECT meetup_id FROM games WHERE id = ?", (gid,)).fetchone()
    redraw(conn, gid)
    if row is None:
        flash("이미 다시 뽑은 경기입니다")
        return redirect(url_for("admin.home"))
    return redirect(url_for("admin.meetup", mid=row["meetup_id"]))


@bp.get("/members")
@admin_required
def members():
    conn = get_conn()
    return render_template("admin_members.html", members=list_members(conn), changes=changes(conn, limit=100))


@bp.post("/members/<int:member_id>")
@admin_required
def member_update(member_id):
    conn = get_conn()
    try:
        set_info(conn, member_id, _blank_none(request.form.get("gender")), _blank_none(request.form.get("grade")), by="admin")
        on_info_changed(conn, member_id)
    except ValueError as e:
        flash(str(e))
    return redirect(url_for("admin.members"))


@bp.post("/members/<int:member_id>/reset-pin")
@admin_required
def member_reset_pin(member_id):
    reset_pin(get_conn(), member_id)
    flash("PIN을 초기화했습니다")
    return redirect(url_for("admin.members"))


@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    conn = get_conn()
    if request.method == "POST":
        try:
            offset, window = float(request.form["female_offset"]), int(request.form["mix_window"])
        except ValueError:
            offset, window = -1, -1
        if 0 <= offset <= 5 and 4 <= window <= 20:
            set_setting(conn, "female_offset", offset)
            set_setting(conn, "mix_window", window)
            refill_today(conn)
            flash("설정을 저장했습니다")
        else:
            flash("여자 보정값은 0~5, 혼복 후보 범위는 4~20 사이로 입력해 주세요")
        return redirect(url_for("admin.settings"))
    return render_template("admin_settings.html", female_offset=get_setting(conn, "female_offset"),
                           mix_window=int(get_setting(conn, "mix_window")))
