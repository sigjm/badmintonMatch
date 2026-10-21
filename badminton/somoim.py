"""소모임 웹 모임 페이지에서 정모와 참석자를 읽는다.

공개 API가 없어서 로그인 없이 받는 모임 페이지의 Next.js 스트리밍 데이터
(self.__next_f.push([1,"..."]))를 파싱한다. 소모임이 페이지 구조를 바꾸면 깨질 수 있고,
그때는 SomoimError를 내서 호출 쪽이 붙여넣기로 대신하게 한다.
"""
import html as htmllib
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_decoder = json.JSONDecoder()
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5MB


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
    name: str = ""


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
    chunks = []
    for m in PUSH_RE.finditer(html):
        try:
            chunks.append(json.loads(m.group(1)))
        except Exception as e:
            raise SomoimError(f"소모임 스트리밍 데이터 파싱 실패: {e}") from e
    text = "".join(chunks)
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
    title = re.search(r"<title>(.*?)</title>", html, re.S)  # "남양주시 배드민턴 모임 - Team ONAM 오남배드민턴"
    name = htmllib.unescape(title.group(1)).rsplit(" - ", 1)[-1].strip() if title else ""
    return Group(events, list(members.values()), name)


def attendees(group: Group, event: Event) -> list[SomoimMember]:
    return [m for m in group.members if event.slot in m.attending_slots and not m.banned]


def events_on(group: Group, date: str) -> list[Event]:
    return [e for e in group.events if e.date == date]


def fetch_group(group_id: str, timeout: float = 10) -> Group:
    req = urllib.request.Request(f"https://www.somoim.co.kr/{group_id}", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(content) > MAX_RESPONSE_BYTES:
                raise SomoimError("소모임 응답이 너무 큽니다 (5MB 초과)")
            return parse_group(content.decode("utf-8", errors="replace"))
    except SomoimError:
        raise
    except Exception as e:  # 외부 페이지 경계: 어떤 실패든 붙여넣기 안내로 이어지게 한다
        raise SomoimError(f"소모임 페이지를 읽지 못했습니다: {e}") from e


SEARCH_RE = re.compile(r'<a[^>]+href="/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12,13})"[^>]*>(.*?)</a>', re.S)


def parse_search(html: str) -> list[tuple[str, str]]:
    """소모임 웹 모임 검색 결과에서 (모임 ID, 모임 이름) 목록. 이름은 링크 안 첫 제목(h1~h4) 글자."""
    results, seen = [], set()
    for gid, inner in SEARCH_RE.findall(html):
        title = re.search(r"<h[1-4][^>]*>(.*?)</h[1-4]>", inner, re.S)
        if gid in seen or not title:
            continue
        seen.add(gid)
        results.append((gid, htmllib.unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip()))
    return results


def search_groups(keyword: str, timeout: float = 10) -> list[tuple[str, str]]:
    url = "https://www.somoim.co.kr/groups/search?keyword=" + urllib.parse.quote(keyword)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(content) > MAX_RESPONSE_BYTES:
                raise SomoimError("소모임 검색 응답이 너무 큽니다 (5MB 초과)")
            return parse_search(content.decode("utf-8", errors="replace"))
    except SomoimError:
        raise
    except Exception as e:  # 외부 페이지 경계
        raise SomoimError(f"소모임 검색 실패: {e}") from e
