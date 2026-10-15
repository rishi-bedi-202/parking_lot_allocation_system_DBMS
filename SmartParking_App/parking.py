#!/usr/bin/env python3
"""Smart Parking Lot Allocation and Billing System - Python CLI over SQLite.

All business rules (one vehicle per slot, type matching, temporal integrity,
billing) live in schema.sql as triggers/CHECKs. This file only collects input
and calls the database.

Usage:
  python parking.py                           interactive menu (also what the .exe opens)
  python parking.py register NAME PHONE PLATE TYPE
  python parking.py init                     create parking.db + seed data
  python parking.py demo                      scripted end-to-end walkthrough (re-inits DB)
  python parking.py search car [--floor 1]
  python parking.py reserve KA01AB1234 --slot 5 --hours 2
  python parking.py checkin KA01AB1234 [--slot 5]
  python parking.py checkout KA01AB1234
  python parking.py pay 1 100 UPI [--failed]
  python parking.py pass KA01AB1234 monthly
  python parking.py expire
  python parking.py report                    occupancy, revenue, peak hours
  python parking.py queries                   run queries.sql
  python parking.py backup / restore FILE     storage-layer backup and recovery
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

if getattr(sys, "frozen", False):          # running as a PyInstaller executable
    RES = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))   # bundled schema.sql / queries.sql
    DATA = Path(sys.executable).parent                                  # parking.db lives beside the .exe
else:
    RES = DATA = Path(__file__).resolve().parent
DB = DATA / "parking.db"
FMT = "%Y-%m-%d %H:%M:%S"
PASS_DAYS = {"monthly": 30, "quarterly": 90, "annual": 365}
PASS_PRICE = {"monthly": 1500, "quarterly": 4000, "annual": 14000}


def ts(dt=None):
    return (dt or datetime.now()).strftime(FMT)


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def show(rows, title=None):
    if title:
        print(f"\n== {title} ==")
    rows = [dict(r) for r in rows]
    if not rows:
        print("(no rows)")
        return
    cols = list(rows[0].keys())
    w = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  ".join(c.ljust(w[c]) for c in cols))
    print("  ".join("-" * w[c] for c in cols))
    for r in rows:
        print("  ".join(str(r[c]).ljust(w[c]) for c in cols))


# ---------------------------------------------------------------- setup
SEED = {
    "types": ["two-wheeler", "car", "SUV", "truck"],
    # (floor_no, zone, type, count)
    "layout": [(1, "A", "two-wheeler", 4), (1, "B", "car", 4),
               (2, "C", "car", 4), (2, "D", "SUV", 3), (2, "E", "truck", 2)],
    # type: (hourly, daily_max)
    "tariff": {"two-wheeler": (15, 80), "car": (40, 250), "SUV": (60, 350), "truck": (100, 600)},
    "customers": [("Asha Rao", "9000000001"), ("Vikram Singh", "9000000002"),
                  ("Meera Nair", "9000000003"), ("Rohit Das", "9000000004"),
                  ("Sana Khan", "9000000005")],
    "vehicles": [(1, "car", "TS09AB1001"), (2, "car", "TS09CD2002"), (3, "SUV", "TS10EF3003"),
                 (4, "two-wheeler", "TS11GH4004"), (5, "truck", "TS12JK5005"),
                 (1, "two-wheeler", "TS09AB1002")],
}


def init_db():
    if DB.exists():
        DB.unlink()
    con = connect()
    con.executescript((RES / "schema.sql").read_text())
    tid = {}
    for n in SEED["types"]:
        tid[n] = con.execute("INSERT INTO VehicleType(name) VALUES (?)", (n,)).lastrowid
    fid = con.execute("INSERT INTO Facility(name,address) VALUES ('Woxsen Central Parking','Hyderabad')").lastrowid
    floors, zones = {}, {}
    for fl, zn, ty, cnt in SEED["layout"]:
        if fl not in floors:
            floors[fl] = con.execute("INSERT INTO Floor(facility_id,floor_no) VALUES (?,?)", (fid, fl)).lastrowid
        zid = con.execute("INSERT INTO Zone(floor_id,zone_name) VALUES (?,?)", (floors[fl], zn)).lastrowid
        for i in range(1, cnt + 1):
            con.execute("INSERT INTO Slot(zone_id,slot_code,type_id) VALUES (?,?,?)",
                        (zid, f"{zn}{i}", tid[ty]))
    for ty, (h, d) in SEED["tariff"].items():
        con.execute("INSERT INTO Tariff(type_id,hourly_rate,daily_max,effective_from) VALUES (?,?,?,'2020-01-01')",
                    (tid[ty], h, d))
    for name, phone in SEED["customers"]:
        con.execute("INSERT INTO Customer(name,phone) VALUES (?,?)", (name, phone))
    for cid, ty, plate in SEED["vehicles"]:
        con.execute("INSERT INTO Vehicle(customer_id,type_id,plate) VALUES (?,?,?)", (cid, tid[ty], plate))
    con.commit()
    con.close()
    print(f"Database created: {DB}")


# ---------------------------------------------------------------- operations
def vehicle(con, plate):
    v = con.execute("SELECT v.*, vt.name AS type FROM Vehicle v JOIN VehicleType vt USING(type_id) WHERE plate=?",
                    (plate,)).fetchone()
    if not v:
        raise ValueError(f"Unknown vehicle {plate}")
    return v


def search(con, vtype, floor=None, at=None, hours=1):
    at = at or datetime.now()
    end = at + timedelta(hours=hours)
    sql = """
      SELECT s.slot_id, f.floor_no, z.zone_name, s.slot_code
      FROM Slot s JOIN Zone z USING(zone_id) JOIN Floor f USING(floor_id)
      JOIN VehicleType vt ON vt.type_id = s.type_id
      WHERE vt.name = ? AND s.status = 'FREE'
        AND NOT EXISTS (SELECT 1 FROM Reservation r WHERE r.slot_id = s.slot_id AND r.status='ACTIVE'
                        AND r.start_time < ? AND r.expiry_time > ?)
        {fl}
      ORDER BY f.floor_no, z.zone_name, s.slot_code"""
    args = [vtype, ts(end), ts(at)]
    fl = ""
    if floor is not None:
        fl = "AND f.floor_no = ?"
        args.append(floor)
    return con.execute(sql.format(fl=fl), args).fetchall()


def reserve(con, plate, slot_id=None, hours=2, at=None):
    at = at or datetime.now()
    v = vehicle(con, plate)
    if slot_id is None:
        free = search(con, v["type"], at=at, hours=hours)
        if not free:
            raise ValueError("No free slot for that vehicle type")
        slot_id = free[0]["slot_id"]
    cur = con.execute("INSERT INTO Reservation(vehicle_id,slot_id,start_time,expiry_time) VALUES (?,?,?,?)",
                      (v["vehicle_id"], slot_id, ts(at), ts(at + timedelta(hours=hours))))
    con.commit()
    return cur.lastrowid, slot_id


def checkin(con, plate, slot_id=None, at=None):
    at = at or datetime.now()
    v = vehicle(con, plate)
    res = con.execute("""SELECT * FROM Reservation WHERE vehicle_id=? AND status='ACTIVE'
                         AND start_time <= ? ORDER BY start_time LIMIT 1""",
                      (v["vehicle_id"], ts(at))).fetchone()
    res_id = res["reservation_id"] if res else None
    if res:
        slot_id = res["slot_id"]
    elif slot_id is None:
        free = search(con, v["type"], at=at, hours=1)
        if not free:
            raise ValueError("No free slot available")
        slot_id = free[0]["slot_id"]
    cur = con.execute("INSERT INTO ParkingSession(vehicle_id,slot_id,reservation_id,entry_time) VALUES (?,?,?,?)",
                      (v["vehicle_id"], slot_id, res_id, ts(at)))
    con.commit()
    return cur.lastrowid, slot_id, res_id


def checkout(con, plate, at=None):
    at = at or datetime.now()
    v = vehicle(con, plate)
    s = con.execute("SELECT session_id, entry_time FROM ParkingSession WHERE vehicle_id=? AND exit_time IS NULL",
                    (v["vehicle_id"],)).fetchone()
    if not s:
        raise ValueError(f"{plate} has no open session")
    entry = datetime.strptime(s["entry_time"], FMT)
    at = at.replace(microsecond=0)                    # compare at the precision we store
    if at <= entry:                                   # exit must be strictly after entry
        at = entry + timedelta(seconds=1)
    con.execute("UPDATE ParkingSession SET exit_time=? WHERE session_id=?", (ts(at), s["session_id"]))
    con.commit()
    return con.execute("SELECT * FROM v_bill_status WHERE session_id=?", (s["session_id"],)).fetchone()


def pay(con, bill_id, amount, method, failed=False, at=None):
    con.execute("INSERT INTO Payment(bill_id,amount,method,status,paid_at) VALUES (?,?,?,?,?)",
                (bill_id, amount, method, "FAILED" if failed else "SUCCESS", ts(at)))
    con.commit()
    return con.execute("SELECT * FROM v_bill_status WHERE bill_id=?", (bill_id,)).fetchone()


def issue_pass(con, plate, ptype, start=None):
    v = vehicle(con, plate)
    start = start or datetime.now().date()
    end = start + timedelta(days=PASS_DAYS[ptype])
    cur = con.execute("INSERT INTO Pass(customer_id,vehicle_id,pass_type,start_date,end_date,price) VALUES (?,?,?,?,?,?)",
                      (v["customer_id"], v["vehicle_id"], ptype, str(start), str(end), PASS_PRICE[ptype]))
    con.commit()
    return cur.lastrowid


def expire(con, now=None):
    cur = con.execute("UPDATE Reservation SET status='EXPIRED' WHERE status='ACTIVE' AND expiry_time < ?",
                      (ts(now),))
    con.commit()
    return cur.rowcount


def report(con):
    show(con.execute("SELECT * FROM v_occupancy"), "Live occupancy by zone")
    show(con.execute("SELECT * FROM v_revenue_by_type"), "Revenue by vehicle type")
    show(con.execute("""SELECT date(generated_at) AS day, COUNT(*) AS bills, SUM(amount) AS revenue
                        FROM Bill GROUP BY day ORDER BY day"""), "Revenue by day")
    show(con.execute("""SELECT strftime('%H:00', entry_time) AS hour, COUNT(*) AS entries
                        FROM ParkingSession GROUP BY hour ORDER BY entries DESC, hour"""), "Peak entry hours")


def backup(con, dest=None):
    dest = Path(dest) if dest else DATA / f"parking_backup_{datetime.now():%Y%m%d_%H%M%S}.db"
    out = sqlite3.connect(dest)
    con.backup(out)
    out.close()
    return dest


def restore(src):
    src = Path(src)
    if not src.exists():
        raise ValueError(f"Backup not found: {src}")
    s, d = sqlite3.connect(src), sqlite3.connect(DB)
    s.backup(d)
    s.close(); d.close()


def run_queries(con):
    text = (RES / "queries.sql").read_text()
    stmt, title = [], None
    for line in text.splitlines():
        if line.startswith("--"):
            if "===" in line:
                continue
            title = line.lstrip("- ").strip() or title
            continue
        if not line.strip():
            continue
        stmt.append(line)
        if line.rstrip().endswith(";"):
            show(con.execute("\n".join(stmt)), title)
            stmt = []


def register_vehicle(con, name, phone, plate, vtype):
    t = con.execute("SELECT type_id FROM VehicleType WHERE name=?", (vtype,)).fetchone()
    if not t:
        raise ValueError("Vehicle type must be one of: two-wheeler, car, SUV, truck")
    c = con.execute("SELECT customer_id FROM Customer WHERE phone=?", (phone,)).fetchone()
    cid = c["customer_id"] if c else con.execute(
        "INSERT INTO Customer(name,phone) VALUES (?,?)", (name, phone)).lastrowid
    con.execute("INSERT INTO Vehicle(customer_id,type_id,plate) VALUES (?,?,?)",
                (cid, t["type_id"], plate.upper()))
    con.commit()


# ---------------------------------------------------------------- interactive menu
MENU = """
==================== SMART PARKING ====================
  1  Search free slots          7  Expire old reservations
  2  Reserve a slot             8  Reports (occupancy, revenue, peak hours)
  3  Gate check-in              9  Run sample SQL queries
  4  Gate check-out + bill     10 Register customer + vehicle
  5  Pay a bill                11 Backup database
  6  Issue a pass              12 Reset DB and run the scripted demo
  0  Exit
========================================================"""


def ask(prompt, cast=str, default=None):
    while True:
        raw = input(f"{prompt}{'' if default is None else f' [{default}]'}: ").strip()
        if not raw:
            if default is not None:
                return default
            continue
        try:
            return cast(raw)
        except ValueError:
            print("  invalid input, try again")


def menu():
    if not DB.exists():
        init_db()
    while True:
        print(MENU)
        try:
            choice = input("Choose: ").strip()
        except EOFError:
            return
        if choice == "0":
            return
        con = connect()
        try:
            if choice == "1":
                vt = ask("Vehicle type (two-wheeler/car/SUV/truck)")
                fl = ask("Floor (number or 'any')", default="any")
                show(search(con, vt, floor=None if fl == "any" else int(fl)), f"Free {vt} slots")
            elif choice == "2":
                plate = ask("Plate").upper()
                hrs = ask("Hours", int, 2)
                print("reservation, slot =", reserve(con, plate, None, hrs))
            elif choice == "3":
                plate = ask("Plate").upper()
                print("session, slot, reservation =", checkin(con, plate))
            elif choice == "4":
                show([checkout(con, ask("Plate").upper())], "Bill")
            elif choice == "5":
                bill = ask("Bill id", int)
                amt = ask("Amount", float)
                method = ask("Method (UPI/CARD/CASH)", default="UPI").upper()
                failed = ask("Simulate failed payment? (y/n)", default="n").lower() == "y"
                show([pay(con, bill, amt, method, failed)], "Bill status")
            elif choice == "6":
                plate = ask("Plate").upper()
                kind = ask("Pass type (monthly/quarterly/annual)")
                print("pass id", issue_pass(con, plate, kind))
            elif choice == "7":
                print("expired:", expire(con))
            elif choice == "8":
                report(con)
            elif choice == "9":
                run_queries(con)
            elif choice == "10":
                register_vehicle(con, ask("Customer name"), ask("Phone"),
                                 ask("Plate"), ask("Vehicle type (two-wheeler/car/SUV/truck)"))
                print("registered")
            elif choice == "11":
                print("backup written to", backup(con))
            elif choice == "12":
                if ask("This DELETES current data. Type YES to continue", default="no") == "YES":
                    con.close()
                    demo()
            else:
                print("  choose a number from the menu")
        except (sqlite3.DatabaseError, ValueError) as e:
            con.rollback()
            print(f"  Rejected: {e}")
        finally:
            con.close()


# ---------------------------------------------------------------- demo
def attempt(label, fn):
    """Run something expected to be REJECTED by a database trigger/constraint."""
    try:
        fn()
        print(f"  !! {label}: NOT blocked (bug)")
        return False
    except sqlite3.DatabaseError as e:
        print(f"  BLOCKED  {label}\n           -> {e}")
        return True


def demo():
    init_db()
    con = connect()
    t0 = (datetime.now() - timedelta(hours=6)).replace(second=0, microsecond=0)
    at = lambda m: t0 + timedelta(minutes=m)
    step = lambda s: print(f"\n### {s}")

    step("1. Search free car slots on floor 1")
    show(search(con, "car", floor=1, at=at(0)))

    step("2. Asha reserves a car slot for 2 hours")
    rid, slot = reserve(con, "TS09AB1001", at=at(0), hours=2)
    print(f"  reservation {rid} -> slot {slot}")

    step("3. Double-booking and type-mismatch are rejected by triggers")
    attempt("Vikram reserves the same slot in the same window",
            lambda: reserve(con, "TS09CD2002", slot_id=slot, at=at(30), hours=1))
    attempt("Truck tries to reserve a free CAR slot (type mismatch)",
            lambda: reserve(con, "TS12JK5005", slot_id=7, at=at(0), hours=1))
    con.rollback()

    step("4. Gate check-in validates the reservation")
    print("  session, slot, reservation =", checkin(con, "TS09AB1001", at=at(5)))
    attempt("Walk-in tries to park in the occupied slot",
            lambda: checkin(con, "TS09CD2002", slot_id=slot, at=at(10)))
    attempt("Same vehicle checks in twice, into a different free slot",
            lambda: checkin(con, "TS09AB1001", slot_id=7, at=at(11)))
    attempt("Truck checks into a free CAR slot (type mismatch at the gate)",
            lambda: checkin(con, "TS12JK5005", slot_id=7, at=at(12)))
    con.rollback()

    step("5. Other vehicles park; Vikram (walk-in) is auto-allocated")
    for plate, m in (("TS09CD2002", 15), ("TS10EF3003", 20), ("TS11GH4004", 25)):
        print(f"  {plate}: ", checkin(con, plate, at=at(m)))

    step("6. Live occupancy and who is parked")
    show(con.execute("SELECT * FROM v_occupancy"))

    step("7. Exit: bill is generated by the database")
    b = checkout(con, "TS09AB1001", at=at(5 + 130))    # 130 min car -> 3 h * 40 = 120
    show([b]); assert b["amount"] == 120, b["amount"]
    b2 = checkout(con, "TS11GH4004", at=at(25 + 12 * 60 - 1))   # long two-wheeler -> capped at daily max 80
    show([b2]); assert b2["amount"] == 80, b2["amount"]

    step("8. Payments: failed attempt, partial payment, overpayment blocked, then settle")
    bid = b["bill_id"]
    show([pay(con, bid, 120, "UPI", failed=True, at=at(140))])
    show([pay(con, bid, 50, "CASH", at=at(141))])
    attempt("Pay more than the outstanding balance", lambda: pay(con, bid, 100, "CARD", at=at(142)))
    con.rollback()
    show([pay(con, bid, 70, "UPI", at=at(143))])

    step("9. Pass holder parks free (monthly pass)")
    pid = issue_pass(con, "TS09CD2002", "monthly", start=at(0).date())
    print(f"  pass {pid} issued")
    show([checkout(con, "TS09CD2002", at=at(15 + 200))])

    step("10. Unclaimed reservation expires")
    rid2, _ = reserve(con, "TS12JK5005", at=at(100), hours=1)
    print(f"  reservation {rid2} created for the truck, never claimed")
    print(f"  expired {expire(con, now=at(400))} reservation(s)")

    step("11. Temporal CHECK: exit before entry is rejected")
    attempt("Set exit_time earlier than entry_time",
            lambda: con.execute("UPDATE ParkingSession SET exit_time=? WHERE vehicle_id=(SELECT vehicle_id FROM Vehicle WHERE plate='TS10EF3003')",
                                (ts(at(1)),)))
    con.rollback()

    step("12. Operator reports")
    report(con)
    print("\nDemo complete. SQL for the viva: queries.sql  |  python parking.py queries")
    con.close()


# ---------------------------------------------------------------- CLI
def main():
    if len(sys.argv) == 1:                 # double-clicked or run with no arguments
        return menu()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init"); sub.add_parser("demo"); sub.add_parser("expire")
    sub.add_parser("report"); sub.add_parser("queries"); sub.add_parser("backup")
    s = sub.add_parser("restore"); s.add_argument("file")
    s = sub.add_parser("register"); s.add_argument("name"); s.add_argument("phone"); s.add_argument("plate"); s.add_argument("type")
    s = sub.add_parser("search"); s.add_argument("type"); s.add_argument("--floor", type=int)
    s = sub.add_parser("reserve"); s.add_argument("plate"); s.add_argument("--slot", type=int); s.add_argument("--hours", type=int, default=2)
    s = sub.add_parser("checkin"); s.add_argument("plate"); s.add_argument("--slot", type=int)
    s = sub.add_parser("checkout"); s.add_argument("plate")
    s = sub.add_parser("pay"); s.add_argument("bill", type=int); s.add_argument("amount", type=float)
    s.add_argument("method", choices=["UPI", "CARD", "CASH"]); s.add_argument("--failed", action="store_true")
    s = sub.add_parser("pass"); s.add_argument("plate"); s.add_argument("kind", choices=list(PASS_DAYS))
    a = p.parse_args()

    if a.cmd == "init":
        return init_db()
    if a.cmd == "demo":
        return demo()
    if not DB.exists():
        sys.exit("No database yet. Run: python parking.py init")
    con = connect()
    try:
        if a.cmd == "search":
            show(search(con, a.type, floor=a.floor), f"Free {a.type} slots")
        elif a.cmd == "reserve":
            print("reservation, slot =", reserve(con, a.plate, a.slot, a.hours))
        elif a.cmd == "checkin":
            print("session, slot, reservation =", checkin(con, a.plate, a.slot))
        elif a.cmd == "checkout":
            show([checkout(con, a.plate)], "Bill")
        elif a.cmd == "pay":
            show([pay(con, a.bill, a.amount, a.method, a.failed)], "Bill status")
        elif a.cmd == "pass":
            print("pass id", issue_pass(con, a.plate, a.kind))
        elif a.cmd == "expire":
            print("expired:", expire(con))
        elif a.cmd == "report":
            report(con)
        elif a.cmd == "queries":
            run_queries(con)
        elif a.cmd == "register":
            register_vehicle(con, a.name, a.phone, a.plate, a.type)
            print("registered")
        elif a.cmd == "backup":
            print("backup written to", backup(con))
        elif a.cmd == "restore":
            con.close()
            restore(a.file)
            print("database restored from", a.file)
    except (sqlite3.DatabaseError, ValueError) as e:
        con.rollback()
        sys.exit(f"Rejected: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                 # keep a double-clicked window open long enough to read the error
        print(f"Fatal error: {e}")
        if getattr(sys, "frozen", False):
            input("Press Enter to close...")
        sys.exit(1)
