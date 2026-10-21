from badminton.somoim import SomoimError
from conftest import GROUP_ONE_EVENT


def test_admin_requires_login(client):
    assert client.get("/admin").headers["Location"].endswith("/admin/login")


def test_wrong_password_rejected(client):
    assert "비밀번호" in client.post("/admin/login", data={"password": "x"}).get_data(as_text=True)
    assert client.get("/admin").status_code == 302


def test_import_creates_meetup_with_attendees(admin, monkeypatch, conn_of):
    monkeypatch.setattr("badminton.admin.fetch_group", lambda gid: GROUP_ONE_EVENT)  # 참석 2명, 2026-10-23
    r = admin.post("/admin/import", data={"date": "2026-10-23"})
    assert r.status_code == 302 and conn_of().execute("SELECT count(*) FROM attendees").fetchone()[0] == 2


def test_import_no_event_on_date_offers_paste(admin, monkeypatch, conn_of):
    monkeypatch.setattr("badminton.admin.fetch_group", lambda gid: GROUP_ONE_EVENT)
    html = admin.post("/admin/import", data={"date": "2026-10-24"}).get_data(as_text=True)
    assert "붙여넣기" in html and conn_of().execute("SELECT count(*) FROM meetups").fetchone()[0] == 0


def test_somoim_failure_keeps_attendees_and_offers_paste(admin, monkeypatch, conn_of):  # Review Focus 1
    monkeypatch.setattr("badminton.admin.fetch_group", lambda gid: GROUP_ONE_EVENT)
    admin.post("/admin/import", data={"date": "2026-10-23"})

    def boom(gid):
        raise SomoimError("구조 변경")

    monkeypatch.setattr("badminton.admin.fetch_group", boom)
    html = admin.post("/admin/m/1/import").get_data(as_text=True)
    assert "붙여넣기" in html
    assert conn_of().execute("SELECT count(*) FROM attendees WHERE status='present'").fetchone()[0] == 2


def test_meetup_page_renders(admin, seeded_meetup):
    mid, _ = seeded_meetup
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    assert "코트 1" in html and "경기 끝" in html


def test_end_and_redraw_routes(admin, seeded_meetup):
    mid, gid = seeded_meetup
    assert admin.post(f"/admin/game/{gid}/end").status_code == 302
    assert admin.post(f"/admin/game/{gid}/end").status_code == 302  # 두 번째도 오류 없이 처리
    assert admin.post(f"/admin/game/{gid}/redraw").status_code == 302


def test_guest_added_to_meetup(admin, seeded_meetup, conn_of):
    mid, _ = seeded_meetup
    admin.post(f"/admin/m/{mid}/guest", data={"nickname": "손님", "gender": "F", "grade": "C"})
    row = conn_of().execute("SELECT m.is_guest, a.source FROM attendees a JOIN members m ON m.id = a.member_id "
                            "WHERE m.nickname = '손님'").fetchone()
    assert tuple(row) == (1, "guest")


def test_settings_validation(admin, conn_of):
    from badminton.db import get_setting
    admin.post("/admin/settings", data={"female_offset": "abc", "mix_window": "6"})
    assert get_setting(conn_of(), "female_offset") == 1
    admin.post("/admin/settings", data={"female_offset": "9", "mix_window": "6"})
    assert get_setting(conn_of(), "female_offset") == 1
    admin.post("/admin/settings", data={"female_offset": "1.5", "mix_window": "8"})
    assert get_setting(conn_of(), "female_offset") == 1.5 and get_setting(conn_of(), "mix_window") == 8


def test_admin_edit_logged_as_admin(admin, conn_of):
    from badminton.members import changes, create_member
    m = create_member(conn_of(), "가")
    admin.post(f"/admin/members/{m}", data={"gender": "M", "grade": "C"})
    assert {c["by"] for c in changes(conn_of())} == {"admin"}


def test_admin_reset_pin(admin, conn_of):
    from badminton.members import check_pin, create_member, set_pin
    m = create_member(conn_of(), "가")
    set_pin(conn_of(), m, "1234")
    admin.post(f"/admin/members/{m}/reset-pin")
    assert not check_pin(conn_of(), m, "1234")


def test_members_page_lists_changes(admin, conn_of):
    from badminton.members import create_member, set_info
    m = create_member(conn_of(), "가나다")
    set_info(conn_of(), m, "F", "B", by="self")
    html = admin.get("/admin/members").get_data(as_text=True)
    assert "가나다" in html and "변경 기록" in html
