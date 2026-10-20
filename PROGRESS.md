# OwnPOS — progress log

Liquor-store point of sale + accounting for **Delock's Liquors, Munster, Indiana**, built from the
Pasal POS 0.9.1 architecture. Pasal POS itself is never changed by this project.

**How to resume after a session ends:** read this file top to bottom. The newest entry is at the bottom.
Each entry says what is finished, what is half-done, and the exact next step. The newest copy of the code is
`OwnPOS-latest.zip` in `D:\Aayush learning\Ongoing work\OwnPOS\` (same folder as this file on your PC).
Start a new session with: "Continue OwnPOS from PROGRESS.md" and attach (or point Claude at) that folder.

All times are Chicago time (CDT/CST).

## Decisions (from Aayush, 16 Oct 2026)
| Topic | Decision |
|---|---|
| Name | **OwnPOS** |
| Logo | No bottle. Between self-management and secure tech for medium businesses |
| Replaces | QuickBooks Point of Sale 2013 Pro (v11) |
| Computers | 4 POS stations, 2–3 selling at once; i5 server PC; PostgreSQL on the server, every station connects to it |
| Server/network down | Stations stop and say so clearly; no offline selling in v1. The cart on screen is kept so it can be finished when the server is back |
| Cards | Separate card machine; POS records Card + optional last 4 + auth code |
| ID checks | No register prompt (staff licensed and trained; everyone ID'd at the door) |
| Books | Full built-in accounting |
| Volume | 10,000–20,000 items, ~100 vendors, ~20 customers, 1,000–2,000 sales/day |
| Import | Items (price, cost, UPC, on hand), vendors, customers, from any reasonable Excel/CSV layout (auto-detect header and columns, user confirms) |
| Reports | Report choices and types like QuickBooks POS; Excel-like tables on screen; careful Excel and PDF output |
| Discounts | Employee discount buttons 10% (used), 15%, 20%; cashier discretion, logged |
| Pricing | Price levels / employee price |
| Stock | One stock count per item. Packs/cases sell from the same count. Receiving shows on hand immediately |
| Categories | Drinks, accessories, snacks, party props; departments editable later |
| Tax | Everything taxable, Indiana 7%; filed quarterly; tax shown on the daily closing report |
| Sale hours | Warn when selling outside legal hours, but allow continuing (system clock could be wrong) |
| Payroll | Clock in/out hours per employee for the accountant; optional pay-rate calculation |
| Roles | Owner/admin gives and removes permissions easily |
| Protection | Password on the installer + signed licence keys only Aayush can make; an outer layer that never touches sales data |
| Checkpoints | Timestamped log (this file) + copy to PC after every stage — top priority |

## Stages
| # | Stage | Status |
|---|---|---|
| 0 | Checkpoint system | done |
| 1 | Research (QB POS 2013 Pro, leading liquor POS, Indiana tax, installer password) | done — `docs/RESEARCH.md` |
| 2 | Design doc `docs/DESIGN.md` | done |
| 3 | New package `ownpos`, Nepal-only parts removed | done (old `devpos/` removed 17 Oct) |
| 4 | PostgreSQL layer + concurrency safety | done, tested |
| 5 | US/Indiana tailoring | done |
| 6 | Store features (navigator, item fields, price levels, discounts, packs, fast register) | done |
| 7 | Smart import + Excel/PDF output | done |
| 8 | Time clock, payroll hours, roles editor | done |
| 9 | Licence layer, installer password, logo | done |
| 10 | Packaging (server + station installer) | done (Windows build runs on Aayush's PC) |
| 11 | Verification (tests, 4-station concurrency, 20k-item load, balance checks) | done |
| 12 | Docs + delivery | done |

## Work queue — do strictly in this order (ticked items are done and checkpointed)
Requests of 17 Oct 2026 01:26 are included (history screens, missing daily tools, AI options, Cancel/Clear on
every entry form, "Roles and Permissions" label).
- [x] Q1 Screens ported: Item List, Receive & Adjust, Lists/Employees, Report Center (Excel + PDF), Accounting,
      Settings, AI assistant page (reports-only, no free SQL), Help (+ Connection)
- [x] Q2 Time Clock page (punches, fix a punch, payroll shortcuts)
- [x] Q3 "Roles and Permissions" button label; Cancel / Clear on every entry form
- [x] Q4 History screens like QuickBooks POS: Sales History (find / view / reprint / return a receipt),
      Receiving (voucher) History, Purchase Order History, Item History, Customer and Vendor history
- [x] Q5 Other day-to-day tools found missing (list in docs/RESEARCH.md "Day-to-day tools"), most needed first
      (e.g. Return to vendor)
- [x] Q6 AI assistant — CHOSEN 17 Oct 01:31: **Option C** = "Ask OwnPOS" question box that answers everyday
      questions WITHOUT AI (exact reports, date phrases like "last week"), and only questions it cannot understand
      go to Claude Haiku 5.5 (reports-only tools), and only when the owner switches that on. Rule: the AI never
      does arithmetic; every answer shows the report it came from. (Options A cloud-only and B local model were
      not chosen; reasons and sources in docs/RESEARCH.md "AI assistant options".)
- [x] Q7 Screen smoke test: every page opened on PostgreSQL with the sample store; every report; self-test
- [x] Q8 Packaging: ownpos.spec, installer (password + encryption), build_exe.bat, write_version, dev_check
- [x] Q9 Docs: user guide, step-by-step setup, README, CHANGELOG; remove `devpos/` and old `tests/`
- [x] Q10 Final verification and delivery to the PC folder

## Log
- **2026-10-16 20:35** — Project started. Repo created from Pasal POS 0.9.1 (commit "Base"). Folder
  `D:\Aayush learning\Ongoing work\OwnPOS` created for checkpoints. Next: research.
- **2026-10-16 20:55** — Research written (`docs/RESEARCH.md`, every rule with its source). Design written
  (`docs/DESIGN.md`). Key choices: one store-wide write lock in PostgreSQL (same safety as the tested single-PC
  program), sale key against double-saving, server clock for all stations, shifts per station, invoice-basis 7% tax.
  Next: stage 3 — new `ownpos` package.
- **2026-10-16 22:10** — Stage 3/4 core written in new package `ownpos/core` (Pasal's `devpos/` kept only as
  reference until the UI is ported): PostgreSQL `db.py` (store-wide write lock, sale keys, server clock,
  ServerUnavailable/CommitUnknown), new `schema.sql`, US money/tax (invoice-basis 7%), US chart of accounts,
  receipts/returns/voids, per-station drawers + Day Closing, receiving, packs/cases sharing one stock count,
  pricing (5 levels), lists, smart Excel import (QB POS column names, .xls/.xlsx/.csv), 58 reports in QuickBooks
  groups, agents (run on one station), time clock + payroll estimate, licence (Ed25519) + `tools/licence_tool.py`,
  backups with pg_dump/pg_restore. Smoke test on PostgreSQL 16: sale/receive/Z-Out/ledger balanced, all 58
  reports run. Next: automated tests (incl. 4-station concurrency), then the screens.
- **2026-10-16 22:40** — 36 automated tests pass on PostgreSQL 16 (`tests_pg/`): Indiana tax rounding and invoice
  basis, packs/cases from one stock count, UPC lookup variants, discounts, split payments, house charge limits,
  returns adding up exactly, voids mirroring payments, per-station drawers + Day Closing, sale hours, time clock
  overtime, licence signing/expiry/station limit, QuickBooks-style Excel import (title rows, apostrophes, scientific-
  notation UPCs flagged), CSV customer import with balances, backup + restore to a new database, all agents, all
  reports. **4-station test**: 4 processes selling the same items at once (60 receipts each + returns): stock exact,
  receipt numbers gap-free, every drawer balanced to the cent, audit chain intact. Network-drop tests: a sale is
  never saved twice, and a half-saved sale leaves nothing behind. Next: sample store data, load test, screens.
- **2026-10-16 23:30** — Sample store (`core/demo.py`) and load test (`tools/load_test.py`, 20,000 items, 20,000
  receipts): UPC scan 0.14 ms, receipt saved median 3.8 ms, every report fast, ledger balanced.
- **2026-10-16 21:28 CDT (17 Oct 02:28 UTC)** — Screens being ported into `ownpos/ui` (WORK IN PROGRESS, not yet
  runnable end to end). Done: logo + icons (padlock-O with rising bars, no bottle), theme (teal/navy), branding,
  server/station connection screen with "set this PC up as the server", licence activation screen, first-run +
  sign-in with Clock in / Clock out, main window (QuickBooks-style left menu), register (US tender, card last-4 /
  auth, 10/15/20% employee discount buttons, sale-hours warning, server-down handling), Home, End of Day (per-station
  Z-Out + Day Closing). Next: Item List, Receive & Adjust, Lists/Employees, Report Center (Excel + PDF), Settings,
  Time Clock page, helper dialogs; then screen smoke test on PostgreSQL.
- **2026-10-17 01:35** — Session 3 started (the container had restarted; test database started again, 38 tests
  pass). Screens ported since the last entry (Q1): Item List (one stock count, packs & cases, bulk price changes
  with preview, shelf tags with UPC barcodes, owner cost correction), Receive & Adjust (scan to add, duplicate
  vendor-invoice warning, cost-jump warning, POs, breakage, counts by department with cases + loose bottles,
  vendor bills and payments), Lists and Employees (pay visible only with the payroll permission), Report Center
  (US date presets, Excel with real dates, Save as PDF, no false totals on statements), Accounting (deposits /
  card settlements with fees, Pay sales tax with the allowance kept), Settings (sale-hours editor, stations,
  devices of this PC, licence, backups to a new database), AI assistant (reports only), Help (+ Connection).
  Safety fixes in the core: register cash (1000) can only move while that station's drawer is open; UPC
  duplicates compared without leading zeros; list checks run under the store-wide lock; stock movements no
  longer mark items as edited. New tests: sample store balances; drawer guard. Next: Q2.
- **2026-10-17 01:31** — AI assistant: three options researched and sent to Aayush; he chose **C** (see Q6).
  Time clock: a punch that runs past midnight into a new workweek is now split at the week boundary (FLSA
  counts hours in the week they are worked); test added. Next: Q2 Time Clock page.
- **2026-10-17 01:50** — Q2 done: Time Clock page (who is in, punches, add / fix a punch with a required reason,
  hours by workweek with overtime, estimated gross pay only with the payroll permission). Q3 done: the button is
  now "Roles and Permissions" (a single "&" in a Qt button is a keyboard-shortcut marker, which is why the old
  label looked wrong; the same fix applied to "Packs & cases" and the Receive & Adjust tabs); Clear buttons on
  receiving, breakage, counts and vendor payment forms (each asks before throwing away entries); "Clear sale" is a
  visible register button. Bug found and fixed: Home page used the navigator before creating it (would have
  crashed on opening). New screen smoke test (`tests_pg/test_ui_smoke.py`): every screen opens twice, as owner and
  as cashier, on the sample store, and every report runs in the Report Center, with no error box. Next: Q4.
- **2026-10-17 02:10** — Q4 done: **Sales History** (Point of Sale menu: every receipt and return by date preset,
  type, station, cashier, customer, receipt # or an item on it; lines, payments with card last-4 / auth / change;
  View, Reprint (logged as copy), Return / void through the register's checked return), **Purchasing History**
  (Inventory menu: receiving vouchers + vendor returns with their lines, purchase orders by status with the
  voucher they were received as, vendor payments), **Item history** (Item List button: every stock movement with
  the running on-hand, costs only with the costs permission), **Customer / Vendor history** (History button in
  their lists: receipts or vouchers, and the account with running balance). Smoke test extended: receipt lines add
  up to the receipt total; item history's running balance ends at today's on hand. Bug caught by the test and fixed
  (date filter ran before the page was built). Next: Q5.
- **2026-10-17 02:30** — Q5 done: gap check written (docs/RESEARCH.md "Day-to-day tools"). Added **Return to
  vendor** (core `vendor_return`: stock leaves at average cost from the one count, credit on the vendor's account or
  money received; difference to 5100; refuses returning more than on hand; cash refunds need an open drawer and show
  on the Z-Out as "Cash refunds from vendors"; open bills, purchase reports and histories count returns as
  negative), **Copy as new item**, and **Pay sales tax** (from the earlier step) now tested. Fixed: open POs'
  estimated cost ignored the pack factor. New tests: 5 vendor-return / sales-tax tests and a screen test that scans
  a can twice and a case once on the receiving form (2 cans + 1 case = 26 on hand) and returns a can. 48 tests
  pass. Not built (ask first): gift cards, layaways, special orders, loyalty, mix-and-match, lottery. Next: Q6.
- **2026-10-17 03:00** — Q6 done (option C): **Ask OwnPOS** (`core/ask.py`, menu ADMIN > Ask OwnPOS, open to
  anyone with the reports permission). Reads date phrases (today, yesterday, this/last week/month/quarter/year,
  Q3 2026, month names, weekdays, "last 30 days", explicit ranges) and ~35 topic patterns into the built-in reports
  with department / item / employee filters; the answer sentence is built only from the report's rows; the report is
  shown under it with Open in Report Center / Excel / PDF; report permissions are respected. Not understood ->
  says so; the AI fallback button appears only when the owner has switched it on (default model now
  claude-haiku-5-5). 42 parsing tests (20 date phrases, 18 questions, item/department detection, exact answer
  figures, permissions) + a screen test asking all 15 examples. Next: Q7 full verification.
- **2026-10-17 03:30** — Q7 done. New screen tests: a full sale on the register screen (scan "3*" cans + a case
  barcode, Employee 10% button, exact cash: discount, tax, total, stock and books all checked to the cent) and Ask
  OwnPOS examples. Built-in self-test (`--self-test` with a PostgreSQL server) opens all 18 screens and runs all 58
  reports: OK. New `tools/ui_load_test.py` (20,000 items, 3,000 receipts) found and fixed real slowness:
  Receive & Adjust took **64 s** to open (Qt measured all 20,000 rows of every item drop-down under the style
  sheet) -> about 2 s first time, 0.2 s after; Item List 3.6 s -> 0.4 s (faster row conversion, no double load,
  footer totals without re-sorting). All other screens open in under 0.6 s. 92 tests pass. Next: Q8 packaging.
- **2026-10-17 03:50** — Q8 done: `ownpos.spec` (all ownpos modules, psycopg, cryptography, openpyxl, xlrd,
  optional anthropic), `installer/OwnPOS.iss` (new AppId so it never replaces Pasal POS; **Password + Encryption=yes**:
  the setup asks for the password and its files cannot be unpacked without it; the password is never stored in any
  file), `tools/make_installer.ps1` (asks the password twice, 10+ characters from a safe set, passes it to Inno
  Setup), `build_exe.bat` (refuses to build while the licence key is empty, runs the tests when OWNPOS_TEST_PG is
  set, self-tests the built program), `install_windows.bat`, `run_ownpos.bat`, `tools/dev_check.py`. Trial build
  with PyInstaller on Linux: the built program's self-test opened all 18 screens and ran all 58 reports on
  PostgreSQL. (The Windows build itself runs on Aayush's PC.) Pasal packaging files removed from this project.
  Next: Q9 docs and cleanup.
- **2026-10-17 04:05** — Found while checking the setup path: the training database (ownpos_demo, made by the server
  setup) stayed EMPTY and would have asked for a licence. Fixed: its first-run screen offers "fill it with the sample
  store"; a sample-store database may sell without a licence and says TRAINING on every receipt and screen; the
  sample no longer changes this PC's station number; `python -m ownpos` entry point added (the server setup's
  administrator step needs it when run from source). Test added. Next: Q9 docs.
- **2026-10-17 04:20** — Audit of settings that did nothing (found 4, all fixed): "Blind Z-Out" now really
  switches between a blind count and showing the expected cash (default: blind); "Warn when a drawer is over /
  short by more than" now warns after the Z-Out; "Allow selling when on hand shows zero" switched off now stops
  the sale (checked under the store-wide lock, nothing half-saved; default stays ON so a count error never stops a
  sale); the Indiana Taxpayer ID field says it is for records only. Unused "refund limit" setting removed.
  End of Day printouts use the receipt width setting. 94 tests pass. Next: Q9 docs.
- **2026-10-17 04:40** — Q9 and Q10 done. New user guide (`ownpos/docs/user_guide.md`, also Help / F1),
  step-by-step `docs/SETUP.md` (A: build the installer + licence key; B: server PC with PostgreSQL; C: each
  register; D: switch-over day from QuickBooks POS incl. training database; E: making a licence; updates; server
  failure), new README and CHANGELOG 1.0.0. Old Pasal code (`devpos/`, `tests/`, Pasal dev docs) removed from this
  project. Wording fixes (no "bill" for receipts, no Nepali months). `tools/dev_check.py` passes: 94 tests,
  self-test, version / changelog / installer / CRLF / password checks. **All queue items done.**
  What remains is outside this workspace: build the installer on Aayush's PC and set up the store (SETUP.md).
