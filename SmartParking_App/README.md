# Smart Parking Lot Allocation and Billing System

Python CLI + SQLite. Business rules live in the database (`schema.sql`), not in Python.

## Three ways to run it
**1. Double-click (needs Python 3.8+ installed):** `run.bat` on Windows, `./run.sh` on Linux/macOS. Opens the interactive menu.

**2. Standalone `SmartParking.exe` (no Python needed on the machine that runs it):** on a Windows PC with Python, double-click `build_exe.bat`. It produces `dist\SmartParking.exe`. Copy that one file anywhere and double-click it. `parking.db` is created beside it on first launch. (PyInstaller cannot cross-compile, so the .exe must be built on Windows. On Linux/macOS use `build_exe.sh`.)

**3. Command line:**
```
python parking.py             # interactive menu
python parking.py demo        # resets parking.db and runs the full scripted walkthrough
python parking.py queries     # joins, aggregates, subqueries, views
python parking.py report      # occupancy, revenue, peak hours
python parking.py register "Name" 9876543210 TS01XY1234 car
python parking.py backup      # then: python parking.py restore <file>
```
The first launch creates the database with seed data (17 slots, 6 vehicles). Menu option 12 or `demo` resets everything.
Manual flow: `search car` -> `reserve PLATE` -> `checkin PLATE` -> `checkout PLATE` -> `pay BILL AMOUNT UPI` -> `pass PLATE monthly`.
Seed plates: TS09AB1001, TS09CD2002 (car), TS10EF3003 (SUV), TS11GH4004, TS09AB1002 (two-wheeler), TS12JK5005 (truck).

## Where each deck claim lives
| Slide claim | Implementation |
|---|---|
| One active vehicle per slot | `trg_one_active_vehicle_per_slot` + unique index `ux_vehicle_one_open_session` |
| Vehicle type matching | `trg_vehicle_type_matches_slot` (entry), `trg_reservation_type_matches_slot` |
| Double-booking prevention | `trg_reservation_no_overlap`, `trg_walkin_respects_reservation` |
| Temporal integrity | `CHECK (exit_time IS NULL OR exit_time > entry_time)` |
| Gate validates reservation | `trg_gate_validates_reservation` |
| Auto-billing | `trg_session_exit`: ceil to the hour, capped at daily max, full days charged at daily max, pass holders pay 0 |
| Reservation auto-expiry | `python parking.py expire` (run from a cron or scheduler in a real deployment) |
| Payments 1:N per bill | `Payment` table, partial payments and failed attempts allowed, overpayment blocked by `trg_payment_not_over_bill` |
| Reporting | views `v_occupancy`, `v_bill_status`, `v_revenue_by_type`, `v_current_parked` |
| Backup and recovery | `backup` / `restore` commands (SQLite online backup API) |

## Known limits (say these before a reviewer does)
- SQLite only, one facility, single-writer. MySQL would need the triggers rewritten in its dialect.
- Expiry is a command, not a background job.
- No refunds, no authentication or roles in the CLI (Customer/Gate/Admin are modelled by commands, not logins).
- Tariff changes are versioned by `effective_from`, but there is no admin command for them yet.
