from dataclasses import dataclass

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


def pick_four(queue: list[Player], mix_window: int = 6) -> list[Player] | None:
    if len(queue) < 4:
        return None
    window = queue[:max(mix_window, 4)]
    men = [p for p in window if p.gender == "M"]
    women = [p for p in window if p.gender == "F"]
    if len(men) >= 2 and len(women) >= 2:
        return men[:2] + women[:2]
    return queue[:4]


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
