# Gender-Based Player Name Colors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render male player names in blue, female player names in the existing magenta, and names with no gender in the default text color.

**Architecture:** Use shared `is-male` and `is-female` classes for server-rendered player names and the dynamic manual-match draft. Define the male color once and keep the live gender-edit behavior in sync with the classes.

**Tech Stack:** Jinja templates and existing CSS/JavaScript in `base.html`.

**Spec:** `docs/superpowers/specs/2026-10-22-meetup-management-and-audit-log-design.md` (player-name section)

## Global Constraints

- "남성 이름은 파란색, 여성 이름은 기존 자주색, 성별 미입력 이름은 기본 글자색을 사용한다."
- "색상은 대기열, 코트, 예약, 회원 화면 및 수동 대진 초안에 일관되게 적용한다."
- Color player names only; existing gender tags and grade badges keep their current styling.

## Review Focus

- Male names in all server-rendered list and board fragments receive the blue class.
- Female names retain the current magenta class and value.
- Missing gender removes both gender color classes and restores default text color.
- Changing gender inline updates the name color without a page reload.
- The dynamically rendered manual draft uses the same mapping as server-rendered names.

---

### Task 1: Shared Player Name Classes and Styles

**Files:**
- Modify: `badminton/templates/_macros.html`
- Modify: `badminton/templates/_player_card.html`
- Modify: `badminton/templates/base.html`

**Interfaces:**
- Use `.player-name.is-male` for blue and preserve `.player-name.is-female` for the current magenta.
- Apply the same gender classes to `draft-name` elements created by the manual-match JavaScript.

- [ ] Add a single male-name color token matching the existing blue tag color and style `.player-name.is-male`.
- [ ] Add the appropriate class in the shared player-name macro and attendee card template.
- [ ] Track gender in the manual draft model and apply `is-male` or `is-female` to each drafted name.
- [ ] Update inline member-info save handlers to remove the previous gender class and apply the new class without refreshing the page.
- [ ] Confirm public court, queue, reservation, and admin member views all use one of the shared render paths.

Implementation constraint: do not add or run tests unless the user asks for testing or verification.
