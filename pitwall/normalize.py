"""Turn raw iRacing variables into the protocol messages in docs/PROTOCOL.md.

Provenance rule (the whole point of this project):
    * Anything under a plain key is copied straight from an iRacing variable.
    * Anything under a "derived" key was computed here. The UI must label it.
    * Tire wear / tread values are copied as-is but flagged "snapshot" in the
      protocol docs because the sim only refreshes them at pit stops.

No module above this one is allowed to compute racing math from raw variables;
if a number needs deriving, it happens here under "derived" so it is auditable
in one place.
"""
from __future__ import annotations

# The code lives in pitwall/norm/ (one module per topic; the Deriver is put together from
# pitwall/norm/d_*.py). Everything is imported back here, so `from pitwall import normalize`
# and `normalize.X` work as before. Change a constant where it is defined, not here.
#
#   norm/common.py     enum decoders, incident table, small readers
#   norm/session.py    the session message and the starting grid
#   norm/gaps.py       gaps between cars, battle pressure
#   norm/race.py       flags, race length, strength of field, results
#   norm/fuel.py       laps left in a timed race, fuel plan, theoretical best
#   norm/deriver.py    the Deriver (history), from d_base, d_laps, d_pits, d_traffic, d_events,
#                      d_incidents, d_finish, d_tv, d_strategy, d_weather
#   norm/state.py      the state and cars messages, RefView

from .norm.common import (
    CAMERA_STATE_BITS, CAR_LEFT_RIGHT, ENGINE_WARNING_BITS, FLAG_BITS, INCIDENT_POINTS, LIVE_TOLERANCE_S,
    PIT_SV_BITS, SCHEMA_VERSION, SESSION_STATE, SKIES, TRACK_SURFACE, TRACK_WETNESS, _arr, _compound, _num,
    decode_bits, incident_causes, live_edge, surface_kind,
)  # noqa: F401
from .norm.session import (
    _DRIVER_KEYS, _camera_groups, _grid, _grid_derived, _qual_rows, build_session, grid_per_row,
)  # noqa: F401
from .norm.gaps import (
    PRESSURE_ATTACK, PRESSURE_GAP_S, PRESSURE_RATE_S, _dist, _parse_laps, _parse_secs, battle_pressure,
    f2_is_real, gap_between, live_gaps, live_order, ref_gap, run_gaps,
)  # noqa: F401
from .norm.race import (
    SOF_K, _is_race, _sof, hold_cars, race_flags, race_laps_left, race_length, results_positions,
    strength_of_field,
)  # noqa: F401
from .norm.fuel import (
    THEO_SLACK_S, TIMED_PACE_MARGIN, fuel_plan, fuel_save, theoretical_best, timed_laps_left, vs_you,
)  # noqa: F401
from .norm.deriver import Deriver  # noqa: F401
from .norm.state import (
    REF_CAR_FIELDS, RefView, _DC_VARS, _delta, build_cars, build_state, relative_to, track_length_m,
)  # noqa: F401
