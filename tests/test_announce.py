from badminton import announce
from badminton.announce import announcement_text


def test_announcement_text_reads_court_and_names():
    assert announcement_text(1, ["김민재", "이정진", "박지원", "김승용"]) == "1코트, 김민재, 이정진, 박지원, 김승용 님 들어가세요."
    assert announcement_text(3, ["Tom★", "😀", "a b", "최"]) == "3코트, Tom, 선수, a b, 최 님 들어가세요."


def test_announce_route_returns_wav_for_game(client, seeded_meetup, monkeypatch):
    mid, gid = seeded_meetup
    said = []
    monkeypatch.setattr(announce, "speak", lambda text: said.append(text) or b"ID3mp3")
    resp = client.get(f"/announce/{gid}.mp3")
    assert resp.status_code == 200 and resp.mimetype == "audio/mpeg" and resp.data == b"ID3mp3"
    assert said == [announcement_text(1, ["m1", "m2", "f1", "f2"])] or said[0].startswith("1코트, ")
    assert client.get("/announce/99999.mp3").status_code == 404


def test_announce_route_503_when_tts_unavailable(client, seeded_meetup, monkeypatch):
    mid, gid = seeded_meetup
    def broken(text):
        raise RuntimeError("model missing")
    monkeypatch.setattr(announce, "speak", broken)
    assert client.get(f"/announce/{gid}.mp3").status_code == 503
    assert client.get("/announce/hello.mp3").status_code == 503


def test_court_cards_carry_game_and_announcement(admin, client, seeded_meetup):
    mid, gid = seeded_meetup
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    assert f'data-game-id="{gid}"' in html and 'data-announce="1코트, ' in html
    assert "data-announce-toggle" in html and "data-announce-replay" in html
    public = client.get("/board").get_data(as_text=True)
    assert f'data-game-id="{gid}"' in public and "data-announce-toggle" in public


def test_announce_runs_outside_the_one_request_at_a_time_lock(app, client, seeded_meetup, monkeypatch):
    mid, gid = seeded_meetup
    monkeypatch.setattr(announce, "speak", lambda text: b"ID3mp3")
    with app.serial_lock:  # 다른 요청이 DB 작업 중이어도 음성은 기다리지 않는다
        assert client.get(f"/announce/{gid}.mp3").status_code == 200
    assert client.get("/board").status_code == 200  # 잠금이 풀리면 일반 요청도 정상


def test_announce_reads_while_another_request_is_writing(client, seeded_meetup, conn_of, monkeypatch):
    mid, gid = seeded_meetup
    monkeypatch.setattr(announce, "speak", lambda text: b"ID3mp3")
    writer = conn_of()
    writer.execute("BEGIN IMMEDIATE")  # 잠금 안의 다른 요청이 쓰는 중. 안내 방송은 그 잠금 밖에서 돈다
    try:
        assert client.get(f"/announce/{gid}.mp3").status_code == 200
    finally:
        writer.execute("ROLLBACK")
