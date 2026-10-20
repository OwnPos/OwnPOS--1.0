"""The sample store: builds, balances, never goes negative, and every report runs on it."""
from __future__ import annotations

import pytest

from ownpos.core.demo import fill_sample
from ownpos.core.ledger import DrawerClosed
from ownpos.core.reports import REGISTRY, run_report
from ownpos.core.stations import register


def test_sample_store_balances(s):
    register(s.db, 1)
    register(s.db, 2)
    res = fill_sample(s, s.owner.id, days=3, sales_per_day=40)
    assert res
    assert s.ledger.is_balanced()
    assert not s.db.val("SELECT COUNT(*) FROM items WHERE on_hand < 0", default=0)
    for key in REGISTRY:
        run_report(s.db, key, {})


def test_register_cash_needs_open_drawer(s):
    """Paying a vendor from Cash in registers with no drawer open on this station is refused."""
    from tests_pg.helpers import vendor
    v = vendor(s)
    with pytest.raises(DrawerClosed):
        s.purchasing.pay_vendor(v, 1000, "1000", s.owner.id)
    s.purchasing.pay_vendor(v, 1000, "1010", s.owner.id)      # from the safe: fine


def test_training_database_sells_without_licence_and_says_training(s, monkeypatch):
    """With licensing ON, a sample-store database may sell (it is marked TRAINING everywhere); a normal empty
    store may not. The computer keeps its own station number after the sample is made."""
    from ownpos.core import licence
    from ownpos.core.receipt import receipt_html
    monkeypatch.setattr(licence, "public_key_b64", lambda: "x" * 43)
    assert licence.status(s.db).state == "missing"
    s.db.station_id = 3
    register(s.db, 3)
    fill_sample(s, s.owner.id, days=1, sales_per_day=10)
    assert s.db.station_id == 3
    st = licence.status(s.db)
    assert st.state == "demo" and st.can_sell and "TRAINING" in st.banner
    assert licence.allowed_stations(s.db) is None
    sid = s.db.val("SELECT id FROM sales ORDER BY id LIMIT 1")
    assert "TRAINING" in receipt_html(s.db, sid)
