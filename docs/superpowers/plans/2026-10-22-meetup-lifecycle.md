# Meetup Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let operators create, archive, and restore meetups without deleting their attendance or match history.

**Architecture:** Store a nullable archive timestamp on each meetup. Add authenticated POST actions and admin views for active and archived meetups, then exclude archived meetups from public selection and automatic refill.

**Tech Stack:** Flask, SQLite, Jinja templates.

**Spec:** `docs/superpowers/specs/2026-10-22-meetup-management-and-audit-log-design.md` (meetup sections)

## Global Constraints

- "삭제는 데이터를 영구 제거하지 않고 보관 처리한다."
- "보관할 때 관련 참석자, 경기, 예약 데이터는 유지한다."
- "보관된 정모는 공개 대진판과 활성 정모 목록에 나타나지 않고, 자동 매칭·예약 채우기 대상에서도 제외한다."
- "동일한 소모임 이벤트 ID를 다시 불러오려는 경우 중복 생성을 피하고, 운영자에게 보관 정모를 먼저 복구하라고 안내한다."
- Existing records remain active after migration; no server deployment or manual production DB change.

## Review Focus

- Duplicate manual creation for the same active group/date/name returns the existing meetup.
- A same-date/name archived meetup is never silently reactivated or duplicated without a clear operator path.
- Importing an archived meetup by `eid` does not create a duplicate or expose it publicly.
- Archiving a started meetup hides it from public live-board selection while preserving all child rows.
- Direct POST requests cannot mutate a meetup after it has been archived.

---

### Task 1: Archive State and Domain Operations

**Files:**
- Modify: `badminton/db.py`
- Modify: `badminton/meetups.py`

**Interfaces:**
- Produce `archive_meetup(conn, meetup_id) -> bool` and `restore_meetup(conn, meetup_id) -> bool`; return `False` when the meetup does not exist.
- Produce `create_manual_meetup(conn, date, name, group_id) -> tuple[int, bool]`, returning the meetup ID and whether a new row was created; an archived date/name match raises `ArchivedMeetupError` instead of silently restoring or duplicating it.
- Keep `get_or_create_meetup` idempotent for active imported events; report an archived `eid` to the route as a distinct conflict.

- [ ] Add an idempotent `meetups.archived_at` column migration in `connect`; existing rows remain active.
- [ ] Implement archive/restore domain helpers without deleting attendees, games, or reservations.
- [ ] Implement manual creation with trimmed required name, date and group validation, and active duplicate reuse.
- [ ] Reject an archived same-group/date/name match with `ArchivedMeetupError`; expose a restore link/message from the route.
- [ ] Make archived `eid` conflicts distinguishable so the import route can direct the operator to restore the existing meetup.

### Task 2: Admin Lifecycle Actions and Screens

**Files:**
- Modify: `badminton/admin.py`
- Modify: `badminton/templates/admin_home.html`
- Modify: `badminton/templates/admin_meetup.html`

**Interfaces:**
- Add authenticated POST actions for manual creation, archive, and restore.
- Keep `_meetup_row(mid)` for existence lookup and add an active-meetup guard for operational POST routes.

- [ ] Add the date/name create form scoped to `_current_group()` and redirect to the resulting meetup.
- [ ] Show active and archived meetups in separate sections on the admin home.
- [ ] Add confirmed archive actions to each active row and the active meetup detail page.
- [ ] Add restore actions to archived rows and show archived meetup details as read-only.
- [ ] Reject operational POSTs for archived meetups while allowing restore; preserve existing same-origin checks.
- [ ] Do not auto-fill courts or attach live-update polling to an archived detail page.

### Task 3: Active Query Boundaries

**Files:**
- Modify: `badminton/admin.py`
- Modify: `badminton/meetups.py`
- Modify: `badminton/public.py`

**Interfaces:**
- Active home queries, `_started(conn, group_id)`, and `refill_upcoming(conn)` must exclude rows with `archived_at IS NOT NULL`.

- [ ] Filter archived rows from recent active meetups, public current-meetup selection, and upcoming refill.
- [ ] Handle attempts to import an archived `eid` by showing the restore instruction without changing archived state.
- [ ] Ensure archive/restore redirects and live-board selection reflect the new state immediately.

Implementation constraint: do not add or run tests unless the user asks for testing or verification.
