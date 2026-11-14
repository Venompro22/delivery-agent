"""📝 Parser v2: real WeChat messages, with or without spaces."""
from order_parser import parse_message, parse_many


def test_no_spaces_at_all():
    r = parse_message("我要两桶水文三路200号下午5点前13800138001")
    assert r["phone"] == "13800138001" and r["qty"] == 2
    assert r["address"] == "文三路200号" and r["deadline"] == "17:00"


def test_names_and_usual_place():
    r = parse_message("王姐 13800138001 老地方 两桶")
    assert r["name"] == "王姐" and r["usual_place"] and r["qty"] == 2


def test_how_soon():
    r = parse_message("半小时内送一桶 阳光小区3单元502室")
    assert r["soon_minutes"] == 30 and r["urgent"] and r["qty"] == 1 and "阳光小区" in r["address"]


def test_tomorrow_half_past():
    r = parse_message("明天上午10点半 送3桶")
    assert r["day"] == 1 and r["deadline"] == "10:30" and r["qty"] == 3


def test_several_orders_in_one_message():
    assert len(parse_many("王芳 两桶 文三路100号；另外李明 一桶 阳光小区")) == 2


def test_highlights_never_overlap():
    ents = parse_message("李明 13912345678 文三路100号 两桶水 下午3点前")["entities"]
    assert all(a["end"] <= b["start"] for a, b in zip(ents, ents[1:]))
