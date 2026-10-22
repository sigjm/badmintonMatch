import time
from datetime import datetime

import pytest

from badminton.members import create_member, set_info
from badminton.meetups import (SEOUL, add_attendee, add_pasted, board, end_game, get_or_create_meetup,
                               on_info_changed, queue, redraw, redraw_reservation, refill_reservations,
                               reservations, session_day, set_status, start, sync_somoim, today)
from badminton.somoim import SomoimMember


def mk(conn, nick, g, grade, guest=False):
    return create_member(conn, nick, is_guest=guest, gender=g, grade=grade)


def seat(conn, mid, *ids):
    for i in ids:
        add_attendee(conn, mid, i, "paste")


def SM(mid, nick):
    return SomoimMember(mid, nick, frozenset({1}), False)


def member_id_of(conn, nick):
    return conn.execute("SELECT id FROM members WHERE nickname = ?", (nick,)).fetchone()[0]


def status_of(conn, mid, nick):
    return conn.execute("SELECT status FROM attendees WHERE meetup_id = ? AND member_id = ?",
                        (mid, member_id_of(conn, nick))).fetchone()[0]


def current_game(conn, mid):
    return conn.execute("SELECT id FROM games WHERE meetup_id = ? AND ended_at IS NULL", (mid,)).fetchone()[0]


def game_players(conn, gid):
    return list(conn.execute("SELECT a1, a2, b1, b2 FROM games WHERE id = ?", (gid,)).fetchone())


def one_court_game(conn, extra=0):
    mid = get_or_create_meetup(conn, today(), "t", None)
    ids = [mk(conn, "m1", "M", "B"), mk(conn, "m2", "M", "C"), mk(conn, "f1", "F", "B"), mk(conn, "f2", "F", "C")]
    ids += [mk(conn, f"x{i}", "M", "N") for i in range(extra)]  # 초심이라 첫 경기에서 빠지고 대기한다
    seat(conn, mid, *ids)
    start(conn, mid, 1)
    return mid, current_game(conn, mid)


def test_start_fills_courts_mixed(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"m{i}", "M", "B") for i in range(4)], *[mk(conn, f"f{i}", "F", "B") for i in range(4)])
    start(conn, mid, 2)
    b = board(conn, mid)
    assert all(c["teams"] for c in b["courts"]) and b["queue"] == []


def test_end_game_twice_is_noop(conn):  # Review Focus 2
    mid, gid = one_court_game(conn)
    end_game(conn, gid)
    end_game(conn, gid)
    assert [r["games"] for r in conn.execute("SELECT games FROM attendees")] == [1, 1, 1, 1]
    assert conn.execute("SELECT count(*) FROM games").fetchone()[0] == 2  # 끝난 경기 + 새로 채운 경기


def test_short_queue_leaves_court_empty_then_fills(conn):  # Review Focus 3
    mid = get_or_create_meetup(conn, today(), "t", None)
    ids = [mk(conn, f"p{i}", "M", "B") for i in range(3)] + [mk(conn, "nograde", "M", None)]
    seat(conn, mid, *ids)
    start(conn, mid, 1)
    assert board(conn, mid)["courts"][0]["teams"] is None
    on_info_changed(conn, ids[3], set_info(conn, ids[3], "M", "C", by="self"))
    assert board(conn, mid)["courts"][0]["teams"] is not None


def test_excluded_reasons(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    a, b = mk(conn, "a", "M", None), mk(conn, "b", "M", "A")
    seat(conn, mid, a, b)
    set_status(conn, mid, b, "late")
    assert {e["nickname"]: e["reason"] for e in board(conn, mid)["excluded"]} == {"a": "급수 미입력", "b": "지각"}


def test_sync_marks_removed_canceled_and_keeps_guests(conn):
    mid = get_or_create_meetup(conn, "2026-10-23", "금야민+게1", "e1")
    sync_somoim(conn, mid, [SM("x", "엑스"), SM("y", "와이")])
    g = mk(conn, "게스트", "F", "C", guest=True)
    add_attendee(conn, mid, g, "guest")
    assert sync_somoim(conn, mid, [SM("x", "엑스")]) == {"added": 0, "canceled": 1, "restored": 0}
    assert status_of(conn, mid, "와이") == "canceled" and status_of(conn, mid, "게스트") == "present"


def test_canceled_somoim_member_is_hidden_and_returns_to_queue(conn):
    from badminton.meetups import manual_candidates

    mid = get_or_create_meetup(conn, "2026-10-23", "금야민 복귀 확인", "e-rejoin")
    sync_somoim(conn, mid, [SM("returning", "복귀 회원")])
    member_id = member_id_of(conn, "복귀 회원")
    set_info(conn, member_id, "M", "C", by="test")

    assert sync_somoim(conn, mid, []) == {"added": 0, "canceled": 1, "restored": 0}
    assert status_of(conn, mid, "복귀 회원") == "canceled"
    assert conn.execute("SELECT count(*) FROM attendees WHERE meetup_id = ? AND member_id = ?",
                        (mid, member_id)).fetchone()[0] == 1
    assert "복귀 회원" not in board(conn, mid)["queue"]
    assert "복귀 회원" not in {row["nickname"] for row in board(conn, mid)["excluded"]}
    assert member_id not in {row["id"] for row in manual_candidates(conn, mid)}

    assert sync_somoim(conn, mid, [SM("returning", "복귀 회원")]) == {"added": 0, "canceled": 0, "restored": 1}
    assert status_of(conn, mid, "복귀 회원") == "present"
    assert "복귀 회원" in board(conn, mid)["queue"]


def test_sync_restores_rejoined_but_not_manual_status(conn):
    mid = get_or_create_meetup(conn, "2026-10-23", "금야민+게1", "e1")
    sync_somoim(conn, mid, [SM("x", "엑스"), SM("y", "와이")])
    sync_somoim(conn, mid, [SM("x", "엑스")])  # 와이 취소
    set_status(conn, mid, member_id_of(conn, "엑스"), "gone")  # 엑스는 운영진이 귀가 처리
    assert sync_somoim(conn, mid, [SM("x", "엑스"), SM("y", "와이")]) == {"added": 0, "canceled": 0, "restored": 1}
    assert status_of(conn, mid, "와이") == "present" and status_of(conn, mid, "엑스") == "gone"


def test_redraw_keeps_queue_order(conn):
    mid, gid = one_court_game(conn, extra=1)  # 5명: 4명 경기 중, 1명 대기

    def snap():
        return dict(conn.execute("SELECT member_id, queued_at FROM attendees").fetchall())

    before = snap()
    away = game_players(conn, gid)[0]
    set_status(conn, mid, away, "away")
    redraw(conn, gid)
    assert away not in game_players(conn, current_game(conn, mid))
    assert snap() == before


def test_paste_reports_ambiguous(conn):  # Review Focus 4
    mid = get_or_create_meetup(conn, today(), "t", None)
    mk(conn, "철수", "M", "A")
    mk(conn, "철수", "M", "B")
    mk(conn, "영희", "F", "A")
    r = add_pasted(conn, mid, " 영희 \n\n철수\n새사람\n")
    assert r == {"added": ["영희"], "created": ["새사람"], "ambiguous": ["철수"]}


def test_queue_excludes_playing_and_incomplete(conn):
    mid, gid = one_court_game(conn, extra=1)
    assert [p.id for p in queue(conn, mid)] == [member_id_of(conn, "x0")]


def test_session_day_runs_past_midnight():
    assert session_day(datetime(2026, 10, 24, 1, 30, tzinfo=SEOUL)) == "2026-10-23"
    assert session_day(datetime(2026, 10, 24, 7, 0, tzinfo=SEOUL)) == "2026-10-24"


def test_board_keeps_stranded_court_after_reducing(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"p{i}", "M", "B") for i in range(12)])
    start(conn, mid, 3)
    start(conn, mid, 2)
    courts = board(conn, mid)["courts"]
    assert [c["court"] for c in courts] == [1, 2, 3] and courts[2]["teams"] is not None


def test_redraw_gives_new_game_and_stale_end_is_noop(conn):
    mid, gid = one_court_game(conn, extra=1)
    set_status(conn, mid, game_players(conn, gid)[0], "away")
    redraw(conn, gid)
    new = current_game(conn, mid)
    end_game(conn, gid)  # 다른 폰의 오래된 화면에서 누른 경기 끝
    assert new != gid and current_game(conn, mid) == new
    assert {r["games"] for r in conn.execute("SELECT games FROM attendees")} == {0}


def test_resave_same_info_keeps_queue_spot(conn):
    mid, gid = one_court_game(conn, extra=1)
    waiting = member_id_of(conn, "x0")
    before = conn.execute("SELECT queued_at FROM attendees WHERE member_id = ?", (waiting,)).fetchone()[0]
    on_info_changed(conn, waiting, set_info(conn, waiting, "M", "N", by="self"))
    assert conn.execute("SELECT queued_at FROM attendees WHERE member_id = ?", (waiting,)).fetchone()[0] == before


def test_pasted_member_adopted_by_later_import(conn):
    mid = get_or_create_meetup(conn, "2026-10-23", "금야민+게1", "e1")
    add_pasted(conn, mid, "새사람")
    m = member_id_of(conn, "새사람")
    set_info(conn, m, "F", "B", by="admin")
    sync_somoim(conn, mid, [SM("z", "새사람")])
    assert conn.execute("SELECT count(*) FROM members WHERE nickname = '새사람'").fetchone()[0] == 1
    assert conn.execute("SELECT somoim_mid, grade FROM members WHERE id = ?", (m,)).fetchone()[:] == ("z", "B")


def test_paste_ignores_guests_with_same_name(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    mk(conn, "철수", "M", "A", guest=True)
    mk(conn, "철수", "M", "B")
    assert add_pasted(conn, mid, "철수")["added"] == ["철수"]


def test_next_round_does_not_repeat_same_four(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"p{i}", "M", "A") for i in range(8)])
    start(conn, mid, 1)
    first = set(game_players(conn, current_game(conn, mid)))
    end_game(conn, current_game(conn, mid))   # 대기하던 4명이 친다
    end_game(conn, current_game(conn, mid))   # 이제 8명 모두 1판 → 직전 멤버끼리 다시 묶이지 않아야 함
    assert set(game_players(conn, current_game(conn, mid))) != first


def test_redrawn_game_excluded_from_recent_mates(conn):
    mid, gid = one_court_game(conn, extra=1)
    set_status(conn, mid, game_players(conn, gid)[0], "away")
    redraw(conn, gid)
    # redraw된 게임은 canceled=1이므로 _recent_mates에 남지 않는다
    from badminton.meetups import _recent_mates
    assert _recent_mates(conn, mid) == {}


def test_guest_reactivated_on_readd(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    g = mk(conn, "게스트1", "M", "A", guest=True)
    add_attendee(conn, mid, g, "guest")
    set_status(conn, mid, g, "away")
    assert conn.execute("SELECT status FROM attendees WHERE meetup_id = ? AND member_id = ?", (mid, g)).fetchone()[0] == "away"
    # 게스트 다시 추가 시 present로 복구
    add_attendee(conn, mid, g, "guest")
    assert conn.execute("SELECT status FROM attendees WHERE meetup_id = ? AND member_id = ?", (mid, g)).fetchone()[0] == "present"


def test_on_info_changed_with_specific_meetup_id(conn):
    future_mid = get_or_create_meetup(conn, "2099-12-31", "미래정모", None)
    m = create_member(conn, "미래회원")  # 성별, 급수 없음
    add_attendee(conn, future_mid, m, "paste")
    before_q = conn.execute("SELECT queued_at FROM attendees WHERE meetup_id = ? AND member_id = ?", (future_mid, m)).fetchone()[0]
    # 성별/급수 채우기
    changed = set_info(conn, m, "M", "B", by="admin")
    on_info_changed(conn, m, changed, meetup_id=future_mid)
    after_q = conn.execute("SELECT queued_at FROM attendees WHERE meetup_id = ? AND member_id = ?", (future_mid, m)).fetchone()[0]
    assert after_q >= before_q


def test_assign_manual_validations(conn):
    from badminton.meetups import (add_manual_reservation, update_reservation,
                                   prioritize_reservation, start_reservation_now)
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 처음에는 3명만 참석시켜 코트 1, 2가 모두 빈 상태로 시작되게 함
    p1 = mk(conn, "선수1", "M", "A")
    p2 = mk(conn, "선수2", "M", "B")
    p3 = mk(conn, "선수3", "F", "A")
    for p in (p1, p2, p3):
        add_attendee(conn, mid, p, "paste")

    # 1. 미시작 정모 거부
    with pytest.raises(ValueError, match="먼저 코트를 시작해 주세요"):
        add_manual_reservation(conn, mid, [p1, p2, p3, p1])

    start(conn, mid, 2)  # 코트 2면 시작 (4명 미만이므로 자동 매칭 없이 코트 1, 2 모두 비어 있음)

    # 추가 선수 등록
    p4 = mk(conn, "선수4", "F", "B")
    p5 = mk(conn, "선수5", "M", "A")
    p6 = mk(conn, "선수6", "F", "C")
    p7 = mk(conn, "선수7", "M", "C")
    p8 = mk(conn, "선수8", "F", "D")
    p9 = mk(conn, "선수9", "M", "D")
    for p in (p4, p5, p6, p7, p8, p9):
        add_attendee(conn, mid, p, "paste")

    # 2. 같은 사람 중복 거부 (add_manual_reservation)
    with pytest.raises(ValueError, match="서로 다른 선수 네 명"):
        add_manual_reservation(conn, mid, [p1, p1, p3, p4])

    # 3. 참석 상태가 아닌 사람 거부
    set_status(conn, mid, p1, "away")
    with pytest.raises(ValueError, match="현재 참석 중인 선수만"):
        add_manual_reservation(conn, mid, [p1, p2, p3, p4])
    set_status(conn, mid, p1, "present")

    # 4. 수동 예약 추가 성공 (add_manual_reservation)
    rid1 = add_manual_reservation(conn, mid, [p1, p2, p3, p4])
    resv1 = conn.execute("SELECT * FROM reservations WHERE id = ?", (rid1,)).fetchone()
    assert resv1 is not None
    assert resv1["seq"] == 1 and resv1["source"] == "manual"
    assert (resv1["a1"], resv1["a2"], resv1["b1"], resv1["b2"]) == (p1, p2, p3, p4)

    # 5. 이미 예약된 선수 다시 선택 불가
    with pytest.raises(ValueError, match="이미 예약된 선수"):
        add_manual_reservation(conn, mid, [p1, p5, p6, p7])

    # 6. 두 번째 수동 예약 추가
    rid2 = add_manual_reservation(conn, mid, [p5, p6, p7, p8])
    resv2 = conn.execute("SELECT * FROM reservations WHERE id = ?", (rid2,)).fetchone()
    assert resv2 is not None
    assert resv2["seq"] == 2 and resv2["source"] == "manual"

    # 7. update_reservation 검증 (중복, 다른 예약 포함 불가, 정상 수정)
    with pytest.raises(ValueError, match="서로 다른 선수 네 명"):
        update_reservation(conn, rid2, [p5, p5, p7, p8])
    with pytest.raises(ValueError, match="다른 예약에 포함된 선수"):
        update_reservation(conn, rid2, [p1, p6, p7, p8])
    res = update_reservation(conn, rid2, [p5, p6, p7, p9])
    assert res is not None and res[0] == mid
    resv2_updated = conn.execute("SELECT * FROM reservations WHERE id = ?", (rid2,)).fetchone()
    assert (resv2_updated["a1"], resv2_updated["a2"], resv2_updated["b1"], resv2_updated["b2"]) == (p5, p6, p7, p9)

    # 8. prioritize_reservation 검증 (rid2를 1순위로 올림)
    prioritize_reservation(conn, rid2)
    resv2_prio = conn.execute("SELECT seq FROM reservations WHERE id = ?", (rid2,)).fetchone()[0]
    resv1_prio = conn.execute("SELECT seq FROM reservations WHERE id = ?", (rid1,)).fetchone()[0]
    assert resv2_prio == 1 and resv1_prio == 2

    # 9. start_reservation_now 검증 (rid2를 빈 코트 1에 즉시 시작)
    ret_mid, status, game_id, court = start_reservation_now(conn, rid2)
    assert ret_mid == mid and status == "started" and court == 1
    # DB 확인: 코트 1에 게임 생성, rid2는 예약에서 삭제, rid1은 seq 1로 당겨짐
    game1 = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    assert game1 is not None and game1["court"] == 1
    assert (game1["a1"], game1["a2"], game1["b1"], game1["b2"]) == (p5, p6, p7, p9)
    assert conn.execute("SELECT 1 FROM reservations WHERE id = ?", (rid2,)).fetchone() is None
    resv1_after = conn.execute("SELECT seq FROM reservations WHERE id = ?", (rid1,)).fetchone()[0]
    assert resv1_after == 1


def test_reservations_max_capped_by_courts(conn):
    # start 후 대기 8명, 코트 1 -> 예약 최대 1개 (코트 수만큼)
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 총 12명 (남6, 여6)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(6)] + [mk(conn, f"f{i}", "F", "A") for i in range(6)]
    seat(conn, mid, *players)
    start(conn, mid, 1)

    # 코트 1면에 4명 경기 중
    resvs = reservations(conn, mid)
    assert len(resvs) == 1
    assert resvs[0]["seq"] == 1

    # 남은 대기자는 4명 (예약된 4명은 queue에서 제외)
    q = queue(conn, mid)
    assert len(q) == 4

    # board() 계약 확인
    b = board(conn, mid)
    assert "reservations" in b
    assert len(b["reservations"]) == 1
    assert b["reservations"][0]["no"] == 1
    assert len(b["reservations"][0]["teams"]) == 2
    assert len(b["queue"]) == 4


def test_reservations_one_slot_when_eight_waiting_on_three_courts(conn):
    # (1) 20명·코트 3 시작 직후 대기 8명이면 예약은 1개, 예약 안 된 대기 4명.
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 총 20명 (코트 3면에 12명, 대기 8명)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(10)] + [mk(conn, f"f{i}", "F", "A") for i in range(10)]
    seat(conn, mid, *players)
    start(conn, mid, 3)

    resvs = reservations(conn, mid)
    assert len(resvs) == 1
    assert resvs[0]["seq"] == 1

    # 남은 대기자(예약 안 된 사람)는 4명
    assert len(queue(conn, mid)) == 4
    b = board(conn, mid)
    assert len(b["reservations"]) == 1
    assert len(b["queue"]) == 4


def test_end_game_promotes_reservation_and_mixes_eight_candidates(conn):
    # (2) 경기 하나가 끝나면 예약1이 코트로 들어가고, 새 예약이 8명 후보에서 만들어지며 방금 끝난 4명과 완전히 같은 조가 아니다.
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 총 20명 (코트 3면에 12명, 대기 8명)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(10)] + [mk(conn, f"f{i}", "F", "A") for i in range(10)]
    seat(conn, mid, *players)
    start(conn, mid, 3)

    resv1 = reservations(conn, mid)[0]
    r1_four = {resv1["a1"], resv1["a2"], resv1["b1"], resv1["b2"]}

    # 코트 1 경기 종료: 코트 1에 있던 4명
    g1 = conn.execute("SELECT id, a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()
    g1_id = g1["id"]
    g1_four = {g1["a1"], g1["a2"], g1["b1"], g1["b2"]}
    end_game(conn, g1_id)

    # 1. 예약 1이 코트 1로 들어감
    g1_new = conn.execute("SELECT a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()
    assert set(g1_new) == r1_four

    # 2. 새 예약이 8명 후보(이전 대기 4명 + 방금 끝난 4명)에서 만들어짐
    new_resvs = reservations(conn, mid)
    assert len(new_resvs) == 1
    new_r_four = {new_resvs[0]["a1"], new_resvs[0]["a2"], new_resvs[0]["b1"], new_resvs[0]["b2"]}

    # 방금 끝난 4명과 완전히 같은 조가 아님 (섞였음)
    assert new_r_four != g1_four


def test_end_game_promotes_reservation_and_shifts_and_refills(conn):
    # 경기 끝 -> 예약 1이 그 코트로, 예약 번호가 당겨지고 새 예약이 채워진다.
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 총 28명 (코트 3면에 12명, 대기 16명 -> 예약 3개 최대)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(14)] + [mk(conn, f"f{i}", "F", "A") for i in range(14)]
    seat(conn, mid, *players)
    start(conn, mid, 3)

    resvs_before = reservations(conn, mid)
    assert len(resvs_before) == 3
    r1_four = {resvs_before[0]["a1"], resvs_before[0]["a2"], resvs_before[0]["b1"], resvs_before[0]["b2"]}
    r2_id = resvs_before[1]["id"]
    r2_four = {resvs_before[1]["a1"], resvs_before[1]["a2"], resvs_before[1]["b1"], resvs_before[1]["b2"]}

    # 코트 1 경기 종료
    g1 = conn.execute("SELECT id FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()[0]
    end_game(conn, g1)

    # 1. 예약 1의 선수들이 코트 1의 새 경기에 투입됨
    g1_new = conn.execute("SELECT a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()
    assert set(g1_new) == r1_four

    # 2. 예약 번호가 당겨짐 (기존 예약 2가 이제 seq 1이 됨)
    resvs_after = reservations(conn, mid)
    assert len(resvs_after) == 3
    assert resvs_after[0]["id"] == r2_id
    assert resvs_after[0]["seq"] == 1
    assert {resvs_after[0]["a1"], resvs_after[0]["a2"], resvs_after[0]["b1"], resvs_after[0]["b2"]} == r2_four

    # 3. 새 예약이 마지막 번호(seq 3)로 채워짐
    assert [r["seq"] for r in resvs_after] == [1, 2, 3]


def test_reservations_keep_members_when_other_games_end(conn):
    # 예약은 다른 경기가 끝나도 멤버가 바뀌지 않는다.
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 총 20명 (코트 2면(8명), 대기 12명 -> 예약 2개)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(10)] + [mk(conn, f"f{i}", "F", "A") for i in range(10)]
    seat(conn, mid, *players)
    start(conn, mid, 2)

    r_init = reservations(conn, mid)
    assert len(r_init) == 2
    r2_members = (r_init[1]["a1"], r_init[1]["a2"], r_init[1]["b1"], r_init[1]["b2"])

    # 코트 1 경기 종료 -> 예약 1이 코트 1로 승격
    g1 = conn.execute("SELECT id FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()[0]
    end_game(conn, g1)

    # 기존 예약 2는 번호만 1로 당겨지고 4명 멤버는 그대로 유지되어야 함
    r_next = reservations(conn, mid)
    assert (r_next[0]["a1"], r_next[0]["a2"], r_next[0]["b1"], r_next[0]["b2"]) == r2_members


def test_away_status_drops_reservation_and_keeps_others_queue_time(conn):
    # 예약된 사람을 away로 바꾸면 그 예약만 사라지고 나머지 3명은 queued_at이 그대로다.
    mid = get_or_create_meetup(conn, today(), "t", None)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(6)] + [mk(conn, f"f{i}", "F", "A") for i in range(6)]
    seat(conn, mid, *players)
    start(conn, mid, 1)  # 코트 1면(4명), 대기 8명 -> 예약 1개, 대기 4명

    r1 = reservations(conn, mid)[0]
    p_away = r1["a1"]
    other_3 = [r1["a2"], r1["b1"], r1["b2"]]
    times_before = {p: conn.execute("SELECT queued_at FROM attendees WHERE meetup_id = ? AND member_id = ?", (mid, p)).fetchone()[0] for p in other_3}

    # a1 선수를 away로 변경
    set_status(conn, mid, p_away, "away")

    # 1. 예약 1이 해제되고, 새로 채워진 예약에는 away된 사람이 없음
    resvs = reservations(conn, mid)
    if resvs:
        assert p_away not in (resvs[0]["a1"], resvs[0]["a2"], resvs[0]["b1"], resvs[0]["b2"])

    # 2. 나머지 3명의 queued_at은 변경되지 않고 그대로 유지
    for p in other_3:
        t_after = conn.execute("SELECT queued_at FROM attendees WHERE meetup_id = ? AND member_id = ?", (mid, p)).fetchone()[0]
        assert t_after == times_before[p]


def test_redraw_reservation(conn):
    # redraw_reservation
    mid = get_or_create_meetup(conn, today(), "t", None)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(6)] + [mk(conn, f"f{i}", "F", "A") for i in range(6)]
    seat(conn, mid, *players)
    start(conn, mid, 1)

    r1 = reservations(conn, mid)[0]
    r1_id = r1["id"]
    t1 = r1["created_at"]
    ret_mid = redraw_reservation(conn, r1_id)
    assert ret_mid == mid

    # 해당 예약을 지우고 새 예약으로 다시 채움
    r_new = reservations(conn, mid)
    assert len(r_new) == 1
    assert r_new[0]["seq"] == 1
    assert r_new[0]["created_at"] >= t1

    # 없는 id일 때는 None
    assert redraw_reservation(conn, 99999) is None


def test_queue_excludes_reserved_players(conn):
    # queue()에서 예약된 사람이 빠진다.
    mid = get_or_create_meetup(conn, today(), "t", None)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(6)] + [mk(conn, f"f{i}", "F", "A") for i in range(6)]
    seat(conn, mid, *players)
    start(conn, mid, 1)  # 코트 1면(4명), 예약 1(4명), 대기열 4명

    resv_players = {reservations(conn, mid)[0][k] for k in ("a1", "a2", "b1", "b2")}
    q_players = {p.id for p in queue(conn, mid)}
    # 예약된 선수들은 대기열에 포함되지 않아야 함
    assert resv_players.isdisjoint(q_players)
    assert len(q_players) == 4


def test_reservation_does_not_make_uneven_gender_ratios(conn):
    # 남1여3 (또는 여1남3)이 예약으로도 만들어지지 않는다.
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 경기 중 4명 (남2 여2) + 남은 대기 4명 (남1 여3)
    p_game = [mk(conn, "g_m1", "M", "A"), mk(conn, "g_m2", "M", "A"), mk(conn, "g_f1", "F", "A"), mk(conn, "g_f2", "F", "A")]
    p_uneven = [mk(conn, "u_m1", "M", "A"), mk(conn, "u_f1", "F", "A"), mk(conn, "u_f2", "F", "A"), mk(conn, "u_f3", "F", "A")]
    seat(conn, mid, *p_game, *p_uneven)
    start(conn, mid, 1)

    # 남1여3은 성비 규칙에 위배되므로 예약이 생기지 않음
    assert len(reservations(conn, mid)) == 0
    # 4명은 예약되지 않은 채 대기열에 그대로 남음
    assert len(queue(conn, mid)) == 4


def test_info_cleared_drops_reservation(conn):
    # 급수나 성별이 비워지면 그 회원이 속한 예약이 해제된다
    mid = get_or_create_meetup(conn, today(), "t", None)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(6)] + [mk(conn, f"f{i}", "F", "A") for i in range(6)]
    seat(conn, mid, *players)
    start(conn, mid, 1)  # 코트 1면(4명), 예약 1개(4명), 대기 4명

    r1 = reservations(conn, mid)[0]
    target_member = r1["a1"]

    # target_member의 급수를 None으로 변경
    changed = set_info(conn, target_member, "M", None, by="admin")
    on_info_changed(conn, target_member, changed, meetup_id=mid)

    # 1. 새로 채워진 예약에는 target_member가 없어야 함
    new_resvs = reservations(conn, mid)
    if new_resvs:
        assert target_member not in (new_resvs[0]["a1"], new_resvs[0]["a2"], new_resvs[0]["b1"], new_resvs[0]["b2"])

    # 2. target_member는 급수 미입력으로 대진 제외(excluded)에 들어가야 함
    b = board(conn, mid)
    assert any(x["nickname"] == "m0" or x.get("id") == target_member for x in b["excluded"])


def test_reduce_courts_clears_excess_reservations(conn):
    # 코트 3면에서 1면으로 축소 시, 코트 수를 초과하는 예약(seq > 1)은 삭제되고
    # 해당 선수들은 대기열(queue)로 복귀해야 한다
    mid = get_or_create_meetup(conn, today(), "t", None)
    players = [create_member(conn, f"p{i}", gender="M", grade="A") for i in range(28)]
    for p in players:
        add_attendee(conn, mid, p, "paste")
    start(conn, mid, 3)

    assert len(reservations(conn, mid)) == 3
    assert len(queue(conn, mid)) == 4

    # 코트 수를 1면으로 축소
    start(conn, mid, 1)

    # 예약은 최대 코트 수인 1개만 남아야 함
    resvs = reservations(conn, mid)
    assert len(resvs) == 1
    assert resvs[0]["seq"] == 1

    # 삭제된 예약 2, 3의 8명이 대기열로 복귀하여 대기자가 12명이어야 함
    assert len(queue(conn, mid)) == 12
    b = board(conn, mid)
    assert len(b["reservations"]) == 1
    assert len(b["queue"]) == 12


def test_gender_change_drops_reservation_and_prevents_uneven_game(conn):
    # 예약에 포함된 선수의 성별이 변경(M -> F)되면 성비가 깨지므로(3:1 등) 예약이 해제되어야 한다
    mid = get_or_create_meetup(conn, today(), "t", None)
    for i in range(12):
        create_member(conn, f"m{i}", gender="M", grade="A")
        add_attendee(conn, mid, i + 1, "paste")
    start(conn, mid, 1)

    resvs = reservations(conn, mid)
    assert len(resvs) == 1
    target = resvs[0]["a1"]

    # 예약된 선수의 성별을 M -> F로 변경
    changed = set_info(conn, target, "F", "A", by="admin")
    on_info_changed(conn, target, changed, meetup_id=mid)

    # 남1여3 / 남3여1이 포함된 예약이 그대로 유지되어서는 안 됨
    resvs_after = reservations(conn, mid)
    if resvs_after:
        r = resvs_after[0]
        women = sum(conn.execute("SELECT gender FROM members WHERE id = ?", (r[k],)).fetchone()[0] == "F"
                    for k in ("a1", "a2", "b1", "b2"))
        assert women in (0, 2, 4)

    # 코트 경기 역시 3:1이나 1:3으로 생성되어서는 안 됨 (경기가 생겼다면 허용 성비(0, 2, 4)여야 함)
    g1 = conn.execute("SELECT id FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()[0]
    end_game(conn, g1)

    active_g = conn.execute("SELECT a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()
    if active_g is not None:
        women_in_game = sum(conn.execute("SELECT gender FROM members WHERE id = ?", (p,)).fetchone()[0] == "F"
                            for p in active_g)
        assert women_in_game in (0, 2, 4)


def test_board_tolerates_missing_attendee_in_game(conn):
    # 경기 데이터에 attendee 레코드가 누락된 회원이 있더라도 board()가 KeyError 없이 "?"로 방어 렌더링되어야 함
    mid = get_or_create_meetup(conn, today(), "t", None)
    m1 = create_member(conn, "선수1", gender="M", grade="A")
    m2 = create_member(conn, "선수2", gender="M", grade="A")
    m3 = create_member(conn, "선수3", gender="M", grade="A")
    m4 = create_member(conn, "선수4", gender="M", grade="A")
    add_attendee(conn, mid, m1, "paste")
    add_attendee(conn, mid, m2, "paste")
    add_attendee(conn, mid, m3, "paste")
    # m4는 attendees에 없음
    conn.execute("UPDATE meetups SET courts = 1, started = 1 WHERE id = ?", (mid,))
    conn.execute("INSERT INTO games(meetup_id, court, a1, a2, b1, b2, started_at) VALUES (?, 1, ?, ?, ?, ?, ?)",
                 (mid, m1, m2, m3, m4, time.time()))

    b = board(conn, mid)
    assert b["courts"][0]["teams"] == [["선수1", "선수2"], ["선수3", "?"]]


def test_board_card_data_has_game_time_last_end_and_states(conn):
    mid, gid = one_court_game(conn, extra=1)  # x0 은 대기
    court = board(conn, mid)["courts"][0]
    assert court["started_at"] and court["elapsed_clock"] == "0:00"
    end_game(conn, gid)
    b = board(conn, mid)
    played = [c for c in b["queue_cards"] if c["played"]]
    assert played and all(c["since"] for c in played) and b["queue_cards"][0]["order"] == 1
    away = played[0]["id"]
    set_status(conn, mid, away, "away")
    ex = {e["id"]: e for e in board(conn, mid)["excluded"]}
    assert ex[away]["state"] == "away" and ex[away]["reason"] == "휴식" and ex[away]["games"] >= 1


def test_court_rest_keeps_court_empty_for_15_seconds(conn, monkeypatch):
    from badminton import meetups
    monkeypatch.setattr(meetups, "COURT_REST_SECONDS", 15)
    mid, gid = one_court_game(conn, extra=4)
    end_game(conn, gid)
    court = board(conn, mid)["courts"][0]
    assert court["game_id"] is None and 0 < court["ready_in"] <= 15 and "ready_until" not in court
    meetups.fill_courts(conn, mid)  # 15초 전에는 다시 불러도 비어 있다
    assert board(conn, mid)["courts"][0]["game_id"] is None
    conn.execute("UPDATE games SET ended_at = ended_at - 16 WHERE id = ?", (gid,))
    meetups.fill_courts(conn, mid)
    assert board(conn, mid)["courts"][0]["game_id"] is not None


def test_court_rest_ready_in_uses_ceil(conn, monkeypatch):
    from badminton import meetups
    monkeypatch.setattr(meetups, "COURT_REST_SECONDS", 15)
    mid, gid = one_court_game(conn, extra=4)
    end_game(conn, gid)
    now = time.time()
    # 15.0초 남았을 때 ceil은 15
    conn.execute("UPDATE games SET ended_at = ? WHERE id = ?", (now, gid))
    assert board(conn, mid)["courts"][0]["ready_in"] == 15
    # 14.1초 남았을 때 ceil은 15
    conn.execute("UPDATE games SET ended_at = ? WHERE id = ?", (now - 0.9, gid))
    assert board(conn, mid)["courts"][0]["ready_in"] == 15
    # 14.0초 남았을 때 ceil은 14
    conn.execute("UPDATE games SET ended_at = ? WHERE id = ?", (now - 1.0, gid))
    assert board(conn, mid)["courts"][0]["ready_in"] == 14
    # 0.1초 남았을 때 ceil은 1
    conn.execute("UPDATE games SET ended_at = ? WHERE id = ?", (now - 14.9, gid))
    assert board(conn, mid)["courts"][0]["ready_in"] == 1
    # 0초 이하일 때는 None
    conn.execute("UPDATE games SET ended_at = ? WHERE id = ?", (now - 15.0, gid))
    assert board(conn, mid)["courts"][0]["ready_in"] is None


def test_court_rest_skipped_for_redraw_and_force(conn, monkeypatch):
    from badminton import meetups
    monkeypatch.setattr(meetups, "COURT_REST_SECONDS", 15)
    mid, gid = one_court_game(conn, extra=4)
    redraw(conn, gid)  # 다시 뽑기는 바로 채운다
    assert board(conn, mid)["courts"][0]["game_id"] is not None
    end_game(conn, current_game(conn, mid))
    meetups.fill_courts(conn, mid, force=True)  # 운영진의 '빈 코트 자동 매칭'
    assert board(conn, mid)["courts"][0]["game_id"] is not None


def test_memo_is_trimmed_shown_and_cleared(conn):
    from badminton.meetups import set_memo
    mid, gid = one_court_game(conn, extra=1)
    waiting = board(conn, mid)["queue_cards"][0]["id"]
    set_memo(conn, mid, waiting, "  콕 가볼  ")
    assert board(conn, mid)["queue_cards"][0]["memo"] == "콕 가볼"
    playing = board(conn, mid)["courts"][0]["team_players"][0][0]["id"]
    set_memo(conn, mid, playing, "라켓 수리")
    assert board(conn, mid)["courts"][0]["team_players"][0][0]["memo"] == "라켓 수리"
    with pytest.raises(ValueError):
        set_memo(conn, mid, waiting, "가" * 101)
    set_memo(conn, mid, waiting, "   ")
    assert board(conn, mid)["queue_cards"][0]["memo"] is None


def _four(row):
    return {row["a1"], row["a2"], row["b1"], row["b2"]}


def test_redraw_game_without_reservation_picks_different_four(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"m{i}", "M", "C") for i in range(6)])
    start(conn, mid, 1)
    g = conn.execute("SELECT * FROM games WHERE ended_at IS NULL").fetchone()
    redraw(conn, g["id"])
    g2 = conn.execute("SELECT * FROM games WHERE ended_at IS NULL").fetchone()
    assert _four(g2) != _four(g)


def test_redraw_reservation_picks_different_four(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"m{i}", "M", "C") for i in range(13)])
    start(conn, mid, 1)
    (old,) = reservations(conn, mid)
    redraw_reservation(conn, old["id"])
    (new,) = reservations(conn, mid)
    assert _four(new) != _four(old)


def test_delete_reservation_holds_slot_until_next_game_starts(conn):
    from badminton.meetups import delete_reservation
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"m{i}", "M", "C") for i in range(13)])
    start(conn, mid, 1)
    (r,) = reservations(conn, mid)
    assert delete_reservation(conn, r["id"]) == mid
    assert reservations(conn, mid) == [] and len(queue(conn, mid)) == 9  # 4명이 대기열로 돌아온다
    refill_reservations(conn, mid)  # 1초 갱신이 불러도 다시 만들지 않는다
    assert reservations(conn, mid) == []
    end_game(conn, current_game(conn, mid))  # 다음 경기가 코트에 들어가면 예약을 다시 채운다
    assert len(reservations(conn, mid)) == 1
    assert delete_reservation(conn, 99999) is None


def test_delete_reservation_is_not_refilled_when_queue_limited_reservations(conn):
    from badminton.meetups import delete_reservation
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"m{i}", "M", "C") for i in range(28)])
    start(conn, mid, 4)  # 코트 4면 16명, 대기 12명 → 예약은 대기 인원 때문에 2개뿐
    assert len(reservations(conn, mid)) == 2
    delete_reservation(conn, reservations(conn, mid)[0]["id"])
    refill_reservations(conn, mid)
    assert len(reservations(conn, mid)) == 1  # 목표(4)보다 적어도 다음 경기 전까지는 새로 만들지 않는다


def test_reservation_hold_is_released_by_auto_match_and_court_change(conn):
    from badminton.meetups import delete_reservation, fill_courts
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"m{i}", "M", "C") for i in range(13)])
    start(conn, mid, 1)
    delete_reservation(conn, reservations(conn, mid)[0]["id"])
    fill_courts(conn, mid, force=True)  # 코트가 다 차 있어도 운영진이 자동 매칭을 누르면 예약을 다시 채운다
    assert len(reservations(conn, mid)) == 1
    delete_reservation(conn, reservations(conn, mid)[0]["id"])
    start(conn, mid, 1)  # 코트 수 저장도 마찬가지
    assert len(reservations(conn, mid)) == 1


def test_repeat_four_is_not_reserved_even_with_a_manual_reservation(conn):
    from badminton.db import set_setting
    from badminton.meetups import add_manual_reservation
    set_setting(conn, "mix_window", 4)  # 앞 4명만 후보 → 직전 경기 4명이 그대로 다시 뽑힌다
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"p{i}", "M", "C") for i in range(8)])
    start(conn, mid, 1)
    end_game(conn, current_game(conn, mid))  # 첫 4명이 끝나고 나머지 4명이 들어간다
    late = [mk(conn, f"late{i}", "M", "C") for i in range(8)]
    seat(conn, mid, *late)
    conn.execute(f"UPDATE attendees SET games = 9 WHERE member_id IN ({','.join('?' * 8)})", late)  # 줄 맨 뒤로
    conn.execute("DELETE FROM reservations")
    add_manual_reservation(conn, mid, late[:4])
    assert [r["source"] for r in reservations(conn, mid)] == ["manual"]


def test_auto_reserve_toggle_disables_and_refills(conn):
    from badminton.db import get_setting, set_setting
    from badminton.meetups import add_manual_reservation, board as get_board, refill_reservations

    # 1. 기본값은 켜짐 (1)
    assert get_setting(conn, "auto_reserve") == 1.0

    mid = get_or_create_meetup(conn, today(), "t", None)
    players = [mk(conn, f"m{i}", "M", "C") for i in range(16)]
    seat(conn, mid, *players)
    start(conn, mid, 1)

    # 1코트(4명) + 대기 12명 -> 1개 자동 예약 생성
    resvs = reservations(conn, mid)
    assert len(resvs) == 1 and resvs[0]["source"] == "auto"

    # 수동 예약 1개 추가 (4명) -> 총 2개 예약 (auto, manual)
    q_pids = [p.id for p in queue(conn, mid)[:4]]
    add_manual_reservation(conn, mid, q_pids)
    resvs_before = reservations(conn, mid)
    assert len(resvs_before) == 2
    sources = [r["source"] for r in resvs_before]
    assert "auto" in sources and "manual" in sources

    # 2. auto_reserve 끄기 (0)
    set_setting(conn, "auto_reserve", 0.0)
    refill_reservations(conn, mid)

    # 자동 예약은 삭제되고, 수동 예약만 seq=1로 남는다
    resvs_off = reservations(conn, mid)
    assert len(resvs_off) == 1
    assert resvs_off[0]["source"] == "manual"
    assert resvs_off[0]["seq"] == 1

    # refill_reservations을 다시 호출해도 새 자동 예약이 생기지 않는다
    refill_reservations(conn, mid)
    assert len(reservations(conn, mid)) == 1

    b = get_board(conn, mid)
    assert b["auto_reserve"] is False

    # 3. auto_reserve 다시 켜기 (1)
    set_setting(conn, "auto_reserve", 1.0)
    refill_reservations(conn, mid)

    # 대기열 인원이 충분하므로 자동 예약이 다시 생성된다
    resvs_on = reservations(conn, mid)
    assert len(resvs_on) == 2
    assert {r["source"] for r in resvs_on} == {"manual", "auto"}
    b_on = get_board(conn, mid)
    assert b_on["auto_reserve"] is True


def test_auto_fill_toggle_disables_and_refills(conn):
    from badminton.db import get_setting, set_setting
    from badminton.meetups import board as get_board, end_game, fill_courts

    # 1. 기본값은 켜짐 (1)
    assert get_setting(conn, "auto_fill") == 1.0

    mid = get_or_create_meetup(conn, today(), "t", None)
    players = [mk(conn, f"m{i}", "M", "C") for i in range(8)]
    seat(conn, mid, *players)
    start(conn, mid, 1)

    row = conn.execute("SELECT id FROM games WHERE meetup_id = ? AND ended_at IS NULL", (mid,)).fetchone()
    assert row is not None
    gid = row[0]

    # 2. auto_fill 끄기 (0)
    set_setting(conn, "auto_fill", 0.0)
    b = get_board(conn, mid)
    assert b["auto_fill"] is False

    # 경기 종료 -> auto_fill이 꺼져 있으므로 코트에 새 경기가 들어가지 않고 비어 있음
    end_game(conn, gid)
    assert conn.execute("SELECT id FROM games WHERE meetup_id = ? AND ended_at IS NULL", (mid,)).fetchone() is None

    # fill_courts(force=False)를 직접 호출해도 채워지지 않음
    fill_courts(conn, mid, force=False)
    assert conn.execute("SELECT id FROM games WHERE meetup_id = ? AND ended_at IS NULL", (mid,)).fetchone() is None

    # force=True (운영진 수동 자동 매칭)는 auto_fill이 꺼져 있어도 동작함
    fill_courts(conn, mid, force=True)
    row2 = conn.execute("SELECT id FROM games WHERE meetup_id = ? AND ended_at IS NULL", (mid,)).fetchone()
    assert row2 is not None
    gid2 = row2[0]

    # 다시 경기 종료 후 빈 코트 상태로 만들기
    end_game(conn, gid2)
    assert conn.execute("SELECT id FROM games WHERE meetup_id = ? AND ended_at IS NULL", (mid,)).fetchone() is None

    # 3. auto_fill 다시 켜기 (1)
    set_setting(conn, "auto_fill", 1.0)
    b_on = get_board(conn, mid)
    assert b_on["auto_fill"] is True

    # 코트 자동 배정이 활성화되어 빈 코트를 채움
    fill_courts(conn, mid, force=False)
    assert conn.execute("SELECT id FROM games WHERE meetup_id = ? AND ended_at IS NULL", (mid,)).fetchone() is not None


def test_db_connect_creates_court_states_on_legacy_db(tmp_path):
    import sqlite3
    from badminton.db import connect
    db_file = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(db_file)
    raw.execute("CREATE TABLE meetups(id INTEGER PRIMARY KEY, date TEXT NOT NULL, name TEXT NOT NULL, eid TEXT UNIQUE, "
                "courts INTEGER NOT NULL DEFAULT 0, started INTEGER NOT NULL DEFAULT 0)")
    raw.close()

    migrated = connect(db_file)
    cols = [r[1] for r in migrated.execute("PRAGMA table_info(court_states)")]
    assert cols == ["meetup_id", "court", "state"]
    migrated.close()


def test_court_state_skips_fill_and_caps_auto_reservations(conn):
    from badminton.meetups import fill_courts, set_court_state
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"p{i}", "M", "C") for i in range(4)])
    start(conn, mid, 3)  # 1코트 경기 중, 2·3코트 빈 코트

    set_court_state(conn, mid, 2, "empty")
    set_court_state(conn, mid, 3, "lesson")
    courts = board(conn, mid)["courts"]
    assert [c["state"] for c in courts] == ["game", "empty", "lesson"]

    # 대기 인원이 충분해도 empty/lesson 코트에는 경기가 배정되지 않고 자동 예약은 game 코트 수(1개)까지만 생성된다
    seat(conn, mid, *[mk(conn, f"w{i}", "M", "C") for i in range(20)])
    fill_courts(conn, mid, force=True)
    courts = board(conn, mid)["courts"]
    assert courts[0]["game_id"] is not None
    assert courts[1]["game_id"] is None and courts[1]["state"] == "empty"
    assert courts[2]["game_id"] is None and courts[2]["state"] == "lesson"
    assert len(reservations(conn, mid)) == 1


def test_court_state_return_to_game_fills_immediately_and_validation(conn, monkeypatch):
    from badminton import meetups
    from badminton.meetups import set_court_state
    monkeypatch.setattr(meetups, "COURT_REST_SECONDS", 15)
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"p{i}", "M", "C") for i in range(20)])
    start(conn, mid, 2)

    # 경기 중인 코트는 상태 변경 불가
    with pytest.raises(ValueError, match="경기 중"):
        set_court_state(conn, mid, 1, "lesson")
    # 범위 밖 코트·잘못된 상태값 거절
    with pytest.raises(ValueError):
        set_court_state(conn, mid, 0, "lesson")
    with pytest.raises(ValueError):
        set_court_state(conn, mid, 3, "lesson")
    with pytest.raises(ValueError):
        set_court_state(conn, mid, 1, "invalid")

    # 2코트 경기를 끝내고(휴식 상태) 레슨으로 바꾸면 자동 예약이 2개 -> 1개로 줄어든다
    assert len(reservations(conn, mid)) == 2
    g2 = next(c["game_id"] for c in board(conn, mid)["courts"] if c["court"] == 2)
    end_game(conn, g2)
    set_court_state(conn, mid, 2, "lesson")
    assert board(conn, mid)["courts"][1]["state"] == "lesson"
    assert board(conn, mid)["courts"][1]["ready_in"] is None
    assert len(reservations(conn, mid)) == 1

    # 다시 game으로 되돌리면 쉬는 시간과 무관하게 즉시 채워지고 예약도 2개로 복구된다
    set_court_state(conn, mid, 2, "game")
    courts = board(conn, mid)["courts"]
    assert courts[1]["state"] == "game" and courts[1]["game_id"] is not None
    assert len(reservations(conn, mid)) == 2
