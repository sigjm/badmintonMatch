from badminton import announce
from badminton.announce import announcement_text


def test_announcement_text_reads_court_and_names():
    assert announcement_text(1, ["김민재", "이정진", "박지원", "김승용"]) == "1코트, 김민재, 이정진, 박지원, 김승용 님 들어가세요."
    assert announcement_text(3, ["Tom★", "😀", "a b", "최"]) == "3코트, Tom, 선수, a b, 최 님 들어가세요."


def test_announce_route_returns_wav_for_game(client, seeded_meetup, monkeypatch):
    mid, gid = seeded_meetup
    said = []
    monkeypatch.setattr(announce, "speak", lambda text: said.append(text) or b"RIFF....WAVE")
    resp = client.get(f"/announce/{gid}.wav")
    assert resp.status_code == 200 and resp.mimetype == "audio/wav" and resp.data == b"RIFF....WAVE"
    assert said == [announcement_text(1, ["m1", "m2", "f1", "f2"])] or said[0].startswith("1코트, ")
    assert client.get("/announce/99999.wav").status_code == 404


def test_announce_route_503_when_tts_unavailable(client, seeded_meetup, monkeypatch):
    mid, gid = seeded_meetup
    def broken(text):
        raise RuntimeError("model missing")
    monkeypatch.setattr(announce, "speak", broken)
    assert client.get(f"/announce/{gid}.wav").status_code == 503
    assert client.get("/announce/hello.wav").status_code == 503


def test_court_cards_carry_game_and_announcement(admin, client, seeded_meetup):
    mid, gid = seeded_meetup
    html = admin.get(f"/admin/m/{mid}").get_data(as_text=True)
    assert f'data-game-id="{gid}"' in html and 'data-announce="1코트, ' in html
    assert "data-announce-toggle" in html and "data-announce-replay" in html
    public = client.get("/board").get_data(as_text=True)
    assert f'data-game-id="{gid}"' in public and "data-announce-toggle" in public
