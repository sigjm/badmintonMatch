import pytest

from badminton.db import get_setting
from badminton.members import (changes, check_pin, create_member, list_members, rename_member,
                               reset_pin, set_info, set_pin, upsert_somoim)


def test_upsert_somoim_updates_nickname(conn):
    a = upsert_somoim(conn, "m1", "옛닉")
    b = upsert_somoim(conn, "m1", "새닉")
    assert a == b and list_members(conn)[0]["nickname"] == "새닉"


def test_set_info_logs_only_changes(conn):
    m = create_member(conn, "가")
    set_info(conn, m, "F", "B", by="self")
    set_info(conn, m, "F", "A", by="admin")
    assert [(c["field"], c["old"], c["new"], c["by"]) for c in changes(conn)] == [
        ("grade", "B", "A", "admin"), ("grade", None, "B", "self"), ("gender", None, "F", "self")]


def test_set_info_rejects_bad_codes(conn):
    m = create_member(conn, "가")
    with pytest.raises(ValueError):
        set_info(conn, m, "X", "A", by="admin")


def test_pin_rules(conn):
    m = create_member(conn, "가")
    for bad in ("123", "12345", "abcd"):
        with pytest.raises(ValueError):
            set_pin(conn, m, bad)
    set_pin(conn, m, "0420")
    assert check_pin(conn, m, "0420") and not check_pin(conn, m, "0000")
    reset_pin(conn, m)
    assert not check_pin(conn, m, "0420")


def test_settings_defaults(conn):
    assert get_setting(conn, "female_offset") == 1.5 and get_setting(conn, "mix_ratio") == 0


def test_rename_member_success_and_logs_change(conn):
    m = create_member(conn, "원래이름")
    rename_member(conn, m, "  새이름  ", by="admin")
    row = conn.execute("SELECT nickname FROM members WHERE id = ?", (m,)).fetchone()
    assert row["nickname"] == "새이름"
    c = changes(conn)[0]
    assert c["member_id"] == m
    assert c["field"] == "nickname"
    assert c["old"] == "원래이름"
    assert c["new"] == "새이름"
    assert c["by"] == "admin"


def test_rename_member_validations(conn):
    m = create_member(conn, "테스트")
    with pytest.raises(ValueError, match="이름을 입력해 주세요"):
        rename_member(conn, m, "", by="admin")
    with pytest.raises(ValueError, match="이름을 입력해 주세요"):
        rename_member(conn, m, "   ", by="admin")
    with pytest.raises(ValueError, match="이름은 40자 이하"):
        rename_member(conn, m, "가" * 41, by="admin")
    with pytest.raises(ValueError, match="존재하지 않는 회원"):
        rename_member(conn, 99999, "이름", by="admin")


def test_rename_member_noop_when_identical(conn):
    m = create_member(conn, "동일이름")
    rename_member(conn, m, "동일이름", by="admin")
    assert len(changes(conn)) == 0


def test_get_setting_fallback_on_missing_or_none(conn):
    # settings 테이블에 키가 없거나 값이 None이어도 500 오류 없이 기본값(또는 지정 기본값)을 반환해야 함
    conn.execute("DELETE FROM settings WHERE key = 'female_offset'")
    assert get_setting(conn, "female_offset") == 1.5
    assert get_setting(conn, "unknown_key", default=42.0) == 42.0


def test_transaction_rolls_back_on_error(conn):
    from badminton.db import transaction
    try:
        with transaction(conn):
            conn.execute("INSERT INTO members(nickname) VALUES ('롤백테스트')")
            raise RuntimeError("작업 중단")
    except RuntimeError:
        pass
    assert conn.execute("SELECT count(*) FROM members WHERE nickname = '롤백테스트'").fetchone()[0] == 0



def test_members_and_changes_are_listed_per_group(conn):
    from badminton.groups import add_group
    from badminton.meetups import add_attendee, get_or_create_meetup
    ga, gb = add_group(conn, "a" * 36, "모임A"), add_group(conn, "b" * 36, "모임B")
    ma = get_or_create_meetup(conn, "2026-10-23", "A정모", None, group_id=ga)
    mb = get_or_create_meetup(conn, "2026-10-24", "B정모", None, group_id=gb)
    only_a, only_b, both = create_member(conn, "에이"), create_member(conn, "비이"), create_member(conn, "둘다")
    guest_a = create_member(conn, "에이손님", is_guest=True, gender="M", grade="C")
    for mid, ids, src in ((ma, (only_a, both), "paste"), (mb, (only_b, both), "paste"), (ma, (guest_a,), "guest")):
        for i in ids:
            add_attendee(conn, mid, i, src)
    assert [r["nickname"] for r in list_members(conn, guests=False, group_id=ga)] == ["둘다", "에이"]
    assert [r["nickname"] for r in list_members(conn, guests=False, group_id=gb)] == ["둘다", "비이"]
    assert [r["nickname"] for r in list_members(conn, guests=True, group_id=gb)] == []
    set_info(conn, only_b, "F", "D", by="admin")
    assert [c["nickname"] for c in changes(conn, group_id=ga)] == []
    assert {c["nickname"] for c in changes(conn, group_id=gb)} == {"비이"}
