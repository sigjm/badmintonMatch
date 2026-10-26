"""정모 도메인 로직. 기능별 모듈로 나눴지만 `from badminton.meetups import X`와 `meetups.X`는 예전 그대로 쓸 수 있다.

의존 방향: common ← scheduling ← attendance·lifecycle, board는 common만 쓴다 (순환 없음).
COURT_REST_SECONDS처럼 값을 바꿔 쓰는 상수는 읽는 쪽(common)에서 바꿔야 코드에 닿는다: `meetups.common.COURT_REST_SECONDS`.
"""
from .common import (STATUS_LABELS, COURT_STATE_LABELS, EXCLUDED_ORDER, SEOUL, ACTIVE, COURT_REST_SECONDS, MEMO_MAX,
                     ArchivedMeetupError, session_day, today, _rest_until, _court_states, _playing, _reserved,
                     reservations, queue, _recent_mates)  # noqa: F401
from .scheduling import (get_court_state, _drop_reservations_with, _renumber_reservations, _prefer_mixed,
                         refill_reservations, add_manual_reservation, reservation_waiting_for_game, update_reservation,
                         prioritize_reservation, delete_reservation, redraw_reservation, _record_game_assignment,
                         _reservation_state, start_reservation_now, fill_courts, set_court_state, _game, end_game,
                         redraw, refill_upcoming)  # noqa: F401
from .attendance import (add_attendee, sync_somoim, add_pasted, set_status, set_memo, manual_candidates,
                         playing_candidates, on_info_changed)  # noqa: F401
from .lifecycle import get_or_create_meetup, archive_meetup, restore_meetup, create_manual_meetup, start, end_meetup  # noqa: F401
from .board import _minutes_label, _clock_label, board  # noqa: F401
