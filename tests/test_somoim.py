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


def test_bad_event_fields_skipped():
    bad = dict(EV, eid="g1bad", emm="$undefined")
    g = parse_group(page([EV, bad], [M("a", "가", "Y")]))
    assert [e.eid for e in g.events] == ["g1202610232200"]


def test_duplicate_member_records_merge_slots():
    g = parse_group(page([EV], [M("a", "가", "N", "Y"), M("a", "가", "Y")]))
    assert g.members[0].attending_slots == frozenset({1, 2})


def test_fetch_group_wraps_unexpected_errors(monkeypatch):
    from badminton import somoim

    def reset(*a, **k):
        raise ConnectionResetError("reset by peer")

    monkeypatch.setattr(somoim.urllib.request, "urlopen", reset)
    with pytest.raises(SomoimError):
        somoim.fetch_group("g1")


def test_group_name_from_title():
    html = "<title>노원구 배드민턴 모임 - 금요일 야간민턴</title>" + page([EV], [])
    assert parse_group(html).name == "금요일 야간민턴"


def test_parse_search_results():
    from badminton.somoim import parse_search
    html = ('<a class="x" href="/8119d5e4-f96f-11ef-a485-0a4564f15a111"><img/><h3>📢 Team RBT 민턴 회원모집</h3><p>소개</p></a>'
            '<a href="/8119d5e4-f96f-11ef-a485-0a4564f15a111"><h3>📢 Team RBT 민턴 회원모집</h3></a>'
            '<a href="/groups/rcmd">추천</a>'
            '<a href="/d49e0b02-b22a-413b-8295-53dc895bc4411"><h3>Team ONAM &amp; 친구</h3></a>')
    assert parse_search(html) == [("8119d5e4-f96f-11ef-a485-0a4564f15a111", "📢 Team RBT 민턴 회원모집"),
                                  ("d49e0b02-b22a-413b-8295-53dc895bc4411", "Team ONAM & 친구")]
