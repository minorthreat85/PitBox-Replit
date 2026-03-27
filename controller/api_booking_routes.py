"""
PitBox booking API — replaces the external Fastest-Lap-Hub service.
All routes are mounted under the /api prefix (added in main.py).
Frontend calls /api/admin/bookings, /api/admin/checkin, etc.
Data is stored in a SQLite database alongside the controller config.
"""

import json
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

def _db_path() -> str:
    config_home = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    db_dir = os.path.join(config_home, "PitBox", "Controller")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "bookings.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db() -> None:
    with _get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            confirmationNumber  TEXT    NOT NULL UNIQUE,
            customerName        TEXT    NOT NULL DEFAULT '',
            customerPhone       TEXT    NOT NULL DEFAULT '',
            customerEmail       TEXT    NOT NULL DEFAULT '',
            date                TEXT    NOT NULL,
            time                TEXT    NOT NULL DEFAULT '',
            durationMinutes     INTEGER NOT NULL DEFAULT 60,
            numberOfRacers      INTEGER NOT NULL DEFAULT 1,
            simulatorNumbers    TEXT    NOT NULL DEFAULT '[]',
            status              TEXT    NOT NULL DEFAULT 'confirmed',
            paymentStatus       TEXT    NOT NULL DEFAULT 'pending',
            depositAmount       REAL    NOT NULL DEFAULT 0,
            depositPaidAt       TEXT,
            remainingBalance    REAL    NOT NULL DEFAULT 0,
            notes               TEXT    NOT NULL DEFAULT '',
            waiverSigned        INTEGER NOT NULL DEFAULT 0,
            createdAt           TEXT    NOT NULL,
            updatedAt           TEXT    NOT NULL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL DEFAULT '',
            phone       TEXT    NOT NULL DEFAULT '',
            email       TEXT    NOT NULL DEFAULT '',
            notes       TEXT    NOT NULL DEFAULT '',
            createdAt   TEXT    NOT NULL,
            updatedAt   TEXT    NOT NULL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS membership_tiers (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT    NOT NULL DEFAULT '',
            priceCents       INTEGER NOT NULL DEFAULT 0,
            durationDays     INTEGER NOT NULL DEFAULT 365,
            sessionsIncluded INTEGER NOT NULL DEFAULT 0,
            description      TEXT    NOT NULL DEFAULT '',
            createdAt        TEXT    NOT NULL,
            updatedAt        TEXT    NOT NULL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS memberships (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            customerId   INTEGER NOT NULL,
            tierId       INTEGER NOT NULL,
            startDate    TEXT    NOT NULL,
            endDate      TEXT    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'active',
            sessionsUsed INTEGER NOT NULL DEFAULT 0,
            notes        TEXT    NOT NULL DEFAULT '',
            createdAt    TEXT    NOT NULL,
            updatedAt    TEXT    NOT NULL,
            FOREIGN KEY (customerId) REFERENCES customers(id),
            FOREIGN KEY (tierId)     REFERENCES membership_tiers(id)
        )
        """)
        conn.commit()


# Initialise on import
_init_db()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_iso() -> str:
    return date.today().isoformat()


def _gen_confirmation() -> str:
    return "WI-" + uuid.uuid4().hex[:6].upper()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["simulatorNumbers"] = json.loads(d.get("simulatorNumbers") or "[]")
    except Exception:
        d["simulatorNumbers"] = []
    d["waiverSigned"] = bool(d.get("waiverSigned", 0))
    return d


def _get_booking_by_id(conn: sqlite3.Connection, booking_id: int) -> Optional[Dict]:
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Time-slot helpers for schedule
# ---------------------------------------------------------------------------

def _build_time_slots(start_hour: int = 9, end_hour: int = 22, interval_minutes: int = 30):
    """Return list of 'HH:MM' strings from start_hour to end_hour (exclusive)."""
    slots = []
    t = datetime(2000, 1, 1, start_hour, 0)
    end = datetime(2000, 1, 1, end_hour, 0)
    while t < end:
        slots.append(t.strftime("%H:%M"))
        t += timedelta(minutes=interval_minutes)
    return slots


def _time_to_minutes(t: str) -> int:
    """'HH:MM' → minutes since midnight."""
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class WalkInRequest(BaseModel):
    date: str
    time: str
    durationMinutes: int = 60
    numberOfRacers: int = 1
    customerName: str = ""
    customerPhone: str = ""
    customerEmail: str = ""
    notes: str = ""
    simulatorNumbers: List[int] = []


class PatchBookingRequest(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    customerPhone: Optional[str] = None
    customerEmail: Optional[str] = None


class AssignSimulatorsRequest(BaseModel):
    simulatorNumbers: List[int]


class RescheduleRequest(BaseModel):
    time: str
    durationMinutes: int
    simulatorNumbers: Optional[List[int]] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/admin/bookings")
def list_bookings(date: Optional[str] = None, status: Optional[str] = None):
    """List bookings, optionally filtered by date and/or status."""
    if not date:
        date = _today_iso()
    with _get_conn() as conn:
        query = "SELECT * FROM bookings WHERE 1=1"
        params: list = []
        if date:
            query += " AND date = ?"
            params.append(date)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY time ASC"
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.post("/admin/bookings/walk-in", status_code=201)
def create_walk_in(body: WalkInRequest):
    """Create a confirmed walk-in booking (no deposit required)."""
    now = _now_iso()
    confirmation = _gen_confirmation()
    sims_json = json.dumps(body.simulatorNumbers)
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO bookings
               (confirmationNumber, customerName, customerPhone, customerEmail,
                date, time, durationMinutes, numberOfRacers, simulatorNumbers,
                status, paymentStatus, depositAmount, remainingBalance,
                notes, waiverSigned, createdAt, updatedAt)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                confirmation,
                body.customerName,
                body.customerPhone,
                body.customerEmail,
                body.date,
                body.time,
                body.durationMinutes,
                body.numberOfRacers,
                sims_json,
                "confirmed",
                "pending",
                0,
                0,
                body.notes,
                0,
                now,
                now,
            ),
        )
        conn.commit()
        booking_id = cur.lastrowid
        booking = _get_booking_by_id(conn, booking_id)
    return booking


@router.patch("/admin/bookings/{booking_id}")
def patch_booking(booking_id: int, body: PatchBookingRequest):
    """Update status and/or other mutable fields on a booking."""
    now = _now_iso()
    with _get_conn() as conn:
        booking = _get_booking_by_id(conn, booking_id)
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        updates: Dict[str, Any] = {"updatedAt": now}
        if body.status is not None:
            updates["status"] = body.status
        if body.notes is not None:
            updates["notes"] = body.notes
        if body.customerPhone is not None:
            updates["customerPhone"] = body.customerPhone
        if body.customerEmail is not None:
            updates["customerEmail"] = body.customerEmail
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [booking_id]
        conn.execute(f"UPDATE bookings SET {set_clause} WHERE id = ?", params)
        conn.commit()
        updated = _get_booking_by_id(conn, booking_id)
    return updated


@router.post("/admin/bookings/{booking_id}/mark-deposit-paid")
def mark_deposit_paid(booking_id: int):
    """Mark the deposit for a booking as paid."""
    now = _now_iso()
    with _get_conn() as conn:
        booking = _get_booking_by_id(conn, booking_id)
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        conn.execute(
            "UPDATE bookings SET paymentStatus = 'deposit_paid', depositPaidAt = ?, status = 'confirmed', updatedAt = ? WHERE id = ?",
            (now, now, booking_id),
        )
        conn.commit()
        updated = _get_booking_by_id(conn, booking_id)
    return updated


@router.post("/admin/checkin/{booking_id}")
def checkin_booking(booking_id: int):
    """Check a customer in for their booking."""
    now = _now_iso()
    with _get_conn() as conn:
        booking = _get_booking_by_id(conn, booking_id)
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        conn.execute(
            "UPDATE bookings SET status = 'checked_in', updatedAt = ? WHERE id = ?",
            (now, booking_id),
        )
        conn.commit()
        updated = _get_booking_by_id(conn, booking_id)
    return updated


@router.get("/admin/checkin")
def list_checkin():
    """Return today's bookings for the check-in page."""
    today = _today_iso()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE date = ? ORDER BY time ASC",
            (today,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.patch("/admin/bookings/{booking_id}/simulators")
def assign_simulators(booking_id: int, body: AssignSimulatorsRequest):
    """Assign simulator numbers to a booking."""
    now = _now_iso()
    sims_json = json.dumps(body.simulatorNumbers)
    with _get_conn() as conn:
        booking = _get_booking_by_id(conn, booking_id)
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        conn.execute(
            "UPDATE bookings SET simulatorNumbers = ?, updatedAt = ? WHERE id = ?",
            (sims_json, now, booking_id),
        )
        conn.commit()
        updated = _get_booking_by_id(conn, booking_id)
    return updated


@router.patch("/admin/bookings/{booking_id}/reschedule")
def reschedule_booking(booking_id: int, body: RescheduleRequest):
    """Reschedule a booking to a new time and/or duration."""
    now = _now_iso()
    with _get_conn() as conn:
        booking = _get_booking_by_id(conn, booking_id)
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        updates: Dict[str, Any] = {
            "time": body.time,
            "durationMinutes": body.durationMinutes,
            "updatedAt": now,
        }
        if body.simulatorNumbers is not None:
            updates["simulatorNumbers"] = json.dumps(body.simulatorNumbers)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [booking_id]
        conn.execute(f"UPDATE bookings SET {set_clause} WHERE id = ?", params)
        conn.commit()
        updated = _get_booking_by_id(conn, booking_id)
    return updated


@router.post("/admin/bookings/{booking_id}/notifications/resend-email")
def resend_email(booking_id: int):
    """Stub — PitBox is a LAN system and does not send emails."""
    with _get_conn() as conn:
        booking = _get_booking_by_id(conn, booking_id)
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
    return {"message": "Email notifications are not available on a LAN system."}


@router.get("/admin/schedule")
def get_schedule(date: Optional[str] = None):
    """Return schedule grid data for a given date."""
    if not date:
        date = _today_iso()
    time_slots = _build_time_slots(start_hour=9, end_hour=22, interval_minutes=30)
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE date = ? AND status NOT IN ('cancelled', 'no_show') ORDER BY time ASC",
            (date,),
        ).fetchall()
    bookings = [_row_to_dict(r) for r in rows]

    # Annotate each booking with slot index + duration in slots
    slot_minutes = 30
    first_slot_minutes = _time_to_minutes(time_slots[0]) if time_slots else 0
    for b in bookings:
        start_min = _time_to_minutes(b.get("time", "00:00"))
        start_idx = max(0, (start_min - first_slot_minutes) // slot_minutes)
        dur_slots = max(1, (b.get("durationMinutes", 60) + slot_minutes - 1) // slot_minutes)
        b["startSlotIndex"] = start_idx
        b["durationSlots"] = dur_slots
        dur = b.get("durationMinutes", 60)
        b["durationDisplay"] = f"{dur} min"

    return {
        "date": date,
        "isClosed": False,
        "timeSlots": time_slots,
        "displayTimeSlots": time_slots,
        "totalSimulators": 8,
        "bookings": bookings,
    }


@router.get("/admin/analytics")
def get_analytics(period: str = "30d"):
    """Return analytics summary for the given period (7d, 30d, 90d)."""
    days = 30
    if period == "7d":
        days = 7
    elif period == "90d":
        days = 90
    elif period == "all":
        days = 3650

    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)

    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE date >= ? AND date <= ? ORDER BY date ASC",
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()

    bookings = [_row_to_dict(r) for r in rows]

    total = len(bookings)
    total_revenue_cents = int(sum(b.get("depositAmount", 0) * 100 for b in bookings))
    avg_racers = (sum(b.get("numberOfRacers", 0) for b in bookings) / total) if total else 0

    # Revenue / bookings by day
    rev_by_day: Dict[str, Dict] = {}
    cur = start_date
    while cur <= end_date:
        rev_by_day[cur.isoformat()] = {"date": cur.isoformat(), "bookings": 0, "revenueCents": 0}
        cur += timedelta(days=1)
    for b in bookings:
        d = b.get("date", "")
        if d in rev_by_day:
            rev_by_day[d]["bookings"] += 1
            rev_by_day[d]["revenueCents"] += int(b.get("depositAmount", 0) * 100)

    # Duration breakdown
    dur_map: Dict[int, int] = {}
    for b in bookings:
        dm = b.get("durationMinutes", 60)
        dur_map[dm] = dur_map.get(dm, 0) + 1
    duration_breakdown = [{"durationMinutes": k, "count": v} for k, v in sorted(dur_map.items())]

    # Status breakdown
    status_map: Dict[str, int] = {}
    for b in bookings:
        s = b.get("status", "unknown")
        status_map[s] = status_map.get(s, 0) + 1
    status_breakdown = [{"status": k, "label": k.replace("_", " ").title(), "count": v} for k, v in status_map.items()]

    return {
        "period": period,
        "totalBookings": total,
        "totalRevenueCents": total_revenue_cents,
        "avgRacersPerBooking": round(avg_racers, 2),
        "revenueByDay": list(rev_by_day.values()),
        "durationBreakdown": duration_breakdown,
        "statusBreakdown": status_breakdown,
    }


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

class CustomerRequest(BaseModel):
    name: str = ""
    phone: str = ""
    email: str = ""
    notes: str = ""


@router.get("/admin/customers")
def list_customers(search: Optional[str] = None):
    with _get_conn() as conn:
        if search:
            like = f"%{search}%"
            rows = conn.execute(
                "SELECT * FROM customers WHERE name LIKE ? OR phone LIKE ? OR email LIKE ? ORDER BY name ASC",
                (like, like, like),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM customers ORDER BY name ASC").fetchall()
    return [dict(r) for r in rows]


@router.post("/admin/customers", status_code=201)
def create_customer(body: CustomerRequest):
    now = _now_iso()
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO customers (name, phone, email, notes, createdAt, updatedAt) VALUES (?,?,?,?,?,?)",
            (body.name, body.phone, body.email, body.notes, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


@router.patch("/admin/customers/{customer_id}")
def patch_customer(customer_id: int, body: CustomerRequest):
    now = _now_iso()
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Customer not found")
        conn.execute(
            "UPDATE customers SET name=?, phone=?, email=?, notes=?, updatedAt=? WHERE id=?",
            (body.name, body.phone, body.email, body.notes, now, customer_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return dict(updated)


@router.delete("/admin/customers/{customer_id}", status_code=204)
def delete_customer(customer_id: int):
    with _get_conn() as conn:
        row = conn.execute("SELECT id FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Customer not found")
        conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Membership Tiers
# ---------------------------------------------------------------------------

class TierRequest(BaseModel):
    name: str = ""
    priceCents: int = 0
    durationDays: int = 365
    sessionsIncluded: int = 0
    description: str = ""


@router.get("/admin/membership-tiers")
def list_tiers():
    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM membership_tiers ORDER BY name ASC").fetchall()
    return [dict(r) for r in rows]


@router.post("/admin/membership-tiers", status_code=201)
def create_tier(body: TierRequest):
    now = _now_iso()
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO membership_tiers (name, priceCents, durationDays, sessionsIncluded, description, createdAt, updatedAt) VALUES (?,?,?,?,?,?,?)",
            (body.name, body.priceCents, body.durationDays, body.sessionsIncluded, body.description, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM membership_tiers WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


@router.patch("/admin/membership-tiers/{tier_id}")
def patch_tier(tier_id: int, body: TierRequest):
    now = _now_iso()
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM membership_tiers WHERE id = ?", (tier_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Tier not found")
        conn.execute(
            "UPDATE membership_tiers SET name=?, priceCents=?, durationDays=?, sessionsIncluded=?, description=?, updatedAt=? WHERE id=?",
            (body.name, body.priceCents, body.durationDays, body.sessionsIncluded, body.description, now, tier_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM membership_tiers WHERE id = ?", (tier_id,)).fetchone()
    return dict(updated)


@router.delete("/admin/membership-tiers/{tier_id}", status_code=204)
def delete_tier(tier_id: int):
    with _get_conn() as conn:
        row = conn.execute("SELECT id FROM membership_tiers WHERE id = ?", (tier_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Tier not found")
        conn.execute("DELETE FROM membership_tiers WHERE id = ?", (tier_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------

class MembershipRequest(BaseModel):
    customerId: int
    tierId: int
    startDate: str
    endDate: str
    status: str = "active"
    sessionsUsed: int = 0
    notes: str = ""


class MembershipPatchRequest(BaseModel):
    status: Optional[str] = None
    sessionsUsed: Optional[int] = None
    endDate: Optional[str] = None
    notes: Optional[str] = None


@router.get("/admin/memberships")
def list_memberships(customer_id: Optional[int] = None, status: Optional[str] = None):
    with _get_conn() as conn:
        q = """
            SELECT m.*, c.name AS customerName, c.email AS customerEmail, c.phone AS customerPhone,
                   t.name AS tierName, t.priceCents, t.sessionsIncluded
            FROM memberships m
            LEFT JOIN customers c ON c.id = m.customerId
            LEFT JOIN membership_tiers t ON t.id = m.tierId
            WHERE 1=1
        """
        params: list = []
        if customer_id is not None:
            q += " AND m.customerId = ?"
            params.append(customer_id)
        if status:
            q += " AND m.status = ?"
            params.append(status)
        q += " ORDER BY m.startDate DESC"
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


@router.post("/admin/memberships", status_code=201)
def create_membership(body: MembershipRequest):
    now = _now_iso()
    with _get_conn() as conn:
        for fk_table, fk_id in [("customers", body.customerId), ("membership_tiers", body.tierId)]:
            if not conn.execute(f"SELECT id FROM {fk_table} WHERE id = ?", (fk_id,)).fetchone():
                raise HTTPException(status_code=400, detail=f"{fk_table} id {fk_id} not found")
        cur = conn.execute(
            "INSERT INTO memberships (customerId, tierId, startDate, endDate, status, sessionsUsed, notes, createdAt, updatedAt) VALUES (?,?,?,?,?,?,?,?,?)",
            (body.customerId, body.tierId, body.startDate, body.endDate, body.status, body.sessionsUsed, body.notes, now, now),
        )
        conn.commit()
        mid = cur.lastrowid
        row = conn.execute("""
            SELECT m.*, c.name AS customerName, t.name AS tierName
            FROM memberships m
            LEFT JOIN customers c ON c.id = m.customerId
            LEFT JOIN membership_tiers t ON t.id = m.tierId
            WHERE m.id = ?
        """, (mid,)).fetchone()
    return dict(row)


@router.patch("/admin/memberships/{membership_id}")
def patch_membership(membership_id: int, body: MembershipPatchRequest):
    now = _now_iso()
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM memberships WHERE id = ?", (membership_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Membership not found")
        updates: Dict[str, Any] = {"updatedAt": now}
        if body.status is not None:
            updates["status"] = body.status
        if body.sessionsUsed is not None:
            updates["sessionsUsed"] = body.sessionsUsed
        if body.endDate is not None:
            updates["endDate"] = body.endDate
        if body.notes is not None:
            updates["notes"] = body.notes
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE memberships SET {set_clause} WHERE id = ?", list(updates.values()) + [membership_id])
        conn.commit()
        updated = conn.execute("""
            SELECT m.*, c.name AS customerName, t.name AS tierName
            FROM memberships m
            LEFT JOIN customers c ON c.id = m.customerId
            LEFT JOIN membership_tiers t ON t.id = m.tierId
            WHERE m.id = ?
        """, (membership_id,)).fetchone()
    return dict(updated)


@router.delete("/admin/memberships/{membership_id}", status_code=204)
def delete_membership(membership_id: int):
    with _get_conn() as conn:
        row = conn.execute("SELECT id FROM memberships WHERE id = ?", (membership_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Membership not found")
        conn.execute("DELETE FROM memberships WHERE id = ?", (membership_id,))
        conn.commit()
