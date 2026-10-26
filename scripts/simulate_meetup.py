#!/usr/bin/env python3
"""배드민턴 정모 매칭 시뮬레이터 (사본 DB 기반 안전 실행)

원본 DB 파일을 직접 변경하지 않고 tempfile 사본에서만 시뮬레이션을 실행하여
대기 시간, 게임 수 분포, 급수 균형 등을 검증합니다.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
from io import StringIO
import os
from pathlib import Path
import random
import shutil
import sys
import tempfile
from typing import TextIO

# 프로젝트 루트를 sys.path에 추가하여 badminton 패키지를 import할 수 있게 함
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from badminton import db  # noqa: E402
from badminton.matching import GRADE_LABELS, GRADE_POINTS  # noqa: E402
from badminton import meetups  # noqa: E402
from badminton.meetups import end_game, start  # noqa: E402
meetups.COURT_REST_SECONDS = 0  # 가상 시계로 돌리므로 실제 15초 코트 쉬는 시간은 끈다
from badminton.members import set_info  # noqa: E402


def compute_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def clock(m: float) -> str:
    h, mm = divmod(22 * 60 + int(m), 60)
    return f"{h % 24:02d}:{mm:02d}"


def _simulate_core(
    conn,
    meetup_id: int,
    courts: int,
    minutes: int,
    seed: int,
    random_grades: bool,
    out: TextIO,
    mix_ratio: int | None = None,
) -> dict:
    if mix_ratio is not None:
        db.set_setting(conn, "mix_ratio", mix_ratio)
    rnd = random.Random(seed)
    ids = [r[0] for r in conn.execute("SELECT member_id FROM attendees WHERE meetup_id = ?", (meetup_id,))]

    if random_grades:
        for mid in ids:
            row = conn.execute("SELECT gender, grade FROM members WHERE id = ?", (mid,)).fetchone()
            g, gr = (row["gender"] if row else None), (row["grade"] if row else None)
            new_g = g
            new_gr = gr
            if not g:
                new_g = rnd.choices("MF", weights=[6, 4])[0]
            if not gr:
                new_gr = rnd.choices("SABCDN", weights=[2, 8, 20, 30, 25, 15])[0]
            if new_g != g or new_gr != gr:
                set_info(conn, mid, new_g, new_gr, by="admin")

    info = {r[0]: (r[1], r[2], r[3]) for r in conn.execute("SELECT id, nickname, gender, grade FROM members")}

    def tag(i: int) -> str:
        n, g, gr = info[i]
        g_str = "남" if g == "M" else ("여" if g == "F" else "?")
        gr_str = GRADE_LABELS.get(gr, gr or "")
        return f"{n}({g_str}{gr_str})"

    now = 0.0
    ends: dict[int, float] = {}  # game_id -> 종료 시각(분)
    free_since = {i: 0.0 for i in ids}
    waits: dict[int, list[float]] = defaultdict(list)
    games_played: Counter[int] = Counter()
    log: list[tuple[float, int, str, list[int], float, float]] = []

    def record_new_games():
        for g in conn.execute(
            "SELECT * FROM games WHERE meetup_id = ? AND ended_at IS NULL ORDER BY id", (meetup_id,)
        ):
            gid = g["id"]
            if gid in ends:
                continue
            ends[gid] = now + rnd.uniform(10, 16)
            four = [g["a1"], g["a2"], g["b1"], g["b2"]]
            for i in four:
                waits[i].append(now - free_since[i])
            pts = [GRADE_POINTS[info[i][2]] for i in four]
            women = sum(info[i][1] == "F" for i in four)
            kind = {2: "혼복", 0: "남복", 4: "여복"}.get(women, f"남{4 - women}여{women}")
            log.append((now, g["court"], kind, four, sum(pts) / 4, max(pts) - min(pts)))

    start(conn, meetup_id, courts)
    record_new_games()

    while ends:
        gid = min(ends, key=lambda k: (ends[k], k))
        now = ends.pop(gid)
        g = conn.execute("SELECT * FROM games WHERE id = ?", (gid,)).fetchone()
        for i in (g["a1"], g["a2"], g["b1"], g["b2"]):
            free_since[i] = now
            games_played[i] += 1
        if now < minutes:
            end_game(conn, gid)
            record_new_games()
        else:
            conn.execute("UPDATE games SET ended_at = 1 WHERE id = ?", (gid,))

    men_cnt = sum(info[i][1] == "M" for i in ids)
    women_cnt = sum(info[i][1] == "F" for i in ids)

    print(
        f"참석 {len(ids)}명 (남 {men_cnt} · 여 {women_cnt}) · 코트 {courts}면 · "
        f"{clock(0)}~{clock(minutes)} · 경기 {len(log)}판 · 급수점수 S6 A5 B4 C3 D2 초심1",
        file=out,
    )
    print(file=out)
    print("| # | 시작 | 코트 | 종류 | 팀 A | 팀 B | 급수 평균 | 급수 폭 |", file=out)
    print("|---|---|---|---|---|---|---|---|", file=out)
    for n, (t, court, kind, four, avg, spread) in enumerate(log, 1):
        team_a = f"{tag(four[0])} · {tag(four[1])}"
        team_b = f"{tag(four[2])} · {tag(four[3])}"
        print(f"| {n} | {clock(t)} | {court} | {kind} | {team_a} | {team_b} | {avg:.2f} | {spread} |", file=out)

    print(file=out)
    print("| 이름 | 급수 | 경기 | 대기 합계 | 1회 평균 대기 | 최대 대기 |", file=out)
    print("|---|---|---|---|---|---|", file=out)
    for i in sorted(ids, key=lambda i: -sum(waits[i])):
        w = waits[i]
        w_sum = sum(w)
        w_avg = w_sum / len(w) if w else 0.0
        w_max = max(w) if w else 0.0
        g_char = "남" if info[i][1] == "M" else ("여" if info[i][1] == "F" else "?")
        gr_char = GRADE_LABELS.get(info[i][2], info[i][2] or "")
        print(
            f"| {info[i][0]} | {g_char}{gr_char} | {games_played[i]} | "
            f"{w_sum:.0f}분 | {w_avg:.1f}분 | {w_max:.0f}분 |",
            file=out,
        )

    allw = [x for i in ids for x in waits[i][1:]]
    avg_allw = sum(allw) / len(allw) if allw else 0.0
    max_allw = max(allw) if allw else 0.0
    min_gp = min((games_played[i] for i in ids), default=0)
    max_gp = max((games_played[i] for i in ids), default=0)

    print(file=out)
    print(f"경기 사이 평균 대기 {avg_allw:.1f}분 · 최대 {max_allw:.0f}분 · 1인당 경기 {min_gp}~{max_gp}판", file=out)

    avg_pts = sum(a for *_, a, _s in log) / len(log) if log else 0.0
    avg_spread = sum(s for *_, s in log) / len(log) if log else 0.0
    max_spread = max((s for *_, s in log), default=0.0)
    print(f"한 경기 급수 평균의 평균 {avg_pts:.2f} · 급수 폭 평균 {avg_spread:.2f} · 최대 {int(max_spread) if max_spread.is_integer() else max_spread}", file=out)

    kinds_dict = dict(Counter(k for _t, _c, k, *_ in log))
    mixed_cnt = kinds_dict.get("혼복", 0)
    mixed_pct = (mixed_cnt / len(log) * 100) if log else 0.0
    print(f"경기 종류: {kinds_dict} · 혼복 비율 {mixed_cnt}/{len(log)} ({mixed_pct:.1f}%)", file=out)

    same4 = Counter(frozenset(four) for _t, _c, _k, four, *_ in log)
    max_same4 = max(same4.values()) if same4 else 0
    print(f"같은 4명 조합 최다 반복 {max_same4}회 · 서로 다른 조합 {len(same4)}/{len(log)}", file=out)

    return {
        "attendees": len(ids),
        "games": len(log),
        "avg_wait": avg_allw,
        "max_wait": max_allw,
        "min_games": min_gp,
        "max_games": max_gp,
        "avg_points": avg_pts,
        "avg_spread": avg_spread,
        "max_spread": max_spread,
        "kinds": kinds_dict,
        "mixed_games": mixed_cnt,
        "mixed_ratio": mixed_pct,
        "max_same4": max_same4,
        "unique_combinations": len(same4),
    }


def simulate(
    db_path: str | Path,
    meetup_id: int = 1,
    courts: int = 3,
    minutes: int = 180,
    seed: int = 42,
    random_grades: bool = False,
    out: TextIO | None = None,
    mix_ratio: int | None = None,
) -> dict:
    if out is None:
        out = sys.stdout

    orig_path = Path(db_path).resolve()
    if not orig_path.exists():
        raise FileNotFoundError(f"DB 파일을 찾을 수 없습니다: {orig_path}")

    orig_stat = orig_path.stat()
    orig_mtime_ns = orig_stat.st_mtime_ns
    orig_hash = compute_file_hash(orig_path)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        shutil.copy2(orig_path, tmp_path)
        conn = db.connect(str(tmp_path))
        try:
            res = _simulate_core(
                conn,
                meetup_id=meetup_id,
                courts=courts,
                minutes=minutes,
                seed=seed,
                random_grades=random_grades,
                out=out,
                mix_ratio=mix_ratio,
            )
        finally:
            conn.close()
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    # 원본 파일 불변 검증
    after_stat = orig_path.stat()
    after_mtime_ns = after_stat.st_mtime_ns
    after_hash = compute_file_hash(orig_path)

    if orig_mtime_ns != after_mtime_ns:
        raise RuntimeError(f"원본 DB mtime이 변경되었습니다! (이전: {orig_mtime_ns}, 이후: {after_mtime_ns})")
    if orig_hash != after_hash:
        raise RuntimeError(f"원본 DB 해시가 변경되었습니다! (이전: {orig_hash}, 이후: {after_hash})")

    return res


def main():
    parser = argparse.ArgumentParser(description="배드민턴 정모 매칭 시뮬레이터")
    parser.add_argument("db_path", help="원본 SQLite DB 파일 경로")
    parser.add_argument("--meetup", type=int, default=1, help="정모 ID (기본: 1)")
    parser.add_argument("--courts", type=int, default=3, help="코트 수 (기본: 3)")
    parser.add_argument("--minutes", type=int, default=180, help="시뮬레이션 시간(분) (기본: 180)")
    parser.add_argument("--seed", type=int, default=42, help="랜덤 시드 (기본: 42)")
    parser.add_argument("--random-grades", action="store_true", help="급수/성별이 빈 참석자에게 랜덤 값 채움")
    parser.add_argument("--mix-ratio", type=int, default=None, help="혼복 목표 비율 %% (0~100)")

    args = parser.parse_args()

    simulate(
        db_path=args.db_path,
        meetup_id=args.meetup,
        courts=args.courts,
        minutes=args.minutes,
        seed=args.seed,
        random_grades=args.random_grades,
        mix_ratio=args.mix_ratio,
    )


if __name__ == "__main__":
    main()
