from dataclasses import dataclass
from itertools import combinations

GRADE_POINTS = {"S": 6, "A": 5, "B": 4, "C": 3, "D": 2, "N": 1}
GRADE_LABELS = {"S": "S", "A": "A", "B": "B", "C": "C", "D": "D", "N": "초심"}

# 4명을 두 팀으로 나누는 3가지 경우 (인덱스)
PAIRINGS = [((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2))]


@dataclass(frozen=True)
class Player:
    id: int
    gender: str  # "M" | "F"
    grade: str
    games: int
    queued_at: float


def score(p: Player, female_offset: float) -> float:
    return GRADE_POINTS[p.grade] - (female_offset if p.gender == "F" else 0)


def sort_queue(players: list[Player]) -> list[Player]:
    return sorted(players, key=lambda p: (p.games, p.queued_at, p.id))


def pick_four(queue: list[Player], mix_window: int = 6, female_offset: float = 1,
              recent: dict[int, set[int]] | None = None) -> list[Player] | None:
    """대기열 맨 앞 사람 + 앞쪽 mix_window명 안에서 급수가 가장 비슷한 3명.
    경기 수가 가장 적은 사람이 4명 이상이면 그 안에서만 고른다(경기 수 공평성 우선).
    recent(사람 → 직전 경기에서 같이 친 사람들)가 주어지면 그 사람들끼리 다시 묶이는 쌍이 적은 조합을 먼저 고른다.
    (급수 폭 + 직전 멤버 겹친 쌍 × 2) 최소 → 겹침 적은 쪽 → 혼복 → 남복/여복 → 대기 순서 앞쪽 순."""
    recent = recent or {}
    if len(queue) < 4:
        return None
    window = queue[:max(mix_window, 4)]
    tier = [p for p in window if p.games == queue[0].games]
    pool = tier if len(tier) >= 4 else window
    head, rest = pool[0], pool[1:]
    best = None
    for combo in combinations(range(len(rest)), 3):
        four = [head] + [rest[i] for i in combo]
        scores = [score(p, female_offset) for p in four]
        women = sum(p.gender == "F" for p in four)
        kind = 0 if women == 2 else 1 if women in (0, 4) else 2
        repeat = sum(1 for x, y in combinations(four, 2) if y.id in recent.get(x.id, ()))
        spread = max(scores) - min(scores)
        key = (spread + 2 * repeat, repeat, kind, sum(combo))  # 직전 멤버 1쌍 = 급수 2단계 벌점
        if best is None or key < best[0]:
            best = (key, four)
    return best[1]


def split_teams(four: list[Player], female_offset: float) -> tuple[tuple[Player, Player], tuple[Player, Player]]:
    mixed = sum(p.gender == "F" for p in four) == 2
    best = None
    for a, b in PAIRINGS:
        ta, tb = (four[a[0]], four[a[1]]), (four[b[0]], four[b[1]])
        if mixed and ta[0].gender == ta[1].gender:
            continue
        diff = abs(sum(score(p, female_offset) for p in ta) - sum(score(p, female_offset) for p in tb))
        if best is None or diff < best[0]:
            best = (diff, (ta, tb))
    return best[1]
