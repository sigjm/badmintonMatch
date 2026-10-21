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


def test_pick_four_prefers_mixed_when_levels_tie():
    # 여S(6-1=5) 와 남A(5) 는 같은 수준 → 급수 폭이 같으면 혼복을 고른다
    q = [P(1, "M", "A"), P(2, "M", "A"), P(3, "M", "A"), P(4, "F", "S"), P(5, "F", "S"), P(6, "M", "A")]
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


def test_pick_four_groups_similar_grades_with_head():
    q = [P(1, "M", "A"), P(2, "M", "D"), P(3, "M", "A"), P(4, "M", "D"), P(5, "M", "A"), P(6, "M", "B")]
    assert {p.id for p in pick_four(q, 6, 1)} == {1, 3, 5, 6}


def test_pick_four_grade_similarity_beats_mixed():
    q = [P(1, "M", "S"), P(2, "F", "D"), P(3, "F", "D"), P(4, "M", "S"), P(5, "M", "S"), P(6, "M", "A")]
    assert {p.id for p in pick_four(q, 6, 1)} == {1, 4, 5, 6}


def test_pick_four_uses_female_offset_for_similarity():
    # 여A(5-1=4) 는 남B(4) 와 같은 수준으로 본다
    q = [P(1, "M", "B"), P(2, "M", "S"), P(3, "F", "A"), P(4, "M", "B"), P(5, "F", "A"), P(6, "M", "S")]
    assert {p.id for p in pick_four(q, 6, 1)} == {1, 3, 4, 5}


def test_pick_four_keeps_fewest_games_tier():
    # 경기 수가 같은(0판) 사람이 4명 이상이면, 급수가 더 비슷해도 이미 더 친 사람(1판)은 뽑지 않는다
    q = [P(1, "M", "A"), P(2, "M", "D"), P(3, "M", "D"), P(4, "M", "D"),
         P(5, "M", "A", games=1), P(6, "M", "A", games=1), P(7, "M", "A", games=1)]
    assert {p.id for p in pick_four(q, 7, 1)} == {1, 2, 3, 4}
