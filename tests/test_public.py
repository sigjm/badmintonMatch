from badminton.members import changes, check_pin, create_member, set_pin


def test_root_redirects_to_board(client):
    assert client.get("/").headers["Location"].endswith("/board")


def test_board_without_meetup(client):
    assert "진행 중인 정모가 없습니다" in client.get("/board").get_data(as_text=True)


def test_board_shows_started_meetup(client, seeded_meetup):
    html = client.get("/board").get_data(as_text=True)
    assert "코트 1" in html and "m1" in html and "경기 끝" not in html


def test_me_first_time_sets_pin_and_info(client, conn_of):
    m = create_member(conn_of(), "가")
    client.post("/me", data={"member_id": m, "pin": "1234", "gender": "F", "grade": "C"})
    row = conn_of().execute("SELECT gender, grade FROM members WHERE id = ?", (m,)).fetchone()
    assert tuple(row) == ("F", "C") and check_pin(conn_of(), m, "1234")


def test_me_wrong_or_bad_pin_changes_nothing(client, conn_of):  # Review Focus 5
    m = create_member(conn_of(), "가", gender="M", grade="B")
    set_pin(conn_of(), m, "1234")
    for pin, msg in (("0000", "PIN이 맞지 않습니다"), ("12a4", "PIN은 숫자 4자리")):
        html = client.post("/me", data={"member_id": m, "pin": pin, "gender": "M", "grade": "S"}).get_data(as_text=True)
        assert msg in html
    assert conn_of().execute("SELECT grade FROM members WHERE id = ?", (m,)).fetchone()[0] == "B"
    assert changes(conn_of()) == []


def test_me_first_time_bad_pin_rejected(client, conn_of):
    m = create_member(conn_of(), "가")
    html = client.post("/me", data={"member_id": m, "pin": "12", "gender": "F", "grade": "C"}).get_data(as_text=True)
    assert "PIN은 숫자 4자리" in html
    assert conn_of().execute("SELECT grade FROM members WHERE id = ?", (m,)).fetchone()[0] is None


def test_me_list_hides_guests_and_marks_duplicates(client, conn_of):
    c = conn_of()
    a, b = create_member(c, "철수"), create_member(c, "철수")
    create_member(c, "손님", is_guest=True)
    html = client.get("/me").get_data(as_text=True)
    assert f"철수 (#{a})" in html and f"철수 (#{b})" in html and "손님" not in html
