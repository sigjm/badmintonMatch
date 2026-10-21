import hmac
from functools import wraps

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from . import get_conn, meetups
from .db import get_setting, set_setting
from .groups import add_group, get_group, list_groups, parse_gid
from .meetups import (add_attendee, add_pasted, assign_manual, board, end_game, get_or_create_meetup,
                      fill_courts, manual_candidates, on_info_changed, redraw, refill_upcoming, set_status, start,
                      sync_somoim, today)
from .members import changes, create_member, list_members, rename_member, reset_pin, set_info
from .somoim import SomoimError, attendees, events_on, fetch_group, search_groups

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


def _current_group():
    """운영진이 고른 모임 (세션). 고른 적 없으면 첫 모임."""
    conn = get_conn()
    return get_group(conn, session.get("group_id")) or list_groups(conn)[0]


@bp.context_processor
def inject_group():
    return {"group": _current_group()} if session.get("admin") else {}


def _home(**ctx):
    group = _current_group()
    meetups = get_conn().execute("SELECT * FROM meetups WHERE group_id = ? ORDER BY date DESC, id DESC LIMIT 10",
                                 (group["id"],)).fetchall()
    ctx.setdefault("date", today())
    return render_template("admin_home.html", meetups=meetups, groups=list_groups(get_conn()), **ctx)


@bp.get("")
@admin_required
def home():
    return _home()


def _import(group, date: str, eid: str | None = None, name: str = "", mid: int | None = None):
    """소모임에서 그 날짜 정모 참석자를 가져와 동기화한다. 실패하면 DB를 건드리지 않고 붙여넣기를 안내한다.
    mid는 다시 불러오기 중인 정모 — 실패 시 붙여넣기가 새 정모가 아니라 그 정모에 들어가게 한다."""
    fail = dict(date=date, name=name, mid=mid)
    try:
        page = fetch_group(group["gid"])
    except SomoimError as e:
        return _home(**fail, error=f"소모임에서 명단을 가져오지 못했습니다 ({e}). 붙여넣기로 등록해 주세요.")
    events = [e for e in events_on(page, date) if eid is None or e.eid == eid]
    if not events:
        return _home(**fail, error=f"{date}에 열리는 정모를 소모임에서 찾지 못했습니다. 붙여넣기로 등록해 주세요.")
    if len(events) > 1:
        return _home(date=date, choices=events)
    ev = events[0]
    found = attendees(page, ev)
    if not found:  # 페이지 구조가 바뀌어 참석 표시를 못 읽은 경우: 동기화하면 전원이 취소되므로 멈춘다
        return _home(**{**fail, "name": name or ev.name},
                     error="소모임에서 참석자를 한 명도 찾지 못했습니다. 페이지가 바뀌었을 수 있어요. 붙여넣기로 등록해 주세요.")
    conn = get_conn()
    mid = get_or_create_meetup(conn, ev.date, ev.name, ev.eid, group_id=group["id"])
    r = sync_somoim(conn, mid, found)
    refill_upcoming(conn)
    flash(f"불러오기 완료: 추가 {r['added']} · 취소 {r['canceled']} · 복귀 {r['restored']}")
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/import")
@admin_required
def import_date():
    return _import(_current_group(), request.form["date"], _blank_none(request.form.get("eid")))


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
    session["group_id"] = m["group_id"]  # 실패 화면과 붙여넣기가 이 정모의 모임 기준이 되게
    return _import(get_group(get_conn(), m["group_id"]) or _current_group(), m["date"], m["eid"], m["name"], mid)


@bp.post("/paste")
@admin_required
def paste():
    conn = get_conn()
    mid = request.form.get("mid", type=int)
    date = request.form.get("date") or today()
    if mid is None or conn.execute("SELECT 1 FROM meetups WHERE id = ?", (mid,)).fetchone() is None:
        mid = get_or_create_meetup(conn, date, request.form.get("name") or "정모", None,
                                   group_id=_current_group()["id"])
    r = add_pasted(conn, mid, request.form.get("text", ""))
    refill_upcoming(conn)
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
    candidates = manual_candidates(conn, mid)
    return render_template("admin_meetup.html", m=m, rows=rows, board=board(conn, mid), guests=guests,
                           manual_candidates=candidates, manual_candidate_ids={r["id"] for r in candidates},
                           group=get_group(conn, m["group_id"]) or _current_group())


@bp.get("/m/<int:mid>/updates")
@admin_required
def meetup_updates(mid):
    conn = get_conn()
    m = _meetup_row(mid)
    current_board = board(conn, mid)
    candidates = manual_candidates(conn, mid)
    response = jsonify(
        meetup_id=mid,
        court_board=render_template("_court_board_content.html", m=m, board=current_board, admin=True,
                                    manual_candidates=candidates),
        queue=render_template("queue_list.html", board=current_board, admin=True, m=m,
                              manual_candidate_ids={r["id"] for r in candidates}),
        reservations=current_board.get("reservations", []) if current_board else [],
        active_courts=sum(bool(c["game_id"]) for c in current_board["courts"]),
        queue_count=len(current_board["queue"]),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/m/<int:mid>/status")
@admin_required
def status(mid):
    conn = get_conn()
    try:
        set_status(conn, mid, int(request.form["member_id"]), request.form["status"])
    except ValueError as e:
        flash(str(e))
    refill_upcoming(conn)
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/info")
@admin_required
def info(mid):
    conn = get_conn()
    member_id = int(request.form["member_id"])
    try:
        changed = set_info(conn, member_id, _blank_none(request.form.get("gender")),
                           _blank_none(request.form.get("grade")), by="admin")
        on_info_changed(conn, member_id, changed, meetup_id=mid)
    except ValueError as e:
        flash(str(e))
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/rename")
@admin_required
def rename(mid):
    conn = get_conn()
    _meetup_row(mid)
    try:
        member_id = int(request.form["member_id"])
        nickname = request.form.get("nickname", "")
        rename_member(conn, member_id, nickname, by="admin")
    except (KeyError, ValueError) as e:
        flash(str(e))
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/manual/<int:court>")
@bp.post("/m/<int:mid>/manual", defaults={"court": None})
@admin_required
def manual_match(mid, court=None):
    _meetup_row(mid)
    court = court or request.form.get("court", type=int)
    if court is None:
        flash("배정할 빈 코트를 선택해 주세요")
        return redirect(url_for("admin.meetup", mid=mid))
    try:
        player_ids = [int(request.form.get(name, "")) for name in ("a1", "a2", "b1", "b2")]
    except (TypeError, ValueError):
        flash("선수 네 명을 모두 선택해 주세요")
    else:
        try:
            assign_manual(get_conn(), mid, court, player_ids)
            flash(f"코트 {court}에 수동 대진을 배정했습니다")
        except ValueError as e:
            flash(str(e))
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/auto-match")
@admin_required
def auto_match(mid):
    m = _meetup_row(mid)
    if not m["started"]:
        flash("먼저 코트를 시작해 주세요")
    else:
        conn = get_conn()
        before = conn.execute("SELECT count(*) FROM games WHERE meetup_id = ? AND ended_at IS NULL", (mid,)).fetchone()[0]
        fill_courts(conn, mid)
        after = conn.execute("SELECT count(*) FROM games WHERE meetup_id = ? AND ended_at IS NULL", (mid,)).fetchone()[0]
        added = after - before
        flash(f"빈 코트 자동 매칭 완료: {added}경기" if added else "자동 매칭할 빈 코트가 없거나 대기 가능한 선수가 4명 미만입니다")
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
    refill_upcoming(conn)
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
    mid = _game_meetup(gid)
    redraw(get_conn(), gid)
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/reservation/<int:rid>/redraw")
@admin_required
def redraw_reservation(rid):
    conn = get_conn()
    mid = meetups.redraw_reservation(conn, rid)
    if mid is None:
        mid = request.form.get("mid", type=int)
    if mid is None:
        return redirect(url_for("admin.home"))
    return redirect(url_for("admin.meetup", mid=mid))


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
        changed = set_info(conn, member_id, _blank_none(request.form.get("gender")),
                           _blank_none(request.form.get("grade")), by="admin")
        on_info_changed(conn, member_id, changed)
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
            refill_upcoming(conn)
            flash("설정을 저장했습니다")
        else:
            flash("여자 보정값은 0~5, 혼복 후보 범위는 4~20 사이로 입력해 주세요")
        return redirect(url_for("admin.settings"))
    return render_template("admin_settings.html", female_offset=get_setting(conn, "female_offset"),
                           mix_window=int(get_setting(conn, "mix_window")))


@bp.route("/groups", methods=["GET", "POST"])
@admin_required
def groups():
    conn = get_conn()
    if request.method == "POST":
        try:
            gid = parse_gid(request.form.get("link", ""))
        except ValueError as e:
            flash(str(e))
            return redirect(url_for("admin.groups"))
        name = request.form.get("name", "").strip()
        if not name:
            try:
                name = fetch_group(gid).name
            except SomoimError as e:
                flash(f"모임 정보를 가져오지 못했습니다 ({e}). 모임 이름을 직접 입력해 주세요.")
                return redirect(url_for("admin.groups"))
        session["group_id"] = add_group(conn, gid, name or gid)
        flash(f"{name or gid} 모임을 추가했습니다")
        return redirect(url_for("admin.home"))
    q, results, search_error = request.args.get("q", "").strip(), [], None
    if q:
        try:
            results = search_groups(q)
        except SomoimError:
            search_error = "소모임에서 검색하지 못했습니다. 잠시 후 다시 시도하거나 링크를 붙여넣어 주세요."
    known = {g["gid"] for g in list_groups(conn)}
    return render_template("admin_groups.html", groups=list_groups(conn), q=q, known=known,
                           results=[r for r in results if r[0] not in known], search_error=search_error)


@bp.post("/groups/select")
@admin_required
def select_group():
    g = get_group(get_conn(), request.form.get("group_id", type=int))
    if g:
        session["group_id"] = g["id"]
    return redirect(url_for("admin.home"))
