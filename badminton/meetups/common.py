"""정모 공통: 상수, 보관 오류, 여러 모듈이 같이 쓰는 작은 조회."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..matching import Player, sort_queue


STATUS_LABELS = {"present": "참석", "late": "지각", "away": "휴식", "gone": "퇴장", "canceled": "취소"}
COURT_STATE_LABELS = {"game": "게임", "empty": "빈코트", "lesson": "레슨"}
EXCLUDED_ORDER = {"noinfo": 0, "away": 1, "late": 2, "gone": 3, "canceled": 4}
SEOUL = ZoneInfo("Asia/Seoul")
ACTIVE = "SELECT a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND ended_at IS NULL"
COURT_REST_SECONDS = 15  # 경기를 끝낸 코트는 이만큼 비워 둔다 (선수 교대 시간)
MEMO_MAX = 100


class ArchivedMeetupError(ValueError):
    def __init__(self, meetup_id: int):
        super().__init__("보관된 정모입니다. 먼저 복구해 주세요")
        self.meetup_id = meetup_id


def session_day(now: datetime) -> str:
    # ponytail: 새벽 6시 전은 전날 정모로 본다 (22시 정모가 자정을 넘김). 새벽 정모가 생기면 정모별 종료 시각으로 바꿀 것
    return (now - timedelta(hours=6)).date().isoformat()


def today() -> str:
    return session_day(datetime.now(SEOUL))


def _rest_until(conn, meetup_id: int) -> dict[int, float]:
    """코트별로 다음 경기를 넣어도 되는 시각 (다시 뽑기로 취소된 경기는 대기 없이 바로 채운다)."""
    return {r["court"]: r["ended"] + COURT_REST_SECONDS for r in conn.execute(
        "SELECT court, max(ended_at) AS ended FROM games WHERE meetup_id = ? AND ended_at IS NOT NULL "
        "AND canceled = 0 GROUP BY court", (meetup_id,))}


def _court_states(conn, meetup_id: int) -> dict[int, str]:
    return {r["court"]: r["state"] for r in conn.execute(
        "SELECT court, state FROM court_states WHERE meetup_id = ?", (meetup_id,))}


def _playing(conn, meetup_id: int) -> set[int]:
    return {pid for row in conn.execute(ACTIVE, (meetup_id,)) for pid in row}


def _reserved(conn, meetup_id: int) -> set[int]:
    return {pid for row in conn.execute("SELECT a1, a2, b1, b2 FROM reservations WHERE meetup_id = ?", (meetup_id,)) for pid in row}


def reservations(conn, meetup_id: int) -> list:
    return conn.execute("SELECT * FROM reservations WHERE meetup_id = ? ORDER BY seq, id", (meetup_id,)).fetchall()


def queue(conn, meetup_id: int) -> list[Player]:
    busy = _playing(conn, meetup_id) | _reserved(conn, meetup_id)
    rows = conn.execute("""SELECT m.id, m.gender, m.grade, a.games, a.queued_at
                           FROM attendees a JOIN members m ON m.id = a.member_id
                           WHERE a.meetup_id = ? AND a.status = 'present'
                             AND m.gender IS NOT NULL AND m.grade IS NOT NULL""", (meetup_id,))
    return sort_queue([Player(r["id"], r["gender"], r["grade"], r["games"], r["queued_at"])
                       for r in rows if r["id"] not in busy])


def _recent_mates(conn, meetup_id: int) -> dict[int, set[int]]:
    """사람마다 가장 최근에 끝난 경기에서 같이 친 3명 (다시 뽑기로 취소된 경기는 제외)."""
    mates = {}
    for g in conn.execute("SELECT a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND ended_at IS NOT NULL "
                          "AND canceled = 0 ORDER BY ended_at, id", (meetup_id,)):
        four = set(g)
        for p in four:
            mates[p] = four - {p}
    return mates
