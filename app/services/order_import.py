"""Bulk order import from CSV (order-first): creates un-assigned, PENDING orders.

CSV layout — one row per order *line*; rows that share an ``order_ref`` collapse
into a single multi-line order::

    order_ref,customer_code,branch_code,cylinder_capacity_kg,quantity_ordered,notes
    A1,CUST001,,17,10,Morning drop
    A1,CUST001,,21,5,
    A2,CUST002,BR-002,33,2,Fragile

References use human-friendly business codes, never numeric DB ids:
  * ``customer_code``        -> :attr:`Customer.code`
  * ``branch_code``          -> :attr:`CustomerBranch.branch_code` (optional)
  * ``cylinder_capacity_kg`` -> :attr:`CylinderType.capacity_kg`

The parser is pure/transport-agnostic: it validates and builds (un-committed)
:class:`Order` objects plus a list of structured row errors. The caller (route)
decides how to surface results and whether to commit.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.customer import Customer, CustomerBranch
from app.models.cylinder import CylinderType
from app.models.delivery import Order, OrderItem
from app.models.enums import DeliveryStatus
from app.services.delivery import generate_order_number

# Header contract shared by the parser, the /import/template endpoint and the
# sample file, so they can never drift apart.
REQUIRED_HEADERS = ["order_ref", "customer_code", "cylinder_capacity_kg", "quantity_ordered"]
OPTIONAL_HEADERS = ["branch_code", "notes"]
TEMPLATE_HEADERS = ["order_ref", "customer_code", "branch_code", "cylinder_capacity_kg", "quantity_ordered", "notes"]

# A few illustrative rows for the downloadable template / sample file.
_TEMPLATE_ROWS = [
    ["A1", "CUST001", "", "17", "10", "Morning drop"],
    ["A1", "CUST001", "", "21", "5", ""],
    ["A2", "CUST002", "BR-002", "33", "2", "Fragile - handle with care"],
]

# Safety cap so a huge/garbage upload can't exhaust memory.
MAX_IMPORT_ROWS = 5000


def build_template_csv() -> str:
    """Return the CSV import template (header + example rows) as text."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(TEMPLATE_HEADERS)
    writer.writerows(_TEMPLATE_ROWS)
    return buf.getvalue()


@dataclass
class _Error:
    error: str
    row: int | None = None
    order_ref: str | None = None


@dataclass
class ParsedImport:
    """Result of parsing a CSV upload (orders are NOT yet added to the session)."""

    orders: list[Order] = field(default_factory=list)
    errors: list[_Error] = field(default_factory=list)
    total_rows: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class _PendingLine:
    row: int
    cylinder_type_id: int
    capacity_kg: float
    quantity: int


@dataclass
class _PendingOrder:
    order_ref: str
    first_row: int
    customer_code: str = ""
    branch_code: str = ""
    customer_id: int | None = None
    branch_id: int | None = None
    notes: str | None = None
    lines: list[_PendingLine] = field(default_factory=list)
    seen_capacities: set[float] = field(default_factory=set)


def parse_orders_csv(raw: bytes, db: Session) -> ParsedImport:
    """Validate CSV bytes and build un-committed PENDING orders.

    Returns a :class:`ParsedImport`. When ``errors`` is non-empty the caller
    should treat the whole import as failed (all-or-nothing) and not commit.
    """
    result = ParsedImport()

    try:
        text = raw.decode("utf-8-sig")  # tolerate Excel's UTF-8 BOM
    except UnicodeDecodeError:
        result.errors.append(_Error(error="File is not valid UTF-8 text. Save the sheet as CSV (UTF-8)."))
        return result

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        result.errors.append(_Error(error="The file is empty."))
        return result

    headers = {(h or "").strip().lower() for h in reader.fieldnames}
    missing = [h for h in REQUIRED_HEADERS if h not in headers]
    if missing:
        result.errors.append(
            _Error(error=f"Missing required column(s): {', '.join(missing)}. Expected header: {', '.join(TEMPLATE_HEADERS)}")
        )
        return result

    # Caches so repeated codes don't re-hit the DB.
    customer_cache: dict[str, Customer | None] = {}
    branch_cache: dict[str, CustomerBranch | None] = {}
    cylinder_cache: dict[float, CylinderType | None] = {}

    def get_customer(code: str) -> Customer | None:
        if code not in customer_cache:
            customer_cache[code] = db.query(Customer).filter(Customer.code == code).first()
        return customer_cache[code]

    def get_branch(code: str) -> CustomerBranch | None:
        if code not in branch_cache:
            branch_cache[code] = (
                db.query(CustomerBranch).filter(CustomerBranch.branch_code == code).first()
            )
        return branch_cache[code]

    def get_cylinder(capacity: float) -> CylinderType | None:
        if capacity not in cylinder_cache:
            cylinder_cache[capacity] = (
                db.query(CylinderType).filter(CylinderType.capacity_kg == capacity).first()
            )
        return cylinder_cache[capacity]

    # Preserve first-seen order of refs for deterministic output.
    pending: dict[str, _PendingOrder] = {}

    # DictReader row index: header is line 1, so first data row is line 2.
    for offset, rawrow in enumerate(reader):
        row_no = offset + 2
        result.total_rows += 1

        if result.total_rows > MAX_IMPORT_ROWS:
            result.errors.append(
                _Error(row=row_no, error=f"Too many rows (limit {MAX_IMPORT_ROWS}). Split the file.")
            )
            break

        row = {(k or "").strip().lower(): (v or "").strip() for k, v in rawrow.items()}

        order_ref = row.get("order_ref", "")
        customer_code = row.get("customer_code", "")
        branch_code = row.get("branch_code", "")
        capacity_raw = row.get("cylinder_capacity_kg", "")
        qty_raw = row.get("quantity_ordered", "")
        notes = row.get("notes", "") or None

        # Skip fully blank lines silently (trailing newlines from Excel).
        if not any([order_ref, customer_code, branch_code, capacity_raw, qty_raw]):
            result.total_rows -= 1
            continue

        if not order_ref:
            result.errors.append(_Error(row=row_no, error="order_ref is required"))
            continue
        if not customer_code:
            result.errors.append(_Error(row=row_no, order_ref=order_ref, error="customer_code is required"))
            continue

        # Validate cylinder capacity.
        try:
            capacity = float(capacity_raw)
        except ValueError:
            result.errors.append(
                _Error(row=row_no, order_ref=order_ref, error=f"cylinder_capacity_kg '{capacity_raw}' is not a number")
            )
            continue

        # Validate quantity.
        try:
            quantity = int(qty_raw)
        except ValueError:
            result.errors.append(
                _Error(row=row_no, order_ref=order_ref, error=f"quantity_ordered '{qty_raw}' is not an integer")
            )
            continue
        if quantity < 0:
            result.errors.append(_Error(row=row_no, order_ref=order_ref, error="quantity_ordered cannot be negative"))
            continue

        customer = get_customer(customer_code)
        if customer is None:
            result.errors.append(_Error(row=row_no, order_ref=order_ref, error=f"Unknown customer_code '{customer_code}'"))
            continue
        if not customer.is_active:
            result.errors.append(_Error(row=row_no, order_ref=order_ref, error=f"Customer '{customer_code}' is inactive"))
            continue

        branch_id: int | None = None
        if branch_code:
            branch = get_branch(branch_code)
            if branch is None:
                result.errors.append(_Error(row=row_no, order_ref=order_ref, error=f"Unknown branch_code '{branch_code}'"))
                continue
            if branch.customer_id != customer.id:
                result.errors.append(
                    _Error(row=row_no, order_ref=order_ref, error=f"Branch '{branch_code}' does not belong to customer '{customer_code}'")
                )
                continue
            branch_id = branch.id

        cylinder = get_cylinder(capacity)
        if cylinder is None:
            result.errors.append(
                _Error(row=row_no, order_ref=order_ref, error=f"No cylinder type with capacity {capacity_raw} kg")
            )
            continue
        if not cylinder.is_active:
            result.errors.append(
                _Error(row=row_no, order_ref=order_ref, error=f"Cylinder type {capacity_raw} kg is inactive")
            )
            continue

        # Group into its order, enforcing per-group consistency.
        po = pending.get(order_ref)
        if po is None:
            po = _PendingOrder(
                order_ref=order_ref,
                first_row=row_no,
                customer_code=customer_code,
                branch_code=branch_code,
                customer_id=customer.id,
                branch_id=branch_id,
                notes=notes,
            )
            pending[order_ref] = po
        else:
            if customer_code != po.customer_code:
                result.errors.append(
                    _Error(row=row_no, order_ref=order_ref, error=f"order_ref '{order_ref}' has conflicting customer_code values")
                )
                continue
            if branch_code != po.branch_code:
                result.errors.append(
                    _Error(row=row_no, order_ref=order_ref, error=f"order_ref '{order_ref}' has conflicting branch_code values")
                )
                continue
            if capacity in po.seen_capacities:
                result.errors.append(
                    _Error(row=row_no, order_ref=order_ref, error=f"Duplicate cylinder {capacity_raw} kg within order_ref '{order_ref}'")
                )
                continue

        po.seen_capacities.add(capacity)
        po.lines.append(_PendingLine(row=row_no, cylinder_type_id=cylinder.id, capacity_kg=capacity, quantity=quantity))

    # If any row failed, abort (all-or-nothing) — don't build partial orders.
    if result.errors:
        return result

    for po in pending.values():
        order = Order(
            order_number=generate_order_number(db),
            delivery_id=None,
            customer_id=po.customer_id,
            branch_id=po.branch_id,
            notes=po.notes,
            status=DeliveryStatus.PENDING,
        )
        for line in po.lines:
            order.items.append(
                OrderItem(cylinder_type_id=line.cylinder_type_id, quantity_ordered=line.quantity)
            )
        result.orders.append(order)

    return result
