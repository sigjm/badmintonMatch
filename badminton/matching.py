from dataclasses import dataclass
from itertools import combinations

GRADE_POINTS = {"S": 6, "A": 5, "B": 4, "C": 3, "D": 2, "N": 1}
GRADE_LABELS = {"S": "S", "A": "A", "B": "B", "C": "C", "D": "D", "N": "초심"}
MIX_WINDOW = 6

# 4명을 두 팀으로 나누는 3가지 경우 (인덱스)
PAIRINGS = [((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2))]


@dataclass(frozen=True)
class Player:
    id: int
    gender: str  # "M" | "F"
    grade: str
    games: int
    queued_at: float


def score(p: Player, female_offset: float = 1.5) -> float:
    return GRADE_POINTS[p.grade] - (female_offset if p.gender == "F" else 0)


def sort_queue(players: list[Player]) -> list[Player]:
    return sorted(players, key=lambda p: (p.games, p.queued_at, p.id))


def pick_four(queue: list[Player], mix_window: int = MIX_WINDOW, female_offset: float = 1.5,
              recent: dict[int, set[int]] | None = None, avoid: frozenset[int] | None = None,
              prefer_mixed: bool = False) -> list[Player] | None:
    """대기열 맨 앞 사람을 포함해 급수가 비슷한 4명을 고른다.
    기본 후보는 앞쪽 mix_window명과 최소 경기 수 그룹이며, 성비 조건을 만족하지 못할 때만 전체 대기열로 넓힌다.
    recent(사람 → 직전 경기에서 같이 친 사람들)가 주어지면 그 사람들끼리 다시 묶이는 쌍이 적은 조합을 먼저 고른다.
    (prefer_mixed=False면 남복/여복 우선, True면 혼복 우선) → (급수 폭 + 직전 멤버 겹친 쌍 × 2) 최소 → 겹침 적은 쪽 → 대기 순서 앞쪽 순.
    avoid(다시 뽑기 직전의 4명)와 똑같은 조합은 다른 조합이 하나라도 있으면 고르지 않는다."""
    recent = recent or {}
    if len(queue) < 4:
        return None
    window = queue[:max(mix_window, 4)]
    tier = [p for p in window if p.games == queue[0].games]
    pool = tier if len(tier) >= 4 else window

    def best_balanced(candidates: list[Player]):
        head, rest = candidates[0], candidates[1:]
        best = None
        for combo in combinations(range(len(rest)), 3):
            four = [head] + [rest[i] for i in combo]
            women = sum(p.gender == "F" for p in four)
            # 허용 성비는 혼복 2:2 또는 같은 성별 복식이다. 1:3, 3:1은 만들지 않는다.
            if women not in (0, 2, 4):
                continue
            if avoid and frozenset(p.id for p in four) == avoid:
                continue
            scores = [score(p, female_offset) for p in four]
            repeat = sum(1 for x, y in combinations(four, 2) if y.id in recent.get(x.id, ()))
            spread = max(scores) - min(scores)
            kind = (0 if women == 2 else 1) if prefer_mixed else (1 if women == 2 else 0)
            key = (kind, spread + 2 * repeat, repeat, sum(combo))
            if best is None or key < best[0]:
                best = (key, four)
        return best

    best = best_balanced(pool)
    if best is None and pool != queue:
        # 대기 1순위는 유지하면서 성비가 가능한 조합을 전체 대기열에서 찾는다.
        best = best_balanced(queue)
    if best is None and avoid:
        return pick_four(queue, mix_window, female_offset, recent, prefer_mixed=prefer_mixed)  # 다른 조합이 없으면 그대로
    return best[1] if best else None


def split_teams(four: list[Player], female_offset: float = 1.5) -> tuple[tuple[Player, Player], tuple[Player, Player]]:
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
