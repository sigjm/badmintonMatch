from badminton.members import changes, check_pin, create_member, set_pin


def test_root_redirects_to_board(client):
    assert client.get("/").headers["Location"].endswith("/board")


def test_board_without_meetup(client):
    assert "진행 중인 정모가 없습니다" in client.get("/board").get_data(as_text=True)


def test_board_shows_started_meetup(client, seeded_meetup):
    html = client.get("/board").get_data(as_text=True)
    assert "코트 1" in html and "m1" in html and "경기 끝" not in html


def test_me_is_read_only_notice(client):
    html = client.get("/me").get_data(as_text=True)
    assert "회원 정보는 운영진이 관리합니다" in html
    assert "대진판 보기" in html


def test_me_post_is_disallowed(client):
    resp = client.post("/me", data={"member_id": 1, "gender": "F", "grade": "C"})
    assert resp.status_code == 405


def test_board_updates_endpoint(client, seeded_meetup):
    # 로그인 없는 일반 공개 사용자가 실시간 폴링 가능
    resp = client.get("/board/updates")
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "no-store"
    data = resp.get_json()
    assert set(data.keys()) >= {"meetup_id", "court_board", "queue", "active_courts", "queue_count"}
    mid, _ = seeded_meetup
    assert data["meetup_id"] == mid
    assert data["active_courts"] == 1
    assert isinstance(data["court_board"], str)
    assert isinstance(data["queue"], str)


def test_board_updates_endpoint_without_meetup(client):
    # 정모가 없는 상태에서도 에러 없이 200과 기본 JSON 반환
    resp = client.get("/board/updates")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.keys()) >= {"meetup_id", "court_board", "queue", "active_courts", "queue_count"}
    assert data["meetup_id"] is None
    assert data["active_courts"] == 0


def test_board_switches_group_by_query(client, conn_of):
    from badminton.groups import add_group, list_groups
    from badminton.meetups import get_or_create_meetup, start, today
    c = conn_of()
    other = add_group(c, "d49e0b02-b22a-413b-8295-53dc895bc4411", "Team ONAM")
    mid = get_or_create_meetup(c, today(), "오남 정모", None, group_id=other)
    start(c, mid, 1)
    html = client.get(f"/board?g={other}").get_data(as_text=True)
    assert "오남 정모" in html and "금요일 야간민턴" in html  # 탭에 다른 모임도 보인다
    first = list_groups(c)[0]["id"]
    assert "진행 중인 정모가 없습니다" in client.get(f"/board?g={first}").get_data(as_text=True)
