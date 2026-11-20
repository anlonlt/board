"""Hours to the next high tide, from the last high tide given as HH:MM (semi-diurnal: 12 h 25 min)."""
import sys
from datetime import datetime, timedelta

PERIOD = timedelta(hours=12, minutes=25)


def next_high(last, now):
    t = last
    while t <= now:
        t += PERIOD
    return t


if __name__ == "__main__":
    now = datetime.now()
    h, m = map(int, (sys.argv[1] if len(sys.argv) > 1 else "06:10").split(":"))
    print(next_high(now.replace(hour=h, minute=m, second=0, microsecond=0) - PERIOD * 2, now) - now)
