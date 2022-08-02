# Copyright (C) 2018 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - https://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import datetime

day_of_week = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

def schedule_time_next(schedule_text):
    now = datetime.datetime.utcnow().replace(second=0, microsecond=0)
    weekday = now.weekday()
    best_time = None

    for at in schedule_text.split(","):
        propose = now
        parts = at.split("@", 1)

        # Monday is 0 and Sunday is 6
        day = day_of_week.get(parts[0])
        if day is not None:
            skip_days = 7
            days = (weekday - day) + 1 if day < weekday else day - weekday
            propose += datetime.timedelta(days=days)
        else:
            # Periodic
            if not parts[0].endswith("d") or not parts[0][:-1].isdigit():
                raise ValueError("Periodic time must be in `Xd` format")
            skip_days = 1
            propose += datetime.timedelta(days=int(parts[0][:-1]))

        if len(parts) > 1:
            time = parts[1].split(":", 1)
            propose = propose.replace(hour=int(time[0]), minute=int(time[1]))
            if propose < now:
                propose += datetime.timedelta(days=skip_days)

        if propose > now and (best_time is None or propose < best_time):
            best_time = propose

    return best_time
