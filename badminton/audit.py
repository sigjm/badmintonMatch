"""Append-only operator audit entries and the unified history query."""
import json
import time
from datetime import date, datetime, time as datetime_time, timedelta
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
PAGE_SIZE = 50
SENSITIVE_KEY_PARTS = ("pin", "password", "secret", "token", "session", "credential", "authorization")


def _safe_value(value):
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()
                if not any(part in str(key).lower() for part in SENSITIVE_KEY_PARTS)}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def record(conn, *, action, summary, entity_type, entity_id=None, meetup_id=None, details=None, at=None) -> int:
    payload = json.dumps(_safe_value(details or {}), ensure_ascii=False, separators=(",", ":"))
    cursor = conn.execute(
        """INSERT INTO audit_logs(at, actor, action, entity_type, entity_id, meetup_id, summary, details_json)
           VALUES (?, '운영자', ?, ?, ?, ?, ?, ?)""",
        (time.time() if at is None else float(at), action, entity_type, entity_id, meetup_id, summary, payload))
    return cursor.lastrowid


def _epoch_bound(value: str, *, upper: bool = False) -> float:
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("로그 날짜를 확인해 주세요") from None
    bound = day + timedelta(days=1) if upper else day
    return datetime.combine(bound, datetime_time.min, SEOUL).timestamp()


def _legacy_summary(row) -> str:
    field_labels = {"nickname": "이름", "gender": "성별", "grade": "급수"}
    return (f"회원 {field_labels.get(row['field'], row['field'])} 변경: {row['nickname']} "
            f"{row['old'] or '없음'} → {row['new'] or '없음'}")


def list_entries(conn, *, page, page_size=PAGE_SIZE, date_from=None, date_to=None,
                 action=None, meetup_id=None) -> tuple[list[dict], int]:
    page = max(1, int(page))
    # Keep the interface fixed at 50 so pagination behavior and query cost stay predictable.
    page_size = PAGE_SIZE
    lower = _epoch_bound(date_from) if date_from else None
    upper = _epoch_bound(date_to, upper=True) if date_to else None

    audit_where, audit_args = [], []
    if lower is not None:
        audit_where.append("at >= ?")
        audit_args.append(lower)
    if upper is not None:
        audit_where.append("at < ?")
        audit_args.append(upper)
    if action and action != "member_change":
        audit_where.append("action = ?")
        audit_args.append(action)
    if action == "member_change":
        audit_where.append("1 = 0")
    if meetup_id is not None:
        audit_where.append("meetup_id = ?")
        audit_args.append(meetup_id)
    where_sql = " WHERE " + " AND ".join(audit_where) if audit_where else ""
    audit_total = conn.execute(f"SELECT count(*) FROM audit_logs{where_sql}", audit_args).fetchone()[0]

    changes_enabled = meetup_id is None and action in (None, "", "member_change")
    change_where, change_args = [], []
    if lower is not None:
        change_where.append("c.at >= ?")
        change_args.append(lower)
    if upper is not None:
        change_where.append("c.at < ?")
        change_args.append(upper)
    change_sql = " WHERE " + " AND ".join(change_where) if change_where else ""
    change_total = 0
    if changes_enabled:
        change_total = conn.execute(
            "SELECT count(*) FROM changes c JOIN members m ON m.id = c.member_id" + change_sql,
            change_args).fetchone()[0]

    total = audit_total + change_total
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    candidate_limit = page * page_size
    entries = []
    for row in conn.execute(
            "SELECT id, at, actor, action, entity_type, entity_id, meetup_id, summary, details_json "
            f"FROM audit_logs{where_sql} ORDER BY at DESC, id DESC LIMIT ?", (*audit_args, candidate_limit)):
        try:
            details = json.loads(row["details_json"] or "{}")
        except (TypeError, ValueError):
            details = {}
        entries.append({**dict(row), "details": details, "source": "audit"})

    # Legacy member changes have no meetup reference, so a meetup filter intentionally excludes them.
    if changes_enabled:
        for row in conn.execute(
                """SELECT c.id, c.at, c.by, c.field, c.old, c.new, c.member_id, m.nickname
                   FROM changes c JOIN members m ON m.id = c.member_id""" + change_sql +
                " ORDER BY c.at DESC, c.id DESC LIMIT ?", (*change_args, candidate_limit)):
            entries.append({
                "id": row["id"], "at": row["at"],
                "actor": "본인" if row["by"] == "self" else "운영자",
                "action": "member_change", "entity_type": "member", "entity_id": row["member_id"],
                "meetup_id": None, "summary": _legacy_summary(row),
                "details": {"field": row["field"], "old": row["old"], "new": row["new"]},
                "source": "changes",
            })

    entries.sort(key=lambda entry: (float(entry["at"] or 0), entry["source"], int(entry["id"])), reverse=True)
    for entry in entries:
        entry["when_kst"] = datetime.fromtimestamp(float(entry["at"] or 0), SEOUL).strftime("%Y-%m-%d %H:%M:%S")
    start = (page - 1) * page_size
    return entries[start:start + page_size], total
