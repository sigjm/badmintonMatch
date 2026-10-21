import hmac
import ipaddress
import time
from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from . import audit, get_conn, meetups
from .db import get_setting, set_setting, transaction
from .groups import add_group, get_group, list_groups, parse_gid
from .meetups import (ArchivedMeetupError, add_attendee, add_manual_reservation, add_pasted, board, end_game,
                      get_or_create_meetup, fill_courts, manual_candidates, on_info_changed, playing_candidates,
                      redraw, refill_upcoming, set_memo, set_status, start,
                      start_reservation_now, sync_somoim, today, update_reservation)
from .members import changes, create_member, list_members, rename_member, reset_pin, set_info
from .somoim import SomoimError, attendees, events_on, fetch_group, search_groups

bp = Blueprint("admin", __name__, url_prefix="/admin")
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
_login_failures: dict[str, tuple[float, int]] = {}
AUDIT_ACTION_LABELS = {
    "meetup_create": "정모 추가", "meetup_import": "정모 불러오기", "attendees_paste": "참석자 붙여넣기",
    "meetup_archive": "정모 보관", "meetup_restore": "정모 복구", "attendee_status": "참석 상태 변경",
    "attendee_memo": "참석자 메모 변경", "attendee_guest_add": "게스트 추가", "meetup_start": "코트 시작",
    "courts_change": "코트 수 변경", "auto_match": "자동 매칭", "manual_reservation": "수동 예약",
    "game_assigned": "경기 배정",
    "game_end": "게임 종료", "game_redraw": "경기 다시 뽑기", "reservation_redraw": "예약 다시 뽑기",
    "reservation_edit": "예약 수정", "reservation_priority": "예약 우선 배정",
    "reservation_start_now": "예약 바로 시작", "reservation_delete": "예약 삭제",
    "settings_change": "매칭 설정 변경", "pin_reset": "PIN 초기화",
    "group_add": "모임 추가", "group_rename": "모임 이름 변경", "member_change": "회원 정보 변경",
}


def _login_client_key() -> str:
    forwarded_ip = request.headers.get("CF-Connecting-IP", "").strip()
    remote_ip = request.remote_addr or ""
    for candidate in (forwarded_ip, remote_ip):
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return "unknown"


def _login_is_limited(client_key: str, now: float) -> bool:
    for key, (started, _) in list(_login_failures.items()):
        if now - started >= LOGIN_WINDOW_SECONDS:
            del _login_failures[key]
    attempt = _login_failures.get(client_key)
    return bool(attempt and attempt[1] >= LOGIN_MAX_FAILURES)


def _record_login_failure(client_key: str, now: float) -> None:
    attempt = _login_failures.get(client_key)
    if not attempt or now - attempt[0] >= LOGIN_WINDOW_SECONDS:
        _login_failures[client_key] = (now, 1)
    else:
        _login_failures[client_key] = (attempt[0], attempt[1] + 1)


@bp.before_request
def reject_cross_origin_mutations():
    if request.method != "POST" or request.endpoint == "admin.login" or not session.get("admin"):
        return
    source = request.headers.get("Origin") or request.referrer
    if not source:
        abort(403)
    try:
        source_origin = urlsplit(source)
    except ValueError:
        abort(403)
    expected_scheme = "https" if current_app.config["SESSION_COOKIE_SECURE"] else request.scheme
    if source_origin.scheme != expected_scheme or source_origin.netloc.lower() != request.host.lower():
        abort(403)


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
        client_key = _login_client_key()
        now = time.monotonic()
        if _login_is_limited(client_key, now):
            return render_template("admin_login.html", error="로그인 시도가 많습니다. 15분 후 다시 시도해 주세요."), 429
        expected = current_app.config["ADMIN_PASSWORD"]
        if expected and hmac.compare_digest(request.form.get("password", ""), expected):
            _login_failures.pop(client_key, None)
            session["admin"] = True
            return redirect(url_for("admin.home"))
        _record_login_failure(client_key, now)
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
    conn = get_conn()
    active = conn.execute("SELECT * FROM meetups WHERE group_id = ? AND archived_at IS NULL "
                          "ORDER BY date DESC, id DESC LIMIT 10", (group["id"],)).fetchall()
    archived = conn.execute("SELECT * FROM meetups WHERE group_id = ? AND archived_at IS NOT NULL "
                            "ORDER BY date DESC, id DESC LIMIT 10", (group["id"],)).fetchall()
    ctx.setdefault("date", today())
    ctx.setdefault("meetups", active)
    ctx.setdefault("archived_meetups", archived)
    return render_template("admin_home.html", groups=list_groups(conn), **ctx)


@bp.get("")
@admin_required
def home():
    return _home()


@bp.post("/meetups")
@admin_required
def create_meetup():
    conn = get_conn()
    date, name = request.form.get("date", ""), request.form.get("name", "")
    try:
        with transaction(conn):
            mid, created = meetups.create_manual_meetup(conn, date, name, _current_group()["id"])
            if created:
                audit.record(conn, action="meetup_create", summary=f"정모 추가: {name.strip()}",
                             entity_type="meetup", entity_id=mid, meetup_id=mid,
                             details={"date": date.strip(), "source": "manual"})
    except ArchivedMeetupError as e:
        return _home(date=date, name=name, archived_mid=e.meetup_id,
                     error="같은 날짜와 이름의 정모가 보관되어 있습니다. 먼저 복구해 주세요.")
    except ValueError as e:
        return _home(date=date, name=name, error=str(e))
    if created:
        flash("정모를 추가했습니다")
    else:
        flash("이미 등록된 정모로 이동합니다")
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/archive")
@admin_required
def archive_meetup(mid):
    conn = get_conn()
    row = _meetup_row(mid)
    if row["archived_at"] is None:
        with transaction(conn):
            meetups.archive_meetup(conn, mid)
            audit.record(conn, action="meetup_archive", summary=f"정모 보관: {row['name']}",
                         entity_type="meetup", entity_id=mid, meetup_id=mid,
                         details={"date": row["date"]})
        flash("정모를 보관했습니다")
    return redirect(url_for("admin.home"))


@bp.post("/m/<int:mid>/restore")
@admin_required
def restore_meetup(mid):
    conn = get_conn()
    row = _meetup_row(mid)
    if row["archived_at"] is not None:
        with transaction(conn):
            meetups.restore_meetup(conn, mid)
            audit.record(conn, action="meetup_restore", summary=f"정모 복구: {row['name']}",
                         entity_type="meetup", entity_id=mid, meetup_id=mid,
                         details={"date": row["date"]})
        flash("정모를 복구했습니다")
    return redirect(url_for("admin.meetup", mid=mid))


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
    try:
        with transaction(conn):
            mid = get_or_create_meetup(conn, ev.date, ev.name, ev.eid, group_id=group["id"])
            r = sync_somoim(conn, mid, found)
            refill_upcoming(conn)
            audit.record(conn, action="meetup_import", summary=f"소모임 정모 불러오기: {ev.name}",
                         entity_type="meetup", entity_id=mid, meetup_id=mid,
                         details={"added": r["added"], "canceled": r["canceled"], "restored": r["restored"]})
    except ArchivedMeetupError as e:
        return _home(**fail, archived_mid=e.meetup_id,
                     error="이미 보관된 정모입니다. 중복으로 만들지 않았습니다. 아래에서 정모를 복구해 주세요.")
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


def _active_meetup_row(mid: int):
    row = _meetup_row(mid)
    if row["archived_at"] is not None:
        abort(409, description="보관된 정모는 변경할 수 없습니다")
    return row


@bp.post("/m/<int:mid>/import")
@admin_required
def reimport(mid):
    m = _active_meetup_row(mid)
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
    created = False
    try:
        with transaction(conn):
            if mid is not None and conn.execute("SELECT 1 FROM meetups WHERE id = ?", (mid,)).fetchone():
                _active_meetup_row(mid)
            else:
                mid, created = meetups.create_manual_meetup(
                    conn, date, request.form.get("name") or "정모", _current_group()["id"])
            before_attendees = {row["nickname"]: row["status"] for row in conn.execute(
                "SELECT m.nickname, a.status FROM attendees a JOIN members m ON m.id = a.member_id "
                "WHERE a.meetup_id = ? AND m.is_guest = 0", (mid,))}
            r = add_pasted(conn, mid, request.form.get("text", ""))
            refill_upcoming(conn)
            if created:
                audit.record(conn, action="meetup_create", summary="붙여넣기 정모 추가",
                             entity_type="meetup", entity_id=mid, meetup_id=mid,
                             details={"date": date, "source": "paste"})
            changed_attendees = [nickname for nickname in r["added"]
                                 if before_attendees.get(nickname) != "present"] + r["created"]
            if changed_attendees:
                audit.record(conn, action="attendees_paste", summary="참석자 명단 붙여넣기",
                             entity_type="meetup", entity_id=mid, meetup_id=mid,
                             details={"changed": changed_attendees, "submitted": len(r["added"]) + len(r["created"])})
    except ArchivedMeetupError as e:
        return _home(date=date, name=request.form.get("name", ""), archived_mid=e.meetup_id,
                     error="같은 날짜와 이름의 정모가 보관되어 있습니다. 먼저 복구해 주세요.")
    except ValueError as e:
        return _home(date=date, name=request.form.get("name", ""), error=str(e))
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
    if m["started"] and m["archived_at"] is None:
        fill_courts(conn, mid)  # 쉬는 시간(15초)이 지난 코트 채우기
    rows = conn.execute("""SELECT m.id, m.nickname, m.gender, m.grade, m.is_guest, a.status, a.games
                           FROM attendees a JOIN members m ON m.id = a.member_id
                           WHERE a.meetup_id = ? ORDER BY m.nickname""", (mid,)).fetchall()
    seated = {r["id"] for r in rows}
    guests = [g for g in list_members(conn, guests=True, group_id=m["group_id"]) if g["id"] not in seated]
    candidates = manual_candidates(conn, mid)
    game_candidates = playing_candidates(conn, mid)
    candidate_ids = {r["id"] for r in candidates + game_candidates}
    return render_template("admin_meetup.html", m=m, rows=rows, board=board(conn, mid), guests=guests,
                           manual_candidates=candidates, game_candidates=game_candidates,
                           manual_candidate_ids=candidate_ids,
                           group=get_group(conn, m["group_id"]) or _current_group())


@bp.get("/m/<int:mid>/updates")
@admin_required
def meetup_updates(mid):
    conn = get_conn()
    m = _active_meetup_row(mid)
    if m["started"]:
        fill_courts(conn, mid)  # 3초 갱신이 쉬는 시간(15초)이 지난 코트를 채우는 시계 역할도 한다
    current_board = board(conn, mid)
    candidates = manual_candidates(conn, mid)
    game_candidates = playing_candidates(conn, mid)
    ids = {r["id"] for r in candidates + game_candidates}
    response = jsonify(
        meetup_id=mid,
        court_board=render_template("_court_board_content.html", m=m, board=current_board, admin=True,
                                    manual_candidates=candidates),
        reservation_list=render_template("reservation_list.html", board=current_board, admin=True, m=m),
        queue=render_template("queue_list.html", board=current_board, admin=True, m=m,
                              manual_candidate_ids=ids, game_candidates=game_candidates),
        excluded=render_template("excluded_list.html", board=current_board, admin=True, m=m, manual_candidate_ids=ids),
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
    _active_meetup_row(mid)
    try:
        member_id = int(request.form["member_id"])
        target_status = request.form["status"]
        with transaction(conn):
            before = conn.execute("SELECT a.status, m.nickname FROM attendees a JOIN members m ON m.id = a.member_id "
                                  "WHERE a.meetup_id = ? AND a.member_id = ?",
                                  (mid, member_id)).fetchone()
            set_status(conn, mid, member_id, target_status)
            if before and before["status"] != target_status:
                audit.record(conn, action="attendee_status", summary=f"참석 상태 변경: {before['nickname']}",
                             entity_type="attendee", entity_id=member_id, meetup_id=mid,
                             details={"from": before["status"], "to": target_status})
                refill_upcoming(conn)
    except ValueError as e:
        flash(str(e))
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/info")
@admin_required
def info(mid):
    conn = get_conn()
    _active_meetup_row(mid)
    try:
        member_id = int(request.form["member_id"])
    except ValueError:
        abort(400)
    if conn.execute("SELECT 1 FROM attendees WHERE meetup_id = ? AND member_id = ?",
                    (mid, member_id)).fetchone() is None:
        flash("해당 정모 참석자를 찾을 수 없습니다")
        return redirect(url_for("admin.meetup", mid=mid))
    try:
        with transaction(conn):
            changed = set_info(conn, member_id, _blank_none(request.form.get("gender")),
                               _blank_none(request.form.get("grade")), by="admin")
            if changed:
                on_info_changed(conn, member_id, changed)
    except ValueError as e:
        flash(str(e))
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/memo")
@admin_required
def memo(mid):
    conn = get_conn()
    _active_meetup_row(mid)
    try:
        member_id = int(request.form["member_id"])
        with transaction(conn):
            before = conn.execute("SELECT a.memo, m.nickname FROM attendees a JOIN members m ON m.id = a.member_id "
                                  "WHERE a.meetup_id = ? AND a.member_id = ?",
                                  (mid, member_id)).fetchone()
            set_memo(conn, mid, member_id, request.form.get("memo", ""))
            after = conn.execute("SELECT a.memo FROM attendees a JOIN members m ON m.id = a.member_id "
                                 "WHERE a.meetup_id = ? AND a.member_id = ?",
                                 (mid, member_id)).fetchone()
            if before and after and before["memo"] != after["memo"]:
                audit.record(conn, action="attendee_memo", summary=f"참석자 메모 변경: {before['nickname']}",
                             entity_type="attendee", entity_id=member_id, meetup_id=mid,
                             details={"from": before["memo"], "to": after["memo"]})
    except (KeyError, ValueError) as e:
        flash(str(e))
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/rename")
@admin_required
def rename(mid):
    conn = get_conn()
    _active_meetup_row(mid)
    try:
        member_id = int(request.form["member_id"])
        nickname = request.form.get("nickname", "")
        with transaction(conn):
            rename_member(conn, member_id, nickname, by="admin")
    except (KeyError, ValueError) as e:
        flash(str(e))
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/manual")
@admin_required
def manual_match(mid):
    _active_meetup_row(mid)
    try:
        player_ids = [int(request.form.get(name, "")) for name in ("a1", "a2", "b1", "b2")]
    except (TypeError, ValueError):
        flash("선수 네 명을 모두 선택해 주세요")
    else:
        try:
            conn = get_conn()
            with transaction(conn):
                reservation_id = add_manual_reservation(conn, mid, player_ids)
                names = {r["id"]: r["nickname"] for r in conn.execute(
                    "SELECT id, nickname FROM members WHERE id IN (?, ?, ?, ?)", player_ids)}
                audit.record(conn, action="manual_reservation",
                             summary="수동 대진 예약 추가: " + ", ".join(names.get(pid, "?") for pid in player_ids),
                             entity_type="reservation", entity_id=reservation_id, meetup_id=mid,
                             details={"players": [names.get(pid, "?") for pid in player_ids]})
            if meetups.reservation_waiting_for_game(conn, reservation_id):
                flash("수동 예약을 추가했습니다. 게임 중인 멤버가 있습니다. 경기 종료 후 배정됩니다")
            else:
                flash("수동 예약을 추가했습니다")
        except ValueError as e:
            flash(str(e))
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/auto-match")
@admin_required
def auto_match(mid):
    m = _active_meetup_row(mid)
    if not m["started"]:
        flash("먼저 코트를 시작해 주세요")
    else:
        conn = get_conn()
        with transaction(conn):
            before = conn.execute("SELECT count(*) FROM games WHERE meetup_id = ?", (mid,)).fetchone()[0]
            fill_courts(conn, mid, force=True)  # 운영진이 직접 누르면 쉬는 시간 없이 채운다
            after = conn.execute("SELECT count(*) FROM games WHERE meetup_id = ?", (mid,)).fetchone()[0]
            added = after - before
            if added:
                audit.record(conn, action="auto_match", summary=f"자동 매칭: {added}경기",
                             entity_type="meetup", entity_id=mid, meetup_id=mid, details={"games": added})
        flash(f"빈 코트 자동 매칭 완료: {added}경기" if added else "자동 매칭할 빈 코트가 없거나 대기 가능한 선수가 4명 미만입니다")
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/guest")
@admin_required
def guest(mid):
    conn = get_conn()
    _active_meetup_row(mid)
    try:
        with transaction(conn):
            if request.form.get("member_id"):
                try:
                    member_id = int(request.form["member_id"])
                except ValueError:
                    abort(400)
                if conn.execute("SELECT 1 FROM members WHERE id = ? AND is_guest = 1", (member_id,)).fetchone() is None:
                    raise ValueError("등록된 게스트를 찾을 수 없습니다")
                nickname = conn.execute("SELECT nickname FROM members WHERE id = ?", (member_id,)).fetchone()["nickname"]
            else:
                nickname = request.form.get("nickname", "").strip()
                gender, grade = _blank_none(request.form.get("gender")), _blank_none(request.form.get("grade"))
                if not nickname or not gender or not grade:
                    raise ValueError("게스트는 이름, 성별, 급수를 모두 입력해 주세요")
                member_id = create_member(conn, nickname, is_guest=True, gender=gender, grade=grade)
            before = conn.execute("SELECT status FROM attendees WHERE meetup_id = ? AND member_id = ?",
                                  (mid, member_id)).fetchone()
            add_attendee(conn, mid, member_id, "guest")
            if before is None or before["status"] != "present":
                refill_upcoming(conn)
                audit.record(conn, action="attendee_guest_add", summary=f"게스트 참석 추가: {nickname}",
                             entity_type="attendee", entity_id=member_id, meetup_id=mid,
                             details={"nickname": nickname})
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("admin.meetup", mid=mid))
    name = conn.execute("SELECT nickname FROM members WHERE id = ?", (member_id,)).fetchone()["nickname"]
    flash(f"{name} 님을 게스트로 추가했습니다")  # 화면이 이 문구를 보고 입력칸을 비운다 (data-reset-on-save)
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/m/<int:mid>/start")
@admin_required
def start_meetup(mid):
    m = _active_meetup_row(mid)
    try:
        courts = int(request.form["courts"])
    except ValueError:
        courts = 0
    if not 1 <= courts <= 20:
        flash("코트 수는 1~20 사이로 입력해 주세요")
    else:
        conn = get_conn()
        with transaction(conn):
            changed = not m["started"] or int(m["courts"]) != courts
            if changed:
                start(conn, mid, courts)
                audit.record(conn, action="meetup_start" if not m["started"] else "courts_change",
                             summary=f"{'코트 시작' if not m['started'] else '코트 수 변경'}: {courts}면",
                             entity_type="meetup", entity_id=mid, meetup_id=mid,
                             details={"from_courts": m["courts"], "to_courts": courts})
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
    _active_meetup_row(mid)
    conn = get_conn()
    with transaction(conn):
        game = conn.execute("SELECT ended_at, court FROM games WHERE id = ?", (gid,)).fetchone()
        if game and game["ended_at"] is None:
            end_game(conn, gid)
            audit.record(conn, action="game_end", summary=f"게임 종료: {game['court']}코트",
                         entity_type="game", entity_id=gid, meetup_id=mid, details={"court": game["court"]})
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/game/<int:gid>/redraw")
@admin_required
def redraw_game(gid):
    mid = _game_meetup(gid)
    _active_meetup_row(mid)
    conn = get_conn()
    with transaction(conn):
        game = conn.execute("SELECT ended_at, court FROM games WHERE id = ?", (gid,)).fetchone()
        if game and game["ended_at"] is None:
            redraw(conn, gid)
            audit.record(conn, action="game_redraw", summary=f"경기 다시 뽑기: {game['court']}코트",
                         entity_type="game", entity_id=gid, meetup_id=mid, details={"court": game["court"]})
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/reservation/<int:rid>/edit")
@admin_required
def edit_reservation(rid):
    conn = get_conn()
    before = conn.execute("SELECT meetup_id, seq, source, a1, a2, b1, b2 FROM reservations WHERE id = ?",
                          (rid,)).fetchone()
    if before is None:
        flash("예약을 찾을 수 없습니다")
        return redirect(url_for("admin.home"))
    mid = before["meetup_id"]
    _active_meetup_row(mid)
    try:
        player_ids = [int(request.form.get(name, "")) for name in ("a1", "a2", "b1", "b2")]
    except (TypeError, ValueError):
        flash("선수 네 명을 모두 선택해 주세요")
        return redirect(url_for("admin.meetup", mid=mid))
    try:
        with transaction(conn):
            result = update_reservation(conn, rid, player_ids)
            if result is None:
                abort(404)
            names = {r["id"]: r["nickname"] for r in conn.execute(
                "SELECT id, nickname FROM members WHERE id IN (?, ?, ?, ?, ?, ?, ?, ?)",
                (*[before[k] for k in ("a1", "a2", "b1", "b2")], *player_ids))}
            audit.record(conn, action="reservation_edit",
                         summary=f"예약 수정: {before['seq']}번 · " + ", ".join(names.get(pid, "?") for pid in player_ids),
                         entity_type="reservation", entity_id=rid, meetup_id=mid,
                         details={"sequence": before["seq"], "source_before": before["source"],
                                  "players_before": [names.get(before[k], "?") for k in ("a1", "a2", "b1", "b2")],
                                  "players_after": [names.get(pid, "?") for pid in player_ids]})
            _, waiting_for_game = result
        if waiting_for_game:
            flash("예약을 수정했습니다. 게임 중인 멤버가 있습니다. 경기 종료 후 배정됩니다")
        else:
            flash("예약을 수정했습니다")
    except ValueError as e:
        flash(str(e))
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/reservation/<int:rid>/priority")
@admin_required
def prioritize_reservation_route(rid):
    conn = get_conn()
    row = conn.execute("SELECT meetup_id, seq FROM reservations WHERE id = ?", (rid,)).fetchone()
    if row is None:
        flash("예약을 찾을 수 없습니다")
        return redirect(url_for("admin.home"))
    mid = row["meetup_id"]
    _active_meetup_row(mid)
    with transaction(conn):
        meetups.prioritize_reservation(conn, rid)
        audit.record(conn, action="reservation_priority", summary=f"예약 우선 배정: {row['seq']}번",
                     entity_type="reservation", entity_id=rid, meetup_id=mid,
                     details={"previous_sequence": row["seq"], "sequence": 1})
    flash("예약을 다음 순서로 이동했습니다")
    return redirect(url_for("admin.meetup", mid=mid))


@bp.post("/reservation/<int:rid>/start-now")
@admin_required
def start_reservation_now_route(rid):
    conn = get_conn()
    row = conn.execute("SELECT meetup_id, seq FROM reservations WHERE id = ?", (rid,)).fetchone()
    if row is None:
        flash("예약을 찾을 수 없습니다")
        return redirect(url_for("admin.home"))
    mid = row["meetup_id"]
    _active_meetup_row(mid)
    with transaction(conn):
        result_mid, status, game_id, court = start_reservation_now(conn, rid)
        if status == "started":
            audit.record(conn, action="reservation_start_now", summary=f"예약 바로 시작: {court}코트",
                         entity_type="game", entity_id=game_id, meetup_id=mid,
                         details={"reservation_id": rid, "sequence": row["seq"], "court": court})
    if status == "started":
        flash(f"예약 경기를 {court}코트에서 바로 시작했습니다")
    elif status == "playing":
        flash("게임 중인 멤버가 있습니다. 경기 종료 후 자동 배정됩니다")
    elif status == "no_court":
        flash("빈 코트가 없어 예약 순서대로 대기합니다")
    elif status == "invalid":
        flash("참석 상태를 확인할 수 없어 예약을 시작하지 못했습니다. 예약을 수정해 주세요")
    elif status == "inactive":
        flash("코트를 시작한 뒤 바로 배정할 수 있습니다")
    else:
        flash("예약을 찾을 수 없습니다")
    return redirect(url_for("admin.meetup", mid=result_mid or mid))


@bp.post("/reservation/<int:rid>/delete")
@admin_required
def delete_reservation(rid):
    conn = get_conn()
    row = conn.execute("SELECT meetup_id, seq FROM reservations WHERE id = ?", (rid,)).fetchone()
    if row:
        mid = row["meetup_id"]
        _active_meetup_row(mid)
        with transaction(conn):
            mid = meetups.delete_reservation(conn, rid)
            if mid:
                audit.record(conn, action="reservation_delete", summary=f"예약 삭제: {row['seq']}번",
                             entity_type="reservation", entity_id=rid, meetup_id=mid,
                             details={"sequence": row["seq"]})
        flash(f"예약 {row['seq']}을 삭제했습니다. 다음 경기가 코트에 들어갈 때까지 새 예약은 만들지 않습니다")
    else:
        mid = request.form.get("mid", type=int)
    return redirect(url_for("admin.meetup", mid=mid) if mid else url_for("admin.home"))


@bp.get("/members")
@admin_required
def members():
    conn = get_conn()
    group = _current_group()
    return render_template("admin_members.html", groups=list_groups(conn), back="members",
                           members=list_members(conn, group_id=group["id"]),
                           changes=changes(conn, limit=100, group_id=group["id"]))


@bp.post("/members/<int:member_id>")
@admin_required
def member_update(member_id):
    conn = get_conn()
    if conn.execute("SELECT 1 FROM members WHERE id = ?", (member_id,)).fetchone() is None:
        abort(404)
    try:
        with transaction(conn):
            changed = set_info(conn, member_id, _blank_none(request.form.get("gender")),
                               _blank_none(request.form.get("grade")), by="admin")
            if changed:
                on_info_changed(conn, member_id, changed)
    except ValueError as e:
        flash(str(e))
    return redirect(url_for("admin.members"))


@bp.post("/members/<int:member_id>/reset-pin")
@admin_required
def member_reset_pin(member_id):
    conn = get_conn()
    with transaction(conn):
        row = conn.execute("SELECT pin_hash, nickname FROM members WHERE id = ?", (member_id,)).fetchone()
        if row is None:
            abort(404)
        if row["pin_hash"] is not None:
            reset_pin(conn, member_id)
            audit.record(conn, action="pin_reset", summary=f"회원 PIN 초기화: {row['nickname']}",
                         entity_type="member", entity_id=member_id)
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
            old_offset, old_window = get_setting(conn, "female_offset"), int(get_setting(conn, "mix_window"))
            with transaction(conn):
                if old_offset != offset or old_window != window:
                    set_setting(conn, "female_offset", offset)
                    set_setting(conn, "mix_window", window)
                    refill_upcoming(conn)
                    audit.record(conn, action="settings_change", summary="매칭 설정 변경",
                                 entity_type="settings", details={
                                     "female_offset": {"from": old_offset, "to": offset},
                                     "mix_window": {"from": old_window, "to": window},
                                 })
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
        with transaction(conn):
            existing = conn.execute("SELECT id, name FROM groups WHERE gid = ?", (gid,)).fetchone()
            group_id = add_group(conn, gid, name or gid)
            if existing is None:
                audit.record(conn, action="group_add", summary=f"모임 추가: {name or gid}",
                             entity_type="group", entity_id=group_id, details={"name": name or gid})
            elif existing["name"] != (name or gid):
                audit.record(conn, action="group_rename", summary=f"모임 이름 변경: {name or gid}",
                             entity_type="group", entity_id=group_id,
                             details={"from": existing["name"], "to": name or gid})
        session["group_id"] = group_id
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
    return redirect(url_for("admin.members") if request.form.get("back") == "members" else url_for("admin.home"))


@bp.get("/logs")
@admin_required
def logs():
    conn = get_conn()
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    action = request.args.get("action", "")
    meetup_id = request.args.get("meetup_id", type=int)
    page = max(1, request.args.get("page", 1, type=int) or 1)
    error = None
    try:
        entries, total = audit.list_entries(conn, page=page, date_from=date_from or None,
                                            date_to=date_to or None, action=action or None,
                                            meetup_id=meetup_id)
    except ValueError as e:
        entries, total, error = [], 0, str(e)
    pages = max(1, (total + 49) // 50)
    if page > pages:
        page = pages
        if error is None:
            entries, total = audit.list_entries(conn, page=page, date_from=date_from or None,
                                                date_to=date_to or None, action=action or None,
                                                meetup_id=meetup_id)
    meetup_rows = conn.execute("SELECT id, date, name, archived_at FROM meetups ORDER BY date DESC, id DESC").fetchall()
    return render_template("admin_logs.html", entries=entries, total=total, page=page, pages=pages,
                           date_from=date_from, date_to=date_to, action=action, meetup_id=meetup_id,
                           meetups=meetup_rows, action_labels=AUDIT_ACTION_LABELS, error=error)
