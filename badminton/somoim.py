"""소모임 웹 모임 페이지에서 정모와 참석자를 읽는다.

공개 API가 없어서 로그인 없이 받는 모임 페이지의 Next.js 스트리밍 데이터
(self.__next_f.push([1,"..."]))를 파싱한다. 소모임이 페이지 구조를 바꾸면 깨질 수 있고,
그때는 SomoimError를 내서 호출 쪽이 붙여넣기로 대신하게 한다.
"""
import json
import re
import urllib.request
from dataclasses import dataclass

PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_decoder = json.JSONDecoder()


class SomoimError(Exception):
    pass


@dataclass(frozen=True)
class Event:
    eid: str
    name: str
    date: str  # "2026-10-23"
    time: str  # "22:00"
    place: str
    capacity: int
    slot: int


@dataclass(frozen=True)
class SomoimMember:
    mid: str
    nickname: str
    attending_slots: frozenset[int]
    banned: bool


@dataclass(frozen=True)
class Group:
    events: list[Event]
    members: list[SomoimMember]


def _objects(text: str, marker: str):
    i = text.find(marker)
    while i >= 0:
        try:
            obj, end = _decoder.raw_decode(text, i)
            yield obj
        except ValueError:
            end = i + 1
        i = text.find(marker, end)


def _event(o: dict) -> Event | None:
    d, t = o.get("e_d"), o.get("e_t")
    if not isinstance(d, int) or not isinstance(t, int) or not o.get("eid"):
        return None
    try:
        return Event(o["eid"], o.get("en", ""), f"{d // 10000:04d}-{d // 100 % 100:02d}-{d % 100:02d}",
                     f"{t // 100:02d}:{t % 100:02d}", o.get("el", ""), int(o.get("emm", 0)), int(o.get("enum", 1)))
    except (TypeError, ValueError):
        return None  # 필드 형식이 바뀐 정모는 건너뛴다


def _slot_field(slot: int) -> str:
    return "ijo" if slot == 1 else f"ijo{slot}"


def parse_group(html: str) -> Group:
    text = "".join(json.loads(m.group(1)) for m in PUSH_RE.finditer(html))
    events, seen_eids = [], set()
    for o in _objects(text, '{"jms":'):
        e = _event(o)
        if e and e.eid not in seen_eids:
            seen_eids.add(e.eid)
            events.append(e)
    members = {}
    for o in _objects(text, '{"id":'):
        if "mid" not in o or "mn" not in o:
            continue
        slots = frozenset(n for n in range(1, 5) if o.get(_slot_field(n)) == "Y")
        prev = members.get(o["mid"])
        if prev:  # 같은 회원이 여러 번 나오면(운영진 목록 등) 참석 표시를 합친다
            slots |= prev.attending_slots
        members[o["mid"]] = SomoimMember(o["mid"], prev.nickname if prev else o["mn"], slots, o.get("ban") == "Y")
    if not events and not members:
        raise SomoimError("소모임 페이지에서 정모와 회원 정보를 찾지 못했습니다")
    return Group(events, list(members.values()))


def attendees(group: Group, event: Event) -> list[SomoimMember]:
    return [m for m in group.members if event.slot in m.attending_slots and not m.banned]


def events_on(group: Group, date: str) -> list[Event]:
    return [e for e in group.events if e.date == date]


def fetch_group(group_id: str, timeout: float = 10) -> Group:
    req = urllib.request.Request(f"https://www.somoim.co.kr/{group_id}", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return parse_group(resp.read().decode("utf-8"))
    except SomoimError:
        raise
    except Exception as e:  # 외부 페이지 경계: 어떤 실패든 붙여넣기 안내로 이어지게 한다
        raise SomoimError(f"소모임 페이지를 읽지 못했습니다: {e}") from e
