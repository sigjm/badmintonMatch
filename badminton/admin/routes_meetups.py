"""정모 목록·만들기·보관·불러오기와 운영 콘솔 화면, 코트 시작·정모 끝·코트 상태 라우트."""

from flask import flash, jsonify, redirect, render_template, request, session, url_for

from .. import audit, get_conn, meetups
from ..db import transaction
from ..groups import get_group, list_groups
from ..meetups import (ArchivedMeetupError, add_pasted, board, get_or_create_meetup, fill_courts, manual_candidates,
                       playing_candidates, refill_upcoming, start, sync_somoim, today)
from ..members import list_members
from ..somoim import SomoimError, attendees, events_on, fetch_group
from . import _active_meetup_row, _active_meetups, _blank_none, _current_group, _meetup_row, admin_required, bp


def _home(**ctx):
    group = _current_group()
    conn = get_conn()
    active = _active_meetups(conn, group)
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
                           WHERE a.meetup_id = ? AND a.status != 'canceled' ORDER BY m.nickname""", (mid,)).fetchall()
    seated = {r["id"] for r in rows}
    guests = [g for g in list_members(conn, guests=True, group_id=m["group_id"]) if g["id"] not in seated]
    absent_members = [g for g in list_members(conn, guests=False, group_id=m["group_id"]) if g["id"] not in seated]
    candidates = manual_candidates(conn, mid)
    game_candidates = playing_candidates(conn, mid)
    candidate_ids = {r["id"] for r in candidates + game_candidates}
    return render_template("admin_meetup.html", m=m, rows=rows, board=board(conn, mid), guests=guests,
                           absent_members=absent_members, manual_candidates=candidates, game_candidates=game_candidates,
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


@bp.post("/m/<int:mid>/end")
@admin_required
def end_meetup(mid):
    row = _active_meetup_row(mid)
    if not row["started"]:
        flash("진행 중인 정모만 종료할 수 있습니다")
        return redirect(url_for("admin.settings"))
    conn = get_conn()
    with transaction(conn):
        meetups.end_meetup(conn, mid)
        audit.record(conn, action="meetup_end", summary=f"정모 종료: {row['name']}",
                     entity_type="meetup", entity_id=mid, meetup_id=mid,
                     details={"date": row["date"]})
    flash("정모를 종료했습니다")
    return redirect(url_for("admin.settings"))


@bp.post("/m/<int:mid>/court/<int(signed=True):court>/state")
@admin_required
def court_state(mid, court):
    conn = get_conn()
    _active_meetup_row(mid)
    target_state = request.form.get("state", "").strip()
    try:
        with transaction(conn):
            before = meetups.get_court_state(conn, mid, court)
            meetups.set_court_state(conn, mid, court, target_state)
            audit.record(conn, action="court_state",
                         summary=f"{court}코트 → {meetups.COURT_STATE_LABELS[target_state]}",
                         entity_type="court", entity_id=court, meetup_id=mid,
                         details={"court": court, "from": before, "to": target_state})
    except ValueError as e:
        flash(str(e))
    return redirect(url_for("admin.meetup", mid=mid))
