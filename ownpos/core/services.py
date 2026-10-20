"""One object that owns the database connection and every service, shared by the screens and the agents."""
from __future__ import annotations

from .accounting import AccountingService
from .auth import AuthService
from .db import Database, open_database
from .inventory import Inventory
from .ledger import Ledger
from .lists import ListService
from .pricing import PricingService
from .purchasing import PurchasingService
from .sales import SalesService
from .shifts import ShiftService
from .stock import StockService
from .timeclock import TimeClock


class Services:
    def __init__(self, conninfo: dict | Database, station_id: int | None = None, template: str = "starter",
                 timezone: str = "America/Chicago"):
        if isinstance(conninfo, Database):
            self.db = conninfo
        else:
            self.db = open_database(conninfo, station_id=station_id, template=template, timezone=timezone)
        self.station_id = self.db.station_id
        self.ledger = Ledger(self.db)
        self.inventory = Inventory(self.db, self.ledger)
        self.auth = AuthService(self.db)
        self.lists = ListService(self.db)
        self.sales = SalesService(self.db, self.ledger, self.inventory)
        self.shifts = ShiftService(self.db, self.ledger)
        self.purchasing = PurchasingService(self.db, self.ledger, self.inventory)
        self.stock = StockService(self.db, self.ledger, self.inventory)
        self.accounting = AccountingService(self.db, self.ledger)
        self.pricing = PricingService(self.db)
        self.timeclock = TimeClock(self.db)
        from .agents import AgentManager
        self.agents = AgentManager(self)

    def close(self):
        self.db.close()
