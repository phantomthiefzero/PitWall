"""The Deriver, put together from one part per topic (d_*.py)."""
from __future__ import annotations

from .d_base import Base
from .d_laps import Laps
from .d_pits import Pits
from .d_traffic import Traffic
from .d_events import Events
from .d_incidents import Incidents
from .d_finish import Finish
from .d_tv import RaceTV
from .d_strategy import Strategy
from .d_weather import Weather


class Deriver(Weather, Strategy, RaceTV, Finish, Incidents, Events, Traffic, Pits, Laps, Base):
    """Keeps the little bit of history needed for derived values."""
