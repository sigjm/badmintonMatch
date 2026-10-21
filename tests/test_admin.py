from badminton.somoim import Group, SomoimError, SomoimMember
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
    assert "코트 1" in html and ("경기 끝" in html or "게임 끝내기" in html)


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


GROUP_NO_FLAGS = Group(events=GROUP_ONE_EVENT.events,
                       members=[SomoimMember(m.mid, m.nickname, frozenset(), False) for m in GROUP_ONE_EVENT.members])


def test_reimport_with_no_attendee_flags_keeps_everyone(admin, monkeypatch, conn_of):  # Review Focus 1
    monkeypatch.setattr("badminton.admin.fetch_group", lambda gid: GROUP_ONE_EVENT)
    admin.post("/admin/import", data={"date": "2026-10-23"})
    monkeypatch.setattr("badminton.admin.fetch_group", lambda gid: GROUP_NO_FLAGS)
    html = admin.post("/admin/m/1/import").get_data(as_text=True)
    assert "붙여넣기" in html
    assert conn_of().execute("SELECT count(*) FROM attendees WHERE status='present'").fetchone()[0] == 2


def test_first_import_with_no_attendees_creates_nothing(admin, monkeypatch, conn_of):
    monkeypatch.setattr("badminton.admin.fetch_group", lambda gid: GROUP_NO_FLAGS)
    assert "붙여넣기" in admin.post("/admin/import", data={"date": "2026-10-23"}).get_data(as_text=True)
    assert conn_of().execute("SELECT count(*) FROM meetups").fetchone()[0] == 0


def test_paste_after_failed_reimport_fills_same_meetup(admin, monkeypatch, conn_of):
    monkeypatch.setattr("badminton.admin.fetch_group", lambda gid: GROUP_ONE_EVENT)
    admin.post("/admin/import", data={"date": "2026-10-23"})

    def boom(gid):
        raise SomoimError("x")

    monkeypatch.setattr("badminton.admin.fetch_group", boom)
    html = admin.post("/admin/m/1/import").get_data(as_text=True)
    assert 'name="mid" value="1"' in html
    admin.post("/admin/paste", data={"mid": "1", "date": "2026-10-23", "name": "x", "text": "새사람"})
    c = conn_of()
    assert c.execute("SELECT count(*) FROM meetups").fetchone()[0] == 1
    assert c.execute("SELECT count(*) FROM attendees WHERE meetup_id = 1").fetchone()[0] == 3


def test_pin_reset_confirm_not_injectable(admin, conn_of):
    import re
    from badminton.members import create_member, set_pin
    m = create_member(conn_of(), "x');alert(1);('")
    set_pin(conn_of(), m, "1234")
    html = admin.get("/admin/members").get_data(as_text=True)
    assert not any("alert" in a for a in re.findall(r'onsubmit="([^"]*)"', html))


def test_change_log_shows_time_and_labels(admin, conn_of):
    import re
    from badminton.members import create_member, set_info
    m = create_member(conn_of(), "가나다")
    set_info(conn_of(), m, "F", "N", by="self")
    html = admin.get("/admin/members").get_data(as_text=True)
    assert "초심" in html and "없음 → 여" in html and re.search(r"\d\d-\d\d \d\d:\d\d", html)


def test_status_select_hides_canceled_unless_current(admin, seeded_meetup):
    mid, _ = seeded_meetup
    assert 'value="canceled"' not in admin.get(f"/admin/m/{mid}").get_data(as_text=True)


def test_session_cookie_flags(tmp_path):
    from badminton import create_app
    app = create_app({"DATABASE": str(tmp_path / "c.db")})
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax" and app.config["SESSION_COOKIE_SECURE"] is True


def test_add_group_by_link_and_select_it(admin, monkeypatch, conn_of):
    monkeypatch.setattr("badminton.admin.fetch_group",
                        lambda gid: Group(events=[], members=[SomoimMember("a", "가", frozenset(), False)], name="Team ONAM"))
    admin.post("/admin/groups", data={"link": "https://www.somoim.co.kr/d49e0b02-b22a-413b-8295-53dc895bc4411"})
    row = conn_of().execute("SELECT id, name FROM groups WHERE gid = 'd49e0b02-b22a-413b-8295-53dc895bc4411'").fetchone()
    assert row["name"] == "Team ONAM"
    admin.post("/admin/groups/select", data={"group_id": row["id"]})
    seen = []
    monkeypatch.setattr("badminton.admin.fetch_group", lambda gid: seen.append(gid) or GROUP_ONE_EVENT)
    admin.post("/admin/import", data={"date": "2026-10-23"})
    assert seen == ["d49e0b02-b22a-413b-8295-53dc895bc4411"]
    assert conn_of().execute("SELECT group_id FROM meetups").fetchone()[0] == row["id"]


def test_admin_home_shows_group_tabs(admin, conn_of):
    from badminton.groups import add_group
    add_group(conn_of(), "d49e0b02-b22a-413b-8295-53dc895bc4411", "Team ONAM")
    html = admin.get("/admin").get_data(as_text=True)
    assert "금요일 야간민턴" in html and "Team ONAM" in html


def test_bad_group_link_rejected(admin, conn_of):
    html = admin.post("/admin/groups", data={"link": "https://example.com/x"}, follow_redirects=True).get_data(as_text=True)
    assert "소모임 모임 링크" in html and conn_of().execute("SELECT count(*) FROM groups").fetchone()[0] == 1


def test_group_search_lists_results_with_add_buttons(admin, monkeypatch):
    monkeypatch.setattr("badminton.admin.search_groups",
                        lambda q: [("8119d5e4-f96f-11ef-a485-0a4564f15a111", "Team RBT 민턴")])
    html = admin.get("/admin/groups?q=RBT").get_data(as_text=True)
    assert "Team RBT 민턴" in html and 'value="8119d5e4-f96f-11ef-a485-0a4564f15a111"' in html


def test_group_search_failure_shows_message(admin, monkeypatch):
    def boom(q):
        raise SomoimError("down")
    monkeypatch.setattr("badminton.admin.search_groups", boom)
    assert "검색하지 못했습니다" in admin.get("/admin/groups?q=RBT").get_data(as_text=True)
