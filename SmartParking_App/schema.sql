-- Smart Parking Lot Allocation and Billing System (SQLite)
-- 14 tables, 3NF. Business rules live here as triggers/CHECKs, not in Python.
PRAGMA foreign_keys = ON;

-- ---------- Facility structure ----------
CREATE TABLE VehicleType (
  type_id INTEGER PRIMARY KEY,
  name    TEXT NOT NULL UNIQUE CHECK (name IN ('two-wheeler','car','SUV','truck'))
);
CREATE TABLE Facility (
  facility_id INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  address     TEXT
);
CREATE TABLE Floor (
  floor_id    INTEGER PRIMARY KEY,
  facility_id INTEGER NOT NULL REFERENCES Facility(facility_id),
  floor_no    INTEGER NOT NULL,
  UNIQUE (facility_id, floor_no)
);
CREATE TABLE Zone (
  zone_id   INTEGER PRIMARY KEY,
  floor_id  INTEGER NOT NULL REFERENCES Floor(floor_id),
  zone_name TEXT NOT NULL,
  UNIQUE (floor_id, zone_name)
);
CREATE TABLE Slot (
  slot_id   INTEGER PRIMARY KEY,
  zone_id   INTEGER NOT NULL REFERENCES Zone(zone_id),
  slot_code TEXT NOT NULL,
  type_id   INTEGER NOT NULL REFERENCES VehicleType(type_id),
  status    TEXT NOT NULL DEFAULT 'FREE' CHECK (status IN ('FREE','OCCUPIED','MAINTENANCE')),
  UNIQUE (zone_id, slot_code)
);

-- ---------- Customers and vehicles ----------
CREATE TABLE Customer (
  customer_id INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  phone       TEXT NOT NULL UNIQUE,
  email       TEXT
);
CREATE TABLE Vehicle (
  vehicle_id  INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES Customer(customer_id),
  type_id     INTEGER NOT NULL REFERENCES VehicleType(type_id),
  plate       TEXT NOT NULL UNIQUE
);

-- ---------- Billing ----------
CREATE TABLE Tariff (
  tariff_id      INTEGER PRIMARY KEY,
  type_id        INTEGER NOT NULL REFERENCES VehicleType(type_id),
  hourly_rate    REAL NOT NULL CHECK (hourly_rate > 0),
  daily_max      REAL NOT NULL CHECK (daily_max >= hourly_rate),
  effective_from TEXT NOT NULL,
  UNIQUE (type_id, effective_from)
);

-- ---------- Parking operations ----------
CREATE TABLE Reservation (
  reservation_id INTEGER PRIMARY KEY,
  vehicle_id     INTEGER NOT NULL REFERENCES Vehicle(vehicle_id),
  slot_id        INTEGER NOT NULL REFERENCES Slot(slot_id),
  start_time     TEXT NOT NULL,
  expiry_time    TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'ACTIVE'
                 CHECK (status IN ('ACTIVE','USED','EXPIRED','CANCELLED')),
  CHECK (expiry_time > start_time)
);
CREATE TABLE ParkingSession (
  session_id     INTEGER PRIMARY KEY,
  vehicle_id     INTEGER NOT NULL REFERENCES Vehicle(vehicle_id),
  slot_id        INTEGER NOT NULL REFERENCES Slot(slot_id),
  reservation_id INTEGER REFERENCES Reservation(reservation_id),
  entry_time     TEXT NOT NULL,
  exit_time      TEXT,
  CHECK (exit_time IS NULL OR exit_time > entry_time)      -- temporal integrity
);
-- A vehicle cannot be parked in two places at once.
CREATE UNIQUE INDEX ux_vehicle_one_open_session
  ON ParkingSession(vehicle_id) WHERE exit_time IS NULL;

CREATE TABLE EntryExitLog (
  log_id     INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES ParkingSession(session_id),
  event      TEXT NOT NULL CHECK (event IN ('ENTRY','EXIT')),
  event_time TEXT NOT NULL
);

CREATE TABLE Bill (
  bill_id          INTEGER PRIMARY KEY,
  session_id       INTEGER NOT NULL UNIQUE REFERENCES ParkingSession(session_id),  -- 1:1
  tariff_id        INTEGER NOT NULL REFERENCES Tariff(tariff_id),
  duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
  amount           REAL NOT NULL CHECK (amount >= 0),
  generated_at     TEXT NOT NULL
);
CREATE TABLE Payment (                                                           -- 1:N per bill
  payment_id INTEGER PRIMARY KEY,
  bill_id    INTEGER NOT NULL REFERENCES Bill(bill_id),
  amount     REAL NOT NULL CHECK (amount > 0),
  method     TEXT NOT NULL CHECK (method IN ('UPI','CARD','CASH')),
  status     TEXT NOT NULL DEFAULT 'SUCCESS' CHECK (status IN ('SUCCESS','FAILED')),
  paid_at    TEXT NOT NULL
);

-- ---------- Passes ----------
CREATE TABLE Pass (
  pass_id     INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES Customer(customer_id),
  vehicle_id  INTEGER NOT NULL REFERENCES Vehicle(vehicle_id),
  pass_type   TEXT NOT NULL CHECK (pass_type IN ('monthly','quarterly','annual')),
  start_date  TEXT NOT NULL,
  end_date    TEXT NOT NULL,
  price       REAL NOT NULL CHECK (price > 0),
  CHECK (end_date > start_date)
);

-- =====================================================================
-- TRIGGERS: business rules enforced at the database layer
-- =====================================================================

-- Rule 1: one active vehicle per slot (no double occupancy)
CREATE TRIGGER trg_one_active_vehicle_per_slot
BEFORE INSERT ON ParkingSession
WHEN EXISTS (SELECT 1 FROM ParkingSession WHERE slot_id = NEW.slot_id AND exit_time IS NULL)
BEGIN
  SELECT RAISE(ABORT, 'Slot already occupied by an active session');
END;

CREATE TRIGGER trg_slot_not_in_maintenance
BEFORE INSERT ON ParkingSession
WHEN (SELECT status FROM Slot WHERE slot_id = NEW.slot_id) = 'MAINTENANCE'
BEGIN
  SELECT RAISE(ABORT, 'Slot is under maintenance');
END;

-- Rule 2: vehicle type must match the slot classification
CREATE TRIGGER trg_vehicle_type_matches_slot
BEFORE INSERT ON ParkingSession
WHEN (SELECT type_id FROM Vehicle WHERE vehicle_id = NEW.vehicle_id)
  <> (SELECT type_id FROM Slot    WHERE slot_id    = NEW.slot_id)
BEGIN
  SELECT RAISE(ABORT, 'Vehicle type does not match slot type');
END;

-- Gate verification: a supplied reservation must be active, unexpired, and match vehicle + slot
CREATE TRIGGER trg_gate_validates_reservation
BEFORE INSERT ON ParkingSession
WHEN NEW.reservation_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM Reservation r
  WHERE r.reservation_id = NEW.reservation_id AND r.status = 'ACTIVE'
    AND r.vehicle_id = NEW.vehicle_id AND r.slot_id = NEW.slot_id
    AND r.expiry_time >= NEW.entry_time)
BEGIN
  SELECT RAISE(ABORT, 'Reservation invalid, expired, or does not match vehicle/slot');
END;

-- Walk-ins cannot take a slot reserved for someone else
CREATE TRIGGER trg_walkin_respects_reservation
BEFORE INSERT ON ParkingSession
WHEN NEW.reservation_id IS NULL AND EXISTS (
  SELECT 1 FROM Reservation r
  WHERE r.slot_id = NEW.slot_id AND r.status = 'ACTIVE'
    AND r.start_time <= NEW.entry_time AND r.expiry_time > NEW.entry_time
    AND r.vehicle_id <> NEW.vehicle_id)
BEGIN
  SELECT RAISE(ABORT, 'Slot is reserved for another vehicle');
END;

-- Reservations: type match, no maintenance slots, no overlapping windows on one slot
CREATE TRIGGER trg_reservation_type_matches_slot
BEFORE INSERT ON Reservation
WHEN (SELECT type_id FROM Vehicle WHERE vehicle_id = NEW.vehicle_id)
  <> (SELECT type_id FROM Slot    WHERE slot_id    = NEW.slot_id)
BEGIN
  SELECT RAISE(ABORT, 'Vehicle type does not match slot type');
END;

CREATE TRIGGER trg_reservation_no_overlap
BEFORE INSERT ON Reservation
WHEN EXISTS (
  SELECT 1 FROM Reservation r
  WHERE r.slot_id = NEW.slot_id AND r.status = 'ACTIVE'
    AND r.start_time < NEW.expiry_time AND r.expiry_time > NEW.start_time)
BEGIN
  SELECT RAISE(ABORT, 'Double-booking: slot already reserved for that time window');
END;

CREATE TRIGGER trg_reservation_not_maintenance
BEFORE INSERT ON Reservation
WHEN (SELECT status FROM Slot WHERE slot_id = NEW.slot_id) = 'MAINTENANCE'
BEGIN
  SELECT RAISE(ABORT, 'Slot is under maintenance');
END;

-- Entry: occupy slot, write log, consume reservation
CREATE TRIGGER trg_session_entry
AFTER INSERT ON ParkingSession
BEGIN
  UPDATE Slot SET status = 'OCCUPIED' WHERE slot_id = NEW.slot_id;
  INSERT INTO EntryExitLog(session_id, event, event_time)
    VALUES (NEW.session_id, 'ENTRY', NEW.entry_time);
  UPDATE Reservation SET status = 'USED'
    WHERE reservation_id = NEW.reservation_id;
END;

-- Exit: free slot, write log, auto-generate the bill
--   minutes = ceil(seconds/60); hours billed = ceil(minutes/60)
--   amount  = full_days * daily_max + MIN(remaining_hours * hourly_rate, daily_max)
--   active pass on entry date => amount 0
CREATE TRIGGER trg_session_exit
AFTER UPDATE OF exit_time ON ParkingSession
WHEN OLD.exit_time IS NULL AND NEW.exit_time IS NOT NULL
BEGIN
  UPDATE Slot SET status = 'FREE' WHERE slot_id = NEW.slot_id;
  INSERT INTO EntryExitLog(session_id, event, event_time)
    VALUES (NEW.session_id, 'EXIT', NEW.exit_time);
  INSERT INTO Bill(session_id, tariff_id, duration_minutes, amount, generated_at)
  SELECT NEW.session_id, t.tariff_id, m.mins,
         CASE WHEN EXISTS (SELECT 1 FROM Pass pa
                           WHERE pa.vehicle_id = NEW.vehicle_id
                             AND date(NEW.entry_time) BETWEEN pa.start_date AND pa.end_date)
              THEN 0
              ELSE (m.mins / 1440) * t.daily_max
                   + MIN(((m.mins % 1440) + 59) / 60 * t.hourly_rate, t.daily_max)
         END,
         NEW.exit_time
  FROM (SELECT ((CAST(strftime('%s', NEW.exit_time)  AS INTEGER)
               - CAST(strftime('%s', NEW.entry_time) AS INTEGER)) + 59) / 60 AS mins) m
  JOIN Vehicle v ON v.vehicle_id = NEW.vehicle_id
  JOIN Tariff  t ON t.tariff_id = (
        SELECT t2.tariff_id FROM Tariff t2
        WHERE t2.type_id = v.type_id AND t2.effective_from <= date(NEW.exit_time)
        ORDER BY t2.effective_from DESC LIMIT 1);
END;

-- Payments: successful payments can never exceed the bill (partial payments allowed)
CREATE TRIGGER trg_payment_not_over_bill
BEFORE INSERT ON Payment
WHEN NEW.status = 'SUCCESS' AND
     (SELECT COALESCE(SUM(amount), 0) FROM Payment WHERE bill_id = NEW.bill_id AND status = 'SUCCESS')
     + NEW.amount > (SELECT amount FROM Bill WHERE bill_id = NEW.bill_id)
BEGIN
  SELECT RAISE(ABORT, 'Payment exceeds outstanding bill balance');
END;

-- =====================================================================
-- VIEWS
-- =====================================================================
CREATE VIEW v_occupancy AS
SELECT f.floor_no, z.zone_name,
       COUNT(*)                                   AS total_slots,
       SUM(s.status = 'OCCUPIED')                 AS occupied,
       SUM(s.status = 'FREE')                     AS free,
       ROUND(100.0 * SUM(s.status = 'OCCUPIED') / COUNT(*), 1) AS occupancy_pct
FROM Slot s
JOIN Zone  z ON z.zone_id  = s.zone_id
JOIN Floor f ON f.floor_id = z.floor_id
GROUP BY z.zone_id
ORDER BY f.floor_no, z.zone_name;

CREATE VIEW v_bill_status AS
SELECT x.*,
       x.amount - x.paid AS balance,
       CASE WHEN x.amount - x.paid <= 0 THEN 'PAID'
            WHEN x.paid > 0             THEN 'PARTIAL'
            ELSE 'UNPAID' END AS status
FROM (SELECT b.bill_id, b.session_id, b.duration_minutes, b.amount,
             COALESCE(SUM(CASE WHEN p.status = 'SUCCESS' THEN p.amount END), 0) AS paid
      FROM Bill b LEFT JOIN Payment p ON p.bill_id = b.bill_id
      GROUP BY b.bill_id) x;

CREATE VIEW v_revenue_by_type AS
SELECT vt.name AS vehicle_type, COUNT(*) AS bills,
       SUM(bs.amount) AS billed, SUM(bs.paid) AS collected, SUM(bs.balance) AS outstanding
FROM v_bill_status bs
JOIN ParkingSession ps ON ps.session_id = bs.session_id
JOIN Vehicle v         ON v.vehicle_id  = ps.vehicle_id
JOIN VehicleType vt    ON vt.type_id    = v.type_id
GROUP BY vt.type_id
ORDER BY billed DESC;

CREATE VIEW v_current_parked AS
SELECT c.name AS customer, v.plate, vt.name AS vehicle_type,
       f.floor_no, z.zone_name, s.slot_code, ps.entry_time,
       CAST((julianday('now') - julianday(ps.entry_time)) * 1440 AS INTEGER) AS minutes_parked
FROM ParkingSession ps
JOIN Vehicle v      ON v.vehicle_id  = ps.vehicle_id
JOIN Customer c     ON c.customer_id = v.customer_id
JOIN VehicleType vt ON vt.type_id    = v.type_id
JOIN Slot s         ON s.slot_id     = ps.slot_id
JOIN Zone z         ON z.zone_id     = s.zone_id
JOIN Floor f        ON f.floor_id    = z.floor_id
WHERE ps.exit_time IS NULL;
