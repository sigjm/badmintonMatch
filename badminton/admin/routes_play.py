"""경기 진행 라우트: 참석자 상태·정보·메모, 게스트·회원 추가, 수동 대진, 게임 끝내기·다시 뽑기, 예약 관리."""

from flask import abort, flash, redirect, request, url_for

from .. import audit, get_conn, meetups
from ..db import transaction
from ..meetups import (add_attendee, add_manual_reservation, end_game, on_info_changed, redraw, refill_upcoming,
                       set_memo, set_status, start_reservation_now, update_reservation)
from ..members import IN_GROUP, create_member, rename_member, set_info
from . import _active_meetup_row, _blank_none, admin_required, bp


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


@bp.post("/m/<int:mid>/member")
@admin_required
def add_member(mid):
    """소모임에 신청하지 않고 온 회원, 취소했다가 온 회원, 처음 온 정회원을 정모에 바로 참석시킨다."""
    conn = get_conn()
    m = _active_meetup_row(mid)
    try:
        with transaction(conn):
            created = False
            if request.form.get("member_id"):
                try:
                    member_id = int(request.form["member_id"])
                except ValueError:
                    abort(400)
                row = conn.execute(f"SELECT m.nickname FROM members m WHERE m.id = ? AND m.is_guest = 0 AND {IN_GROUP}",
                                   (member_id, m["group_id"])).fetchone()
                if row is None:
                    raise ValueError("등록된 회원을 찾을 수 없습니다")
                nickname = row["nickname"]
            else:
                nickname = request.form.get("nickname", "").strip()
                gender, grade = _blank_none(request.form.get("gender")), _blank_none(request.form.get("grade"))
                if not nickname or not gender or not grade:
                    raise ValueError("회원은 이름, 성별, 급수를 모두 입력해 주세요")
                if conn.execute(f"SELECT 1 FROM members m WHERE m.is_guest = 0 AND m.nickname = ? AND {IN_GROUP}",
                                (nickname, m["group_id"])).fetchone():
                    raise ValueError("같은 이름의 회원이 있어요. 기존 회원에서 골라 주세요")
                member_id = create_member(conn, nickname, gender=gender, grade=grade)
                created = True
            before = conn.execute("SELECT status FROM attendees WHERE meetup_id = ? AND member_id = ?",
                                  (mid, member_id)).fetchone()
            add_attendee(conn, mid, member_id, "paste")
            if before is None or before["status"] != "present":
                # 소모임에서 취소됐다가 다시 온 사람: source가 somoim이면 다음 다시 불러오기가 또 취소 처리한다
                conn.execute("UPDATE attendees SET source = 'paste' WHERE meetup_id = ? AND member_id = ? AND source = 'somoim'",
                             (mid, member_id))
                refill_upcoming(conn)
                audit.record(conn, action="attendee_member_add", summary=f"회원 참석 추가: {nickname}",
                             entity_type="attendee", entity_id=member_id, meetup_id=mid,
                             details={"nickname": nickname, "created": created})
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("admin.meetup", mid=mid))
    flash(f"{nickname} 님을 회원으로 추가했습니다")  # 화면이 이 문구를 보고 입력칸을 비우고 팝업을 닫는다 (data-reset-on-save)
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


@bp.post("/reservation/<int:rid>/redraw")
@admin_required
def redraw_reservation(rid):
    conn = get_conn()
    row = conn.execute("SELECT meetup_id, seq FROM reservations WHERE id = ?", (rid,)).fetchone()
    if row is None:  # 다른 폰이 먼저 지웠거나 경기로 들어간 예약
        mid = request.form.get("mid", type=int)
        return redirect(url_for("admin.meetup", mid=mid) if mid else url_for("admin.home"))
    mid = row["meetup_id"]
    _active_meetup_row(mid)
    try:
        with transaction(conn):
            meetups.redraw_reservation(conn, rid)
            audit.record(conn, action="reservation_redraw", summary=f"예약 다시 뽑기: {row['seq']}번",
                         entity_type="reservation", entity_id=rid, meetup_id=mid, details={"sequence": row["seq"]})
    except ValueError as e:
        flash(str(e))
    return redirect(url_for("admin.meetup", mid=mid))


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
