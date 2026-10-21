import os

from flask import Flask, current_app, g

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
