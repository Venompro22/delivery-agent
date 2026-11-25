"""save() writes only the orders that changed, and the database always matches memory."""
from models import Order, OrderStatus
from store import OrderStore


def make(i):
    return Order(id=f"o{i}", customer_name=f"C{i}", lat=30.25, lng=119.72, phone="", note="")


def fresh(path):
    return {o.id: o.to_dict() for o in OrderStore(path).all()}


def test_nothing_changed_means_no_write(tmp_path):
    s = OrderStore(str(tmp_path / "o.db"))
    for i in range(5):
        s.add(make(i))
    assert s.save() == 0


def test_only_the_edited_order_is_written(tmp_path):
    path = str(tmp_path / "o.db")
    s = OrderStore(path)
    for i in range(50):
        s.add(make(i))
    s.get("o7").status = OrderStatus.DELIVERED
    assert s.save() == 1
    assert fresh(path)["o7"]["status"] == "delivered"
    assert len(fresh(path)) == 50


def test_removed_orders_leave_the_database(tmp_path):
    path = str(tmp_path / "o.db")
    s = OrderStore(path)
    for i in range(3):
        s.add(make(i))
    s.orders = [o for o in s.orders if o.id != "o1"]
    assert s.save() == 1
    assert set(fresh(path)) == {"o0", "o2"}


def test_orders_appended_without_add_are_saved(tmp_path):
    path = str(tmp_path / "o.db")
    s = OrderStore(path)
    s.orders.append(make(9))
    assert s.save() == 1
    assert set(fresh(path)) == {"o9"}


def test_memory_and_database_match_after_mixed_changes(tmp_path):
    path = str(tmp_path / "o.db")
    s = OrderStore(path)
    for i in range(4):
        s.add(make(i))
    s.get("o0").note = "edited"
    s.orders = [o for o in s.orders if o.id != "o3"]
    s.orders.append(make(8))
    s.save()
    assert fresh(path) == {o.id: o.to_dict() for o in s.all()}
