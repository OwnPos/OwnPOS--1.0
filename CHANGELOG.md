# Changelog

## 1.0.0 - 2026-10-17

First version of OwnPOS (built from the Pasal POS 0.9.1 design, for a US liquor store with several registers).

- PostgreSQL store database on a server PC; stations connect over the store network. Store-wide write lock,
  sale keys against double saving, server clock for every station, a clear stop-and-retry when the server is not
  reachable. Tested with 4 stations selling at once.
- Indiana sales tax 7% (invoice basis, half-up rounding), ST-103 report lines, sales tax payment with collection
  allowance, daily tax on the Day Closing.
- Register: UPC lookup variants, packs and cases from one stock count, employee discount buttons, price levels,
  card last-4 / approval code, sale-hours warning, Clear sale button.
- Per-station drawers with blind Z-Out and over/short warning; Day Closing for the whole store.
- Item List, Receive & Adjust (scan to receive, duplicate-invoice and cost-jump warnings, purchase orders,
  return to vendor, breakage, counts with cases + loose bottles, vendor bills and payments).
- Sales History, Purchasing History, Item / Customer / Vendor history.
- Report Center with 58 reports, Excel (real dates and amounts) and PDF output; Ask OwnPOS (no AI) with an
  optional Claude Haiku fallback.
- Time clock with FLSA weekly overtime (punches past midnight split at the week boundary), payroll estimate,
  Roles and Permissions editor.
- Smart Excel / CSV import of items, vendors and customers (QuickBooks POS column names, .xls and .xlsx).
- Signed licences (Ed25519) and a password-protected, encrypted installer; training database with a sample store.
