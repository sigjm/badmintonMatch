# 배드민턴 정모 자동 매칭 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 소모임 "금요일 야간민턴" 정모 참석자를 자동으로 불러오고, 급수·성별로 혼복 밸런스를 맞춰 코트별 다음 경기를 자동으로 짜는 모바일 웹앱을 만든다.

**Architecture:** Flask 앱 하나가 서버 렌더링 HTML을 내보내고, 데이터는 SQLite 파일 하나에 둔다. 매칭 규칙(`matching.py`)과 소모임 파싱(`somoim.py`)은 DB를 모르는 순수 모듈이다. `members.py`와 `meetups.py`가 DB와 두 모듈을 엮는다. 사용자 Ubuntu 서버에 Docker로 올리고, 기존 Cloudflare 터널로 `badminton.jmllem.com`에 공개한다.

**Tech Stack:** Python 3.12, Flask 3.1, gunicorn 23, 표준 라이브러리 `sqlite3` / `urllib` / `zoneinfo`, `werkzeug.security`(Flask에 포함), pytest

**Spec:** `docs/superpowers/specs/2026-10-21-badminton-match-design.md`

## Global Constraints

- 대상 모임 ID: `9b34753d-4105-4c55-9310-bb923ba2a8e61`. 환경 변수 `SOMOIM_GROUP_ID`의 기본값으로 쓴다.
- 소모임 계정 정보는 저장하지 않는다. 공개 페이지 `https://www.somoim.co.kr/<모임ID>`를 로그인 없이 GET으로만 읽는다.
- 급수 점수: S=6, A=5, B=4, C=3, D=2, 초심=1. DB 코드는 `S A B C D N`이고, `N`은 화면에 "초심"으로 표시한다.
- 여자 보정값의 기본은 1, 혼복 후보 범위의 기본은 6이다. 둘 다 `settings` 테이블에 두고 운영진이 바꿀 수 있다.
- 성별 코드는 `M` / `F`다. 참석 상태 코드와 화면 표기:
  - `present` = 참석
  - `late` = 지각
  - `away` = 자리 비움
  - `gone` = 귀가
  - `canceled` = 취소. 다시 불러오기에서만 이 상태로 바뀐다.
- 날짜와 시간은 Asia/Seoul 기준이다. 날짜는 문자열 `YYYY-MM-DD`, 시각은 `time.time()` 실수로 저장한다.
- PIN은 숫자 4자리이고, `werkzeug.security.generate_password_hash`로 해시해 저장한다.
- 운영진 비밀번호는 환경 변수 `ADMIN_PASSWORD`로만 정한다. 화면에서는 바꿀 수 없다.
- 하지 않는 것: 점수 기록, 파트너 반복 회피, 자동 주기 불러오기, 운영진 개인 계정, 여러 모임 지원.
- 화면 문구는 한국어로, 폰 세로 화면 기준으로 만든다. `base.html`에 viewport 메타 태그와 인라인 CSS를 넣고, 외부 CSS나 JS 의존성은 쓰지 않는다.
- 커밋 메시지 마지막 줄: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
- 실제 회원 이름이 들어간 HTML은 저장소에 커밋하지 않는다. 테스트 픽스처는 합성 데이터로 만든다.

## Review Focus

1. **소모임 페이지를 못 받거나 구조가 바뀐 경우**: 오류 문구와 붙여넣기 입력을 보여 주고, 이미 등록된 참석자는 건드리지 않는다. 가장 위험한 경우는 파싱이 실패했는데 "사라진 사람은 귀가" 규칙이 돌아 전원이 귀가 처리되는 것이다. → Task 2, Task 5 테스트
2. **"경기 끝"을 두 번 누른 경우**(두 폰에서 동시에, 또는 더블탭): 두 번째는 아무 일도 하지 않는다. 경기 수가 두 번 오르거나 경기가 두 개 생기면 안 된다. → Task 4 테스트
3. **매칭 가능한 사람이 4명보다 적은 경우**: 코트를 비워 두고, 누군가 급수를 입력하는 등 4번째 사람이 생기는 순간 채운다. → Task 4 테스트
4. **닉네임이 같은 회원이 둘 이상인 경우**: 붙여넣기에서 아무나 고르지 않고 "모호함"으로 보고한다. → Task 4 테스트
5. **PIN이 틀리거나 4자리 숫자가 아닌 경우**: 거부하고, 아무것도 바꾸지 않는다. → Task 3, Task 7 테스트

---

## File Structure

```
requirements.txt          flask==3.1.*, gunicorn==23.0.*
requirements-dev.txt      -r requirements.txt, pytest==8.*
.gitignore                .venv/ data/ .env __pycache__/
badminton/__init__.py     create_app()
badminton/matching.py     Player, 점수, 대기열 정렬, 4명 고르기, 팀 나누기 (순수)
badminton/somoim.py       소모임 페이지 받기 + 파싱 (순수 + urllib)
badminton/db.py           SQLite 연결, 스키마, settings
badminton/members.py      회원 정보, PIN, 변경 기록, 게스트
badminton/meetups.py      정모, 참석자 동기화, 상태, 대기열, 코트, 경기
badminton/admin.py        운영진 Blueprint (/admin/...)
badminton/public.py       회원 Blueprint (/, /board, /me)
badminton/templates/      base.html 외 화면별 템플릿
tests/conftest.py         tmp DB를 쓰는 app / conn 픽스처
tests/test_matching.py, test_somoim.py, test_members.py, test_meetups.py, test_admin.py, test_public.py
Dockerfile, docker-compose.yml
```

---

### Task 1: 프로젝트 뼈대와 매칭 규칙

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `.gitignore`, `badminton/__init__.py`(빈 파일, Task 5에서 채움), `badminton/matching.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Produces:
  ```python
  GRADE_POINTS = {"S": 6, "A": 5, "B": 4, "C": 3, "D": 2, "N": 1}
  GRADE_LABELS = {"S": "S", "A": "A", "B": "B", "C": "C", "D": "D", "N": "초심"}
  @dataclass(frozen=True)
  class Player: id: int; gender: str; grade: str; games: int; queued_at: float
  def score(p: Player, female_offset: float) -> float
  def sort_queue(players: list[Player]) -> list[Player]          # games↑, queued_at↑, id↑
  def pick_four(queue: list[Player], mix_window: int = 6) -> list[Player] | None   # queue는 정렬된 상태
  def split_teams(four: list[Player], female_offset: float) -> tuple[tuple[Player, Player], tuple[Player, Player]]
  ```

- [ ] **Step 1: 저장소 초기화**

Run: `git init && python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`
Expected: pytest 설치 완료

- [ ] **Step 2: 실패하는 테스트 작성** (`tests/test_matching.py`, 도우미 `P(id, g, grade, games=0, t=0)`)

```python
def test_score_female_offset():
    assert score(P(1, "F", "A"), 1) == 4
    assert score(P(2, "M", "A"), 1) == 5

def test_sort_queue_games_then_wait():
    q = sort_queue([P(1, "M", "A", games=2, t=1), P(2, "M", "A", games=1, t=9), P(3, "M", "A", games=1, t=5)])
    assert [p.id for p in q] == [3, 2, 1]

def test_pick_four_none_when_short():
    assert pick_four([P(i, "M", "A") for i in range(3)]) is None

def test_pick_four_prefers_mixed_within_window():
    q = [P(1,"M","A"), P(2,"M","A"), P(3,"M","A"), P(4,"F","A"), P(5,"F","A"), P(6,"M","A")]
    assert {p.id for p in pick_four(q, 6)} == {1, 2, 4, 5}

def test_pick_four_women_outside_window_ignored():
    q = [P(i,"M","A") for i in range(1,7)] + [P(7,"F","A"), P(8,"F","A")]
    assert {p.id for p in pick_four(q, 6)} == {1, 2, 3, 4}

def test_pick_four_single_woman_takes_head():
    q = [P(1,"F","A"), P(2,"M","A"), P(3,"M","A"), P(4,"M","A"), P(5,"M","A")]
    assert {p.id for p in pick_four(q, 6)} == {1, 2, 3, 4}

def test_split_mixed_only_and_balanced():
    ms, md, fa, fc = P(1,"M","S"), P(2,"M","D"), P(3,"F","A"), P(4,"F","C")
    teams = split_teams([ms, md, fa, fc], 1)
    assert {frozenset(p.id for p in t) for t in teams} == {frozenset({1, 4}), frozenset({2, 3})}

def test_split_same_gender_balanced():
    teams = split_teams([P(1,"M","S"), P(2,"M","A"), P(3,"M","C"), P(4,"M","D")], 1)
    assert {frozenset(p.id for p in t) for t in teams} == {frozenset({1, 4}), frozenset({2, 3})}
```

- [ ] **Step 3: 실패 확인**

Run: `.venv/bin/pytest tests/test_matching.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 4: `badminton/matching.py` 구현**

`pick_four`: 대기열 앞에서 `max(mix_window, 4)`명을 후보로 본다. 후보 안에 남자와 여자가 각각 2명 이상이면, 남자 중 앞의 2명과 여자 중 앞의 2명을 고른다. 이렇게 하면 대기열 맨 앞 사람은 항상 포함된다. 아니면 대기열 앞 4명을 고른다.

`split_teams`: 4명을 두 팀으로 나누는 3가지 경우 `(0,1|2,3)`, `(0,2|1,3)`, `(0,3|1,2)` 중에서 점수 합의 차이가 가장 작은 것을 고른다. 차이가 같으면 먼저 나온 경우를 고른다. 남2·여2라면 두 팀이 모두 남1·여1인 경우만 후보로 본다.

- [ ] **Step 5: 통과 확인**

Run: `.venv/bin/pytest tests/test_matching.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add . && git commit -m "feat: matching rules (queue order, mixed pick, balanced split)"
```

---

### Task 2: 소모임 페이지 파싱

**Files:**
- Create: `badminton/somoim.py`
- Test: `tests/test_somoim.py`

**Interfaces:**
- Produces:
  ```python
  class SomoimError(Exception)
  @dataclass(frozen=True)
  class Event: eid: str; name: str; date: str; time: str; place: str; capacity: int; slot: int   # date "2026-10-23", time "22:00"
  @dataclass(frozen=True)
  class SomoimMember: mid: str; nickname: str; attending_slots: frozenset[int]; banned: bool
  @dataclass(frozen=True)
  class Group: events: list[Event]; members: list[SomoimMember]
  def parse_group(html: str) -> Group             # 정모와 회원이 둘 다 0개면 SomoimError
  def attendees(group: Group, event: Event) -> list[SomoimMember]   # slot 참석 + ban 아님, mid 중복 제거
  def events_on(group: Group, date: str) -> list[Event]
  def fetch_group(group_id: str, timeout: float = 10) -> Group      # 네트워크/HTTP 오류 → SomoimError
  ```

실제 페이지 형식(검증됨):
- 데이터는 `<script>self.__next_f.push([1,"<JS 문자열>"])</script>` 여러 개로 쪼개져 있다.
- 각 문자열 리터럴을 `json.loads`로 풀고 순서대로 이어 붙이면 그 안에 JSON 객체가 들어 있다.
- 정모 객체: `{"jms":[...],"eid":…,"en":이름,"e_d":20261023,"e_t":2200,"el":장소,"emm":정원,"enum":슬롯,…}`
- 회원 객체: `{"id":…,"gid":…,"mid":…,"mn":닉네임,…,"ban":"N","ijo":"Y","ijo2":"N","ijo3":"N","ijo4":"N",…}`
- 슬롯 1번은 `ijo`, 슬롯 n번(2~4)은 `ijo{n}` 필드다.
- `"e_d":"$undefined"`처럼 숫자가 아닌 정모 객체는 건너뛴다.

- [ ] **Step 1: 실패하는 테스트 작성** (합성 픽스처)

```python
def page(events, members):
    body = json.dumps({"events": events, "jm": members}, ensure_ascii=False)
    half = len(body) // 2   # 실제처럼 push 두 개로 쪼갬
    return "".join(f'<script>self.__next_f.push([1,{json.dumps(c)}])</script>' for c in (body[:half], body[half:]))

EV = {"jms": [], "eid": "g1202610232200", "en": "금야민+게1", "e_d": 20261023, "e_t": 2200, "el": "더콕", "emm": 21, "enum": 1}
def M(mid, mn, ijo="N", ijo2="N", ban="N"):
    return {"id": "g1" + mid, "gid": "g1", "mid": mid, "mn": mn, "ban": ban, "ijo": ijo, "ijo2": ijo2, "ijo3": "N", "ijo4": "N"}

def test_parse_event_fields():
    g = parse_group(page([EV], [M("a", "가")]))
    assert g.events == [Event("g1202610232200", "금야민+게1", "2026-10-23", "22:00", "더콕", 21, 1)]

def test_attendees_by_slot_dedup_and_ban():
    g = parse_group(page([EV], [M("a","가","Y"), M("a","가","Y"), M("b","나","N","Y"), M("c","다","Y",ban="Y")]))
    assert [m.mid for m in attendees(g, g.events[0])] == ["a"]

def test_events_on_date():
    g = parse_group(page([EV], []))
    assert events_on(g, "2026-10-23") == g.events and events_on(g, "2026-10-24") == []

def test_garbage_raises():
    with pytest.raises(SomoimError):
        parse_group("<html>점검 중</html>")
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest tests/test_somoim.py -v` → FAIL

- [ ] **Step 3: `badminton/somoim.py` 구현**

push 문자열은 정규식 `self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)`로 찾는다. 이어 붙인 텍스트에서 `{"jms":`와 `{"id":`의 위치마다 문자열을 고려한 중괄호 짝 맞추기로 객체 하나를 잘라 `json.loads`한다. 실패한 조각은 건너뛰고, `mid`가 없는 `{"id":` 객체도 무시한다. `fetch_group`은 `urllib.request`로 `User-Agent: Mozilla/5.0` 헤더를 붙여 GET한다.

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/pytest tests/test_somoim.py -v` → 4 passed

- [ ] **Step 5: 실제 페이지로 검증** (커밋하지 않음)

Run: `.venv/bin/python -c "from badminton.somoim import *; g=fetch_group('9b34753d-4105-4c55-9310-bb923ba2a8e61'); e=events_on(g,'2026-10-23'); print(e, len(attendees(g,e[0])))"`
Expected: 이름 `금야민+게1`, 시각 `22:00`, 슬롯 1, 정원 21인 정모 1개, 참석자 20명 안팎. 정모가 끝나 목록에서 사라졌다면 그 시점의 다음 정모로 확인하고, 참석자 수를 소모임 화면의 숫자와 대조한다.

- [ ] **Step 6: Commit** — `git commit -am "feat: parse somoim group page (events, attendees)"` (새 파일은 `git add` 먼저)

---

### Task 3: DB와 회원

**Files:**
- Create: `badminton/db.py`, `badminton/members.py`, `tests/conftest.py`
- Test: `tests/test_members.py`

**Interfaces:**
- Produces:
  ```python
  # db.py
  def connect(path: str) -> sqlite3.Connection    # Row factory, foreign_keys=ON, 스키마 생성, settings 기본값 삽입
  def get_setting(conn, key: str) -> float         # "female_offset"=1, "mix_window"=6
  def set_setting(conn, key: str, value: float) -> None
  # members.py
  def upsert_somoim(conn, mid: str, nickname: str) -> int                 # mid로 찾아 닉네임 갱신, 없으면 생성
  def create_member(conn, nickname: str, *, is_guest: bool = False, gender: str | None = None, grade: str | None = None) -> int
  def set_info(conn, member_id: int, gender: str | None, grade: str | None, by: str) -> None   # by: "self" | "admin"; 바뀐 필드만 기록; 잘못된 코드면 ValueError
  def set_pin(conn, member_id: int, pin: str) -> None      # 4자리 숫자가 아니면 ValueError
  def check_pin(conn, member_id: int, pin: str) -> bool
  def reset_pin(conn, member_id: int) -> None
  def list_members(conn, *, guests: bool | None = None) -> list[sqlite3.Row]   # 닉네임순
  def changes(conn, limit: int = 100) -> list[sqlite3.Row]                    # 최신순, 닉네임 포함
  ```
- 스키마 (`db.py`):
  ```sql
  members(id INTEGER PRIMARY KEY, somoim_mid TEXT UNIQUE, nickname TEXT NOT NULL,
          gender TEXT CHECK(gender IN ('M','F')), grade TEXT CHECK(grade IN ('S','A','B','C','D','N')),
          pin_hash TEXT, is_guest INTEGER NOT NULL DEFAULT 0)
  changes(id INTEGER PRIMARY KEY, member_id INTEGER REFERENCES members, by TEXT, field TEXT, old TEXT, new TEXT, at REAL)
  settings(key TEXT PRIMARY KEY, value REAL)
  meetups(id INTEGER PRIMARY KEY, date TEXT NOT NULL, name TEXT NOT NULL, eid TEXT UNIQUE, courts INTEGER NOT NULL DEFAULT 0, started INTEGER NOT NULL DEFAULT 0)
  attendees(meetup_id INTEGER REFERENCES meetups, member_id INTEGER REFERENCES members, status TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('somoim','guest','paste')), games INTEGER NOT NULL DEFAULT 0,
            queued_at REAL NOT NULL, PRIMARY KEY(meetup_id, member_id))
  games(id INTEGER PRIMARY KEY, meetup_id INTEGER REFERENCES meetups, court INTEGER NOT NULL,
        a1 INTEGER, a2 INTEGER, b1 INTEGER, b2 INTEGER, started_at REAL NOT NULL, ended_at REAL)
  ```

- [ ] **Step 1: `tests/conftest.py`에 픽스처 작성**: `conn`은 `db.connect(str(tmp_path / "t.db"))`. `app`/`client`는 Task 5에서 추가한다.

- [ ] **Step 2: 실패하는 테스트 작성**

```python
def test_upsert_somoim_updates_nickname(conn):
    a = upsert_somoim(conn, "m1", "옛닉"); b = upsert_somoim(conn, "m1", "새닉")
    assert a == b and list_members(conn)[0]["nickname"] == "새닉"

def test_set_info_logs_only_changes(conn):
    m = create_member(conn, "가")
    set_info(conn, m, "F", "B", by="self"); set_info(conn, m, "F", "A", by="admin")
    assert [(c["field"], c["old"], c["new"], c["by"]) for c in changes(conn)] == [
        ("grade", "B", "A", "admin"), ("grade", None, "B", "self"), ("gender", None, "F", "self")]

def test_set_info_rejects_bad_codes(conn):
    m = create_member(conn, "가")
    with pytest.raises(ValueError): set_info(conn, m, "X", "A", by="admin")

def test_pin_rules(conn):
    m = create_member(conn, "가")
    for bad in ("123", "12345", "abcd"):
        with pytest.raises(ValueError): set_pin(conn, m, bad)
    set_pin(conn, m, "0420")
    assert check_pin(conn, m, "0420") and not check_pin(conn, m, "0000")
    reset_pin(conn, m); assert not check_pin(conn, m, "0420")

def test_settings_defaults(conn):
    assert get_setting(conn, "female_offset") == 1 and get_setting(conn, "mix_window") == 6
```

`changes` 정렬 기준: `at`이 늦은 순, 같으면 `id`가 큰 순.

- [ ] **Step 3: 실패 확인** — Run: `.venv/bin/pytest tests/test_members.py -v` → FAIL
- [ ] **Step 4: `db.py`, `members.py` 구현**
- [ ] **Step 5: 통과 확인** — Run: `.venv/bin/pytest tests/test_members.py -v` → 5 passed
- [ ] **Step 6: Commit** — `"feat: sqlite schema, members, PIN, change log"`

---

### Task 4: 정모, 참석자, 코트

**Files:**
- Create: `badminton/meetups.py`
- Test: `tests/test_meetups.py`

**Interfaces:**
- Consumes: Task 1 `Player`, `sort_queue`, `pick_four`, `split_teams`, `score`. Task 2 `SomoimMember`. Task 3 `upsert_somoim`, `create_member`, `get_setting`.
- Produces:
  ```python
  STATUS_LABELS = {"present": "참석", "late": "지각", "away": "자리 비움", "gone": "귀가", "canceled": "취소"}
  def today() -> str                                                       # Asia/Seoul
  def get_or_create_meetup(conn, date: str, name: str, eid: str | None) -> int    # eid가 있으면 eid로, 없으면 (date, name)으로 찾음
  def sync_somoim(conn, meetup_id: int, members: list[SomoimMember]) -> dict   # {"added": int, "canceled": int, "restored": int}
  def add_pasted(conn, meetup_id: int, text: str) -> dict    # {"added": [닉네임], "created": [닉네임], "ambiguous": [닉네임]}
  def add_attendee(conn, meetup_id: int, member_id: int, source: str) -> None   # 이미 있으면 무시
  def set_status(conn, meetup_id: int, member_id: int, status: str) -> None
  def queue(conn, meetup_id: int) -> list[Player]            # 매칭 가능한 사람, 정렬된 상태
  def fill_courts(conn, meetup_id: int) -> None
  def start(conn, meetup_id: int, courts: int) -> None       # courts 저장, started=1, fill_courts
  def end_game(conn, game_id: int) -> None
  def redraw(conn, game_id: int) -> None
  def board(conn, meetup_id: int) -> dict
  #   {"courts": [{"court": 1, "game_id": int | None, "teams": [[닉네임, 닉네임], [닉네임, 닉네임]] | None}],
  #    "queue": [닉네임], "excluded": [{"nickname", "reason"}]}   # reason: 상태 라벨 또는 "급수 미입력"/"성별 미입력"
  def refill_today(conn) -> None                             # date == today()이고 started=1인 정모마다 fill_courts
  def on_info_changed(conn, member_id: int) -> None          # 매칭 가능해졌으면 오늘 정모 행의 queued_at=now, 그 뒤 refill_today
  ```

규칙(스펙에서 옮김):
- **매칭 가능**: `status='present'`이고, 성별과 급수가 있고, 끝나지 않은 경기(`ended_at IS NULL`)에 없는 사람.
- **`queued_at` 갱신**:
  - 참석자를 처음 추가할 때: `now`
  - `set_status`로 `present`가 될 때: `now`
  - 경기가 끝날 때: 끝난 시각
  - `redraw` 때: 바꾸지 않음. 그래서 대기 순서가 그대로 유지된다.
- `members.set_info`로 성별이나 급수가 채워져 매칭 가능해지는 경우에도 `queued_at`을 `now`로 둔다. 구현은 `meetups.on_info_changed(conn, member_id) -> None`으로 하고, 오늘 정모의 해당 attendee 행을 갱신한 뒤 `refill_today`를 부른다. 웹 계층이 `set_info` 다음에 이 함수를 부른다.
- **`sync_somoim`** (다시 불러오기, 명단 갱신용):
  - 목록에 새로 생긴 사람은 `present`, `source='somoim'`으로 추가한다.
  - 목록에서 사라진 `source='somoim'` 행 중 `canceled`가 아닌 것은 `canceled`로 바꾼다.
  - 목록에 다시 나타난 `canceled` 행은 `present`로 바꾸고 `queued_at`을 `now`로 둔다(`restored`).
  - 기존 행의 상태는 그 밖에는 바꾸지 않는다. 게스트와 붙여넣기 행은 건드리지 않는다.
- **`fill_courts`**: 1번부터 `courts`번 코트까지 빈 코트마다 `pick_four`(설정값 `mix_window`) → `split_teams`(설정값 `female_offset`) 순으로 경기를 만든다. `pick_four`가 `None`이면 멈춘다.
- **`end_game`**: 이미 끝난 경기면 아무것도 하지 않는다. 아니면 `ended_at`을 기록하고, 4명의 `games`를 1 올리고 `queued_at`을 갱신한 뒤 `fill_courts`를 부른다.
- **`redraw`**: 끝난 경기면 아무것도 하지 않는다. 아니면 경기 행을 삭제하고 `fill_courts`를 부른다.
- **`add_pasted`**: 줄 단위로 앞뒤 공백을 지우고 빈 줄은 버린다. 닉네임이 정확히 같은 회원이 한 명이면 추가하고, 둘 이상이면 `ambiguous`로 보고하고, 없으면 회원을 새로 만들어 추가한다(`created`). 모두 `source='paste'`다.

- [ ] **Step 1: 실패하는 테스트 작성** (도우미: `mk(conn, nick, g, grade)`는 회원 생성, `seat(conn, mid, *ids)`는 참석자로 추가)

```python
def test_start_fills_courts_mixed(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"m{i}", "M", "B") for i in range(4)], *[mk(conn, f"f{i}", "F", "B") for i in range(4)])
    start(conn, mid, 2)
    b = board(conn, mid)
    assert all(c["teams"] for c in b["courts"]) and b["queue"] == []

def test_end_game_twice_is_noop(conn):          # Review Focus 2
    mid, gid = one_court_game(conn)              # 4명, 코트 1개
    end_game(conn, gid); end_game(conn, gid)
    assert [r["games"] for r in conn.execute("select games from attendees")] == [1, 1, 1, 1]
    assert conn.execute("select count(*) from games").fetchone()[0] == 2   # 끝난 경기 + 새로 채운 경기

def test_short_queue_leaves_court_empty_then_fills(conn):   # Review Focus 3
    mid = get_or_create_meetup(conn, today(), "t", None)
    ids = [mk(conn, f"p{i}", "M", "B") for i in range(3)] + [mk(conn, "nograde", "M", None)]
    seat(conn, mid, *ids); start(conn, mid, 1)
    assert board(conn, mid)["courts"][0]["teams"] is None
    set_info(conn, ids[3], "M", "C", by="self"); on_info_changed(conn, ids[3])
    assert board(conn, mid)["courts"][0]["teams"] is not None

def test_excluded_reasons(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    a, b = mk(conn, "a", "M", None), mk(conn, "b", "M", "A")
    seat(conn, mid, a, b); set_status(conn, mid, b, "late")
    assert {e["nickname"]: e["reason"] for e in board(conn, mid)["excluded"]} == {"a": "급수 미입력", "b": "지각"}

def test_sync_marks_removed_canceled_and_keeps_guests(conn):
    mid = get_or_create_meetup(conn, "2026-10-23", "금야민+게1", "e1")
    sync_somoim(conn, mid, [SM("x", "엑스"), SM("y", "와이")])
    g = mk(conn, "게스트", "F", "C", guest=True); add_attendee(conn, mid, g, "guest")
    assert sync_somoim(conn, mid, [SM("x", "엑스")]) == {"added": 0, "canceled": 1, "restored": 0}
    assert status_of(conn, mid, "와이") == "canceled" and status_of(conn, mid, "게스트") == "present"

def test_sync_restores_rejoined_but_not_manual_status(conn):
    mid = get_or_create_meetup(conn, "2026-10-23", "금야민+게1", "e1")
    sync_somoim(conn, mid, [SM("x", "엑스"), SM("y", "와이")])
    sync_somoim(conn, mid, [SM("x", "엑스")])                      # 와이 취소
    set_status(conn, mid, member_id_of(conn, "엑스"), "gone")      # 엑스는 운영진이 귀가 처리
    assert sync_somoim(conn, mid, [SM("x", "엑스"), SM("y", "와이")]) == {"added": 0, "canceled": 0, "restored": 1}
    assert status_of(conn, mid, "와이") == "present" and status_of(conn, mid, "엑스") == "gone"

def test_redraw_keeps_queue_order(conn):
    mid, gid = one_court_game(conn, extra=1)     # 5명: 4명 경기 중, 1명 대기
    snap = lambda: dict(conn.execute("select member_id, queued_at from attendees").fetchall())
    before = snap()
    away = game_players(conn, gid)[0]; set_status(conn, mid, away, "away"); redraw(conn, gid)
    assert away not in game_players(conn, current_game(conn, mid))
    assert snap() == before

def test_paste_reports_ambiguous(conn):         # Review Focus 4
    mid = get_or_create_meetup(conn, today(), "t", None)
    mk(conn, "철수", "M", "A"); mk(conn, "철수", "M", "B"); mk(conn, "영희", "F", "A")
    r = add_pasted(conn, mid, " 영희 \n\n철수\n새사람\n")
    assert r == {"added": ["영희"], "created": ["새사람"], "ambiguous": ["철수"]}
```

`SM(mid, nick)`는 `SomoimMember(mid, nick, frozenset({1}), False)`의 줄임이다. `status_of`, `member_id_of`, `one_court_game`, `game_players`, `current_game`은 테스트 파일 안의 작은 쿼리 도우미다.

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest tests/test_meetups.py -v` → FAIL
- [ ] **Step 3: `badminton/meetups.py` 구현**
- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/pytest -v` → 전체 통과
- [ ] **Step 5: Commit** — `"feat: meetups, attendee sync, court filling, end/redraw"`

---

### Task 5: 운영진 화면 — 로그인, 불러오기, 정모 운영

**Files:**
- Modify: `badminton/__init__.py`
- Create: `badminton/admin.py`, `badminton/templates/base.html`, `admin_login.html`, `admin_home.html`, `admin_meetup.html`
- Modify: `tests/conftest.py`(`app`, `client`, `admin` 픽스처 추가)
- Test: `tests/test_admin.py`

**Interfaces:**
- Consumes: Task 2 `fetch_group`, `events_on`, `attendees`, `SomoimError`. Task 3, Task 4의 함수 전부.
- Produces:
  - `create_app(config: dict | None = None) -> Flask`
    - 설정 키: `DATABASE`(기본 `/data/badminton.db`), `ADMIN_PASSWORD`, `SECRET_KEY`, `SOMOIM_GROUP_ID`(기본은 Global Constraints의 모임 ID). 환경 변수를 먼저 읽고 `config` 인자로 덮어쓴다.
    - 요청마다 `flask.g.conn`을 쓰고, teardown에서 닫는다.
  - `admin_required` 데코레이터: `session["admin"]`이 없으면 `/admin/login`으로 보낸다.
  - 라우트 (모두 `admin_required`, 변경 라우트는 POST 후 `/admin/m/<id>`로 리다이렉트하고 끝에 `refill_today`):
    - `GET|POST /admin/login`
    - `GET /admin`: 날짜 입력, 기본값 `today()`. 최근 정모 목록.
    - `POST /admin/import`: 폼 `date`
      - 정모가 1개면 `get_or_create_meetup` 후 `sync_somoim`을 하고 해당 정모로 이동한다.
      - 여러 개면 고르는 화면을 보여 준다. 폼 `eid`로 다시 제출받는다.
      - 0개거나 `SomoimError`면 `admin_home`에 오류 문구와 붙여넣기 폼(날짜, 정모 이름, 텍스트)을 보여 준다. DB는 바꾸지 않는다.
    - `POST /admin/m/<id>/import`: "다시 불러오기" 버튼. 같은 정모의 명단을 갱신한다(`sync_somoim`). 결과를 "추가 n · 취소 n · 복귀 n"으로 보여 준다. 오류 시 동작은 위와 같다.
    - `POST /admin/paste`: 폼 `date`, `name`, `text` → `get_or_create_meetup(date, name, None)`, `add_pasted`
    - `GET /admin/m/<id>`: 참석자 표(닉네임, 성별, 급수, 상태, 경기 수, 급수와 성별 바로 입력), 게스트 추가 폼(새 게스트, 또는 저장된 게스트 고르기), 코트 수와 시작, 코트 보드(코트별 팀, "경기 끝", "다시 뽑기"), 대기열, 제외 목록
    - `POST /admin/m/<id>/status`: `member_id`, `status`
    - `POST /admin/m/<id>/info`: `member_id`, `gender`, `grade` → `set_info(by="admin")`, `on_info_changed`
    - `POST /admin/m/<id>/guest`: `nickname`, `gender`, `grade` 또는 `member_id` → `create_member(is_guest=True)`, `add_attendee(source="guest")`
    - `POST /admin/m/<id>/start`: `courts`
    - `POST /admin/game/<gid>/end`, `POST /admin/game/<gid>/redraw`

- [ ] **Step 1: 실패하는 테스트 작성** (`monkeypatch.setattr("badminton.admin.fetch_group", ...)`로 소모임 대체)

```python
def test_admin_requires_login(client):
    assert client.get("/admin").headers["Location"].endswith("/admin/login")

def test_wrong_password_rejected(client):
    assert "비밀번호" in client.post("/admin/login", data={"password": "x"}).get_data(as_text=True)

def test_import_creates_meetup_with_attendees(admin, monkeypatch, conn_of):
    monkeypatch.setattr("badminton.admin.fetch_group", lambda gid: GROUP_ONE_EVENT)   # 참석 2명, 2026-10-23
    r = admin.post("/admin/import", data={"date": "2026-10-23"})
    assert r.status_code == 302 and conn_of().execute("select count(*) from attendees").fetchone()[0] == 2

def test_somoim_failure_keeps_attendees_and_offers_paste(admin, monkeypatch, conn_of):   # Review Focus 1
    monkeypatch.setattr("badminton.admin.fetch_group", lambda gid: GROUP_ONE_EVENT)
    admin.post("/admin/import", data={"date": "2026-10-23"})
    def boom(gid): raise SomoimError("구조 변경")
    monkeypatch.setattr("badminton.admin.fetch_group", boom)
    html = admin.post("/admin/m/1/import").get_data(as_text=True)
    assert "붙여넣기" in html
    assert conn_of().execute("select count(*) from attendees where status='present'").fetchone()[0] == 2

def test_end_and_redraw_routes(admin, seeded_meetup):     # 4명 + 코트 1개로 시작된 정모
    mid, gid = seeded_meetup
    assert admin.post(f"/admin/game/{gid}/end").status_code == 302
    assert admin.post(f"/admin/game/{gid}/end").status_code == 302   # 두 번째도 오류 없이 처리
```

`conn_of()`는 같은 DB 파일을 여는 픽스처다. `GROUP_ONE_EVENT`와 `seeded_meetup`은 `conftest.py`에 둔다.

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest tests/test_admin.py -v` → FAIL
- [ ] **Step 3: `create_app`, `admin.py`, 템플릿 구현**. 로그인 비교는 `hmac.compare_digest`로 한다.
- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/pytest -v` → 전체 통과
- [ ] **Step 5: 로컬 확인**

Run: `ADMIN_PASSWORD=pw SECRET_KEY=dev DATABASE=./data/dev.db .venv/bin/flask --app badminton run`

브라우저에서 확인한다.
- `/admin`에서 로그인한 뒤, 오늘 이후의 실제 정모 날짜로 불러오기를 한다. 참석자 수가 소모임 화면과 같아야 한다.
- 화면 폭 390px에서 가로 스크롤 없이 보여야 한다.

- [ ] **Step 6: Commit** — `"feat: admin login, somoim import, meetup console"`

---

### Task 6: 운영진 화면 — 회원 관리, 변경 기록, 설정

**Files:**
- Modify: `badminton/admin.py`
- Create: `badminton/templates/admin_members.html`, `admin_settings.html`
- Test: `tests/test_admin.py`에 추가

**Interfaces:**
- 라우트 (모두 `admin_required`):
  - `GET /admin/members`: 회원 목록(닉네임, 성별, 급수, 게스트 표시, PIN 설정 여부)과 변경 기록 `changes(limit=100)`
  - `POST /admin/members/<id>`: `gender`, `grade` → `set_info(by="admin")`, `on_info_changed`
  - `POST /admin/members/<id>/reset-pin`
  - `GET|POST /admin/settings`: `female_offset`, `mix_window`
    - `female_offset`은 0 이상 5 이하의 숫자(소수 허용)다.
    - `mix_window`는 4 이상 20 이하의 정수다.
    - 범위를 벗어나면 오류 문구를 보여 주고 저장하지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_settings_validation(admin, conn_of):
    admin.post("/admin/settings", data={"female_offset": "abc", "mix_window": "6"})
    assert get_setting(conn_of(), "female_offset") == 1
    admin.post("/admin/settings", data={"female_offset": "1.5", "mix_window": "8"})
    assert get_setting(conn_of(), "female_offset") == 1.5 and get_setting(conn_of(), "mix_window") == 8

def test_admin_edit_logged_as_admin(admin, conn_of):
    m = create_member(conn_of(), "가")
    admin.post(f"/admin/members/{m}", data={"gender": "M", "grade": "C"})
    assert {c["by"] for c in changes(conn_of())} == {"admin"}
```

- [ ] **Step 2: 실패 확인** → **Step 3: 구현** → **Step 4: 통과 확인** (`.venv/bin/pytest -v`)
- [ ] **Step 5: Commit** — `"feat: admin members, change log, settings"`

---

### Task 7: 회원 화면 — 대진 보기, 내 정보

**Files:**
- Create: `badminton/public.py`, `badminton/templates/board.html`, `me.html`
- Modify: `badminton/__init__.py` (Blueprint 등록)
- Test: `tests/test_public.py`

**Interfaces:**
- 라우트 (로그인 없음):
  - `GET /`: `/board`로 리다이렉트
  - `GET /board`: 오늘 시작된 정모의 `board()`를 읽기 전용으로 보여 준다. 없으면 가장 최근에 시작된 정모를 보여 준다. 그것도 없으면 "진행 중인 정모가 없습니다"를 보여 준다. `<meta http-equiv="refresh" content="10">`로 새로고침한다.
  - `GET /me`: 회원 고르기. 게스트를 뺀 회원 목록이다. 같은 닉네임이 여럿이면 `닉네임 (#id)`로 구분한다.
  - `POST /me`: `member_id`, `pin`, 선택 항목 `gender`, `grade`
    - PIN이 아직 없으면 `set_pin` 후 정보를 저장한다.
    - PIN이 있으면 `check_pin`이 맞을 때만 `set_info(by="self")`와 `on_info_changed`를 하고 `refill_today`를 부른다.
    - PIN이 틀리거나 형식이 잘못되면 "PIN이 맞지 않습니다" 또는 "PIN은 숫자 4자리"를 보여 주고, 아무것도 바꾸지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_board_without_meetup(client):
    assert "진행 중인 정모가 없습니다" in client.get("/board").get_data(as_text=True)

def test_me_first_time_sets_pin_and_info(client, conn_of):
    m = create_member(conn_of(), "가")
    client.post("/me", data={"member_id": m, "pin": "1234", "gender": "F", "grade": "C"})
    row = conn_of().execute("select gender, grade from members where id=?", (m,)).fetchone()
    assert tuple(row) == ("F", "C") and check_pin(conn_of(), m, "1234")

def test_me_wrong_or_bad_pin_changes_nothing(client, conn_of):      # Review Focus 5
    m = create_member(conn_of(), "가", gender="M", grade="B"); set_pin(conn_of(), m, "1234")
    for pin in ("0000", "12a4"):
        client.post("/me", data={"member_id": m, "pin": pin, "gender": "M", "grade": "S"})
    assert conn_of().execute("select grade from members where id=?", (m,)).fetchone()[0] == "B"
    assert changes(conn_of()) == []
```

- [ ] **Step 2: 실패 확인** → **Step 3: 구현** → **Step 4: 통과 확인** (`.venv/bin/pytest -v`)
- [ ] **Step 5: Commit** — `"feat: public board and member self-service"`

---

### Task 8: 배포와 Cloudflare 터널

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `.dockerignore`(`.git .venv data .env tests __pycache__`)

**사실 (2026-10-21 확인):**
- 서버: `sigjm@192.168.75.208`, Ubuntu 24.04, Docker 설치됨. SSH는 키 인증으로 BatchMode 접속이 된다.
- `cloudflared` 컨테이너: 원격에서 관리되는 토큰 방식 터널이고, Docker 네트워크 `server-edge_default`에 붙어 있다. 기존 호스트 이름은 `*.jmllem.com`이다.

- [ ] **Step 1: Dockerfile과 compose 파일 작성**

Dockerfile:
- `python:3.12-slim` 이미지를 쓰고 `pip install -r requirements.txt`로 설치한다.
- `COPY badminton /app/badminton`
- 실행: `CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:8000", "badminton:create_app()"]`
- `# ponytail: 워커 1개라 모든 요청이 순서대로 처리됨 → 경기 끝 동시 클릭 경쟁 없음. 모임이 여러 개로 늘면 트랜잭션 잠금으로 바꿀 것`

docker-compose.yml:
```yaml
services:
  badminton:
    build: .
    container_name: badminton
    restart: unless-stopped
    env_file: .env
    environment: { DATABASE: /data/badminton.db, TZ: Asia/Seoul }
    volumes: ["./data:/data"]
    networks: [edge]
networks:
  edge: { external: true, name: server-edge_default }
```

- [ ] **Step 2: 서버로 복사**

Run: `rsync -az --delete --exclude .git --exclude .venv --exclude data --exclude .env --exclude __pycache__ ./ sigjm@192.168.75.208:badminton/`

- [ ] **Step 3: 서버 `.env` 생성** (`~/badminton/.env`가 없을 때만, `chmod 600`)

```
ADMIN_PASSWORD=<python3 -c "import secrets;print(secrets.token_urlsafe(9))">
SECRET_KEY=<python3 -c "import secrets;print(secrets.token_hex(32))">
SOMOIM_GROUP_ID=9b34753d-4105-4c55-9310-bb923ba2a8e61
```

생성한 운영진 비밀번호는 마지막 보고에서 사용자에게 알린다.

- [ ] **Step 4: 실행과 내부 확인**

Run: `ssh sigjm@192.168.75.208 'cd badminton && docker compose up -d --build && docker run --rm --network server-edge_default curlimages/curl:8.10.1 -s -o /dev/null -w "%{http_code}" http://badminton:8000/board'`
Expected: `200`

- [ ] **Step 5: Cloudflare 공개 호스트 이름 추가** (사용자가 대신 등록하라고 승인함)

ego-browser로 `https://one.dash.cloudflare.com`을 열어 다음 경로로 간다: Networks → Tunnels → `jmllem.com` 호스트를 서비스하는 터널 → Public Hostname → Add.
- Subdomain: `badminton`
- Domain: `jmllem.com`
- Service: `HTTP` / `badminton:8000`

로그인되어 있지 않으면 `task.handOff()`로 사용자에게 로그인을 부탁하고 기다린다. 기존 호스트 이름은 수정하지 않는다.

- [ ] **Step 6: 외부 확인**

Run: `curl -s -o /dev/null -w "%{http_code}" https://badminton.jmllem.com/board`
Expected: `200`. DNS 전파 때문에 1~2분 걸릴 수 있다.

- [ ] **Step 7: 실제 데이터로 확인**

`https://badminton.jmllem.com/admin`에서 로그인한 뒤, 다음 금요일 정모 날짜로 불러오기를 한다. 참석자 수가 소모임 화면의 숫자와 같아야 한다.

- [ ] **Step 8: Commit** — `"chore: docker deploy"`
