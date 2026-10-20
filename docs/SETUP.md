# OwnPOS: step-by-step setup

For Delock's Liquors (4 registers, one server PC). Do the steps in order. Parts A and E are done once by Aayush
on his own PC; parts B–D in the store. Times are rough.

---

## A. Make the installer (Aayush's PC, once per version) — 30 min the first time

1. Install **Python 3.12 or 3.13** (python.org; tick *Add python.exe to PATH*) and **Inno Setup 6.4 or newer**
   (jrsoftware.org/isdl.php).
2. Unzip `OwnPOS-latest.zip` to a folder of its own, e.g. `C:\OwnPOS-src`. Double-click **install_windows.bat**.
3. **Licence key, once ever:** open a Command Prompt in that folder and run
   `.venv\Scripts\python.exe tools\licence_tool.py init`
   Choose a passphrase (12+ characters). The signing key is saved as `OwnPOS-signing-key.pem` in your Windows user
   folder — **copy it to a USB stick and keep it safe**. Without it you cannot make licences for stores that
   already run OwnPOS. Never put it in the project folder or send it to anyone.
4. (Recommended) Install PostgreSQL on this PC too (part B, steps 1–2) so the tests can run, then in the Command
   Prompt: `set OWNPOS_TEST_PG=host=localhost port=5432 user=postgres password=YOUR-POSTGRES-PASSWORD`
5. Run **build_exe.bat** in the same Command Prompt. It refuses to build if the licence key is missing, runs the
   tests (when step 4 was done), builds the program, self-tests it, and asks for the **installer password** twice
   (10+ characters: letters, digits and `- _ . @ # !`). Keep that password safe: the setup file will not install
   without it, and its files cannot be unpacked without it.
6. The result: `dist\installer\OwnPOS-Setup-1.0.0.exe`. Copy it to a USB stick.

## B. The server PC (in the store) — 30 min

1. Download **PostgreSQL for Windows** (the EDB installer from postgresql.org/download/windows; version 16 or 17
   recommended, 13–18 work). Run it with the defaults: port **5432**, and choose a **password for the user
   `postgres`** — write it down and keep it in the safe. pgAdmin and Stack Builder are not needed.
2. Make sure this PC's network is set as **Private** in Windows (Settings > Network & internet > your network >
   Private), and give it a fixed name or a reserved IP address in the router (e.g. `STORE-SERVER`).
3. Run **OwnPOS-Setup-1.0.0.exe** (type the installer password). Start OwnPOS.
4. On the first screen choose the tab **Set up THIS PC as the store server**:
   - type the `postgres` password from step 1;
   - leave *Store database name* as `ownpos_store`, keep *Also make a sample store database for training* ticked;
   - **1. Create the store database**;
   - **2. Allow stations to connect** — Windows asks for administrator permission; say Yes. (This lets only PCs on
     the store's own network connect, always with a password, and opens the PostgreSQL port in the firewall for
     Private networks.);
   - **3. Save station setup file...** to a USB stick (it contains the database password: delete it from the
     stick after part C);
   - **Use this PC as station 1 and continue**.
5. **Activate:** the activation screen shows the **Store ID**. Send it to Aayush (part E). Paste or load the
   licence he sends and press **Activate**. (Until then *Open without selling* lets you look around.)
6. **First run:** store name, address, *City, IN ZIP*, phone, ATC permit # (optional, printed on receipts), how
   often sales tax is filed, and the **owner** login (password 8+ characters and a PIN). Keep the owner password
   safe.
7. In **Settings > Stations & devices**: rename station 1 (e.g. "Back office"), choose its receipt printer, and
   leave *Background helpers and automatic backups run on station #* at **1** — the server PC has the PostgreSQL
   tools that backups need.
8. In **Settings > Backups**: choose a **second backup folder** on a USB drive or a cloud-synced folder, then
   **Back up now** once to check it works.

## C. Each register (stations 2, 3, 4) — 10 min each

1. Run **OwnPOS-Setup-1.0.0.exe** (installer password). Start OwnPOS.
2. Tab **Connect this station to the store server**: **Load station setup file...** (the USB stick from B4), then
   give **this station's number** (2, 3, 4 — each different) and a name (e.g. "Front counter"). **Test
   connection**, then **Save and continue**. Sign in.
3. **Settings > Stations & devices**: choose **this station's receipt printer**; if the cash drawer is plugged into
   the receipt printer leave *Same as the receipt printer*, otherwise give its port. **Test drawer**.
4. The licence counts stations: a fifth PC can open reports but cannot sell until a bigger licence is installed or
   another station is turned off.

## D. Moving over from QuickBooks Point of Sale — on the switch-over day

1. **Practice first** on the training database: on any station, *Help > Connection...*, change the database to
   `ownpos_demo`, restart, tick *fill it with the sample store*. Train the cashiers there (every receipt says
   TRAINING). Change the connection back to `ownpos_store` afterwards.
2. In QuickBooks POS, export the **Item List**, **Vendor List** and **Customer List** to Excel (format UPC
   columns as Text first, so long codes keep every digit).
3. In OwnPOS, in this order: **Vendors > Import from Excel**, then **Customers** (with their opening balances, if
   any), then **Item List > Import from Excel** — check the column matches and the preview, then **Import**. The
   on-hand quantities and costs of new items become the **opening stock**.
4. **Lists > Departments, Sizes, Discount buttons, Price levels**: check them. **Employees**: add each employee
   with a PIN; **Roles and Permissions** to fine-tune what each role may do.
5. **Accounting > Opening balance** (owner): cash in the safe, the checking account balance, sales tax payable
   not yet paid, loans, equipment — as of the switch-over day.
6. Do one test sale and one return on each register, run an X-Out, then void the test receipts (Return / void >
   Void the whole receipt). Run **Day Closing** at night.

## E. Making a licence (Aayush's PC)

`.venv\Scripts\python.exe tools\licence_tool.py issue`
It asks for the store name, the **Store ID** from the store's activation screen (or Settings > Licence > Copy store
ID), the number of stations (4 for Delock's) and an expiry date (empty = never). It writes a
`.ownpos-licence` file: send it to the store, which loads it on the activation screen or in Settings > Licence.
`tools\licence_tool.py verify FILE` checks a licence file.

## Every day

Open drawer → sell → X-Out if wanted → **Z-Out on each station** → **Day Closing** (sales tax of the day,
report workbook, summary and a backup). Check the Home page's agent findings now and then (low stock, margins,
backups, sales tax due date).

## Updating OwnPOS later

Build the new installer (part A, step 5), run it on the server first, then on each station. The store database is
never touched by installing; when a new version needs a newer database layout it updates it on its first start,
under the store-wide lock, after Day Closing is best. Make a backup first.

## If the server PC breaks

Install PostgreSQL and OwnPOS on another PC (part B, steps 1–4, same database name), restore the newest backup
(**Settings > Backups > Restore a backup into a NEW database**), then point every station at the restored database
(*Help > Connection...*). Install the store's licence again if asked (the Store ID travels with the database).
