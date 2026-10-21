# Manual Match Reservations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put operator-selected manual matches into the ordered reservation queue instead of assigning them immediately to an empty court.

**Architecture:** Mark reservation rows as automatic or manual. Append manual rows after existing reservations, protect them from the automatic reservation cap, and let the existing sequence-based court fill consume them in order.

**Tech Stack:** Flask, SQLite, Jinja templates, existing inline-save JavaScript.

**Spec:** `docs/superpowers/specs/2026-10-22-meetup-management-and-audit-log-design.md` (manual reservation section)

## Global Constraints

- "수동 대진에서 고른 네 명은 코트에 즉시 배정하지 않고 예약 목록에 추가한다."
- "새 수동 예약은 현재 예약의 순서를 유지하면서 맨 뒤에 추가한다."
- "자동 예약 수가 코트 수에 도달했더라도 운영자가 지정한 수동 예약은 유지한다."
- A reserved or playing player cannot be reserved a second time.
- Existing automatic reservations and their order remain unchanged when a manual reservation is added.
- Manual selection and consumption require current attendance, not gender or grade; automatic reservations retain the existing eligibility rules.

## Review Focus

- Add a manual reservation when automatic reservations already equal the court count.
- A manual reservation remains after refill, redraw, and court-count changes until consumed or explicitly removed.
- Selected IDs that are duplicated, absent, not present, playing, or already reserved are rejected without partial writes; missing gender/grade alone is allowed.
- Consuming reservations uses sequence order and does not skip an earlier manual row.
- Empty-court manual assignment paths are removed from both the central picker and court cards.

---

### Task 1: Reservation Source and Domain Rules

**Files:**
- Modify: `badminton/db.py`
- Modify: `badminton/meetups.py`

**Interfaces:**
- Add `reservations.source TEXT NOT NULL DEFAULT 'auto'` with allowed values `auto` and `manual`; existing rows migrate as `auto`.
- Produce `add_manual_reservation(conn, meetup_id, player_ids) -> int`, returning the new reservation ID or raising `ValueError` for invalid selections.
- Preserve the existing `reservations(conn, meetup_id)` sequence ordering and `fill_courts` consumption contract.

- [ ] Add the idempotent `source` column migration.
- [ ] Update `refill_reservations` to cap only automatic reservations, never delete manual rows because `seq > courts`, and append new automatic rows after all existing rows.
- [ ] Make newly generated automatic rows use `source='auto'`.
- [ ] Implement manual validation and append at `MAX(seq)+1` with `source='manual'`.
- [ ] Include current-present, non-playing, non-reserved attendees in `manual_candidates` even when gender/grade is missing; show those selectable attendees from the excluded/no-info area.
- [ ] Reject duplicate, absent, non-present, playing, or already reserved IDs at the domain boundary, while allowing missing gender/grade.
- [ ] When consuming a manual reservation, require all four attendees to remain present and not playing, but skip automatic gender/grade/sex-balance validation; retain those checks for automatic reservations.
- [ ] Ensure `_reserved` includes both sources and sequence renumbering remains stable after consume/delete/redraw.

### Task 2: Replace Immediate Assignment UI and Route

**Files:**
- Modify: `badminton/admin.py`
- Modify: `badminton/templates/admin_meetup.html`
- Modify: `badminton/templates/court_list.html`
- Modify: `badminton/templates/base.html`
- Modify: `badminton/templates/reservation_list.html` only if needed to render the new reservation status.

**Interfaces:**
- Replace the `manual_match` call to `assign_manual` with `add_manual_reservation`.
- Remove court-targeted manual assignment controls; the manual form has one action: `예약에 추가`.

- [ ] Remove the `빈 코트에 넣기` action and court-specific `이 코트에 배정` buttons.
- [ ] Update the manual endpoint to create a reservation without requiring a court number.
- [ ] Keep inline save and partial board/queue/reservation refresh; update success-message handling to `수동 예약을 추가했습니다`.
- [ ] Ensure selected players leave the queue immediately and appear once in the reservation list.
- [ ] Surface validation errors without losing the manual draft or causing a full page reload.

Implementation constraint: do not add or run tests unless the user asks for testing or verification.
