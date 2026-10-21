import pytest

from badminton.db import get_setting
from badminton.members import changes, check_pin, create_member, list_members, reset_pin, set_info, set_pin, upsert_somoim


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
    assert get_setting(conn, "female_offset") == 1.5 and get_setting(conn, "mix_window") == 6
