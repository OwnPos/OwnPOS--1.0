# OwnPOS

Point of sale, inventory and built-in accounting for a liquor store in Indiana, with several registers sharing
one store database (PostgreSQL) on a server PC. Built for Delock's Liquors (Munster, IN) to replace QuickBooks
Point of Sale 2013 Pro, and modelled on its way of working.

Version 1.0.0

- **Registers:** fast UPC scanning (leading zeros and check digits handled), packs and cases from one stock count,
  employee 10/15/20% buttons, price levels, hold/recall, returns and voids tied to the receipt, card last-4 and
  approval code, house charges and store credit, Indiana 7% sales tax, alcohol sale-hours warning.
- **Several stations safely:** every change is one transaction on the server under a store-wide write lock; a
  sale is never saved twice, even when the network breaks while saving; each station has its own drawer.
- **Inventory:** Item List like a spreadsheet, receiving by scanning, purchase orders, return to vendor,
  breakage, physical counts, item history.
- **History:** Sales History, Purchasing History, Item / Customer / Vendor history.
- **Reports:** 58 reports in QuickBooks groups, Excel and PDF output, memorized reports; **Ask OwnPOS** answers
  everyday questions from the reports without AI.
- **Books:** double-entry accounting in cents, sales tax (ST-103 lines), P&L, balance sheet, vendor bills.
- **People:** roles and permissions, time clock with weekly overtime, payroll estimate.
- **Protection:** signed licence keys (Ed25519) that only Aayush can make, and a password-protected, encrypted
  installer. Reports, exports and backups always work, licence or not.
- **Backups:** PostgreSQL backups checked after writing; restore into a new database only.

## Documents

- `docs/SETUP.md` — step by step: making the installer, the server PC, the registers, moving over from
  QuickBooks POS, licences.
- `ownpos/docs/user_guide.md` — the user guide (also inside the program: Help, F1).
- `docs/RESEARCH.md` — Indiana tax and alcohol rules, QuickBooks POS, leading liquor-store systems, AI options,
  each with its source. `docs/DESIGN.md` — how it is built and why.
- `PROGRESS.md` — the timestamped build log and the ordered work queue.

## For developers

- `install_windows.bat` makes `.venv`; `run_ownpos.bat` starts the source code with its own settings folder
  (`%LOCALAPPDATA%\OwnPOS-Dev`). Point it at a test database, never the store's.
- Tests need a PostgreSQL server: `set OWNPOS_TEST_PG=host=localhost port=5432 user=postgres password=...` then
  `.venv\Scripts\python.exe -m pytest -q tests_pg`, or `tools\dev_check.py "<same connection>"`.
- `build_exe.bat` builds `dist\installer\OwnPOS-Setup-x.y.z.exe` (see docs/SETUP.md part A).
- `tools/load_test.py` and `tools/ui_load_test.py` time a 20,000-item store.

OwnPOS is a separate program from Pasal POS: different program folder, settings folder, database and installer ID.
