"""대진판과 화면에 보여 줄 값 만들기."""

import math
import time

from ..announce import announcement_text
from ..db import get_setting
from .common import EXCLUDED_ORDER, STATUS_LABELS, _court_states, _playing, _reserved, _rest_until, queue, reservations


def _minutes_label(seconds: float) -> str:
    minutes = max(0, int(seconds // 60))
    return f"{minutes // 60}시간 {minutes % 60}분" if minutes >= 60 else f"{minutes}분"


def _clock_label(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def board(conn, meetup_id: int) -> dict:
    now = time.time()
    member_by_id = {r["id"]: {"id": r["id"], "nickname": r["nickname"], "gender": r["gender"], "grade": r["grade"],
                              "memo": r["memo"]} for r in conn.execute(
        "SELECT m.id, m.nickname, m.gender, m.grade, a.memo FROM members m JOIN attendees a ON a.member_id = m.id "
        "WHERE a.meetup_id = ?", (meetup_id,))}
    names = {member_id: member["nickname"] for member_id, member in member_by_id.items()}
    meetup = conn.execute("SELECT courts, started, started_at, ended_at FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
    courts = meetup["courts"]
    active = {r["court"]: r for r in conn.execute("SELECT * FROM games WHERE meetup_id = ? AND ended_at IS NULL",
                                                  (meetup_id,))}
    rest_until = _rest_until(conn, meetup_id)
    court_states = _court_states(conn, meetup_id)
    court_list = []
    for c in sorted(set(range(1, courts + 1)) | set(active)):  # 코트 수를 줄여도 진행 중인 경기는 보여 준다
        g = active.get(c)
        state = "game" if g else court_states.get(c, "game")
        court_list.append({"court": c, "state": state, "game_id": g["id"] if g else None,
                           "started_at": g["started_at"] if g else None,
                           "elapsed_clock": _clock_label(now - g["started_at"]) if g else None,
                           "ready_in": None if g or state != "game" or rest_until.get(c, 0) <= now else math.ceil(rest_until[c] - now),
                           "announce": announcement_text(c, [names.get(g[k], "") for k in ("a1", "a2", "b1", "b2")]) if g else None,
                           "teams": [[names.get(g["a1"], "?"), names.get(g["a2"], "?")],
                                     [names.get(g["b1"], "?"), names.get(g["b2"], "?")]] if g else None,
                           "team_players": [[member_by_id.get(g["a1"]), member_by_id.get(g["a2"])],
                                            [member_by_id.get(g["b1"]), member_by_id.get(g["b2"])]] if g else None})
    playing = _playing(conn, meetup_id)
    reserved = _reserved(conn, meetup_id)
    last_end = {}
    for g in conn.execute("SELECT a1, a2, b1, b2, ended_at FROM games WHERE meetup_id = ? AND ended_at IS NOT NULL "
                          "AND canceled = 0", (meetup_id,)):
        for pid in (g["a1"], g["a2"], g["b1"], g["b2"]):
            last_end[pid] = max(last_end.get(pid, 0), g["ended_at"])
    excluded = []
    for r in conn.execute("""SELECT m.id, m.nickname, m.gender, m.grade, a.status, a.games, a.memo FROM attendees a
                             JOIN members m ON m.id = a.member_id WHERE a.meetup_id = ?""", (meetup_id,)):
        if r["status"] == "canceled":
            continue
        if r["id"] in playing or r["id"] in reserved:
            continue
        if r["status"] != "present":
            state, reason = r["status"], STATUS_LABELS[r["status"]]
        elif r["grade"] is None:
            state, reason = "noinfo", "급수 미입력"
        elif r["gender"] is None:
            state, reason = "noinfo", "성별 미입력"
        else:
            continue
        excluded.append({"id": r["id"], "nickname": r["nickname"], "gender": r["gender"], "grade": r["grade"],
                         "games": r["games"], "memo": r["memo"], "state": state, "reason": reason})
    excluded.sort(key=lambda e: (EXCLUDED_ORDER[e["state"]], e["nickname"]))
    queued = queue(conn, meetup_id)
    queue_cards = []
    for order, player in enumerate(queued, 1):
        if player.id in last_end:
            since = last_end[player.id]
        elif not meetup["started"]:
            since = now
        elif meetup["started_at"] is not None:
            since = max(player.queued_at, meetup["started_at"])
        else:
            since = player.queued_at
        queue_cards.append({**member_by_id[player.id], "games": player.games, "order": order,
                            "played": player.id in last_end, "since": since, "since_label": _minutes_label(now - since)})
    resv_list = []
    for r in reservations(conn, meetup_id):
        player_ids = [r[key] for key in ("a1", "a2", "b1", "b2")]
        resv_list.append({
            "id": r["id"],
            "no": r["seq"],
            "source": r["source"],
            "waiting_for_game": bool(set(player_ids) & playing),
            "teams": [[names.get(r["a1"], "?"), names.get(r["a2"], "?")],
                      [names.get(r["b1"], "?"), names.get(r["b2"], "?")]],
            "team_players": [[member_by_id.get(r["a1"]), member_by_id.get(r["a2"])],
                             [member_by_id.get(r["b1"]), member_by_id.get(r["b2"])]],
        })
    return {"courts": court_list, "reservations": resv_list, "queue": [names[p.id] for p in queued],
            "queue_cards": queue_cards, "excluded": excluded,
            "auto_reserve": bool(int(get_setting(conn, "auto_reserve", 1))),
            "auto_fill": bool(int(get_setting(conn, "auto_fill", 1)))}
