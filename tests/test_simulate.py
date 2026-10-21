import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from badminton import db
from badminton.meetups import add_attendee, get_or_create_meetup
from badminton.members import create_member
from scripts.simulate_meetup import compute_file_hash, simulate


def test_simulate_with_set_members_keeps_db_intact(tmp_path):
    """급수/성별이 있는 참석자 10명으로 시뮬레이션 실행 후 요약 값 검증 및 원본 DB 불변 확인."""
    db_file = tmp_path / "orig.db"
    conn = db.connect(str(db_file))
    mid = get_or_create_meetup(conn, "2026-10-23", "테스트정모", None)

    # 참석자 10명 (남 5명, 여 5명)
    players = [
        ("철수", "M", "A"),
        ("영희", "F", "A"),
        ("민수", "M", "B"),
        ("지영", "F", "B"),
        ("동현", "M", "C"),
        ("수진", "F", "C"),
        ("준호", "M", "D"),
        ("혜진", "F", "D"),
        ("상우", "M", "N"),
        ("유진", "F", "N"),
    ]
    for nick, gender, grade in players:
        mem_id = create_member(conn, nick, gender=gender, grade=grade)
        add_attendee(conn, mid, mem_id, "paste")
    conn.close()

    orig_mtime = db_file.stat().st_mtime_ns
    orig_hash = compute_file_hash(db_file)

    res = simulate(
        db_path=db_file,
        meetup_id=mid,
        courts=2,
        minutes=60,
        seed=42,
        random_grades=False,
    )

    # 요약 결과 검증
    assert res["attendees"] == 10
    assert res["games"] > 0
    assert res["avg_wait"] >= 0
    assert res["max_wait"] >= 0
    assert res["min_games"] >= 0
    assert res["max_games"] >= res["min_games"]
    assert "혼복" in res["kinds"] or "남복" in res["kinds"] or "여복" in res["kinds"]

    # 원본 파일 무결성 검증
    assert db_file.stat().st_mtime_ns == orig_mtime
    assert compute_file_hash(db_file) == orig_hash

    # 원본 DB 내용 불변 검증 (게임이 생성되지 않았어야 함)
    chk_conn = db.connect(str(db_file))
    game_cnt = chk_conn.execute("SELECT count(*) FROM games WHERE meetup_id = ?", (mid,)).fetchone()[0]
    chk_conn.close()
    assert game_cnt == 0


def test_simulate_cli_random_grades_and_intact(tmp_path):
    """급수/성별이 비어있는 참석자 8명에 대해 CLI로 --random-grades 실행 후 검증."""
    db_file = tmp_path / "blank.db"
    conn = db.connect(str(db_file))
    mid = get_or_create_meetup(conn, "2026-10-23", "빈회원정모", None)

    # 급수/성별 없는 참석자 8명
    for i in range(1, 9):
        mem_id = create_member(conn, f"미입력{i}", gender=None, grade=None)
        add_attendee(conn, mid, mem_id, "paste")
    conn.close()

    orig_mtime = db_file.stat().st_mtime_ns
    orig_hash = compute_file_hash(db_file)

    script_path = Path(__file__).resolve().parent.parent / "scripts" / "simulate_meetup.py"
    cmd = [
        sys.executable,
        str(script_path),
        str(db_file),
        "--meetup",
        str(mid),
        "--courts",
        "2",
        "--minutes",
        "60",
        "--seed",
        "42",
        "--random-grades",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)

    # CLI 출력 표 및 요약 검증
    output = proc.stdout
    assert "참석 8명" in output
    assert "코트 2면" in output
    assert "| # | 시작 | 코트 | 종류 | 팀 A | 팀 B | 급수 평균 | 급수 폭 |" in output
    assert "| 이름 | 급수 | 경기 | 대기 합계 | 1회 평균 대기 | 최대 대기 |" in output
    assert "경기 사이 평균 대기" in output
    assert "경기 종류:" in output
    assert "같은 4명 조합 최다 반복" in output

    # 원본 파일 무결성 검증
    assert db_file.stat().st_mtime_ns == orig_mtime
    assert compute_file_hash(db_file) == orig_hash

    # 원본 DB 회원의 급수/성별이 여전히 NULL인지 확인
    chk_conn = db.connect(str(db_file))
    filled_cnt = chk_conn.execute(
        "SELECT count(*) FROM members WHERE gender IS NOT NULL OR grade IS NOT NULL"
    ).fetchone()[0]
    game_cnt = chk_conn.execute("SELECT count(*) FROM games WHERE meetup_id = ?", (mid,)).fetchone()[0]
    chk_conn.close()
    assert filled_cnt == 0
    assert game_cnt == 0
