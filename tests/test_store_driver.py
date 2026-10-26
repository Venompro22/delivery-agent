"""🚚 Orders remember which driver they belong to."""
import sqlite3

from models import Order
from store import OrderStore

OLD_SCHEMA = """CREATE TABLE orders (
    id TEXT PRIMARY KEY, customer_name TEXT NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL,
    phone TEXT DEFAULT '', note TEXT DEFAULT '', service_minutes REAL DEFAULT 5, promised_by REAL,
    status TEXT NOT NULL DEFAULT 'pending', created_at REAL, delivered_at REAL)"""


def test_driver_id_is_written():
    row = OrderStore._row(Order(customer_name="A", lat=30.0, lng=120.0, driver_id="abc123"))
    # Look up driver_id by its column position, so adding columns later never breaks this.
    from store import COLUMNS
    assert row[COLUMNS.index("driver_id")] == "abc123"


def test_old_database_gets_the_new_column(tmp_path):
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.execute(OLD_SCHEMA)                         # a database from before this change
    con.execute("INSERT INTO orders (id, customer_name, lat, lng, status, created_at) "
                "VALUES ('a1b2c3d4', 'Old order', 30, 120, 'pending', 0)")
    con.commit()
    con.close()

    store = OrderStore(path)                        # the migration runs here
    assert store.orders[0].customer_name == "Old order"   # nothing was lost
    assert store.orders[0].driver_id is None              # old orders: nobody yet
