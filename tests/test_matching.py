from badminton.matching import Player, pick_four, score, sort_queue, split_teams


def P(id, g, grade, games=0, t=0):
    return Player(id, g, grade, games, t)


def test_score_female_offset():
    assert score(P(1, "F", "A"), 1) == 4
    assert score(P(2, "M", "A"), 1) == 5


def test_sort_queue_games_then_wait():
    q = sort_queue([P(1, "M", "A", games=2, t=1), P(2, "M", "A", games=1, t=9), P(3, "M", "A", games=1, t=5)])
    assert [p.id for p in q] == [3, 2, 1]


def test_pick_four_none_when_short():
    assert pick_four([P(i, "M", "A") for i in range(3)]) is None


def test_pick_four_prefers_mixed_within_window():
    q = [P(1, "M", "A"), P(2, "M", "A"), P(3, "M", "A"), P(4, "F", "A"), P(5, "F", "A"), P(6, "M", "A")]
    assert {p.id for p in pick_four(q, 6)} == {1, 2, 4, 5}


def test_pick_four_women_outside_window_ignored():
    q = [P(i, "M", "A") for i in range(1, 7)] + [P(7, "F", "A"), P(8, "F", "A")]
    assert {p.id for p in pick_four(q, 6)} == {1, 2, 3, 4}


def test_pick_four_single_woman_takes_head():
    q = [P(1, "F", "A"), P(2, "M", "A"), P(3, "M", "A"), P(4, "M", "A"), P(5, "M", "A")]
    assert {p.id for p in pick_four(q, 6)} == {1, 2, 3, 4}


def test_split_mixed_only_and_balanced():
    ms, md, fa, fc = P(1, "M", "S"), P(2, "M", "D"), P(3, "F", "A"), P(4, "F", "C")
    teams = split_teams([ms, md, fa, fc], 1)
    assert {frozenset(p.id for p in t) for t in teams} == {frozenset({1, 4}), frozenset({2, 3})}


def test_split_same_gender_balanced():
    teams = split_teams([P(1, "M", "S"), P(2, "M", "A"), P(3, "M", "C"), P(4, "M", "D")], 1)
    assert {frozenset(p.id for p in t) for t in teams} == {frozenset({1, 4}), frozenset({2, 3})}
