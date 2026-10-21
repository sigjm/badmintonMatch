from datetime import datetime

from badminton.members import create_member, set_info
from badminton.meetups import (SEOUL, add_attendee, add_pasted, board, end_game, get_or_create_meetup,
                               on_info_changed, queue, redraw, session_day, set_status, start, sync_somoim, today)
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
    ids += [mk(conn, f"x{i}", "M", "B") for i in range(extra)]
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
    on_info_changed(conn, waiting, set_info(conn, waiting, "M", "B", by="self"))
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
