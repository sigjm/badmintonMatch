import pytest

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


def test_info_edit_invalidates_shared_member_reservations_across_meetups(admin, seeded_meetup, conn_of):
    from badminton.members import create_member
    from badminton.meetups import add_attendee, get_or_create_meetup, today

    meetup_a, _ = seeded_meetup
    conn = conn_of()
    meetup_b = get_or_create_meetup(conn, today(), "다른 정모", None)
    member_id = create_member(conn, "공통 회원", gender="F", grade="B")
    player_ids = [member_id] + [create_member(conn, f"다른-{i}", gender="M", grade="C") for i in range(3)]
    add_attendee(conn, meetup_a, member_id, "paste")
    for player_id in player_ids:
        conn.execute("INSERT INTO attendees(meetup_id, member_id, status, source, queued_at) "
                     "VALUES (?, ?, 'present', 'paste', 0)", (meetup_b, player_id))
    conn.execute("UPDATE meetups SET courts = 1, started = 1 WHERE id = ?", (meetup_b,))
    conn.execute("INSERT INTO reservations(meetup_id, seq, a1, a2, b1, b2, created_at) "
                 "VALUES (?, 1, ?, ?, ?, ?, 0)", (meetup_b, *player_ids))

    admin.post(f"/admin/m/{meetup_a}/info", data={"member_id": member_id, "gender": "", "grade": ""})

    assert conn.execute("SELECT 1 FROM reservations WHERE meetup_id = ?", (meetup_b,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM games WHERE meetup_id = ? AND ended_at IS NULL "
                         "AND (a1 = ? OR a2 = ? OR b1 = ? OR b2 = ?)",
                         (meetup_b, member_id, member_id, member_id, member_id)).fetchone() is None


@pytest.mark.parametrize("origin", ["https://evil.jmllem.com", "https://localhost"])
def test_admin_mutation_rejects_cross_origin_post(admin, conn_of, origin):
    from badminton.db import get_setting

    response = admin.post("/admin/settings", data={"female_offset": "3", "mix_window": "8"},
                          environ_overrides={"HTTP_ORIGIN": origin})

    assert response.status_code == 403
    assert get_setting(conn_of(), "female_offset") == 1.5


def test_admin_mutation_rejects_post_without_origin_or_referer(admin):
    response = admin.post("/admin/logout", environ_overrides={"HTTP_ORIGIN": "", "HTTP_REFERER": ""})

    assert response.status_code == 403


@pytest.mark.parametrize(("admin_password", "secret_key", "missing"), [
    ("", "session-secret", "ADMIN_PASSWORD"),
    ("   ", "session-secret", "ADMIN_PASSWORD"),
    ("admin-secret", None, "SECRET_KEY"),
    ("admin-secret", "  ", "SECRET_KEY"),
])
def test_app_rejects_missing_production_secrets(tmp_path, admin_password, secret_key, missing):
    from badminton import create_app

    with pytest.raises(RuntimeError, match=missing):
        create_app({"DATABASE": str(tmp_path / "app.db"), "ADMIN_PASSWORD": admin_password,
                    "SECRET_KEY": secret_key})


@pytest.mark.parametrize(("path", "data"), [
    ("/m/{mid}/info", {"member_id": "bad", "gender": "F", "grade": "B"}),
    ("/m/{mid}/guest", {"member_id": "bad"}),
    ("/m/{mid}/status", {"status": "present"}),
    ("/m/{mid}/start", {}),
    ("/settings", {}),
])
def test_malformed_admin_form_fields_do_not_raise_500(admin, seeded_meetup, path, data):
    mid, _ = seeded_meetup
    admin.application.config["PROPAGATE_EXCEPTIONS"] = False

    response = admin.post("/admin" + path.format(mid=mid), data=data)

    assert response.status_code < 500


@pytest.mark.parametrize(("path", "data"), [
    ("/m/{mid}/guest", {"member_id": "999999"}),
    ("/members/999999", {"gender": "F", "grade": "B"}),
])
def test_stale_admin_member_ids_do_not_raise_500(admin, seeded_meetup, path, data):
    mid, _ = seeded_meetup
    admin.application.config["PROPAGATE_EXCEPTIONS"] = False

    response = admin.post("/admin" + path.format(mid=mid), data=data)

    assert response.status_code < 500


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


def test_end_and_redraw_routes(admin, seeded_meetup, conn_of):
    mid, gid = seeded_meetup
    c = conn_of()
    assert admin.post(f"/admin/game/{gid}/end").status_code == 302
    assert admin.post(f"/admin/game/{gid}/end").status_code == 302  # 두 번째도 오류 없이 처리
    assert c.execute("SELECT ended_at FROM games WHERE id = ?", (gid,)).fetchone()[0] is not None
    assert [r[0] for r in c.execute("SELECT games FROM attendees WHERE meetup_id = ?", (mid,))] == [1, 1, 1, 1]
    nxt = c.execute("SELECT id FROM games WHERE meetup_id = ? AND ended_at IS NULL", (mid,)).fetchone()[0]
    assert nxt != gid  # 같은 4명이 다시 코트를 채움
    assert admin.post(f"/admin/game/{nxt}/redraw").status_code == 302
    assert c.execute("SELECT canceled FROM games WHERE id = ?", (nxt,)).fetchone()[0] == 1
    assert c.execute("SELECT count(*) FROM games WHERE meetup_id = ? AND ended_at IS NULL", (mid,)).fetchone()[0] == 1
    assert [r[0] for r in c.execute("SELECT games FROM attendees WHERE meetup_id = ?", (mid,))] == [1, 1, 1, 1]


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


def test_members_page_lists_changes(admin, seeded_meetup, conn_of):
    from badminton.meetups import add_attendee
    from badminton.members import create_member, set_info
    m = create_member(conn_of(), "가나다")
    add_attendee(conn_of(), seeded_meetup[0], m, "paste")  # 회원 화면은 이 모임 정모에 온 사람만
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


def test_change_log_shows_time_and_labels(admin, seeded_meetup, conn_of):
    import re
    from badminton.meetups import add_attendee
    from badminton.members import create_member, set_info
    m = create_member(conn_of(), "가나다")
    add_attendee(conn_of(), seeded_meetup[0], m, "paste")
    set_info(conn_of(), m, "F", "N", by="self")
    html = admin.get("/admin/members").get_data(as_text=True)
    assert "초심" in html and "없음 → 여" in html and re.search(r"\d\d-\d\d \d\d:\d\d", html)


def test_status_select_hides_canceled_unless_current(admin, seeded_meetup):
    mid, _ = seeded_meetup
    assert 'value="canceled"' not in admin.get(f"/admin/m/{mid}").get_data(as_text=True)


def test_session_cookie_flags(tmp_path):
    from badminton import create_app
    app = create_app({"DATABASE": str(tmp_path / "c.db"), "ADMIN_PASSWORD": "pw", "SECRET_KEY": "t",
                      "TESTING": True})
    cookie = app.test_client().post("/admin/login", data={"password": "pw"}).headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Lax" in cookie


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
    resp = client.post(f"/admin/m/{mid}/manual")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_admin_manual_match_routes(admin, seeded_meetup, conn_of, monkeypatch):
    from badminton import meetups
    monkeypatch.setattr(meetups, "COURT_REST_SECONDS", 15)
    mid, gid = seeded_meetup
    c = conn_of()
    # 1. 선수 미선택 실패 플래시
    resp = admin.post(f"/admin/m/{mid}/manual", data={}, follow_redirects=True)
    assert "선수 네 명을 모두 선택해 주세요" in resp.get_data(as_text=True)

    # 2. 추가 선수 등록 후 수동 예약(/manual) 성공
    from badminton.members import create_member
    from badminton.meetups import add_attendee
    p1 = create_member(c, "수동1", gender="M", grade="A")
    p2 = create_member(c, "수동2", gender="M", grade="B")
    p3 = create_member(c, "수동3", gender="F", grade="A")
    p4 = create_member(c, "수동4", gender="F", grade="B")
    p5 = create_member(c, "수동5", gender="M", grade="C")
    for p in (p1, p2, p3, p4, p5):
        add_attendee(c, mid, p, "paste")

    resp2 = admin.post(f"/admin/m/{mid}/manual", data={"a1": p1, "a2": p2, "b1": p3, "b2": p4}, follow_redirects=True)
    assert "수동 예약을 추가했습니다" in resp2.get_data(as_text=True)
    resv = c.execute("SELECT * FROM reservations WHERE meetup_id = ? AND a1 = ?", (mid, p1)).fetchone()
    assert resv is not None and resv["source"] == "manual"
    rid = resv["id"]

    # 3. 예약 수정 라우트 (/admin/reservation/<rid>/edit)
    resp3 = admin.post(f"/admin/reservation/{rid}/edit", data={"a1": p1, "a2": p2, "b1": p3, "b2": p5}, follow_redirects=True)
    assert "예약을 수정했습니다" in resp3.get_data(as_text=True)
    resv_edited = c.execute("SELECT * FROM reservations WHERE id = ?", (rid,)).fetchone()
    assert (resv_edited["a1"], resv_edited["a2"], resv_edited["b1"], resv_edited["b2"]) == (p1, p2, p3, p5)

    # 4. 우선순위 라우트 (/admin/reservation/<rid>/priority)
    resp4 = admin.post(f"/admin/reservation/{rid}/priority", follow_redirects=True)
    assert "예약을 다음 순서로 이동했습니다" in resp4.get_data(as_text=True)
    resv_prio = c.execute("SELECT seq FROM reservations WHERE id = ?", (rid,)).fetchone()
    assert resv_prio["seq"] == 1

    # 5. 즉시 시작 라우트 (/admin/reservation/<rid>/start-now) - 코트 1 경기를 종료 후 즉시 시작
    admin.post(f"/admin/game/{gid}/end")
    resp5 = admin.post(f"/admin/reservation/{rid}/start-now", follow_redirects=True)
    assert "예약 경기를 1코트에서 바로 시작했습니다" in resp5.get_data(as_text=True)
    g = c.execute("SELECT court, a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()
    assert g is not None and (g["a1"], g["a2"], g["b1"], g["b2"]) == (p1, p2, p3, p5)
    assert c.execute("SELECT 1 FROM reservations WHERE id = ?", (rid,)).fetchone() is None


def test_redraw_reservation_requires_login(client):
    resp = client.post("/admin/reservation/1/redraw")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_redraw_reservation_route_replaces_reservation(admin, seeded_meetup, conn_of):
    from badminton.meetups import add_attendee, refill_reservations, reservations
    from badminton.members import create_member
    mid, _ = seeded_meetup
    c = conn_of()
    for i in range(9):
        add_attendee(c, mid, create_member(c, f"r{i}", gender="M", grade="C"), "paste")
    refill_reservations(c, mid)
    (old,) = reservations(c, mid)
    resp = admin.post(f"/admin/reservation/{old['id']}/redraw")
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/admin/m/{mid}")
    (new,) = reservations(c, mid)
    four = lambda r: {r["a1"], r["a2"], r["b1"], r["b2"]}
    assert four(new) != four(old) and new["seq"] == 1


def test_redraw_unknown_reservation_falls_back_to_form_mid(admin, seeded_meetup):
    mid, _ = seeded_meetup
    resp = admin.post("/admin/reservation/999/redraw", data={"mid": mid})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/admin/m/{mid}")


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
    assert 'aria-label="예약 1, 길게 누르거나' in html
    assert 'aria-label="예약 2, 길게 누르거나' in html
    assert "닉A(남A)" in html and "닉H(여D)" in html
    assert "/admin/reservation/9/redraw" in html
    assert "다시 뽑기" in html

    # 2. 관리자 3초 갱신 응답
    resp = admin.get(f"/admin/m/{mid}/updates")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "queue" in data
    assert 'aria-label="예약 1, 길게 누르거나' in data["reservation_list"]
    assert "닉D(여B)" in data["reservation_list"]
    assert "/admin/reservation/9/redraw" in data["reservation_list"]
    assert "다시 뽑기" in data["reservation_list"]
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




def test_court_shows_game_time_and_player_cards_have_actions(admin, client, seeded_meetup, conn_of):
    mid, _ = seeded_meetup
    from badminton.members import create_member
    from badminton.meetups import add_attendee
    c = conn_of()
    waiting = create_member(c, "대기자", gender="M", grade="C")
    add_attendee(c, mid, waiting, "paste")
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    assert "경기 시간" in html
    assert 'class="pcard is-queued' in html
    # 대기 1 태그 및 카드 안 버튼 삭제 확인
    assert "대기 1" not in html
    assert "data-open-info" not in html and 'class="pcard-actions"' not in html
    # 꾹 누르기 관리 팝업 속성 및 안내 문구 확인
    assert f'data-player-status="{waiting}"' in html
    assert 'tabindex="0"' in html
    assert "길게 누르거나 Enter 또는 Space로 관리 팝업 열기" in html
    assert "카드를 길게 눌러 관리 팝업 열기" in html
    assert 'id="quick-status-menu"' in html
    assert 'data-set-player-status="away"' in html and 'data-set-player-status="gone"' in html
    public = client.get("/board").get_data(as_text=True)
    assert "경기 시간" in public and 'class="pcard is-queued' in public
    assert f'data-player-status="{waiting}"' not in public and 'tabindex="0"' not in public
    assert 'id="quick-status-menu"' not in public
    assert 'name="status"' not in public and ">정보</button>" not in public


def test_updates_fill_court_after_rest_and_auto_match_forces(admin, client, seeded_meetup, conn_of, monkeypatch):
    from badminton import meetups
    from badminton.members import create_member
    monkeypatch.setattr(meetups, "COURT_REST_SECONDS", 15)
    mid, gid = seeded_meetup
    c = conn_of()
    for i in range(4):
        meetups.add_attendee(c, mid, create_member(c, f"w{i}", gender="M", grade="C"), "paste")
    admin.post(f"/admin/game/{gid}/end")
    data = admin.get(f"/admin/m/{mid}/updates").get_json()
    assert data["active_courts"] == 0 and "초 후" in data["court_board"]
    assert 'data-court-rest' in data["court_board"] and 'data-ready-in=' in data["court_board"]
    assert 'data-ready-until=' not in data["court_board"]
    assert '<span class="versus">VS</span>' not in data["court_board"]
    assert '<span class="versus">VS</span>' not in data["reservation_list"]
    c.execute("UPDATE games SET ended_at = ended_at - 16 WHERE id = ?", (gid,))
    assert client.get("/board/updates").get_json()["active_courts"] == 1  # 공개 갱신도 채운다
    nxt = c.execute("SELECT id FROM games WHERE ended_at IS NULL").fetchone()[0]
    admin.post(f"/admin/game/{nxt}/end")
    admin.post(f"/admin/m/{mid}/auto-match")
    assert admin.get(f"/admin/m/{mid}/updates").get_json()["active_courts"] == 1


def test_memo_route_saves_and_card_shows_memo(admin, seeded_meetup, conn_of):
    mid, _ = seeded_meetup
    from badminton.members import create_member
    from badminton.meetups import add_attendee
    c = conn_of()
    waiting = create_member(c, "메모대상", gender="M", grade="C")
    add_attendee(c, mid, waiting, "paste")
    admin.post(f"/admin/m/{mid}/memo", data={"member_id": waiting, "memo": "콕 가볼"})
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    assert "메모 · 콕 가볼" in html and 'data-memo="콕 가볼"' in html and 'id="quick-memo-form"' in html
    resp = admin.post(f"/admin/m/{mid}/memo", data={"member_id": waiting, "memo": "가" * 101}, follow_redirects=True)
    assert "100자" in resp.get_data(as_text=True)


def test_excluded_section_is_separate_live_fragment(admin, seeded_meetup, conn_of):
    mid, _ = seeded_meetup
    from badminton.members import create_member
    from badminton.meetups import add_attendee
    c = conn_of()
    add_attendee(c, mid, create_member(c, "급수없음", gender="M", grade=None), "paste")
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    assert 'id="meetup-excluded"' in html and 'class="board-layout"' in html
    data = admin.get(f"/admin/m/{mid}/updates").get_json()
    assert "급수없음" in data["excluded"] and "급수없음" not in data["queue"]


def test_admin_meetup_section_order(admin, seeded_meetup):
    mid, _ = seeded_meetup
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    assert "<span>정모 운영</span>" not in html and f'action="/admin/m/{mid}/start"' not in html
    order = [html.index(x) for x in ("운영 콘솔", 'id="meetup-court-board"',
                                     'id="manual-match-picker"', 'id="meetup-reservations"', 'id="meetup-queue"',
                                     'id="meetup-excluded"', "<span>게스트 추가</span>")]
    assert order == sorted(order)
    data = admin.get(f"/admin/m/{mid}/updates").get_json()
    assert "예약" in data["reservation_list"] and "다음 순서" in data["queue"] and "예약 1" not in data["queue"]


def test_manual_draft_card_and_fill_first_empty_court(admin, seeded_meetup, conn_of, monkeypatch):
    from badminton import meetups
    monkeypatch.setattr(meetups, "COURT_REST_SECONDS", 15)
    mid, gid = seeded_meetup
    c = conn_of()
    admin.post(f"/admin/game/{gid}/end")  # 코트 1이 15초 쉬는 중 → 빈 코트
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    assert "data-manual-draft" in html and html.count("data-draft-slot=") == 4 and "data-manual-fill" in html
    ids = [r[0] for r in c.execute("SELECT member_id FROM attendees WHERE meetup_id = ? ORDER BY member_id", (mid,))]
    resp = admin.post(f"/admin/m/{mid}/manual", data=dict(zip(("a1", "a2", "b1", "b2"), ids)), follow_redirects=True)
    assert "수동 예약을 추가했습니다" in resp.get_data(as_text=True)
    resv = c.execute("SELECT id, a1, a2, b1, b2, source FROM reservations WHERE meetup_id = ?", (mid,)).fetchone()
    assert resv is not None and resv["source"] == "manual" and [resv["a1"], resv["a2"], resv["b1"], resv["b2"]] == ids
    again = admin.post(f"/admin/m/{mid}/manual", data=dict(zip(("a1", "a2", "b1", "b2"), ids)), follow_redirects=True)
    assert "이미 예약된 선수는 다시 선택할 수 없습니다" in again.get_data(as_text=True)
    start_resp = admin.post(f"/admin/reservation/{resv['id']}/start-now", follow_redirects=True)
    assert "예약 경기를 1코트에서 바로 시작했습니다" in start_resp.get_data(as_text=True)
    g = c.execute("SELECT court, a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND ended_at IS NULL", (mid,)).fetchone()
    assert g["court"] == 1 and [g["a1"], g["a2"], g["b1"], g["b2"]] == ids
    assert c.execute("SELECT 1 FROM reservations WHERE id = ?", (resv["id"],)).fetchone() is None


def test_guest_and_court_count_forms_save_without_full_reload(admin, seeded_meetup):
    mid, _ = seeded_meetup
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    guests = html[html.index('id="meetup-guests"'):]
    guests = guests[:guests.index("</section>")]
    assert guests.count("data-save-without-reload") == guests.count("<form") >= 1
    assert 'data-reset-on-save="추가했습니다"' in guests
    resp = admin.post(f"/admin/m/{mid}/guest", data={"nickname": "새게스트", "gender": "F", "grade": "C"}, follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert "새게스트 님을 게스트로 추가했습니다" in body and 'data-nickname="새게스트"' in body


def test_settings_lists_operation_controls_for_active_meetups_of_current_group(admin, seeded_meetup, conn_of):
    from badminton.groups import add_group, list_groups
    from badminton.meetups import archive_meetup, get_or_create_meetup, today
    started, _ = seeded_meetup  # eid 없음, 시작함
    c = conn_of()
    group_id = list_groups(c)[0]["id"]
    idle = get_or_create_meetup(c, today(), "시작 전 정모", "e-idle", group_id=group_id)  # eid 있음, 시작 전
    archived = get_or_create_meetup(c, today(), "보관된 정모", None, group_id=group_id)
    archive_meetup(c, archived)
    other = get_or_create_meetup(c, today(), "다른 모임 정모", None, group_id=add_group(c, "other-gid", "다른 모임"))

    html = admin.get("/admin/settings").get_data(as_text=True)

    assert "<span>정모 운영</span>" in html
    assert f'id="meetup-{started}"' in html and f'id="meetup-{idle}"' in html
    assert f'id="meetup-{archived}"' not in html and f'id="meetup-{other}"' not in html
    for mid in (started, idle):
        assert f'href="/admin/m/{mid}"' in html
        for route in ("start", "archive"):
            assert f'action="/admin/m/{mid}/{route}"' in html
    assert f'action="/admin/m/{started}/auto-match"' in html and f'action="/admin/m/{idle}/auto-match"' not in html
    assert "코트 수 변경" in html and "빈 코트 자동 매칭" in html and "코트 시작" in html  # 시작한 정모와 시작 전 정모
    assert f'action="/admin/m/{idle}/import"' in html and f'action="/admin/m/{started}/import"' not in html
    operation = html[html.index("<span>정모 운영</span>"):html.index("<span>매칭 설정</span>")]
    assert "data-save-without-reload" not in operation  # 눌러서 정모 화면으로 이동하는 게 의도
    assert "매칭 설정" in html and 'name="female_offset"' in html


def test_settings_shows_empty_state_without_active_meetups(admin, seeded_meetup):
    mid, _ = seeded_meetup
    admin.post(f"/admin/m/{mid}/archive")

    html = admin.get("/admin/settings").get_data(as_text=True)

    assert "운영 중인 정모가 없습니다" in html and f'id="meetup-{mid}"' not in html


def test_meetup_page_links_to_its_block_in_settings(admin, seeded_meetup):
    mid, _ = seeded_meetup
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    assert f'href="/admin/settings#meetup-{mid}"' in html and "코트·정모 설정" in html


def test_start_from_settings_redirects_to_meetup_and_changes_courts(admin, seeded_meetup, conn_of):
    mid, _ = seeded_meetup
    resp = admin.post(f"/admin/m/{mid}/start", data={"courts": "3"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/admin/m/{mid}")
    assert conn_of().execute("SELECT courts FROM meetups WHERE id = ?", (mid,)).fetchone()[0] == 3


def _tag(html, needle):
    """needle을 포함한 여는 태그 하나를 돌려준다."""
    start = html.rindex("<", 0, html.index(needle))
    return html[start:html.index(">", start) + 1]


def test_player_menu_is_a_dialog_not_a_menu(admin, seeded_meetup):
    mid, _ = seeded_meetup
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    menu = _tag(html, 'id="quick-status-menu"')
    assert 'role="dialog"' in menu and 'aria-label="참석자 정보 및 상태 변경"' in menu
    assert 'role="menu"' not in html


def test_admin_home_and_groups_inputs_have_accessible_names(admin):
    import re
    assert 'aria-label="모임 이름"' in _tag(admin.get("/admin/groups").get_data(as_text=True), 'name="q"')
    # 이름이 비면 오류 화면이 붙여넣기 폼까지 보여 준다
    html = admin.post("/admin/meetups", data={"date": "2026-10-22", "name": ""}).get_data(as_text=True)
    assert "붙여넣기 등록" in html
    dates = re.findall(r'<input[^>]*type="date"[^>]*>', html)
    assert [re.search(r'aria-label="([^"]*)"', tag).group(1) for tag in dates] == ["불러올 날짜", "정모 날짜", "정모 날짜"]
    names = re.findall(r'<input[^>]*name="name"[^>]*>', html)
    assert [re.search(r'aria-label="([^"]*)"', tag).group(1) for tag in names] == ["정모 이름", "정모 이름"]
    assert 'aria-label="참석자 닉네임"' in _tag(html, 'name="text"')


def test_meetup_page_inputs_have_accessible_names(admin, seeded_meetup):
    import re
    mid, _ = seeded_meetup
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    nicknames = re.findall(r'<input[^>]*name="nickname"[^>]*>', html)  # 게스트 추가, 이름 변경 순서
    assert [re.search(r'aria-label="([^"]*)"', tag).group(1) for tag in nicknames] == ["게스트 이름", "새 이름"]
    assert 'aria-label="메모"' in _tag(html, "data-memo-text")


@pytest.mark.parametrize("path", ["/admin", "/admin/members"])
def test_group_switcher_is_navigation_not_tabs(admin, conn_of, path):
    import re
    from badminton.groups import add_group
    add_group(conn_of(), "other-gid", "다른 모임")
    html = admin.get(path).get_data(as_text=True)
    nav = html[html.index('<nav class="tabs" aria-label="모임 선택">'):]
    nav = nav[:nav.index("</nav>")]
    assert 'role="tab' not in nav and "aria-selected" not in nav  # base.html 스크립트에도 같은 문구가 있어 nav 안만 본다
    buttons = re.findall(r"<button[^>]*>", nav)
    assert len(buttons) == 2
    current = [button for button in buttons if 'aria-current="true"' in button]
    assert len(current) == 1 and 'class="on"' in current[0]


def test_delete_reservation_route(app, admin, seeded_meetup, conn_of):
    from badminton.meetups import add_attendee, refill_reservations, reservations
    from badminton.members import create_member
    mid, _ = seeded_meetup
    c = conn_of()
    for i in range(9):
        add_attendee(c, mid, create_member(c, f"d{i}", gender="M", grade="C"), "paste")
    refill_reservations(c, mid)
    (r,) = reservations(c, mid)
    assert app.test_client().post(f"/admin/reservation/{r['id']}/delete").status_code == 302  # 로그인 안 하면 로그인 화면으로
    assert len(reservations(c, mid)) == 1
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    assert f"/admin/reservation/{r['id']}/delete" in html
    resp = admin.post(f"/admin/reservation/{r['id']}/delete", follow_redirects=True)
    assert "예약 1을 삭제했습니다" in resp.get_data(as_text=True)
    assert reservations(c, mid) == []


def test_members_page_and_saved_guests_follow_selected_group(admin, seeded_meetup, conn_of):
    from badminton.groups import add_group
    from badminton.meetups import add_attendee, get_or_create_meetup
    from badminton.members import create_member
    mid, _ = seeded_meetup
    c = conn_of()
    other = add_group(c, "c" * 36, "다른모임")
    other_meetup = get_or_create_meetup(c, "2026-10-30", "다른정모", None, group_id=other)
    add_attendee(c, other_meetup, create_member(c, "다른모임회원"), "paste")
    add_attendee(c, other_meetup, create_member(c, "다른모임손님", is_guest=True, gender="F", grade="C"), "guest")
    html = admin.get("/admin/members").get_data(as_text=True)
    assert "m1" in html and "다른모임회원" not in html and "다른모임" in html  # 탭에는 다른 모임도 보인다
    assert "다른모임손님" not in admin.get(f"/admin/m/{mid}").get_data(as_text=True)  # 저장된 게스트도 이 모임 것만
    resp = admin.post("/admin/groups/select", data={"group_id": other, "back": "members"})
    assert resp.headers["Location"].endswith("/admin/members")
    html = admin.get("/admin/members").get_data(as_text=True)
    assert "다른모임회원" in html and 'data-player-name="m1"' not in html


def _pcard(html, member_id):
    """대기 카드(.pcard) 하나의 마크업을 돌려준다 (카드의 닫는 </div>는 줄 맨 앞에 있다)."""
    start = html.index(f'data-key="p-{member_id}"')
    return html[start:html.index("\n</div>", start)]


def test_queue_card_is_a_chip_with_a_select_button_for_candidates(admin, seeded_meetup, conn_of):
    from badminton.meetups import add_attendee
    from badminton.members import create_member
    mid, _ = seeded_meetup
    c = conn_of()
    waiting = create_member(c, "대기자", gender="M", grade="C")
    add_attendee(c, mid, waiting, "paste")

    card = _pcard(admin.get(f"/admin/m/{mid}").get_data(as_text=True), waiting)

    button = _tag(card, "data-manual-player=")
    assert button.startswith("<button") and 'class="court-player queue-player-select"' in button
    assert f'data-manual-player="{waiting}"' in button and 'data-grade-label="C"' in button
    assert 'data-nickname="대기자"' in button and 'data-gender="M"' in button
    assert "player-chip is-male" in card and "C/대기자" in card
    assert "grade-badge" not in card and "is-gender-" not in card  # 급수는 칩 안에, 카드 바탕은 흰색
    assert "data-queue-wait" in card and "data-wait-value" in card  # 대기 시간 갱신용 표시는 그대로


def test_playing_tab_cards_are_selectable_chips(admin, seeded_meetup, conn_of):
    mid, _ = seeded_meetup
    member = conn_of().execute("SELECT id FROM members WHERE nickname = 'm1'").fetchone()[0]

    card = _pcard(admin.get(f"/admin/m/{mid}").get_data(as_text=True), member)

    assert f'data-manual-player="{member}"' in card and 'data-grade-label="B"' in card
    assert "player-chip is-male" in card and "B/m1" in card


def test_excluded_card_is_a_chip_without_select_button(admin, seeded_meetup, conn_of):
    from badminton.meetups import add_attendee, set_status
    from badminton.members import create_member
    mid, _ = seeded_meetup
    c = conn_of()
    away = create_member(c, "쉬는사람", gender="F", grade="A")
    add_attendee(c, mid, away, "paste")
    set_status(c, mid, away, "gone")

    card = _pcard(admin.get(f"/admin/m/{mid}").get_data(as_text=True), away)

    assert "data-manual-player" not in card and "queue-player-select" not in card
    assert "player-chip is-female" in card and "A/쉬는사람" in card and "grade-badge" not in card


def test_public_board_queue_cards_are_chips_without_select_button(client, seeded_meetup, conn_of):
    from badminton.meetups import add_attendee
    from badminton.members import create_member
    mid, _ = seeded_meetup
    c = conn_of()
    waiting = create_member(c, "대기자", gender="F", grade="D")
    add_attendee(c, mid, waiting, "paste")

    card = _pcard(client.get("/board").get_data(as_text=True), waiting)

    assert "player-chip is-female" in card and "D/대기자" in card
    assert "data-manual-player" not in card and "<button" not in card and "grade-badge" not in card


def test_manual_match_guides_say_tap_the_card(admin, seeded_meetup, conn_of):
    from badminton.groups import list_groups
    from badminton.meetups import add_attendee, get_or_create_meetup, today
    from badminton.members import create_member
    mid, _ = seeded_meetup
    c = conn_of()
    for nick in ("w1", "w2", "w3", "w4"):
        add_attendee(c, mid, create_member(c, nick, gender="M", grade="C"), "paste")
    idle = get_or_create_meetup(c, today(), "시작 전 정모", "e-idle", group_id=list_groups(c)[0]["id"])

    started = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    unstarted = admin.get(f"/admin/m/{idle}").get_data(as_text=True)

    assert "대기 또는 게임 중 탭에서 카드를 눌러 4명을 고르세요." in started
    assert "다음 순서 카드를 눌러 수동 대진을 짤 수 있습니다." in unstarted
    assert "이름을 눌러" not in started + unstarted


def _selection_bar(html):
    """수동 대진 선택 바 마크업 (바로 뒤에 오는 관리 팝업 앞까지)."""
    start = html.index('id="manual-selection-bar"')
    return html[start:html.index('id="quick-status-menu"', start)]


def test_started_meetup_has_hidden_selection_bar_reusing_form_buttons(admin, seeded_meetup):
    mid, _ = seeded_meetup
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)

    bar = _selection_bar(html)
    assert " hidden" in _tag(html, 'id="manual-selection-bar"')  # 0명이면 숨김
    assert 'role="region"' in _tag(html, 'id="manual-selection-bar"')
    assert 'aria-label="수동 대진 선택"' in _tag(html, 'id="manual-selection-bar"')
    assert 'aria-live="polite"' in bar and "0/4명" in bar
    # 새 처리 코드 없이 기존 폼 버튼과 같은 훅을 쓴다
    assert 'form="manual-match-form"' in _tag(bar, "data-manual-fill") and 'type="submit"' in _tag(bar, "data-manual-fill")
    assert "data-manual-reset" in bar and "예약에 추가" in bar and "초기화" in bar
    # 3초마다 갈아 끼우는 #meetup-* 구역 밖(맨 아래 게스트 추가 구역 뒤)에 있다
    assert html.index('id="manual-selection-bar"') > html.index('id="meetup-guests"')


def test_selection_bar_is_only_drawn_for_manual_match_form(admin, client, seeded_meetup, conn_of):
    from badminton.groups import list_groups
    from badminton.meetups import get_or_create_meetup, today
    mid, _ = seeded_meetup
    c = conn_of()
    idle = get_or_create_meetup(c, today(), "시작 전 정모", "e-idle", group_id=list_groups(c)[0]["id"])

    assert 'id="manual-selection-bar"' not in admin.get(f"/admin/m/{idle}").get_data(as_text=True)  # 폼이 없는 시작 전 정모
    assert 'id="manual-selection-bar"' not in client.get("/board").get_data(as_text=True)  # 공개 대진판
