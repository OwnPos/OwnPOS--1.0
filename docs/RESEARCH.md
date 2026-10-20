# OwnPOS — research notes (checked 16 Oct 2026)

Everything OwnPOS does because of a law, a tax rule or another product is listed here with its source,
so it can be re-checked. Where a source did not say something, that is written down too.

## Indiana sales tax
| Fact | Source |
|---|---|
| State gross retail tax is **7%** of gross retail income (IC 6-2.5-2-2(a)) | [IC 6-2.5-2-2 (Justia)](https://law.justia.com/codes/indiana/title-6/article-2-5/chapter-2/section-6-2-5-2-2/) |
| Rounding: carry the tax to the third decimal; if that digit is greater than four, round up to the next cent (IC 6-2.5-2-2(b)) | same |
| A seller may round on an **item basis or an invoice basis**, but may not round to avoid tax (IC 6-2.5-2-2(c)) | same |
| No local or district add-on sales taxes in Indiana | [TaxCloud – Indiana](https://taxcloud.com/sales-tax/indiana/) |
| Returns filed and paid electronically (INTIME); monthly / quarterly / annual frequencies; due the 20th of the month after the period (annual: 31 Jan); a $0 return is still required | same |
| Form ST-103 lines: 1 Total sales, 2 Exemptions/deductions, 3 Excluded (marketplace) sales, 4 Taxable sales, 5 Tax due, 6 Collection allowance (only if filed on time; % not stated in the instructions), 7 Use tax, 8 Interest, 9 Penalty, 10 Prior payments, 11 Amount due | [ST-103 instructions (IN DOR)](https://www.in.gov/dor/files/st-103inst.pdf) |

**OwnPOS choice:** tax computed on the **invoice basis** (sum of taxable lines × 7%, rounded half-up to the cent),
then spread over the lines so every line knows its own share (needed for exact returns). The store's
"Sales tax" report shows ST-103 lines 1, 2, 4 and 5 for any period (quarter by default). The collection
allowance is left to the accountant because the percentage was not found in an official source.

## Indiana alcohol rules (carryout)
| Fact | Source |
|---|---|
| ID must be checked for carryout buyers who appear under 40 (IC 7.1-5-10-23) | [Indiana ATC – Rules and laws](https://www.in.gov/atc/isep/alcohol-enforcement/rules-and-laws/), [Wikipedia summary](https://en.wikipedia.org/wiki/Alcohol_laws_of_Indiana) |
| Acceptable ID: photo ID such as driver's licence, state ID, U.S. government ID | Indiana ATC page |
| Sale hours Mon–Sat 7:00 am – 3:00 am (next morning); Sunday 12:00 noon – 8:00 pm (carryout, since 2018 SB 1) | [ServSafe Indiana key laws](https://servsafe.com/downloads/pdfs/ssa-key-laws/indiana), [Banks & Brower](https://banksbrower.com/2025/01/22/indianas-sunday-alcohol-law/) |
| Christmas Day ban was lifted in 2015 (HB 1542) | Wikipedia summary (secondary source) |
| Package liquor stores may sell cold beer | Wikipedia summary |
| Package liquor store sellers must be 21 | ServSafe Indiana key laws |

**OwnPOS choice (Aayush, 16 Oct):** no ID prompt (staff trained; everyone is ID'd at the door). Sale hours are a
**warning only**, editable in Settings, because a wrong PC clock must never stop the store.

## Wages (for the optional pay calculation)
| Fact | Source |
|---|---|
| Overtime: at least 1.5 × regular rate for hours over 40 in a workweek | [U.S. DOL – FLSA](https://www.dol.gov/agencies/whd/FLSA) |
| Federal minimum wage $7.25/hour (since 24 Jul 2009); the higher of state/federal applies | same |

**OwnPOS choice:** hours and an estimated **gross** pay only (regular + overtime). No withholding or payroll tax —
that stays with the accountant, as today.

## QuickBooks Point of Sale (what the store is used to)
| Fact | Source |
|---|---|
| QB Desktop POS 19.0 and its services were discontinued 3 Oct 2023: payments, support and security updates ended | [Intuit discontinuation notice](https://quickbooks.intuit.com/learn-support/en-us/help-article/discontinued-offering/quickbooks-desktop-point-sale-discontinuation/L407dYuCG_US_en_US) |
| POS 2013 layout: side toolbar (customisable), Navigator home page with workflow links, Quick Find, Items List, Report Center, Make a Sale, In Progress, End of Day | [QB POS 2013 Getting Started Guide](https://http-download.intuit.com/http.intuit/OpenCms/sites/default/QBSupportSite/PDFs/POSGuides/POSGSG_2013.pdf) |
| Items: Department (required), Item name, item type, on-hand qty, cost, vendor, regular price **plus up to four alternate price levels**, size and attribute | same; also [POS v18 guide](https://abilitybusiness.com/wp-content/uploads/2020/06/QuickBooks-Desktop-Point-of-Sale-V18-User-Guide.pdf) |
| Security: employees with passwords, security groups (custom groups), Sysadmin user | 2013 guide |
| Network: one **Server Workstation** holds the data and must be running; Client workstations use it over the network | 2013 guide |
| Item list columns can be added, removed and dragged | v18 guide |

The official guides do **not** list the exact report names. OwnPOS therefore uses the report families of
QuickBooks Desktop (which the store's accountant knows) with QuickBooks' own names where they apply:

| QuickBooks report family | Examples (QuickBooks' names) |
|---|---|
| Company & Financial | Profit and Loss Standard / Detail, Balance Sheet Standard / Detail |
| Customers & Receivables | A/R Aging Summary / Detail, Open Invoices |
| Sales | Sales by Customer Summary / Detail (OwnPOS adds Sales by Item / Department / Associate / Hour) |
| Vendors & Payables | A/P Aging Summary / Detail, Vendor Balance Summary / Detail, Unpaid Bills Detail |
| Purchases | Purchases by Vendor Summary / Detail, Open Purchase Orders |
| Inventory | Inventory Valuation Summary / Detail, Physical Inventory Worksheet |
| Employees & Payroll | (OwnPOS: Time Clock Summary / Detail, Sales by Associate) |
| Banking | Deposit Detail |
| Accountant & Taxes | Trial Balance, General Ledger, Audit Trail |
| List | Account Listing, Price Listing, Customer Phone List, Vendor Phone List |

Source: [QuickBooks Desktop – Understand reports](https://quickbooks.intuit.com/learn-support/en-us/help-article/memorize-reports/understand-reports/L6mJBjn2h_US_en_US).

## What the leading liquor-store systems prioritise (2026 reviews)
From [FitSmallBusiness – best liquor store POS 2026](https://fitsmallbusiness.com/best-liquor-store-pos-systems/),
[POS Nation – must-have features](https://www.posnation.com/blog/liquor-pos-must-have-features),
[Spartan POS / BizTracker features](https://spartanpos.com/pages/liquor-store-pos-system-features-biztracker-infinity):

| Feature | Who leads | In OwnPOS v1? |
|---|---|---|
| Fast scanning, quick lookup, touch-friendly register | all | **Yes** – indexed UPC lookup, < 50 ms target on 20,000 items |
| Case break / pack pricing from one stock count | Bottle POS, KORONA | **Yes** – packs/cases are units of one item |
| Invoice receiving matched by UPC; distributor cost changes | Scotch, Bottle POS, mPower | **Yes** – receive by scanning; cost change shown with margin |
| Margin protection alerts, bulk product editing | Scotch | **Yes** – margin agent, bulk price change |
| Suggested purchase orders | Bottle POS | **Yes** – reorder agent |
| Blind drawer counts, forced drawer close | KORONA, POS Nation | **Yes** – per-station Z-Out, blind count option |
| Employee permissions, manager approval for refunds/voids/price overrides | all | **Yes** |
| Discount / refund / void / tender / employee-activity reports | Spartan, mPower (70+ reports) | **Yes** |
| Barcode / shelf labels | Spartan | **Yes** (from the base program) |
| ID scanning | POS Nation | No (not wanted) |
| Mix-and-match, loyalty, dual pricing, e-commerce, kegs, allocations | various | No (not used by the store); the design leaves room |
| Offline selling | mPower | No in v1 (decision: stop safely) |

## Several computers on one database
SQLite's authors advise against sharing an SQLite file over a network folder when several computers write:
network file systems do not keep it consistent; use a client/server engine such as PostgreSQL
([SQLite forum, D. Richard Hipp](https://sqlite.org/forum/forumpost/c9d07dd325)).
PostgreSQL Windows installers (versions 13–18) are provided by EDB and include pgAdmin
([postgresql.org/download/windows](https://postgresql.org/download/windows/)).
OwnPOS is tested on PostgreSQL 16 in the build workspace and uses only features present in 13+.

## Installer password
Inno Setup 6.4+ has built-in encryption (XChaCha20, key from PBKDF2-HMAC-SHA256; ISCrypt.dll no longer used)
([Inno Setup 6.4.1 what's new](https://unpkg.com/innosetup@6.4.1/bin/whatsnew.htm)).
With `Password=` and `Encryption=yes`, the installer asks for the password and the files inside cannot be
extracted without it.


## AI assistant options (researched 17 Oct 2026; Aayush chose C)
Principle: the model never does arithmetic. OwnPOS's reports compute every figure exactly (integer cents in
SQL); a model only chooses the report/dates/filters and words the answer, and the answer shows the report used.
| Option | Cost | Notes |
|---|---|---|
| A. Claude Haiku 5.5, reports-only tools | $0.10 / MTok input, $0.50 / MTok output (prompts up to 100K tokens), cache hits $0.01 / MTok — about $0.002 per question, ~$2–3 / month at 30 questions/day | [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing). Needs internet; questions and report rows go to Anthropic |
| B. Local small model (Ollama: Qwen3-4B-Instruct-2507 or Phi-4-mini, ~3.4 GB RAM at Q4) on a back-office PC | $0 | CPU only: roughly 10–20 tokens/s for 3–4B models ([guide](https://bestllmfor.com/guide/llm-local-cpu-sans-gpu/)); tool-calling reliability drops below ~4B parameters ([summary](https://d-central.tech/local-llm-agent-capability/)); Qwen3-4B-Instruct-2507 BFCL 35.7% ([benchmarklist](https://benchmarklist.com/models/qwen3-4b-instruct-2507/)). Small fine-tuned models can be competitive on text-to-SQL but trail large ones ([SLM-SQL](https://arxiv.org/html/2507.22478v1)) |
| **C. "Ask OwnPOS" without AI + Haiku only as an optional fallback** (chosen) | $0 for understood questions; fallback well under $1 / month expected | Instant, works offline, cannot invent numbers; limited to the question patterns it is taught |


## Day-to-day tools (gap check, 17 Oct 2026)
Sources: [CPA Practice Advisor – QuickBooks POS review](https://www.cpapracticeadvisor.com/2014/03/13/2014-review-of-quickbooks-point-of-sale/14490/)
(Report Center by category, memorized reports, Best Sellers, Most Common Returns, audit trail, employee rights by
group, gift/loyalty cards, Quick Pick groupings, lookups during a sale), [Dancing Numbers – QB POS manual overview](https://www.dancingnumbers.com/quickbooks-point-of-sale-manual/)
(Pro: gift cards and layaways, customer orders, holding a sale), [POS Nation – liquor POS must-haves](https://www.posnation.com/blog/liquor-pos-must-have-features)
(case break, mix-and-match, receiving and reordering, best-seller / dead-stock, label printers, returns).

| Daily tool | OwnPOS |
|---|---|
| Sales History (find, view, reprint, return a receipt) | **Added** 17 Oct |
| Receiving / voucher history, PO history, vendor payments history | **Added** (Purchasing History) |
| Item history with running on-hand | **Added** |
| Customer and vendor history / account | **Added** |
| Return to vendor (return voucher, credit or refund) | **Added** 17 Oct |
| Copy an item to make a new size | **Added** |
| Hold / recall a sale, price check, reprint, no-sale, payouts, drops | already in the register |
| Best sellers, slow movers, returns and voids, reorder list | already in the Report Center |
| Store credit, house charge, payments on account | already there |
| Gift cards, layaways, customer special orders, loyalty, mix-and-match | **Not built** — not used by the store per earlier answers; ask before adding |
| Lottery sales / payouts | **Not built** — ask Aayush whether the store sells Hoosier Lottery |
