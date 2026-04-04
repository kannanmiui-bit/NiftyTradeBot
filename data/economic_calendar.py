"""
data/economic_calendar.py — High-impact economic event calendar for India markets.

Events covered:
  - RBI MPC policy announcements (intraday ~10 AM IST)
  - US Fed FOMC decisions (after hours → impacts next-day open)
  - India Union Budget
  - Nifty F&O expiry (weekly = every Thursday, monthly = last Thursday)

Usage:
    cal = EventCalendar()
    cal.is_skip_day(date(2026, 2, 7))       # True — RBI day, skip all trades
    cal.is_blackout(datetime(...))           # True — within blackout window
    cal.get_event_info(date(2026, 2, 7))    # "RBI MPC Announcement"
"""

from datetime import date, datetime, time as dtime, timedelta
from typing import Optional
import calendar as cal_module


# ── RBI MPC Announcement dates (day of announcement, ~10:00 AM IST) ──────────
RBI_MPC_DATES = {
    date(2025, 4, 9),
    date(2025, 6, 6),
    date(2025, 8, 8),
    date(2025, 10, 9),
    date(2025, 12, 6),
    date(2026, 2, 7),
    date(2026, 4, 9),
    date(2026, 6, 6),
}

# ── US Fed FOMC decision dates (after Indian market hours → impacts next day) ─
US_FED_DATES = {
    date(2025, 9, 18),
    date(2025, 11, 7),
    date(2025, 12, 18),
    date(2026, 1, 29),
    date(2026, 3, 19),
    date(2026, 5, 7),
}

# ── India Union Budget ─────────────────────────────────────────────────────────
BUDGET_DATES = {
    date(2026, 2, 1),
    date(2025, 2, 1),
}

# ── India CPI release (usually after market hours — impacts next day open) ────
INDIA_CPI_DATES = {
    date(2025, 10, 14), date(2025, 11, 12), date(2025, 12, 12),
    date(2026, 1, 13),  date(2026, 2, 12),  date(2026, 3, 12),
}


def _last_thursday_of_month(year: int, month: int) -> date:
    """Return the last Thursday of the given month (monthly F&O expiry)."""
    last_day = cal_module.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != 3:  # 3 = Thursday
        d -= timedelta(days=1)
    return d


def _all_thursdays(start: date, end: date):
    """Yield every Thursday between start and end (inclusive)."""
    d = start + timedelta(days=(3 - start.weekday()) % 7)
    while d <= end:
        yield d
        d += timedelta(weeks=1)


class EventCalendar:
    def __init__(
        self,
        skip_rbi: bool = True,
        skip_budget: bool = True,
        skip_fed_next_day: bool = True,   # skip first 45 min after US Fed decision
        skip_expiry_after: Optional[str] = "13:00",  # None = don't skip expiry
    ):
        self.skip_rbi = skip_rbi
        self.skip_budget = skip_budget
        self.skip_fed_next_day = skip_fed_next_day
        self.skip_expiry_after = (
            dtime(*map(int, skip_expiry_after.split(":")))
            if skip_expiry_after else None
        )

        # Pre-build set of next-day-after-Fed dates
        self._fed_next_days = {d + timedelta(days=1) for d in US_FED_DATES}
        # Pre-build set of next-day-after-CPI dates
        self._cpi_next_days = {d + timedelta(days=1) for d in INDIA_CPI_DATES}

    # ── Public API ────────────────────────────────────────────────────────────

    def is_skip_day(self, d: date) -> bool:
        """Returns True if NO trades should be taken the entire day."""
        if self.skip_rbi and d in RBI_MPC_DATES:
            return True
        if self.skip_budget and d in BUDGET_DATES:
            return True
        return False

    def is_blackout(self, dt: datetime) -> bool:
        """
        Returns True if trading should be paused at this specific datetime.
        Covers:
          - Full skip days (RBI, Budget)
          - First 45 min after US Fed next-day open
          - F&O expiry afternoon (after skip_expiry_after time)
        """
        d = dt.date() if hasattr(dt, "date") else dt
        t = dt.time() if hasattr(dt, "time") else dtime(9, 15)

        if self.is_skip_day(d):
            return True

        # US Fed: skip first 45 min of next trading day (volatile gap open)
        if self.skip_fed_next_day and d in self._fed_next_days:
            if t < dtime(10, 0):
                return True

        # F&O expiry afternoon: options decay accelerates
        if self.skip_expiry_after and self.is_expiry_day(d):
            if t >= self.skip_expiry_after:
                return True

        return False

    def is_expiry_day(self, d: date) -> bool:
        """Returns True if d is a weekly or monthly Nifty F&O expiry Thursday."""
        return d.weekday() == 3  # every Thursday is expiry (weekly options)

    def is_monthly_expiry(self, d: date) -> bool:
        return d == _last_thursday_of_month(d.year, d.month)

    def get_event_info(self, d: date) -> Optional[str]:
        """Return a human-readable description of the event on this date, or None."""
        events = []
        if d in RBI_MPC_DATES:
            events.append("RBI MPC Announcement")
        if d in BUDGET_DATES:
            events.append("Union Budget")
        if d in self._fed_next_days:
            events.append("Post US Fed (volatile open)")
        if d in self._cpi_next_days:
            events.append("Post India CPI release")
        if self.is_monthly_expiry(d):
            events.append("Monthly F&O Expiry")
        elif self.is_expiry_day(d):
            events.append("Weekly F&O Expiry")
        return " | ".join(events) if events else None

    def get_skipped_days(self, start: date, end: date) -> list:
        """List all full-skip days in the range (useful for reporting)."""
        skipped = []
        d = start
        while d <= end:
            if self.is_skip_day(d):
                skipped.append((d, self.get_event_info(d)))
            d += timedelta(days=1)
        return skipped
