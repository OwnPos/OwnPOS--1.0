"""Starting data for a new store database: chart of accounts, tax codes, payment methods, departments,
lists, store hours and settings. Everything here can be edited later in Lists and Settings.

Two starting points:
- "blank":   only what the program needs (accounts, tax codes, payment methods, one department, short lists);
- "starter": blank plus the usual liquor-store departments and reasons."""
from __future__ import annotations

ACCOUNTS = [
    # code, name, type, subtype, system
    ("1000", "Cash in registers", "asset", "cash", 1),
    ("1010", "Cash in safe", "asset", "cash", 1),
    ("1030", "Card sales to be deposited", "asset", "bank", 1),
    ("1040", "Checks to be deposited", "asset", "bank", 1),
    ("1100", "Checking account", "asset", "bank", 1),
    ("1200", "Accounts receivable (house charges)", "asset", None, 1),
    ("1300", "Inventory", "asset", None, 1),
    ("1500", "Equipment and fixtures", "asset", None, 0),
    ("2000", "Accounts payable", "liability", None, 1),
    ("2100", "Sales tax payable", "liability", None, 1),
    ("2210", "Store credit owed to customers", "liability", None, 1),
    ("2300", "Loans", "liability", None, 0),
    ("3000", "Owner's equity", "equity", None, 1),
    ("3100", "Opening balance equity", "equity", None, 1),
    ("3200", "Owner's draws", "equity", None, 0),
    ("4000", "Sales", "income", None, 1),
    ("4800", "Sales tax collection allowance and rounding", "income", None, 1),
    ("4900", "Sales returns", "contra_income", None, 1),
    ("5000", "Cost of goods sold", "cogs", None, 1),
    ("5100", "Inventory cost corrections", "cogs", None, 1),
    ("5200", "Shrinkage, breakage and spoilage", "cogs", None, 1),
    ("6000", "Wages", "expense", None, 0),
    ("6050", "Payroll taxes", "expense", None, 0),
    ("6100", "Rent", "expense", None, 0),
    ("6200", "Utilities", "expense", None, 0),
    ("6250", "Telephone and internet", "expense", None, 0),
    ("6300", "Store supplies", "expense", None, 0),
    ("6400", "Card processing fees", "expense", None, 1),
    ("6500", "Bank fees", "expense", None, 1),
    ("6550", "Insurance", "expense", None, 0),
    ("6600", "Cash over / short", "expense", None, 1),
    ("6650", "Licenses and permits", "expense", None, 0),
    ("6700", "Repairs and maintenance", "expense", None, 0),
    ("6800", "Advertising", "expense", None, 0),
    ("6900", "Miscellaneous expenses", "expense", None, 0),
]

TAX_CODES = [
    # code, name, rate %
    ("IN7", "Indiana sales tax 7%", 7.0),
    ("NT", "Not taxable", 0.0),
]

PAYMENT_METHODS = [
    # code, name, account, is_cash, is_card, requires_ref, requires_customer, opens_drawer, on_register, sort
    ("CASH", "Cash", "1000", 1, 0, 0, 0, 1, 1, 1),
    ("CARD", "Credit / debit card", "1030", 0, 1, 0, 0, 0, 1, 2),
    ("CHECK", "Check", "1040", 0, 0, 1, 0, 1, 1, 3),
    ("CHARGE", "House charge", "1200", 0, 0, 0, 1, 0, 1, 4),
    ("STORECREDIT", "Store credit", "2210", 0, 0, 0, 1, 0, 1, 5),
]

LOOKUPS = {
    "unit": [("ea", "Each"), ("btl", "Bottle"), ("can", "Can"), ("pk", "Pack"), ("4pk", "4-pack"),
             ("6pk", "6-pack"), ("12pk", "12-pack"), ("18pk", "18-pack"), ("24pk", "24-pack"), ("30pk", "30-pack"),
             ("case", "Case"), ("box", "Box"), ("bag", "Bag")],
    "size": [("50ML", "50 ml"), ("100ML", "100 ml"), ("200ML", "200 ml"), ("375ML", "375 ml"), ("750ML", "750 ml"),
             ("1L", "1 liter"), ("1.75L", "1.75 liter"), ("12OZ", "12 oz"), ("16OZ", "16 oz"), ("19.2OZ", "19.2 oz"),
             ("24OZ", "24 oz"), ("25OZ", "25 oz"), ("40OZ", "40 oz")],
    "payout_reason": [("SUPPLIES", "Store supplies", "6300"), ("REPAIR", "Repairs", "6700"),
                      ("DRAW", "Owner draw", "3200"), ("OTHER", "Other expense", "6900")],
    "waste_reason": [("BROKEN", "Broken bottle"), ("EXPIRED", "Expired"), ("DAMAGED", "Damaged"),
                     ("SAMPLE", "Tasting sample"), ("OTHER", "Other")],
    "adjust_reason": [("COUNT", "Count correction"), ("FOUND", "Found stock"), ("THEFT", "Missing / theft"),
                      ("OPENING", "Opening stock")],
    "return_reason": [("DEFECT", "Defective / spoiled"), ("WRONG", "Wrong item"), ("CHANGED", "Customer changed mind"),
                      ("ERROR", "Cashier error")],
    "price_level": [("1", "Regular"), ("2", "Price level 2"), ("3", "Price level 3"), ("4", "Price level 4"),
                    ("5", "Price level 5")],
    "discount_button": [("EMP10", "Employee 10%", "10"), ("EMP15", "Employee 15%", "15"),
                        ("EMP20", "Employee 20%", "20")],
}

BLANK_LOOKUPS = {
    "unit": ["ea", "btl", "can", "6pk", "12pk", "24pk", "case"],
    "size": ["750ML", "1.75L", "12OZ"],
    "payout_reason": ["DRAW", "OTHER"],
    "waste_reason": ["BROKEN", "OTHER"],
    "adjust_reason": ["COUNT", "OPENING"],
    "return_reason": ["DEFECT", "ERROR"],
    "price_level": ["1", "2", "3", "4", "5"],
    "discount_button": ["EMP10", "EMP15", "EMP20"],
}

# name, code, income account, tax code, margin target %, color, on register, sort
BLANK_DEPARTMENTS = [
    ("General", "GEN", "4000", "IN7", 0, "#5B6475", 1, 99),
]
STARTER_DEPARTMENTS = [
    ("Liquor", "LIQ", "4000", "IN7", 25, "#8A5A2B", 1, 1),
    ("Beer", "BEER", "4000", "IN7", 20, "#C9A227", 1, 2),
    ("Wine", "WINE", "4000", "IN7", 30, "#7B2D42", 1, 3),
    ("Tobacco", "TOB", "4000", "IN7", 15, "#6B6B6B", 1, 4),
    ("Mixers & soft drinks", "MIX", "4000", "IN7", 35, "#2F7FA3", 1, 5),
    ("Snacks", "SNK", "4000", "IN7", 35, "#C9852B", 1, 6),
    ("Drink accessories", "ACC", "4000", "IN7", 40, "#4F7A5A", 1, 7),
    ("Party supplies", "PARTY", "4000", "IN7", 40, "#8E5BA8", 1, 8),
    ("Ice", "ICE", "4000", "IN7", 40, "#5EA3C7", 1, 9),
] + BLANK_DEPARTMENTS
TEMPLATES = ("blank", "starter")

# Indiana carryout hours (warning only; see RESEARCH.md). day: 0 = Monday ... 6 = Sunday.
# "close" may be after midnight ("03:00" = 3 am the next morning).
INDIANA_SALE_HOURS = {
    "0": ["07:00", "03:00"], "1": ["07:00", "03:00"], "2": ["07:00", "03:00"], "3": ["07:00", "03:00"],
    "4": ["07:00", "03:00"], "5": ["07:00", "03:00"], "6": ["12:00", "20:00"],
}

DEFAULT_SETTINGS = {
    "company_name": "",
    "company_address": "",
    "company_city_state_zip": "",
    "company_phone": "",
    "company_tax_id": "",                      # Indiana Registered Retail Merchant Certificate / TID (optional)
    "company_permit_no": "",                   # ATC permit number (optional, shown on receipts if set)
    "timezone": "America/Chicago",             # Munster, Lake County, Indiana is on Central time
    "receipt_width_mm": 80,
    "receipt_header": "",
    "receipt_footer": "Thank you! Please drink responsibly.",
    "receipt_auto_print": True,
    "receipt_show_savings": True,
    "theme": "system",
    "max_discount_pct_cashier": 20,            # the employee buttons go up to 20%
    "require_shift": True,
    "blind_count": True,                       # Z-Out: cashier counts without seeing the expected amount
    "z_count_denominations": True,
    "cash_over_short_alert": 500,              # cents: $5
    "denominations": [10000, 5000, 2000, 1000, 500, 200, 100, 25, 10, 5, 1],
    "allow_negative_stock": True,
    "sale_hours_check": True,
    "sale_hours": INDIANA_SALE_HOURS,
    "sale_hours_departments": [],              # empty = every department (warning only)
    "slow_mover_days": 60,
    "backup_folder": "",
    "backup_extra_folder": "",
    "backup_keep": 30,
    "report_folder": "",
    "drawer_printer": "",
    "drawer_port": "",
    "customer_display_port": "",
    "ai_enabled": False,
    "ai_api_key": "",
    "ai_model": "claude-haiku-5-5",                # small, cheap: only a fallback
    "idle_lock_minutes": 10,
    "overtime_weekly_hours": 40,
    "week_starts": 6,                          # workweek starts Sunday (0 = Monday ... 6 = Sunday)
}


def seed_departments(db, rows) -> None:
    for row in rows:
        if db.val("SELECT 1 FROM departments WHERE lower(name) = lower(?)", (row[0],)):
            continue
        db.exec("INSERT INTO departments(name, code, income_account, default_tax_code, margin_target, color,"
                " show_on_register, sort) VALUES(?,?,?,?,?,?,?,?)", row)


def seed_defaults(db, template: str = "starter") -> None:
    if template not in TEMPLATES:
        raise ValueError(f"Unknown starting point {template}")
    for code, name, typ, subtype, system in ACCOUNTS:
        db.exec("INSERT INTO accounts(code, name, type, subtype, system) VALUES(?,?,?,?,?)"
                " ON CONFLICT(code) DO NOTHING", (code, name, typ, subtype, system))
    for code, name, rate in TAX_CODES:
        db.exec("INSERT INTO tax_codes(code, name, rate) VALUES(?,?,?) ON CONFLICT(code) DO NOTHING",
                (code, name, rate))
    for row in PAYMENT_METHODS:
        db.exec("INSERT INTO payment_methods(code, name, account, is_cash, is_card, requires_ref,"
                " requires_customer, opens_drawer, on_register, sort) VALUES(?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(code) DO NOTHING", row)
    for list_name, rows in LOOKUPS.items():
        keep = BLANK_LOOKUPS.get(list_name) if template == "blank" else None
        for i, row in enumerate(rows):
            code, name = row[0], row[1]
            if keep is not None and code not in keep:
                continue
            value = row[2] if len(row) > 2 else None
            db.exec("INSERT INTO lookups(list_name, code, name, value, sort) VALUES(?,?,?,?,?)"
                    " ON CONFLICT(list_name, code) DO NOTHING", (list_name, code, name, value, i + 1))
    seed_departments(db, BLANK_DEPARTMENTS if template == "blank" else STARTER_DEPARTMENTS)
    for key, value in DEFAULT_SETTINGS.items():
        if db.one("SELECT 1 FROM settings WHERE key = ?", (key,)) is None:
            db.set_setting(key, value)
    import uuid
    if db.one("SELECT 1 FROM meta WHERE key = 'store_uid'") is None:
        db.exec("INSERT INTO meta(key, value) VALUES('store_uid', ?)", (uuid.uuid4().hex,))
