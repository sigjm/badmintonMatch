import os
import threading

from flask import Flask, current_app, g, request

from . import db
from .matching import GRADE_LABELS
from .meetups import STATUS_LABELS

DEFAULT_GROUP_ID = "9b34753d-4105-4c55-9310-bb923ba2a8e61"  # 소모임 "금요일 야간민턴"
DEFAULT_GROUP_NAME = "금요일 야간민턴"


def get_conn():
    if "conn" not in g:
        g.conn = db.connect(current_app.config["DATABASE"])
    return g.conn


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("DATABASE", "/data/badminton.db"),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD", ""),
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        SOMOIM_GROUP_ID=os.environ.get("SOMOIM_GROUP_ID", DEFAULT_GROUP_ID),
        SESSION_COOKIE_SAMESITE="Lax",  # 다른 사이트에서 보낸 POST에 운영진 세션이 실리지 않게
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "1") == "1",  # 로컬 http 개발 때만 0
    )
    app.config.update(config or {})
    if not app.testing:
        for name in ("ADMIN_PASSWORD", "SECRET_KEY"):
            value = app.config.get(name)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError(f"{name} must be configured with a non-empty value")

    # gunicorn 스레드 4개로 받되 DB를 만지는 요청은 예전처럼 한 번에 하나씩 처리한다 (경기 끝 동시 클릭·코트 채우기 경쟁 방지).
    # 안내 방송 음성 합성(2초 남짓)만 이 잠금 밖에서 돌아 대진판 3초 갱신을 막지 않는다.
    app.serial_lock = threading.Lock()

    @app.before_request
    def one_request_at_a_time():
        if not (request.endpoint or "").startswith("public.announce"):
            app.serial_lock.acquire()
            g.holds_serial_lock = True

    @app.teardown_request
    def release_serial_lock(_exc):
        if g.pop("holds_serial_lock", False):
            app.serial_lock.release()

    @app.teardown_appcontext
    def close_conn(_exc):
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    app.jinja_env.globals.update(GRADE_LABELS=GRADE_LABELS, STATUS_LABELS=STATUS_LABELS, GENDER_LABELS={"M": "남", "F": "여"})

    from .groups import ensure_default_group
    conn = db.connect(app.config["DATABASE"])
    ensure_default_group(conn, app.config["SOMOIM_GROUP_ID"], DEFAULT_GROUP_NAME)
    conn.close()

    from .admin import bp as admin_bp
    from .public import bp as public_bp
    app.register_blueprint(admin_bp)
    app.register_blueprint(public_bp)
    return app
