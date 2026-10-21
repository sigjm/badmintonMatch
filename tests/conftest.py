import pytest

from badminton import create_app, db
from badminton.members import create_member
from badminton.meetups import add_attendee, get_or_create_meetup, start, today
from badminton.somoim import Event, Group, SomoimMember

GROUP_ONE_EVENT = Group(
    events=[Event("e1", "금야민+게1", "2026-10-23", "22:00", "더콕", 21, 1)],
    members=[SomoimMember("a", "가", frozenset({1}), False), SomoimMember("b", "나", frozenset({1}), False),
             SomoimMember("c", "다", frozenset(), False)])


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


@pytest.fixture
def app(tmp_path):
    return create_app({"DATABASE": str(tmp_path / "app.db"), "ADMIN_PASSWORD": "pw", "SECRET_KEY": "t", "TESTING": True,
                       "SESSION_COOKIE_SECURE": False})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin(client):
    client.environ_base["HTTP_ORIGIN"] = "http://localhost"
    client.post("/admin/login", data={"password": "pw"})
    return client


@pytest.fixture
def conn_of(app):
    opened = []

    def _open():
        opened.append(db.connect(app.config["DATABASE"]))
        return opened[-1]

    yield _open
    for c in opened:
        c.close()


@pytest.fixture
def seeded_meetup(conn_of):
    from badminton.groups import list_groups
    c = conn_of()
    mid = get_or_create_meetup(c, today(), "t", None, group_id=list_groups(c)[0]["id"])
    for nick, g, grade in (("m1", "M", "B"), ("m2", "M", "C"), ("f1", "F", "B"), ("f2", "F", "C")):
        add_attendee(c, mid, create_member(c, nick, gender=g, grade=grade), "paste")
    start(c, mid, 1)
    return mid, c.execute("SELECT id FROM games WHERE ended_at IS NULL").fetchone()[0]
