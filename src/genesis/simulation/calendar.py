"""Time in Genesis: one place that knows what a tick means (M8a).

Story mode: 1 tick = 1 hour (24 per day), as in M0–M7.
Millennium mode: 1 tick = 1 watch = 6 hours (4 per day: night, dawn, day, dusk), in years of
360 days and four seasons. See docs/10-millennium-experiment.md (10.4).

Rules stay written per hour (rates) and in days (durations); the calendar converts them, so
the same code runs at both resolutions.
"""

from dataclasses import dataclass

SEASONS = ("spring", "wet season", "dry season", "cold season")
WATCHES = {0: "night", 6: "dawn", 12: "day", 18: "dusk"}


@dataclass(frozen=True)
class Calendar:
    ticks_per_day: int = 24
    days_per_year: int = 360
    start_hour: int = 8
    seasons: bool = False  # resources change with the seasons (millennium worlds)

    @property
    def hours_per_tick(self) -> int:
        return 24 // self.ticks_per_day

    @property
    def watches(self) -> bool:
        return self.ticks_per_day < 24

    # --------------------------------
    # Where a tick falls
    # --------------------------------

    def hours(self, tick: int) -> int:
        """Hours since midnight of day 1 at the start of this tick."""
        return self.start_hour + tick * self.hours_per_tick

    def hour(self, tick: int) -> int:
        return self.hours(tick) % 24

    def day_index(self, tick: int) -> int:
        """Days since the world began (0-based)."""
        return self.hours(tick) // 24

    def day(self, tick: int) -> int:
        return self.day_index(tick) + 1

    def year(self, tick: int) -> int:
        return self.day_index(tick) // self.days_per_year + 1

    def day_of_year(self, tick: int) -> int:
        return self.day_index(tick) % self.days_per_year + 1

    def season_index(self, tick: int) -> int:
        return (self.day_of_year(tick) - 1) * 4 // self.days_per_year

    def season(self, tick: int) -> str:
        return SEASONS[self.season_index(tick)]

    def watch(self, tick: int) -> str:
        return WATCHES.get(self.hour(tick) // 6 * 6, "night")

    def is_day_end(self, next_tick: int) -> bool:
        """True when `next_tick` starts a new day (the day before has just ended)."""
        return self.hour(next_tick) < self.hours_per_tick

    # --------------------------------
    # Durations
    # --------------------------------

    def ticks(self, days: float = 0, hours: float = 0) -> int:
        """A duration in ticks (at least 1 for any positive duration)."""
        total = (days * 24 + hours) / self.hours_per_tick
        return max(1, round(total)) if total > 0 else 0

    def days_between(self, first_tick: int, last_tick: int) -> int:
        return (last_tick - first_tick) * self.hours_per_tick // 24

    def age_days(self, ticks: int) -> int:
        return ticks * self.hours_per_tick // 24

    # --------------------------------
    # Words
    # --------------------------------

    def clock(self, tick: int) -> str:
        if not self.watches:
            return f"Day {self.day(tick)} {self.hour(tick):02d}:00"
        return (
            f"Year {self.year(tick)}, {self.season(tick)} day {self.day_of_year(tick)}, "
            f"{self.watch(tick)}"
        )

    def when(self, tick: int) -> str:
        """Short date for memories and prompts: 'Day 12' or 'Year 3, dry season'."""
        if not self.watches:
            return f"Day {self.day(tick)}"
        return f"Year {self.year(tick)}, {self.season(tick)}"


def calendar_of(config) -> Calendar:
    return Calendar(
        ticks_per_day=config.ticks_per_day,
        days_per_year=config.days_per_year,
        start_hour=config.start_hour,
        seasons=config.seasons,
    )
