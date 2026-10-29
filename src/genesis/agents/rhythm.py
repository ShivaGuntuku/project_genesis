"""Sleep on a schedule (M8a): each agent's hours come from two genes.

    hours asleep = sleep_min_hours + sleep_need × (sleep_max_hours - sleep_min_hours)
    bedtime      = bedtime_earliest + chronotype × bedtime_spread     (wrapping past midnight)

An agent is asleep in a tick when at least half of that tick falls in its sleep window, so in
6-hour watches different people sleep through different watches. Sleepers don't decide,
talk, or call the LLM (unless thirst or hunger becomes urgent and wakes them).
"""


def sleep_hours(genome, rules) -> float:
    return rules.sleep_min_hours + genome.sleep_need * (
        rules.sleep_max_hours - rules.sleep_min_hours
    )


def bedtime(genome, rules) -> float:
    return (rules.bedtime_earliest + genome.chronotype * rules.bedtime_spread) % 24


def overlap(start: float, length: float, window_start: float, window_length: float) -> float:
    """Hours shared by [start, start+length) and a daily window (both mod 24)."""
    total = 0.0
    for shift in (-24, 0, 24):
        a, b = start, start + length
        c, d = window_start + shift, window_start + shift + window_length
        total += max(0.0, min(b, d) - max(a, c))
    return total


def asleep_at(genome, rules, hour: float, hours_per_tick: float) -> bool:
    shared = overlap(hour, hours_per_tick, bedtime(genome, rules), sleep_hours(genome, rules))
    return shared >= hours_per_tick / 2
