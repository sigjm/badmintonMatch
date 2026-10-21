"""운영진이 고를 수 있는 소모임 모임 목록."""
import re

GID_RE = re.compile(r"(?:somoim\.co\.kr/)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12,13})")


def parse_gid(text: str) -> str:
    """소모임 모임 링크나 모임 ID에서 모임 ID를 뽑는다."""
    text = (text or "").strip()
    m = GID_RE.search(text)
    if not m or ("://" in text and "somoim.co.kr" not in text):
        raise ValueError("소모임 모임 링크를 붙여넣어 주세요 (예: https://www.somoim.co.kr/...)")
    return m.group(1)


def add_group(conn, gid: str, name: str) -> int:
    row = conn.execute("SELECT id FROM groups WHERE gid = ?", (gid,)).fetchone()
    if row:
        conn.execute("UPDATE groups SET name = ? WHERE id = ?", (name, row["id"]))
        return row["id"]
    return conn.execute("INSERT INTO groups(gid, name) VALUES (?, ?)", (gid, name)).lastrowid


def ensure_default_group(conn, gid: str, name: str) -> int:
    """처음 실행할 때 기본 모임을 만들고, 모임이 없던 시절의 정모를 그 모임에 붙인다."""
    row = conn.execute("SELECT id FROM groups WHERE gid = ?", (gid,)).fetchone()
    group_id = row["id"] if row else add_group(conn, gid, name)
    conn.execute("UPDATE meetups SET group_id = ? WHERE group_id IS NULL", (group_id,))
    return group_id


def list_groups(conn) -> list:
    return conn.execute("SELECT * FROM groups ORDER BY id").fetchall()


def get_group(conn, group_id: int | None):
    return conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone() if group_id else None
