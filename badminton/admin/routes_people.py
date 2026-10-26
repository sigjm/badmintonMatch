"""회원·모임·운영 로그 화면 라우트."""

from flask import abort, flash, redirect, render_template, request, session, url_for

from .. import audit, get_conn
from ..db import transaction
from ..groups import add_group, get_group, list_groups, parse_gid
from ..meetups import on_info_changed
from ..members import changes, list_members, reset_pin, set_info
from ..somoim import SomoimError, fetch_group, search_groups
from . import AUDIT_ACTION_LABELS, _blank_none, _current_group, admin_required, bp


@bp.get("/members")
@admin_required
def members():
    conn = get_conn()
    group = _current_group()
    return render_template("admin_members.html", groups=list_groups(conn), back="members",
                           members=list_members(conn, group_id=group["id"]),
                           changes=changes(conn, limit=100, group_id=group["id"]))


@bp.post("/members/<int:member_id>")
@admin_required
def member_update(member_id):
    conn = get_conn()
    if conn.execute("SELECT 1 FROM members WHERE id = ?", (member_id,)).fetchone() is None:
        abort(404)
    try:
        with transaction(conn):
            changed = set_info(conn, member_id, _blank_none(request.form.get("gender")),
                               _blank_none(request.form.get("grade")), by="admin")
            if changed:
                on_info_changed(conn, member_id, changed)
    except ValueError as e:
        flash(str(e))
    return redirect(url_for("admin.members"))


@bp.post("/members/<int:member_id>/reset-pin")
@admin_required
def member_reset_pin(member_id):
    conn = get_conn()
    with transaction(conn):
        row = conn.execute("SELECT pin_hash, nickname FROM members WHERE id = ?", (member_id,)).fetchone()
        if row is None:
            abort(404)
        if row["pin_hash"] is not None:
            reset_pin(conn, member_id)
            audit.record(conn, action="pin_reset", summary=f"회원 PIN 초기화: {row['nickname']}",
                         entity_type="member", entity_id=member_id)
    flash("PIN을 초기화했습니다")
    return redirect(url_for("admin.members"))


@bp.route("/groups", methods=["GET", "POST"])
@admin_required
def groups():
    conn = get_conn()
    if request.method == "POST":
        try:
            gid = parse_gid(request.form.get("link", ""))
        except ValueError as e:
            flash(str(e))
            return redirect(url_for("admin.groups"))
        name = request.form.get("name", "").strip()
        if not name:
            try:
                name = fetch_group(gid).name
            except SomoimError as e:
                flash(f"모임 정보를 가져오지 못했습니다 ({e}). 모임 이름을 직접 입력해 주세요.")
                return redirect(url_for("admin.groups"))
        with transaction(conn):
            existing = conn.execute("SELECT id, name FROM groups WHERE gid = ?", (gid,)).fetchone()
            group_id = add_group(conn, gid, name or gid)
            if existing is None:
                audit.record(conn, action="group_add", summary=f"모임 추가: {name or gid}",
                             entity_type="group", entity_id=group_id, details={"name": name or gid})
            elif existing["name"] != (name or gid):
                audit.record(conn, action="group_rename", summary=f"모임 이름 변경: {name or gid}",
                             entity_type="group", entity_id=group_id,
                             details={"from": existing["name"], "to": name or gid})
        session["group_id"] = group_id
        flash(f"{name or gid} 모임을 추가했습니다")
        return redirect(url_for("admin.home"))
    q, results, search_error = request.args.get("q", "").strip(), [], None
    if q:
        try:
            results = search_groups(q)
        except SomoimError:
            search_error = "소모임에서 검색하지 못했습니다. 잠시 후 다시 시도하거나 링크를 붙여넣어 주세요."
    known = {g["gid"] for g in list_groups(conn)}
    return render_template("admin_groups.html", groups=list_groups(conn), q=q, known=known,
                           results=[r for r in results if r[0] not in known], search_error=search_error)


@bp.post("/groups/select")
@admin_required
def select_group():
    g = get_group(get_conn(), request.form.get("group_id", type=int))
    if g:
        session["group_id"] = g["id"]
    return redirect(url_for("admin.members") if request.form.get("back") == "members" else url_for("admin.home"))


@bp.get("/logs")
@admin_required
def logs():
    conn = get_conn()
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    action = request.args.get("action", "")
    meetup_id = request.args.get("meetup_id", type=int)
    page = max(1, request.args.get("page", 1, type=int) or 1)
    error = None
    try:
        entries, total = audit.list_entries(conn, page=page, date_from=date_from or None,
                                            date_to=date_to or None, action=action or None,
                                            meetup_id=meetup_id)
    except ValueError as e:
        entries, total, error = [], 0, str(e)
    pages = max(1, (total + 49) // 50)
    if page > pages:
        page = pages
        if error is None:
            entries, total = audit.list_entries(conn, page=page, date_from=date_from or None,
                                                date_to=date_to or None, action=action or None,
                                                meetup_id=meetup_id)
    meetup_rows = conn.execute("SELECT id, date, name, archived_at FROM meetups ORDER BY date DESC, id DESC").fetchall()
    return render_template("admin_logs.html", entries=entries, total=total, page=page, pages=pages,
                           date_from=date_from, date_to=date_to, action=action, meetup_id=meetup_id,
                           meetups=meetup_rows, action_labels=AUDIT_ACTION_LABELS, error=error)
