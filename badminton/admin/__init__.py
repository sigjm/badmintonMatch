"""운영 콘솔(/admin) 블루프린트: 로그인 제한, Origin 검사, 공용 도우미. 화면별 라우트는 routes_*.py에 있다."""

import hmac
import ipaddress
import time
from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for

from .. import get_conn
from ..groups import get_group, list_groups


bp = Blueprint("admin", __name__, url_prefix="/admin")
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
_login_failures: dict[str, tuple[float, int]] = {}
AUDIT_ACTION_LABELS = {
    "meetup_create": "정모 추가", "meetup_import": "정모 불러오기", "attendees_paste": "참석자 붙여넣기",
    "meetup_archive": "정모 보관", "meetup_restore": "정모 복구", "attendee_status": "참석 상태 변경",
    "attendee_memo": "참석자 메모 변경", "attendee_guest_add": "게스트 추가", "attendee_member_add": "회원 참석 추가", "meetup_start": "코트 시작",
    "meetup_end": "정모 종료", "courts_change": "코트 수 변경", "court_state": "코트 상태 변경", "auto_match": "자동 매칭",
    "auto_reserve": "자동 예약 설정", "auto_fill": "자동 배정 설정",
    "manual_reservation": "수동 예약",
    "game_assigned": "경기 배정",
    "game_end": "게임 종료", "game_redraw": "경기 다시 뽑기", "reservation_redraw": "예약 다시 뽑기",
    "reservation_edit": "예약 수정", "reservation_priority": "예약 우선 배정",
    "reservation_start_now": "예약 바로 시작", "reservation_delete": "예약 삭제",
    "settings_change": "매칭 설정 변경", "pin_reset": "PIN 초기화",
    "group_add": "모임 추가", "group_rename": "모임 이름 변경", "member_change": "회원 정보 변경",
}


def _login_client_key() -> str:
    forwarded_ip = request.headers.get("CF-Connecting-IP", "").strip()
    remote_ip = request.remote_addr or ""
    for candidate in (forwarded_ip, remote_ip):
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return "unknown"


def _login_is_limited(client_key: str, now: float) -> bool:
    for key, (started, _) in list(_login_failures.items()):
        if now - started >= LOGIN_WINDOW_SECONDS:
            del _login_failures[key]
    attempt = _login_failures.get(client_key)
    return bool(attempt and attempt[1] >= LOGIN_MAX_FAILURES)


def _record_login_failure(client_key: str, now: float) -> None:
    attempt = _login_failures.get(client_key)
    if not attempt or now - attempt[0] >= LOGIN_WINDOW_SECONDS:
        _login_failures[client_key] = (now, 1)
    else:
        _login_failures[client_key] = (attempt[0], attempt[1] + 1)


@bp.before_request
def reject_cross_origin_mutations():
    if request.method != "POST" or request.endpoint == "admin.login" or not session.get("admin"):
        return
    source = request.headers.get("Origin") or request.referrer
    if not source:
        abort(403)
    try:
        source_origin = urlsplit(source)
    except ValueError:
        abort(403)
    expected_scheme = "https" if current_app.config["SESSION_COOKIE_SECURE"] else request.scheme
    if source_origin.scheme != expected_scheme or source_origin.netloc.lower() != request.host.lower():
        abort(403)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)
    return wrapped


def _blank_none(value):
    return value or None


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        client_key = _login_client_key()
        now = time.monotonic()
        if _login_is_limited(client_key, now):
            return render_template("admin_login.html", error="로그인 시도가 많습니다. 15분 후 다시 시도해 주세요."), 429
        expected = current_app.config["ADMIN_PASSWORD"]
        if expected and hmac.compare_digest(request.form.get("password", ""), expected):
            _login_failures.pop(client_key, None)
            session["admin"] = True
            return redirect(url_for("admin.home"))
        _record_login_failure(client_key, now)
        error = "비밀번호가 맞지 않습니다"
    return render_template("admin_login.html", error=error)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


def _current_group():
    """운영진이 고른 모임 (세션). 고른 적 없으면 첫 모임."""
    conn = get_conn()
    return get_group(conn, session.get("group_id")) or list_groups(conn)[0]


@bp.context_processor
def inject_group():
    return {"group": _current_group()} if session.get("admin") else {}


def _active_meetups(conn, group):
    return conn.execute("SELECT * FROM meetups WHERE group_id = ? AND archived_at IS NULL "
                        "ORDER BY date DESC, id DESC LIMIT 10", (group["id"],)).fetchall()


def _meetup_row(mid: int):
    row = get_conn().execute("SELECT * FROM meetups WHERE id = ?", (mid,)).fetchone()
    if row is None:
        abort(404)
    return row


def _active_meetup_row(mid: int):
    row = _meetup_row(mid)
    if row["archived_at"] is not None:
        abort(409, description="보관된 정모는 변경할 수 없습니다")
    return row


from . import routes_meetups, routes_play, routes_settings, routes_people  # noqa: E402,F401  (라우트를 같은 bp에 등록)
