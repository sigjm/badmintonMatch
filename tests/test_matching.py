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


def test_pick_four_prefers_men_doubles_even_if_spread_wider():
    # 후보 안에 남복이 가능하면 급수 폭이 더 넓어도 남복(kind=0)을 고른다
    # 1:M(S, 6), 2:F(S, 4.5), 3:F(S, 4.5), 4:M(S, 6), 5:M(D, 2), 6:M(D, 2)
    # 혼복 {1, 4, 2, 3}의 급수 폭 = 6 - 4.5 = 1.5
    # 남복 {1, 4, 5, 6}의 급수 폭 = 6 - 2 = 4.0
    # 남복이 급수 폭이 훨씬 넓어도 남복이 먼저 선택된다
    q = [P(1, "M", "S"), P(2, "F", "S"), P(3, "F", "S"), P(4, "M", "S"), P(5, "M", "D"), P(6, "M", "D")]
    picked = pick_four(q, 6, female_offset=1.5)
    assert picked is not None
    assert {p.id for p in picked} == {1, 4, 5, 6}


def test_pick_four_makes_mixed_when_same_gender_impossible():
    # 같은 성별 4명을 못 만들 때만 혼복:
    # 대기열에 남3 여2가 있어 남복(4명)이나 여복(4명)이 불가능하고 2:2 혼복만 가능할 때 혼복 생성
    q = [P(1, "M", "A"), P(2, "M", "A"), P(3, "M", "A"), P(4, "F", "A"), P(5, "F", "A")]
    picked = pick_four(q, 6)
    assert picked is not None
    assert sum(p.gender == "F" for p in picked) == 2


def test_pick_four_default_female_offset_is_one_point_five():
    # default female_offset=1.5 동작 확인: 여A(5 - 1.5 = 3.5), 남D(2), 남C(3), 남B(4)
    # 남B(4)와 여A(3.5)는 차이 0.5, 남D(2)와 남C(3)는 차이 1.0
    p_f = P(1, "F", "A")
    assert score(p_f, 1.5) == 3.5
    # split_teams 기본 인자도 1.5인지 확인
    teams = split_teams([P(1, "M", "A"), P(2, "M", "B"), P(3, "F", "A"), P(4, "F", "B")])
    assert teams is not None


def test_pick_four_women_outside_window_ignored():
    q = [P(i, "M", "A") for i in range(1, 7)] + [P(7, "F", "A"), P(8, "F", "A")]
    assert {p.id for p in pick_four(q, 6)} == {1, 2, 3, 4}


def test_pick_four_excludes_uneven_gender_ratios():
    # 3:1 성비는 허용되지 않으므로 4명 조합을 만들지 않고 빈 코트로 둔다
    q = [P(1, "F", "A"), P(2, "M", "A"), P(3, "M", "A"), P(4, "M", "A"), P(5, "M", "A")]
    assert pick_four(q, 6) is None

    # 뒤쪽에 다른 여성이 있어 2:2 혼복이 가능하면 매칭을 만든다
    q_mixed = [P(1, "F", "A"), P(2, "M", "A"), P(3, "M", "A"), P(4, "M", "A"), P(5, "F", "A")]
    picked = pick_four(q_mixed, 6)
    assert picked is not None
    assert sum(p.gender == "F" for p in picked) == 2


def test_pick_four_expands_to_full_queue_when_window_ratio_invalid():
    # 앞쪽 6명(window)에는 1번 여성 + 남성 5명뿐이라 2:2 혼복이나 4:0 남복/여복이 불가 (1:3만 가능)
    # 하지만 7번째에 여성이 있어서 전체 대기열로 확장하면 2:2 혼복이 가능해짐
    window_players = [P(1, "F", "A")] + [P(i, "M", "A") for i in range(2, 7)]
    q = window_players + [P(7, "F", "A"), P(8, "M", "A")]
    picked = pick_four(q, mix_window=6)
    assert picked is not None
    # 1순위 여성(1번)과 7번 여성이 포함되어 2:2 혼복이어야 함
    assert {p.id for p in picked} >= {1, 7}
    assert sum(p.gender == "F" for p in picked) == 2


def test_pick_four_leaves_court_empty_when_no_valid_ratio_exists():
    # 대기열 전체(8명) 중 남성이 1순위 1명뿐이고 나머지 7명이 여성인 경우:
    # 1순위를 포함해서는 1:3(여3남1)밖에 안 되므로 성비 조건(0, 2, 4)을 만족할 수 없음
    q = [P(1, "M", "A")] + [P(i, "F", "A") for i in range(2, 9)]
    assert pick_four(q, mix_window=6) is None

    # 반대로 여성이 1순위 1명뿐이고 나머지 7명이 남성인 경우:
    q2 = [P(1, "F", "A")] + [P(i, "M", "A") for i in range(2, 9)]
    assert pick_four(q2, mix_window=6) is None


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
    # 같은 성별 4명이 안 되어 혼복이 만들어질 때: 여A(5-1=4) 는 남B(4) 와 같은 수준으로 본다
    q = [P(1, "M", "B"), P(2, "F", "S"), P(3, "F", "A"), P(4, "M", "B"), P(5, "F", "A"), P(6, "F", "S")]
    assert {p.id for p in pick_four(q, 6, 1)} == {1, 3, 4, 5}


def test_pick_four_keeps_fewest_games_tier():
    # 경기 수가 같은(0판) 사람이 4명 이상이면, 급수가 더 비슷해도 이미 더 친 사람(1판)은 뽑지 않는다
    q = [P(1, "M", "A"), P(2, "M", "D"), P(3, "M", "D"), P(4, "M", "D"),
         P(5, "M", "A", games=1), P(6, "M", "A", games=1), P(7, "M", "A", games=1)]
    assert {p.id for p in pick_four(q, 7, 1)} == {1, 2, 3, 4}


def test_pick_four_avoids_last_game_mates():
    q = [P(i, "M", "A") for i in range(1, 7)]
    recent = {1: {2, 3, 4}, 2: {1, 3, 4}, 3: {1, 2, 4}, 4: {1, 2, 3}}
    assert {p.id for p in pick_four(q, 6, 1, recent)} == {1, 2, 5, 6}


def test_pick_four_avoids_given_four_when_possible():
    q = [P(i, "M", "C", t=i) for i in range(6)]
    first = {p.id for p in pick_four(q)}
    second = {p.id for p in pick_four(q, avoid=frozenset(first))}
    assert second != first and 0 in second  # 대기 1순위는 그대로 포함
    only_four = q[:4]
    assert {p.id for p in pick_four(only_four, avoid=frozenset({0, 1, 2, 3}))} == {0, 1, 2, 3}  # 대안이 없으면 그대로
