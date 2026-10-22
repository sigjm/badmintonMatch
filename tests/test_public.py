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


def test_board_group_links_mark_the_current_group(client, conn_of):
    import re
    from badminton.groups import add_group, list_groups
    c = conn_of()
    first = list_groups(c)[0]["id"]
    other = add_group(c, "d49e0b02-b22a-413b-8295-53dc895bc4411", "Team ONAM")
    html = client.get(f"/board?g={other}").get_data(as_text=True)
    nav = html[html.index('<nav class="tabs" aria-label="모임 선택">'):]
    links = re.findall(r"<a [^>]*>", nav[:nav.index("</nav>")])
    current = [link for link in links if 'aria-current="page"' in link]
    assert len(links) == 2 and len(current) == 1
    assert f'href="/board?g={other}"' in current[0] and 'class="on"' in current[0]
    assert f'href="/board?g={first}"' in next(link for link in links if link not in current)


def test_board_and_updates_show_reservations(client, monkeypatch, seeded_meetup):
    mid, _ = seeded_meetup
    from badminton.meetups import board as orig_board

    def fake_board(conn, meetup_id):
        b = orig_board(conn, meetup_id)
        b["reservations"] = [
            {"id": 9, "no": 1, "teams": [["닉A(남A)", "닉B(여A)"], ["닉C(남B)", "닉D(여B)"]]},
        ]
        return b

    monkeypatch.setattr("badminton.public.board", fake_board)

    # 1. 공개 대진판 화면
    html = client.get("/board").get_data(as_text=True)
    assert 'aria-label="예약 1"' in html
    assert "닉A(남A)" in html and "닉D(여B)" in html
    assert "/admin/reservation" not in html
    assert "다시 뽑기" not in html  # 공개 대진판에는 다시 뽑기 버튼이 없어야 함

    # 2. 공개 3초 갱신 응답
    resp = client.get("/board/updates")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "queue" in data
    assert 'aria-label="예약 1"' in data["reservation_list"]
    assert "/admin/reservation" not in data["reservation_list"]
    assert "다시 뽑기" not in data["reservation_list"]
    assert len(data.get("reservations", [])) == 1


def test_board_with_dict_players_in_reservations(client, monkeypatch, seeded_meetup):
    mid, _ = seeded_meetup
    from badminton.meetups import board as orig_board

    def fake_board(conn, meetup_id):
        b = orig_board(conn, meetup_id)
        b["reservations"] = [
            {
                "id": 1,
                "no": 1,
                "teams": [
                    [{"nickname": "홍길동", "gender": "M", "grade": "A"}, {"nickname": "김영희", "gender": "F", "grade": "B"}],
                    [{"nickname": "이민수", "gender": "M", "grade": "C"}, {"nickname": "박수진", "gender": "F", "grade": "D"}],
                ],
            }
        ]
        return b

    monkeypatch.setattr("badminton.public.board", fake_board)
    html = client.get("/board").get_data(as_text=True)
    assert 'aria-label="예약 1"' in html
    assert "A/홍길동" in html and "D/박수진" in html


def test_board_without_reservations_key(client, monkeypatch, seeded_meetup):
    mid, _ = seeded_meetup
    from badminton.meetups import board as orig_board

    def fake_board_no_resv(conn, meetup_id):
        b = orig_board(conn, meetup_id)
        b.pop("reservations", None)
        return b

    monkeypatch.setattr("badminton.public.board", fake_board_no_resv)
    resp = client.get("/board")
    assert resp.status_code == 200
    resp_up = client.get("/board/updates")
    assert resp_up.status_code == 200


def test_base_script_handles_scroll_and_menu_in_updates(client):
    html = client.get("/board").get_data(as_text=True)
    assert "pointermove" in html
    assert "isStatusMenuOpen" in html


