from badminton.somoim import Group, SomoimError, SomoimMember
from conftest import GROUP_ONE_EVENT


def test_admin_requires_login(client):
    assert client.get("/admin").headers["Location"].endswith("/admin/login")


def test_wrong_password_rejected(client):
    assert "비밀번호" in client.post("/admin/login", data={"password": "x"}).get_data(as_text=True)
    assert client.get("/admin").status_code == 302


def test_admin_login_limits_failed_attempts_by_client(client):
    client_ip = "203.0.113.77"
    for _ in range(5):
        response = client.post("/admin/login", data={"password": "wrong"},
                               environ_overrides={"REMOTE_ADDR": client_ip})
        assert response.status_code == 200

    blocked = client.post("/admin/login", data={"password": "pw"},
                          environ_overrides={"REMOTE_ADDR": client_ip})
    assert blocked.status_code == 429


def test_info_edit_rejects_member_from_another_meetup(admin, seeded_meetup, conn_of):
    from badminton.members import create_member
    from badminton.meetups import get_or_create_meetup, today

    meetup_a, _ = seeded_meetup
    conn = conn_of()
    meetup_b = get_or_create_meetup(conn, today(), "다른 정모", None)
    member_id = create_member(conn, "다른 정모 회원", gender="F", grade="B")
    player_ids = [member_id] + [create_member(conn, f"다른-{i}", gender="M", grade="C") for i in range(3)]
    for player_id in player_ids:
        conn.execute("INSERT INTO attendees(meetup_id, member_id, status, source, queued_at) "
                     "VALUES (?, ?, 'present', 'paste', 0)", (meetup_b, player_id))
    conn.execute("UPDATE meetups SET courts = 1, started = 1 WHERE id = ?", (meetup_b,))
    conn.execute("INSERT INTO reservations(meetup_id, seq, a1, a2, b1, b2, created_at) "
                 "VALUES (?, 1, ?, ?, ?, ?, 0)", (meetup_b, *player_ids))

    response = admin.post(f"/admin/m/{meetup_a}/info", data={"member_id": member_id, "gender": "", "grade": ""})

    member = conn.execute("SELECT gender, grade FROM members WHERE id = ?", (member_id,)).fetchone()
    reservation = conn.execute("SELECT a1 FROM reservations WHERE meetup_id = ?", (meetup_b,)).fetchone()
    assert response.status_code == 302
    assert tuple(member) == ("F", "B")
    assert reservation["a1"] == member_id


def test_admin_mutation_rejects_cross_origin_post(admin, conn_of):
    from badminton.db import get_setting

    response = admin.post("/admin/settings", data={"female_offset": "3", "mix_window": "8"},
                          environ_overrides={"HTTP_ORIGIN": "https://evil.jmllem.com"})

    assert response.status_code == 403
    assert get_setting(conn_of(), "female_offset") == 1.5


def test_admin_mutation_rejects_post_without_origin_or_referer(admin):
    response = admin.post("/admin/logout", environ_overrides={"HTTP_ORIGIN": "", "HTTP_REFERER": ""})

    assert response.status_code == 403


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
    assert get_setting(conn_of(), "female_offset") == 1.5
    admin.post("/admin/settings", data={"female_offset": "9", "mix_window": "6"})
    assert get_setting(conn_of(), "female_offset") == 1.5
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


def test_admin_meetup_updates_requires_login(client, seeded_meetup):
    mid, _ = seeded_meetup
    resp = client.get(f"/admin/m/{mid}/updates")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_admin_meetup_updates_json_keys(admin, seeded_meetup):
    mid, _ = seeded_meetup
    resp = admin.get(f"/admin/m/{mid}/updates")
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "no-store"
    data = resp.get_json()
    assert set(data.keys()) >= {"meetup_id", "court_board", "queue", "active_courts", "queue_count"}
    assert data["meetup_id"] == mid
    assert isinstance(data["active_courts"], int)
    assert isinstance(data["queue_count"], int)


def test_admin_manual_match_requires_login(client, seeded_meetup):
    mid, _ = seeded_meetup
    resp = client.post(f"/admin/m/{mid}/manual/1")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_admin_manual_match_routes(admin, seeded_meetup, conn_of):
    mid, _ = seeded_meetup
    # 1. 선수 미선택 실패 플래시
    resp = admin.post(f"/admin/m/{mid}/manual/2", data={}, follow_redirects=True)
    assert "선수 네 명을 모두 선택해 주세요" in resp.get_data(as_text=True)

    # 2. 빈 코트 미선택 실패 플래시
    resp2 = admin.post(f"/admin/m/{mid}/manual", data={"a1": 1, "a2": 2, "b1": 3, "b2": 4}, follow_redirects=True)
    assert "배정할 빈 코트를 선택해 주세요" in resp2.get_data(as_text=True)

    # 3. 코트 2면으로 확장 후 수동 배정 성공
    from badminton.members import create_member
    from badminton.meetups import add_attendee, start
    c = conn_of()
    start(c, mid, 2)
    p1 = create_member(c, "수동1", gender="M", grade="A")
    p2 = create_member(c, "수동2", gender="M", grade="B")
    p3 = create_member(c, "수동3", gender="F", grade="A")
    p4 = create_member(c, "수동4", gender="F", grade="B")
    for p in (p1, p2, p3, p4):
        add_attendee(c, mid, p, "paste")
    resp3 = admin.post(f"/admin/m/{mid}/manual/2", data={"a1": p1, "a2": p2, "b1": p3, "b2": p4}, follow_redirects=True)
    assert "코트 2에 수동 대진을 배정했습니다" in resp3.get_data(as_text=True)
    game2 = c.execute("SELECT * FROM games WHERE meetup_id = ? AND court = 2 AND ended_at IS NULL", (mid,)).fetchone()
    assert game2 is not None and (game2["a1"], game2["a2"], game2["b1"], game2["b2"]) == (p1, p2, p3, p4)


def test_redraw_reservation_requires_login(client):
    resp = client.post("/admin/reservation/1/redraw")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_redraw_reservation_calls_meetups_and_redirects(admin, monkeypatch, seeded_meetup):
    from badminton import meetups
    mid, _ = seeded_meetup
    called = []

    def fake_redraw(conn, rid):
        called.append(rid)
        return mid

    monkeypatch.setattr(meetups, "redraw_reservation", fake_redraw, raising=False)
    resp = admin.post("/admin/reservation/42/redraw")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/admin/m/{mid}")
    assert called == [42]


def test_redraw_reservation_fallback_mid_from_form(admin, monkeypatch, seeded_meetup):
    from badminton import meetups
    mid, _ = seeded_meetup

    def fake_redraw_none(conn, rid):
        return None

    monkeypatch.setattr(meetups, "redraw_reservation", fake_redraw_none, raising=False)
    resp = admin.post("/admin/reservation/42/redraw", data={"mid": mid})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/admin/m/{mid}")


def test_admin_meetup_and_updates_show_reservations(admin, monkeypatch, seeded_meetup):
    mid, _ = seeded_meetup
    from badminton.meetups import board as orig_board

    def fake_board(conn, meetup_id):
        b = orig_board(conn, meetup_id)
        b["reservations"] = [
            {"id": 9, "no": 1, "teams": [["닉A(남A)", "닉B(여A)"], ["닉C(남B)", "닉D(여B)"]]},
            {"id": 10, "no": 2, "teams": [["닉E(남C)", "닉F(여C)"], ["닉G(남D)", "닉H(여D)"]]},
        ]
        return b

    monkeypatch.setattr("badminton.admin.board", fake_board)

    # 1. 관리자 정모 화면
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    assert 'class="court resv" aria-label="예약 1"' in html
    assert 'class="court resv" aria-label="예약 2"' in html
    assert "닉A(남A)" in html and "닉H(여D)" in html
    assert "/admin/reservation/9/redraw" in html
    assert "다시 뽑기" in html

    # 2. 관리자 3초 갱신 응답
    resp = admin.get(f"/admin/m/{mid}/updates")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "queue" in data
    assert 'aria-label="예약 1"' in data["queue"]
    assert "닉D(여B)" in data["queue"]
    assert "/admin/reservation/9/redraw" in data["queue"]
    assert "다시 뽑기" in data["queue"]
    assert len(data.get("reservations", [])) == 2


def test_rename_requires_login(client, seeded_meetup):
    mid, _ = seeded_meetup
    resp = client.post(f"/admin/m/{mid}/rename", data={"member_id": 1, "nickname": "새이름"})
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_rename_route_success(admin, seeded_meetup, conn_of):
    mid, _ = seeded_meetup
    from badminton.members import create_member
    c = conn_of()
    m_id = create_member(c, "옛닉네임")
    resp = admin.post(f"/admin/m/{mid}/rename", data={"member_id": m_id, "nickname": "  새닉네임  "})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/admin/m/{mid}")
    row = c.execute("SELECT nickname FROM members WHERE id = ?", (m_id,)).fetchone()
    assert row["nickname"] == "새닉네임"
    change = c.execute("SELECT * FROM changes WHERE member_id = ? AND field = 'nickname'", (m_id,)).fetchone()
    assert change is not None
    assert change["old"] == "옛닉네임"
    assert change["new"] == "새닉네임"
    assert change["by"] == "admin"


def test_rename_route_rejects_blank_nickname(admin, seeded_meetup, conn_of):
    mid, _ = seeded_meetup
    from badminton.members import create_member
    c = conn_of()
    m_id = create_member(c, "유지될이름")
    resp = admin.post(f"/admin/m/{mid}/rename", data={"member_id": m_id, "nickname": "   "}, follow_redirects=True)
    assert resp.status_code == 200
    assert "이름을 입력해 주세요" in resp.get_data(as_text=True)
    row = c.execute("SELECT nickname FROM members WHERE id = ?", (m_id,)).fetchone()
    assert row["nickname"] == "유지될이름"


def test_meetup_page_has_no_attendee_list_section(admin, seeded_meetup):
    mid, _ = seeded_meetup
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    # 참석자 목록 섹션은 제거되었어야 함
    assert 'id="meetup-attendee-list"' not in html
    # 게스트 추가 섹션은 유지되어야 함
    assert "게스트 추가" in html
    # 꾹 누르기 메뉴 및 폼 요소들이 존재해야 함
    assert 'id="quick-status-menu"' in html
    assert 'id="quick-info-form"' in html
    assert 'id="quick-rename-form"' in html


def test_excluded_players_have_player_status_attribute(admin, seeded_meetup, conn_of):
    mid, _ = seeded_meetup
    from badminton.members import create_member
    from badminton.meetups import add_attendee
    c = conn_of()
    # 급수 미입력 회원 등록 -> 대진 제외로 들어감
    ex_id = create_member(c, "급수미입력자", gender="M", grade=None)
    add_attendee(c, mid, ex_id, "paste")

    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    assert "대진 제외" in html
    assert f'data-player-status="{ex_id}"' in html


