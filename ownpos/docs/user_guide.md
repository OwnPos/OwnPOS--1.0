# OwnPOS: User Guide

Point of sale, inventory and accounting for a liquor store with several registers sharing one store database.
It keeps the QuickBooks Point of Sale way of working: a menu down the left side, Make a Sale, Item List,
Receive inventory, Report Center and End of Day with X-Out and Z-Out.

Press **F1** on any screen to open this guide. **Find in the guide** at the top searches it.

---

## 1. How the store is set up

- One PC is the **server**: it holds the store database (PostgreSQL). It must be switched on while the store
  is open. It can also be used as a register.
- Every other PC is a **station** with its own number (1, 2, 3, 4...). The number decides which **cash
  drawer** the station uses, so two registers never mix their cash.
- Everything is saved on the server at once. A sale rung up on station 2 is in the reports on station 1 the
  same second, and receiving at the back counter shows on hand everywhere straight away.
- If a station cannot reach the server, it **stops and says so**. Nothing is saved on the station meanwhile.
  The sale on the screen stays there: press **Retry** when the network is back. A sale is never saved twice,
  even if the network breaks halfway through saving it.

The menu on the left: **POINT OF SALE** (Home, Make a Sale, Sales History, Customers, End of Day),
**INVENTORY** (Item List, Receive & Adjust, Purchasing History, Vendors), **REPORTS** (Report Center),
**EMPLOYEES** (Employees, Time Clock), **ACCOUNTING**, and **ADMIN** (Lists, Agents, Ask OwnPOS, Settings, Help).
Each employee sees only the parts their role allows (section 10).

---

## 2. Signing in, the time clock, locking

**Sign in** with your PIN on the number pad and press **Sign in**, or use your login name and password on the
**Password** tab.

**Clock in / Clock out**: type your PIN, then press **Clock in** (start of shift) or **Clock out** (end of
shift). This does not sign you in; it only records your hours. The screen lists who is clocked in.

**Switch user / lock** (bottom of the menu) locks the screen. The screen also locks by itself after a few minutes
without use (Settings > Register & cash). Whoever signs in next gets the menus of their own role.

If this PC's clock is more than 2 minutes off, a note at the bottom says so. OwnPOS always uses the
**server's** clock for receipts, punches and reports, so every station agrees on the time.

---

## 3. Make a Sale

**Adding items**

- **Scan** the barcode. A pack or case barcode adds that pack or case (for example a 12-pack).
- Or type in the box: an item number, part of the name, or the size ("tito 750"). A list drops down while you
  type; pick the line with the arrow keys and Enter. Packs and cases have their own lines in the list.
- **Several at once:** type `3*` and then scan (or `3*1001` for item number 1001): adds 3.
- The same item scanned again goes onto the same line.
- **Quick buttons** on the right (by department) are for items without a barcode, like ice or cups.
  *More... > Arrange quick buttons* chooses them.

UPCs are matched with care: a code scanned with or without its leading zeros finds the same item. If one code
fits two items, OwnPOS refuses to guess and names both, so you can fix the codes in the Item List.

**Changing a line:** click it, then **Qty (F2)**, **− 1 / + 1**, **Pack / case (Ctrl+U)**, **Price** (needs
permission), **Line discount (F3)**, or **Remove (Del)**.

**Employee discounts:** the **Employee 10% / 15% / 20%** buttons take that percentage off the whole receipt and
record which button was used; **No discount** removes it. A cashier can give up to the limit in Settings
(20% by default); more needs a manager's PIN. The buttons and their percentages are in *Lists > Discount buttons*.

**Customer (F4):** pick a customer for a house charge, store credit, or their price level (Price level 2–5).
**Walk-in** removes the customer.

**Hold (F5) / Recall (F6):** park a sale (for example while the customer gets another bottle) and serve the next
customer; recall it later on the same station.

**Price check (F8):** scan an item to see its price, packs and on hand without adding it.

**Pay (F10 or F12, or Enter on an empty scan box):** choose the payment.
- **Cash:** type the amount given or press a quick amount; the change is shown. **F9** = exact cash.
- **Credit / debit card:** the card machine is separate. Enter the amount; the last 4 digits and the approval
  code from the card slip are optional but help find a payment later.
- **Check:** the check number is required.
- **House charge:** needs a customer with a charge limit. **Store credit:** uses the customer's credit.
- Split payments: add one payment, then another, until the total is covered.

The receipt prints on this station's receipt printer (Settings > Stations & devices). With no printer, a
preview opens instead.

**Alcohol sale hours:** outside the hours in *Settings > Sale hours* (Indiana carryout hours by default) the
register **warns** before the first item. If the clock is wrong, press Continue; the sale is marked as made
outside hours. It never blocks the store.

**Clear sale** empties the screen without saving (each removed line is logged).

---

## 4. Returns, voids and reprints

**Return / void (F7):** type or pick the receipt number. Enter the quantity coming back on each line and tick
**Back to stock** if it can be sold again (untick for a broken bottle). Choose the reason and how the refund is
paid. **Void the whole receipt** gives back everything and refunds each payment the way it was paid (needs the
*void receipt* permission). Every return points to its original receipt, and returns of one line always add up
exactly to what was paid for it.

**Reprint:** prints a receipt again, marked **REPRINT - COPY n**. Reprints are logged.

**Sales History** (POINT OF SALE menu) finds any receipt: by date, type (sales / returns), station, cashier,
customer, receipt number, or an item on it. Click a receipt to see its items and payments (card last 4,
approval code, change given). **View receipt**, **Reprint** and **Return / void...** work from there.

---

## 5. End of Day

Each station has its own drawer.

1. **Open drawer** (or the register asks when you start selling): count the starting cash (float).
2. During the day: **Pay out** (cash paid out of the drawer, with a reason), **Cash drop** (cash taken to the
   safe or bank). A vendor paid in cash at delivery can be paid from *Cash in registers* in Receive & Adjust;
   it is counted on this drawer.
3. **Print X-Out**: the drawer so far, without closing it.
4. **Z-Out (close drawer)**: count the bills and coins. By default the count is **blind**: the expected amount
   is shown only after you finish. OwnPOS prints the Z-Out with sales, returns, payments by type, sales tax,
   payouts, drops and the over / short, and warns when the difference is larger than the limit in Settings.
5. When every drawer is closed: **Day Closing (whole store)**. It prints all stations together with the day's
   **sales tax**, payments and drawers, then writes the day's report workbook and summary; on the helper station
   (Settings > Stations & devices) it also makes a backup.

---

## 6. Items (Item List)

One row per item. Type straight into a cell to change it (like a spreadsheet); double-click a row for the full
form. Right-click a column heading to show or hide columns (Price 2–5, MSRP, vendor item #, ...).

- **Add:** the item number is filled in for you. Give the **department** (it sets the tax code), size, UPC,
  regular price, vendor and reorder point. All items are taxable at 7% unless you choose *Not taxable*.
- **One stock count per item.** On hand is always in the item's own unit (a can, a bottle). **Packs & cases**
  adds a 6-pack, 12-pack or case: how many cans it holds, its own price (optional) and its own barcode. Selling or
  receiving a case changes the same on-hand number by 24 at once.
- **Price levels:** Regular price and levels 2–5 (for example an employee price). A customer set to a level gets
  that price; an empty level sells at the regular price.
- **Change prices:** many prices at once (by %, by $, set to $, or to a margin over cost), with rounding to
  .99 or .49 endings, a **preview** and a reason. Old prices are kept in **Price history**.
- **Print shelf tags:** label sizes from 2.25 x 1.25 in; the barcode is the UPC (or the item number).
- **Copy as new item:** start a new item from the selected one (the same spirit in another size).
- **Item history:** every sale, return, receiving, vendor return, count and adjustment of the item with the
  running on hand.
- **On hand** can be corrected by typing the counted number into the cell (posted as a count, logged). The
  **average cost** can be corrected only by the owner, with a reason (posted to 5100).
- **Make inactive:** an item you no longer sell; its history stays.
- **Import from Excel** (section 13).

---

## 7. Receive & Adjust, purchasing

**Receive inventory**: choose the vendor, type the vendor's **invoice number** and date, then **scan** the
bottles or cases (each scan adds one more) or pick items in the lines. The unit starts in the item's buying unit;
the cost starts at the last cost. Choose *Not paid yet* (the bill is owed to the vendor) or the account it was
paid from. **Save receiving voucher**: on hand goes up on every station at once.

OwnPOS checks before saving: the **same invoice number** from the same vendor already received (it asks, so an
invoice is not counted twice), costs of $0.00, and costs more than 30% away from the last cost (a typing
mistake).

**Return to vendor**: goods going back (breakage the vendor credits, wrong items, recalls). The vendor's credit
is taken off what you owe them, or recorded as money received. You cannot return more than is on hand.

**Purchase orders**: **New PO**, or **Draft POs from low stock** (the reorder helper makes one per vendor for
items at or below their reorder point). **Mark ordered**, then **Receive this PO** fills the receiving form.

**Breakage & waste**: broken bottles, samples, spoiled items, with a reason; valued at cost.

**Physical count / opening stock**: choose a department (or all), type the counted cases and loose bottles, and
**Save count**. Count when the store is closed, or one department at a time. *Opening stock mode* is for the
first day only (quantity and cost; credits Opening balance equity).

**Vendor bills & payments**: what you owe each vendor (overdue shown in red), the open bills (oldest paid first),
and **Record payment** (check number, account, date).

**Purchasing History** (INVENTORY menu): every receiving voucher and vendor return with its lines, purchase
orders by status (with the voucher each was received as), and vendor payments.

---

## 8. Customers and vendors

**Customers**: name, phone, price level, discount %, **charge limit** (for house charges), terms, tax exempt.
**History** shows their receipts and their house-charge account with a running balance. Payments on a house
charge: *Make a Sale > More... > Payment on house charge account*.

**Vendors**: name, contact, phone, your account number with them, terms in days (the due date of each bill).
**History** shows their vouchers and returns and the account (what you owe).

---

## 9. Reports (Report Center) and Ask OwnPOS

Reports are grouped like QuickBooks: Sales, Inventory, Purchases, Vendors & Payables, Customers & Receivables,
Employees & Time Clock, Cash & Drawers, Sales Tax, Company & Financial, Accountant and Lists.

Click a report; it runs at once. Choose the dates (Today, This week, This month, This quarter, Last quarter...
or Custom) and filters, then **Run report**. Sort by clicking a column heading, filter with the box above the
grid. **Export to Excel** writes a formatted workbook (real dates and amounts, totals, filters); **Save as PDF**
writes a printable copy with page numbers. **More > Memorize this report** keeps the report with its dates and
filters under ★ Memorized.

**Ask OwnPOS** (ADMIN menu): type a question such as *"best sellers in beer last week"*, *"sales tax last
quarter"*, *"who owes us money?"*, *"how many Tito's did we sell in September?"*. The answer comes from the same
reports, so every number is exact, and the report it used is shown under the answer (**Open in Report Center**,
Excel, PDF). Questions it does not understand can be passed to an optional AI fallback, only if the owner has
switched it on (Settings > AI assistant).

---

## 10. Employees, roles and the time clock

**Employees**: login name, full name, job title, role, PIN and password. Pay type and rate are visible only to
people with the *payroll* permission.

**Roles and Permissions** (owner only): each role is a list of tick boxes (sell, discounts, returns, voids,
receive, costs, reports, financial reports, accounting, payroll, settings...). Give or take away a permission
with one click; it applies the next time the employee signs in. The owner role always has everything.

**Time Clock**: who is clocked in now, every punch, and hours by workweek with **overtime** (over 40 hours in a
workweek by default; the workweek starts on Sunday unless changed in Settings). A punch that runs past midnight
into a new workweek counts in the week each hour is worked. Managers with *fix time clock punches* can add a
missed punch or correct one (a reason is required and logged). With the payroll permission the table also shows
an **estimate of gross pay** for the accountant (no tax withholding is calculated).

---

## 11. Accounting

Every sale, return, receiving voucher, payout and payment posts to the books by itself (double entry, in
cents). The Accounting screen shows this month's net sales, gross and net profit, the sales tax payable, cash +
bank, and every account's balance (double-click for its General Ledger).

- **Record expense**: rent, utilities, supplies..., paid from an account or owed to a vendor.
- **Deposit / transfer**: card settlements (1030 → checking, with the processing fee), cash from the safe to the
  bank, checks deposited.
- **Pay sales tax**: after filing the ST-103 return, enter what was paid to the Indiana Department of Revenue
  and any collection allowance or rounding the return lets the store keep.
- **Manual journal** and **Opening balance**: owner only.
- **Sales Tax** reports: *Sales Tax Liability (ST-103)* shows Lines 1, 2, 4 and 5 for any period (quarter by
  default) and the tax actually charged on receipts; *Sales Tax by Day*. The Day Closing prints the day's tax.
  The return itself is filed by the store or its accountant (INTIME); OwnPOS does not file it.

---

## 12. Settings, stations, backups, licence

**Settings** (owner / settings permission): store details for the receipt, receipt header and footer, register
rules (drawer required, blind Z-Out, discount limit, over/short warning, selling below zero on hand, screen
lock), **sale hours** per weekday, **stations** (names, each station's receipt printer, turning a retired PC
off), devices of *this* computer (cash drawer port, pole display), employees & pay week, **licence**,
**backups**, theme, menu names, AI fallback.

**Backups**: made automatically on the helper station at each Day Closing and whenever the last backup is older
than 24 hours, and on demand with **Back up now**. Each backup file is read back to prove it is complete. Keep the
second backup folder on a USB drive or a cloud-synced folder. **Restore** never overwrites the store: it creates a
new database from the backup and checks that its books balance; you then point the stations at it
(**Help > Connection...**).

**Licence**: the licence says how many stations may sell and until when. Reports, Excel/PDF and backups always
work, even without a licence. A warning appears 14 days before the licence ends.

**Training database**: the server setup can also create *ownpos_demo*. Connect a station to it (Help >
Connection...), and its first start offers to fill it with a **sample store**. Every receipt there says
**TRAINING - NOT A REAL SALE**. Use it to train new cashiers.

---

## 13. Bringing in lists from Excel (QuickBooks POS)

Items, vendors and customers can be imported from **Excel (.xlsx, .xls) or CSV**: export each list from
QuickBooks Point of Sale to Excel, then in OwnPOS open the list and press **Import from Excel**.

1. OwnPOS finds the heading row by itself (title lines above it are fine) and **matches the columns**
   (QuickBooks names such as *Item Name, UPC, Regular Price, Average Unit Cost, On-Hand Qty, Vendor*). Check each
   match and change any that are wrong; the layout can be remembered.
2. The next screen shows **every row** before anything is saved: Add, Update, or Skip with the reason (for
   example a UPC that Excel turned into 1.23457E+11). Departments, vendors and sizes named in the file are
   created.
3. **Import**: everything is saved in one go; a row that fails is skipped and listed, the rest are saved.

Opening on-hand and cost are added **only for rows that are new**, so importing the same file twice never
doubles the stock. Do the import on the day you switch, after the last sale in QuickBooks POS.

Tip: in Excel, format UPC columns as **Text** before exporting, so long codes keep every digit.

---

## 14. When something goes wrong

| What you see | What to do |
|---|---|
| "Server not reachable - sales are paused" | Check the server PC is on and this PC's network. Press **Retry**. The sale on screen is kept. |
| "The connection broke while saving" | Press Retry when it is back: OwnPOS checks whether that sale was saved before saving it, so it is never charged twice. |
| No receipt prints, a preview opens | Check the printer is on; choose it in Settings > Stations & devices (Receipt printer of THIS station). |
| "Outside alcohol sale hours" | Check the clock. If the time is right, do not sell alcohol. If the clock is wrong, Continue (the sale is marked). |
| "This station may not sell (licence)" | Ask the owner. Reports, exports and backups still work. |
| A code fits two items | Fix the UPC of one of them in the Item List. |
| Drawer over / short | Recount; check payouts, drops and cash paid to vendors on the Z-Out. |
