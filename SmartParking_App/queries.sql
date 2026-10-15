-- Sample queries for the viva: joins, aggregates, subqueries, views.
-- Run after the demo:  sqlite3 parking.db < queries.sql   (or: python parking.py queries)

-- ============ JOINS ============
-- J1. Currently parked vehicles with customer details
SELECT c.name, v.plate, vt.name AS type, f.floor_no, z.zone_name, s.slot_code, ps.entry_time
FROM ParkingSession ps
JOIN Vehicle v      ON v.vehicle_id  = ps.vehicle_id
JOIN Customer c     ON c.customer_id = v.customer_id
JOIN VehicleType vt ON vt.type_id    = v.type_id
JOIN Slot s         ON s.slot_id     = ps.slot_id
JOIN Zone z         ON z.zone_id     = s.zone_id
JOIN Floor f        ON f.floor_id    = z.floor_id
WHERE ps.exit_time IS NULL;

-- J2. Full billing trail: customer -> vehicle -> session -> bill -> payments
SELECT c.name, v.plate, ps.session_id, b.amount AS billed,
       COALESCE(SUM(CASE WHEN p.status='SUCCESS' THEN p.amount END),0) AS paid,
       SUM(p.status='FAILED') AS failed_attempts
FROM Bill b
JOIN ParkingSession ps ON ps.session_id = b.session_id
JOIN Vehicle v         ON v.vehicle_id  = ps.vehicle_id
JOIN Customer c        ON c.customer_id = v.customer_id
LEFT JOIN Payment p    ON p.bill_id     = b.bill_id
GROUP BY b.bill_id;

-- ============ AGGREGATES ============
-- A1. Revenue by day
SELECT date(generated_at) AS day, COUNT(*) AS bills, SUM(amount) AS revenue
FROM Bill GROUP BY date(generated_at) ORDER BY day;

-- A2. Average session duration (minutes) per vehicle type
SELECT vt.name, COUNT(*) AS sessions, ROUND(AVG(b.duration_minutes),1) AS avg_minutes
FROM Bill b
JOIN ParkingSession ps ON ps.session_id = b.session_id
JOIN Vehicle v         ON v.vehicle_id  = ps.vehicle_id
JOIN VehicleType vt    ON vt.type_id    = v.type_id
GROUP BY vt.type_id;

-- A3. Peak entry hours
SELECT strftime('%H:00', entry_time) AS hour, COUNT(*) AS entries
FROM ParkingSession GROUP BY hour ORDER BY entries DESC, hour;

-- A4. Overall occupancy rate
SELECT ROUND(100.0 * SUM(status='OCCUPIED') / COUNT(*), 1) AS occupancy_pct FROM Slot;

-- ============ SUBQUERIES ============
-- S1. Reservations that expired without a corresponding session
SELECT r.reservation_id, v.plate, r.slot_id, r.expiry_time
FROM Reservation r JOIN Vehicle v ON v.vehicle_id = r.vehicle_id
WHERE r.status = 'EXPIRED'
  AND NOT EXISTS (SELECT 1 FROM ParkingSession ps WHERE ps.reservation_id = r.reservation_id);

-- S2. Customers whose total billed amount is above the average bill
SELECT c.name, SUM(b.amount) AS total
FROM Customer c
JOIN Vehicle v ON v.customer_id = c.customer_id
JOIN ParkingSession ps ON ps.vehicle_id = v.vehicle_id
JOIN Bill b ON b.session_id = ps.session_id
GROUP BY c.customer_id
HAVING SUM(b.amount) > (SELECT AVG(amount) FROM Bill);

-- S3. Slots never used in any session
SELECT slot_id, slot_code FROM Slot
WHERE slot_id NOT IN (SELECT slot_id FROM ParkingSession);

-- ============ VIEWS ============
-- V1. Live occupancy by zone
SELECT * FROM v_occupancy;
-- V2. Revenue by vehicle type
SELECT * FROM v_revenue_by_type;
-- V3. Bill status (paid / partial / unpaid)
SELECT * FROM v_bill_status;
-- V4. Currently parked vehicles
SELECT * FROM v_current_parked;
