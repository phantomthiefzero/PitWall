"""The Deriver's state."""
from __future__ import annotations



class Base:
    def __init__(self):
        self._last_lap_completed = None
        self._fuel_at_lap_start = None
        self._fuel_per_lap_samples: list[float] = []
        self._gap_hist: dict[int, list[tuple[float, float]]] = {}
        self._gap_ahead: dict[int, int | None] = {}
        self._gap_slope: dict[int, float | None] = {}
        self._tp_slope: dict[int, float | None] = {}
        self._pit: dict[int, dict] = {}
        self._stint: dict[int, dict] = {}
        self._sect: dict[int, dict] = {}
        self._sect_best: list[float | None] = []
        # booth
        self._lapst: dict[int, dict] = {}
        self._laps: dict[int, list[dict]] = {}
        # Per-car running figures, updated as each lap is finished: pace and
        # consistency read the last few racing laps, top speed and laps led the
        # whole race. They were recomputed from the lap list for every car at
        # every update -- which grew with the race -- and that list keeps only
        # 200 laps, so laps led undercounted past lap 200.
        self._lapsum: dict[int, dict] = {}
        # Observed trends (weather, your engine) and the session's peak g. They
        # were computed in the dashboard from what each screen had watched since
        # it connected; here they cover the session, for everyone alike.
        self._trend: dict[str, list] = {"weather": [], "engine": []}
        self._trend_at: dict[str, float | None] = {"weather": None, "engine": None}
        self._tod: list = []                    # (session time, time of day): the sim clock's speed
        self._outlook = None                    # (t, last outlook)
        self.gg_peak = 0.0
        self._lap_finished: list[dict] = []
        self._loss: list[dict] = []
        self._stops_tick: dict[int, dict] = {}
        self._tp_hist: dict[int, list[tuple[float, float]]] = {}
        self._tp_ref: dict[int, int | None] = {}
        self._uc: dict[int, dict] = {}
        self._uc_inpit: dict[int, bool] = {}
        self._player_lap = None
        self._player_idx = None
        # The car the SHARED stream measures everything relative from: your own
        # car when driving, else the camera car when spectating. A viewer who
        # wants another car gets it through a RefView, for them alone.
        self.ref_idx: int | None = None
        self.ref_source: str | None = None
        self._last_lap_seq = 0
        self._spd: dict[int, dict] = {}
        self._cmp: dict[int, dict] = {}
        self._drv: dict[int, dict] = {}
        self._rf_samples: list[tuple[float, float]] = []
        self._rf_rates: list[float] = []
        self._adj_base: dict | None = None
        self._adj_pend: dict[str, dict] = {}
        self.tank_l: float | None = None
        # your car's fuel plan as of the last state update, for the strategy options
        self._fuel_info: dict | None = None
        self._caut = {"t0": None}
        self._fuel_by_lap: dict[int, dict] = {}
        self._fuel_lap_pit = False
        self._fuel_partial = False
        self._inc_by_lap: dict[int, int] = {}
        self._inc_lap = None                    # (lapCompleted, incidents at its start)
        self._near_hist: dict[int, list[tuple[float, float]]] = {}
        self.incident_limit: int | None = None
        self.feed_seq = 0
        self.feed_laps: list[dict] = []
        self.feed_events: list[dict] = []
        # False while a replay is being watched: per-car changes read from the past
        # are not news (field_events is skipped then; this covers the per-car ones)
        self.events_live = True
        self._dist_prev: dict[int, float] = {}
        self._prof: dict = {}                      # class -> lap profile bins (learn_lap_profile)
        self._split: dict = {}                     # idx -> this lap's stretch crossings, and its best lap's (lap_splits)
        self._meet: dict = {}                      # smoothed meetings for the shared reference
        self._ev = {"pit": {}, "best": {}, "bestSeeded": False, "closing": {}, "stable": {}, "surf": {},
                    "flags": None, "flagAt": {}, "inc": None, "warn": None, "tow": None,
                    "wet": None, "rain": None, "declaredWet": None}
