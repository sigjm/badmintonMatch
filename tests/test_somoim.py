import json

import pytest

from badminton.somoim import Event, SomoimError, attendees, events_on, parse_group


def page(events, members):
    body = json.dumps({"events": events, "jm": members}, ensure_ascii=False)
    half = len(body) // 2  # 실제처럼 push 두 개로 쪼갬
    return "".join(f"<script>self.__next_f.push([1,{json.dumps(c)}])</script>" for c in (body[:half], body[half:]))


EV = {"jms": [], "eid": "g1202610232200", "en": "금야민+게1", "e_d": 20261023, "e_t": 2200, "el": "더콕", "emm": 21, "enum": 1}


def M(mid, mn, ijo="N", ijo2="N", ban="N"):
    return {"id": "g1" + mid, "gid": "g1", "mid": mid, "mn": mn, "ban": ban, "ijo": ijo, "ijo2": ijo2, "ijo3": "N", "ijo4": "N"}


def test_parse_event_fields():
    g = parse_group(page([EV], [M("a", "가")]))
    assert g.events == [Event("g1202610232200", "금야민+게1", "2026-10-23", "22:00", "더콕", 21, 1)]


def test_attendees_by_slot_dedup_and_ban():
    g = parse_group(page([EV], [M("a", "가", "Y"), M("a", "가", "Y"), M("b", "나", "N", "Y"), M("c", "다", "Y", ban="Y")]))
    assert [m.mid for m in attendees(g, g.events[0])] == ["a"]


def test_events_on_date():
    g = parse_group(page([EV], []))
    assert events_on(g, "2026-10-23") == g.events and events_on(g, "2026-10-24") == []


def test_garbage_raises():
    with pytest.raises(SomoimError):
        parse_group("<html>점검 중</html>")
