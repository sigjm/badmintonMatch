# Operator Audit Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give operators a chronological, filterable history of successful administrative changes.

**Architecture:** Add a dedicated append-only `audit_logs` table and a small audit module for writing and listing entries. Record changes at authenticated route boundaries in the same transaction as the operation; combine these entries with existing member `changes` rows in one read-only log page.

**Tech Stack:** Flask, SQLite, Jinja templates.

**Spec:** `docs/superpowers/specs/2026-10-22-meetup-management-and-audit-log-design.md` (operator log sections)

## Global Constraints

- "로그는 해당 변경과 같은 DB 트랜잭션 안에서 기록한다. 변경이 실패하면 성공 이력도 남기지 않는다."
- "현재 운영자 인증은 공용 계정이므로 행위자는 `운영자`로 기록한다."
- "PIN 원문·해시, 운영자 비밀번호, 세션값 및 기타 인증 비밀은 로그에 기록하지 않는다."
- "로그를 수정하거나 삭제하는 UI와 자동 보존기간은 제공하지 않는다."
- Existing member field-change history remains intact; all displayed times use Asia/Seoul.

## Review Focus

- Invalid or rejected POST operations do not create success entries.
- A no-op member/settings change does not create a misleading change entry.
- Meetup filters exclude global member changes that have no meetup association; unfiltered views still show them.
- Failed imports record neither success nor partial attendee changes.
- PIN reset entries identify the action without including a PIN or hash.

---

### Task 1: Audit Storage and Query Module

**Files:**
- Modify: `badminton/db.py`
- Create: `badminton/audit.py`

**Interfaces:**
- Produce `audit.record(conn, *, action, summary, entity_type, entity_id=None, meetup_id=None, details=None, at=None) -> int`.
- Produce `audit.list_entries(conn, *, page, page_size=50, date_from=None, date_to=None, action=None, meetup_id=None) -> tuple[list[dict], int]`.
- Store `details` as JSON and omit secrets; fixed actor value is `운영자`.
- Store `id`, `at`, `actor`, `action`, `entity_type`, nullable `entity_id`, nullable `meetup_id`, `summary`, and `details_json`; use epoch seconds for `at`.

- [ ] Create `audit_logs` and indexes idempotently during DB initialization.
- [ ] Implement parameterized insert/list queries and a fixed page size of 50.
- [ ] Merge legacy `changes` entries into unfiltered/date-filtered results without writing duplicate audit rows for changes already represented by `changes`.
- [ ] Keep legacy member-history queries working against the unchanged table.

### Task 2: Record Operator Mutations

**Files:**
- Modify: `badminton/admin.py`
- Modify: `badminton/groups.py` only if a group mutation needs a domain result for its summary.
- Integrate with lifecycle routes from `2026-10-22-meetup-lifecycle.md` and manual reservation route from `2026-10-22-manual-reservations.md`.

**Interfaces:**
- Call `audit.record` at admin POST boundaries; do not add logging to read routes or polling handlers.
- Use `changes` as the source for member field/name changes already recorded there; audit PIN reset and other operator actions separately.

- [ ] Record meetup create/import/paste/archive/restore with the affected meetup ID and operation summary.
- [ ] Record attendee/guest/status/memo changes, start/court changes, automatic matching, manual reservation, game end/redraw, and reservation redraw/delete.
- [ ] Record settings and group changes, and PIN reset without storing credentials or hashes.
- [ ] Wrap each successful state change and its audit insert in one transaction; emit no success log on validation failure.

### Task 3: Global Log Page

**Files:**
- Modify: `badminton/admin.py`
- Modify: `badminton/templates/admin_nav.html`
- Create: `badminton/templates/admin_logs.html`

**Interfaces:**
- Add authenticated `GET /admin/logs` backed by `audit.list_entries`.
- Preserve query parameters across pagination links.

- [ ] Add the log navigation link and a chronological read-only list.
- [ ] Add date range, action type, and meetup filters; render timestamps in Asia/Seoul.
- [ ] Display legacy member changes in the unified view and keep the existing member-specific history panel intact.
- [ ] Verify the screen exposes no edit/delete action and no secret values.

Implementation constraint: do not add or run tests unless the user asks for testing or verification.
