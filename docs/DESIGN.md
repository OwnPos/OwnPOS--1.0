# OwnPOS — design (v1.0 for Delock's Liquors)

## 1. What stays from Pasal POS
Same layers and habits, because they are tested and they work:
- `ownpos/core` — business rules, no screen code. `ownpos/ui` — PySide6 screens. `ownpos/hardware` — drawer, display.
- Money in **integer cents**. Quantities rounded to 3 decimals.
- Every document (sale, return, receiving, payment, adjustment, Z-Out…) posts **one balanced double-entry journal
  entry** inside one database transaction. Unbalanced = refused, nothing saved.
- Issued receipts, journal lines, stock moves and the audit log can **never be edited or deleted** (database
  triggers). Mistakes are corrected with a return, a reversing entry or an adjustment — all logged.
- Hash-chained audit log; spreadsheet-style lists (sort, filter, edit in grid, Excel in/out); agents
  (reorder, margin, stock sanity, backup, integrity, day summary); optional AI assistant.

## 2. What is removed (Nepal-only)
VAT, excise, IRD bill rules (TAX/ABBR invoices, fiscal-year numbering, materialized view, CBMS), Bikram Sambat,
Nepali names/receipts/digits, Fonepay/eSewa/Khalti, production/recipes, farm purchases, advance orders,
online-order inbox, multi-company registry (replaced by server connection, §4).

## 3. Servers and stations
```
            ┌──────────── server PC (i5, always on) ─────────────┐
            │ PostgreSQL 16+  ── database "ownpos_store"          │
            │ (optional) database "ownpos_demo" for training/pitch│
            │ OwnPOS can also run here as a station               │
            └────────────────────┬───────────────────────────────┘
                   shop network (TCP 5432, LAN only)
        ┌──────────────┬─────────┴────┬──────────────┐
     Station 1      Station 2      Station 3      Station 4   (each: OwnPOS + its own drawer/printer)
```
- Each station stores only its **connection file** (server, port, database, user; password encrypted with
  Windows DPAPI so it cannot be copied to another PC) and its **station number**.
- All data, settings, users and licences live in the database on the server.
- **Server time is the store's clock.** Every station reads the server clock on connect and every few minutes;
  documents are stamped with server time, so four PCs can never disagree about dates. A station whose own clock
  is off by more than 2 minutes shows a warning.

### 3.1 Safety rules for several stations at once
| Risk | Rule |
|---|---|
| Two stations change the same thing at the same moment (last bottle, next receipt number, average cost) | Every writing transaction first takes one store-wide write lock (`pg_advisory_xact_lock`). Writes queue up one after another exactly like the tested single-PC program; a sale takes a few milliseconds, so the queue is never felt. Reading (search, reports) never waits. |
| Network drops while saving | A sale either fully commits or not at all (PostgreSQL transaction). Each cart carries a unique **sale key** (UUID). If the connection drops during "Save", the station reconnects and asks the server "does sale key X exist?" — if yes it shows that receipt, if no it saves again. A sale can never be saved twice. |
| Server down | Stations show "Server not reachable — sales are paused" with a Retry button. The cart on screen stays. Nothing is saved locally (decision). |
| Two cashiers, two drawers | **Shifts belong to a station.** X-Out / Z-Out count only that station's drawer. The **Day Closing report** adds up all stations for the date, including sales tax. |
| Someone edits an item while another station sells it | Item edits are single locked statements; the register always reads the item fresh when scanning. |
| Big Excel import during business hours | Import runs in one transaction (all or nothing) with fast bulk statements (20,000 rows in seconds); a warning suggests running it when the store is quiet. |

## 4. Store data (PostgreSQL)
Same tables as Pasal POS where the meaning is the same, with these changes:
- `items`: `upc`, `alt_lookup`, `department_id` (was category), `size` (e.g. 750ML), `attribute`, `vendor_id`,
  `vendor_item_no`, `price1..price5` (Regular + 4 levels, like QB POS), `msrp`, `case_qty`, `taxable` via tax code,
  `on_hand`, `reorder_point`, `reorder_qty`, `cost_avg`, `last_cost`. No variants: one item = one stock count.
- `item_units` (from 0.9.1): extra selling/buying units of one item — "6PK = 6", "CASE = 24" with own price and
  barcode. Stock is always in the item's own unit (bottle/can).
- `sales`: receipt number (one store-wide sequence), `station_id`, `sale_key` (unique), subtotal, discount, tax,
  total, discount reason (e.g. "Employee 10%").
- `stations`: number, name, last seen. `time_entries`: clock in/out. `licence`: signed licence text.
- `shifts.station_id`.

## 5. Money and tax
- Prices are **before tax** (U.S. practice). Tax codes: `IN7` (7% Indiana, default for every item) and `NT` (non-taxable,
  available for things like gift certificates or bottle deposits if ever needed).
- Receipt tax = round-half-up(taxable subtotal after discounts × 7%) — **invoice basis** (IC 6-2.5-2-2(c)).
  The tax is then split over the taxable lines (largest-remainder), so line taxes add up exactly to the receipt tax and
  returns give back exactly what was charged.
- Returns: cumulative rounding from Pasal POS — all returns of a line add up exactly to the original line.

## 6. Chart of accounts (editable)
| Code | Account | Type |
|---|---|---|
| 1000 | Cash in registers | asset |
| 1010 | Cash in safe | asset |
| 1030 | Card sales to be deposited | asset |
| 1040 | Checks to be deposited | asset |
| 1100 | Checking account | asset |
| 1200 | Accounts receivable (house charges) | asset |
| 1300 | Inventory | asset |
| 1500 | Equipment and fixtures | asset |
| 2000 | Accounts payable | liability |
| 2100 | Sales tax payable | liability |
| 2210 | Store credit owed to customers | liability |
| 2300 | Loans | liability |
| 3000 | Owner's equity | equity |
| 3100 | Opening balance equity | equity |
| 3200 | Owner's draws | equity |
| 4000 | Sales | income |
| 4900 | Sales returns | contra income |
| 5000 | Cost of goods sold | COGS |
| 5100 | Inventory cost corrections | COGS |
| 5200 | Shrinkage, breakage and spoilage | COGS |
| 6000–6900 | Wages, payroll taxes, rent, utilities, card processing fees, bank fees, insurance, licenses & permits, repairs, supplies, advertising, cash over/short, miscellaneous | expense |
Departments each point to an income account (Liquor, Beer, Wine, Tobacco, Accessories, Snacks, Party supplies can each
get their own 40xx account if the accountant wants department P&L lines).

## 7. Register (Make a Sale)
- Scan or type UPC / item # / name; `3*` multiplies; pack/case barcodes sell the pack. Lookup order: UPC (exact and
  normalised: with/without leading zeros and check digit) → pack barcode → alternate lookup → item # → name search.
- Keys: Enter = add, F2 qty, F3 price (permission), F4 discount, **Employee 10% / 15% / 20%** buttons (cashier
  discretion, recorded as the receipt's discount reason and shown on the Discount report), F5 hold/recall,
  F9 customer, F12 pay. Payment: Cash (quick $ buttons, change), Card (amount, optional last 4 + auth),
  Check, House charge, Store credit; split payments.
- Sale-hours check on the first item: outside the store's hours (Settings; Indiana defaults) a warning shows
  "Alcohol sales hours are …; continue?" — Continue is logged.
- Returns: by receipt number (exact refund of what was charged) or without receipt (manager permission).
- Speed target: scan → line on screen < 50 ms with 20,000 items (unique index on UPC + item units barcode).

## 8. Stock — one count per item
Receiving, returns, counts, adjustments and sales all change `items.on_hand` in the same transaction, and every open
screen showing that item refreshes when it is shown again; the Items list has a live "On hand" column.
Receiving by scanning: each scan adds 1 of the scanned unit (a case barcode adds 24 bottles).

## 9. Reports
Report Center grouped like QuickBooks (see RESEARCH.md): Sales, Inventory, Purchases, Vendors & Payables,
Customers & Receivables, Employees & Time Clock, Company & Financial, Accountant & Taxes, Lists.
Every report: date presets (Today, Yesterday, This week, This month, This quarter, Last quarter, This year…),
filters, sortable Excel-like table, **Export to Excel** (formatted, frozen header, totals row) and **Save as PDF**
(landscape when wide, repeated header, page numbers, store name and date range).

## 10. Smart import
Reads .xlsx, .xls, .csv. Finds the header row (first 30 rows scanned for known column names), guesses each column
from a dictionary of names used by QuickBooks POS and distributors (Item Number, UPC, ALU, Item Name, Description,
Department Name, Vendor, Regular Price, Price 1–5, Avg Cost, Order Cost, Qty 1/On Hand, Size, Attribute…),
shows the guess for the user to correct, checks every row (missing name, bad number, duplicate UPC, UPC turned into
scientific notation by Excel), shows a summary, then imports all-or-nothing.

## 11. Employees
- Roles & permissions editor (owner only): tick boxes per permission; owner always has everything.
- Time clock: Clock In / Clock Out on the login screen and in the app; owner can fix a missed punch (logged).
  Reports: hours per day/week, regular vs. overtime (> 40 h/week), and optional estimated gross pay from each
  employee's hourly rate. Export to Excel for the accountant.

## 12. Licence (outer layer)
- Aayush keeps a **private signing key** on his PC (encrypted with his passphrase). Only he can make licences.
- A licence is a short signed text: store name, store ID (from the database), number of stations, expiry (or
  none), features. OwnPOS holds only the **public** key and checks the signature (Ed25519). A changed letter breaks it.
- The licence lives in the store database, so replacing a station or restoring the server keeps it valid.
- The check happens before the main window opens and never touches sales data. Without a valid licence, OwnPOS
  shows an activation screen (with the store ID to send to Aayush). If a licence expires, there are 14 days of
  warnings, then 7 days of grace, then selling pauses — but reports, Excel export and backups always keep working,
  so the store's data is never held hostage.
- The installer itself is password-protected and encrypted (Inno Setup).
- Honest limit: no protection of a program that runs on someone else's PC is unbreakable. Signed licences stop
  copying and key-guessing; they do not stop a skilled programmer from editing the program itself.

## 13. Backups
`pg_dump` (custom format) from the server PC: on Day Closing, on a timer, and on demand; copied to a second folder
(USB / cloud-synced). Restore into a new database, check it, then switch — never over the live one.

## 14. Testing
Every change is tested against a real PostgreSQL: unit tests, a **4-station concurrency test** (four processes
selling the same items at once: stock, receipt numbers and ledger must come out exact), a 20,000-item / 60,000-sale
load test (lookup speed, report speed), accounting checks (trial balance zero, stock value = inventory account),
and a self-test of the packaged program.
