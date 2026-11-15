"""🧠 Regular customers: what the shop already knows about a phone number, from past orders.
Pure functions (no Flask, no database), so they are easy to test."""
import re
from collections import Counter
from typing import Optional

# Ways people say "the usual place" in a WeChat message
USUAL_PLACE = re.compile(r"老地方|老位置|老样子|老规矩|还是那[里儿边]|跟上次一样|和上次一样|同上次")
_QTY = re.compile(r"([一二两三四五六七八九十]|\d{1,2})\s*[桶箱瓶件]")
_CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def quantity(text: str) -> Optional[int]:
    """'两桶' -> 2, '3箱' -> 3, nothing -> None."""
    m = _QTY.search(text or "")
    if not m:
        return None
    v = m.group(1)
    return int(v) if v.isdigit() else _CN.get(v)


def wants_usual_place(text: str) -> bool:
    return bool(USUAL_PLACE.search(text or ""))


def profile(phone: str, orders) -> Optional[dict]:
    """Everything we know about this phone from its past orders, or None for a new customer."""
    if not phone:
        return None
    mine = [o for o in orders if o.phone == phone and o.status.value != "cancelled"]
    if not mine:
        return None
    last = max(mine, key=lambda o: o.created_at or 0)
    qtys = [q for q in (quantity(o.note) for o in mine) if q]
    return {"name": last.customer_name, "count": len(mine), "last_order": last,
            "usual_qty": Counter(qtys).most_common(1)[0][0] if qtys else None}


def quiet(orders, now: float, min_orders: int = 3) -> list:
    """Regulars who stopped ordering: their last order is much older than their usual gap.
    Usual gap = median time between their orders (one odd week doesn't fool it)."""
    by_phone = {}
    for o in orders:
        if o.phone and o.created_at and o.status.value != "cancelled":
            by_phone.setdefault(o.phone, []).append(o)
    out = []
    for mine in by_phone.values():
        if len(mine) < min_orders:
            continue
        mine.sort(key=lambda o: o.created_at)
        gaps = sorted(b.created_at - a.created_at for a, b in zip(mine, mine[1:]))
        usual = gaps[len(gaps) // 2]
        since = now - mine[-1].created_at
        if usual <= 0 or since < max(2 * usual, 3 * 86400):
            continue
        out.append({"name": mine[-1].customer_name, "every_days": max(1, round(usual / 86400)),
                    "since_days": round(since / 86400), "count": len(mine), "overdue": round(since / usual, 1)})
    return sorted(out, key=lambda c: -c["overdue"])
