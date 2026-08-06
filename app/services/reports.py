"""Pure helpers for report aggregation (handoff Section 4 Reports).

Kept free of DB/session dependencies so the bucketing logic can be unit-tested directly.
"""

from datetime import date, timedelta


def bucket_start(day: date, group_by: str) -> date:
    """Returns the start-of-period date a given day falls into, for day|week|month grouping.

    week: Monday of that ISO week (undocumented choice -- handoff doesn't specify week start).
    month: the 1st of that month.
    """
    if group_by == "day":
        return day
    if group_by == "week":
        return day - timedelta(days=day.weekday())
    if group_by == "month":
        return day.replace(day=1)
    raise ValueError(f"unknown group_by: {group_by}")
