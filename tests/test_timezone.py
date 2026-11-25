"""Times shown to customers are China time, even on a UTC server like Render."""
from datetime import datetime
from zoneinfo import ZoneInfo

import fileapp


def test_times_are_china_time():
    t = 1795574400
    want = datetime.fromtimestamp(t, ZoneInfo("Asia/Shanghai")).strftime("%H:%M")
    assert fileapp.fmt(t) == want
