"""설정 화면과 자동 예약·자동 배정 스위치 라우트."""

from flask import flash, redirect, render_template, request, url_for

from .. import audit, get_conn, meetups
from ..db import get_setting, set_setting, transaction
from ..meetups import refill_upcoming
from . import _active_meetups, _current_group, admin_required, bp


@bp.post("/settings/auto-reserve")
@admin_required
def toggle_auto_reserve():
    conn = get_conn()
    raw_val = request.form.get("value")
    current_enabled = bool(int(get_setting(conn, "auto_reserve", 1)))
    if raw_val is not None:
        try:
            new_enabled = bool(int(raw_val))
        except ValueError:
            new_enabled = not current_enabled
    else:
        new_enabled = not current_enabled

    mid = request.form.get("mid", type=int)

    if new_enabled != current_enabled:
        with transaction(conn):
            set_setting(conn, "auto_reserve", 1.0 if new_enabled else 0.0)
            if new_enabled:
                conn.execute("UPDATE meetups SET resv_hold = 0 WHERE started = 1 AND archived_at IS NULL")
            for row in conn.execute("SELECT id FROM meetups WHERE started = 1 AND archived_at IS NULL").fetchall():
                meetups.refill_reservations(conn, row["id"])
            audit.record(
                conn,
                action="auto_reserve",
                summary="자동 예약 켬" if new_enabled else "자동 예약 끔",
                entity_type="settings",
                meetup_id=mid,
                details={"auto_reserve": {"from": int(current_enabled), "to": int(new_enabled)}}
            )
        flash("자동 예약을 켰습니다" if new_enabled else "자동 예약을 껐습니다")

    if mid:
        return redirect(url_for("admin.meetup", mid=mid))
    return redirect(request.referrer or url_for("admin.home"))


@bp.post("/settings/auto-fill")
@admin_required
def toggle_auto_fill():
    conn = get_conn()
    raw_val = request.form.get("value")
    current_enabled = bool(int(get_setting(conn, "auto_fill", 1)))
    if raw_val is not None:
        try:
            new_enabled = bool(int(raw_val))
        except ValueError:
            new_enabled = not current_enabled
    else:
        new_enabled = not current_enabled

    mid = request.form.get("mid", type=int)

    if new_enabled != current_enabled:
        with transaction(conn):
            set_setting(conn, "auto_fill", 1.0 if new_enabled else 0.0)
            if new_enabled:
                for row in conn.execute("SELECT id FROM meetups WHERE started = 1 AND archived_at IS NULL").fetchall():
                    meetups.fill_courts(conn, row["id"], force=False)
            audit.record(
                conn,
                action="auto_fill",
                summary="자동 배정 켬" if new_enabled else "자동 배정 끔",
                entity_type="settings",
                meetup_id=mid,
                details={"auto_fill": {"from": int(current_enabled), "to": int(new_enabled)}}
            )
        flash("자동 배정을 켰습니다" if new_enabled else "자동 배정을 껐습니다")

    if mid:
        return redirect(url_for("admin.meetup", mid=mid))
    return redirect(request.referrer or url_for("admin.home"))


@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    conn = get_conn()
    if request.method == "POST":
        try:
            offset, mix_ratio = float(request.form["female_offset"]), int(request.form["mix_ratio"])
        except (KeyError, ValueError):
            offset, mix_ratio = -1, -1
        if 0 <= offset <= 5 and 0 <= mix_ratio <= 100:
            old_offset, old_ratio = get_setting(conn, "female_offset"), int(get_setting(conn, "mix_ratio"))
            with transaction(conn):
                if old_offset != offset or old_ratio != mix_ratio:
                    set_setting(conn, "female_offset", offset)
                    set_setting(conn, "mix_ratio", mix_ratio)
                    refill_upcoming(conn)
                    audit.record(conn, action="settings_change", summary="매칭 설정 변경",
                                 entity_type="settings", details={
                                     "female_offset": {"from": old_offset, "to": offset},
                                     "mix_ratio": {"from": old_ratio, "to": mix_ratio},
                                 })
            flash("설정을 저장했습니다")
        else:
            flash("여자 보정값은 0~5, 혼복 비율은 0~100 사이로 입력해 주세요")
        return redirect(url_for("admin.settings"))
    return render_template("admin_settings.html", female_offset=get_setting(conn, "female_offset"),
                           mix_ratio=int(get_setting(conn, "mix_ratio")),
                           meetups=_active_meetups(conn, _current_group()))
