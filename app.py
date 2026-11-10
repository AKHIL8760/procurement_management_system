#!/usr/bin/env python3
"""Local procurement workflow app backed by SQLite."""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import os
import re
import sqlite3
import sys
from datetime import date, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from generate_procurement import CATEGORIES, CURRENCIES, money, schema_sql, seed_sql, build_dataset

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "procurement-app.sqlite3"
APPROVAL_MANAGER_LIMIT = 25_000
APPROVAL_AUTO_LIMIT = 5_000


class APIError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def connect_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize_database(path: Path, reset: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if reset and path.exists():
        path.unlink()
    connection = connect_database(path)
    try:
        initialized = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='purchase_requests'"
        ).fetchone()
        if initialized:
            return
        schema_path = ROOT / "output" / "sql" / "schema.sql"
        seed_path = ROOT / "output" / "sql" / "seed.sql"
        if schema_path.exists() and seed_path.exists():
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            seed_script = seed_path.read_text(encoding="utf-8")
        else:
            dataset = build_dataset()
            connection.executescript(schema_sql())
            seed_script = seed_sql(dataset["tables"])
        seed_lines = seed_script.splitlines()
        if seed_lines and seed_lines[0].startswith("PRAGMA foreign_keys"):
            seed_lines = seed_lines[1:]
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + "\n".join(seed_lines) + "\nCOMMIT;"
        )
        connection.commit()
    finally:
        connection.close()


def as_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def next_id(connection: sqlite3.Connection, prefix: str, table: str, column: str) -> str:
    values = connection.execute(f"SELECT {column} FROM {table}").fetchall()
    sequence = 0
    for row in values:
        match = re.search(r"(\d+)$", row[0])
        if match:
            sequence = max(sequence, int(match.group(1)))
    return f"{prefix}-{date.today().year}-{sequence + 1:06d}"


def next_event_id(connection: sqlite3.Connection, table: str, prefix: str) -> str:
    count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return f"{prefix}E-{count + 1:06d}"


def add_event(
    connection: sqlite3.Connection,
    table: str,
    parent_column: str,
    parent_id: str,
    event_type: str,
    from_state: str | None,
    to_state: str,
    reason: str,
    actor_id: str | None = None,
) -> None:
    prefix = {"request_events": "REQ", "rfq_events": "RFQ",
              "purchase_order_events": "PO", "delivery_events": "DEL"}[table]
    connection.execute(
        f"""INSERT INTO {table}
            (event_id, {parent_column}, event_type, from_state, to_state, event_at,
             actor_type, actor_id, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (next_event_id(connection, table, prefix), parent_id, event_type, from_state,
         to_state, date.today().isoformat(), "local_user", actor_id, reason),
    )


def required_text(payload: dict[str, Any], key: str, label: str | None = None) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise APIError(400, f"{label or key.replace('_', ' ').capitalize()} is required.")
    return value.strip()


def positive_int(payload: dict[str, Any], key: str, label: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise APIError(400, f"{label} must be a positive whole number.")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise APIError(400, f"{label} must be a positive whole number.") from None
    if result <= 0 or str(value).strip() != str(result):
        raise APIError(400, f"{label} must be a positive whole number.")
    return result


def nonnegative_money(payload: dict[str, Any], key: str, label: str) -> float:
    value = payload.get(key)
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise APIError(400, f"{label} must be a valid amount.") from None
    if not math.isfinite(result) or result < 0:
        raise APIError(400, f"{label} cannot be negative or non-finite.")
    return money(result)


def valid_date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise APIError(400, f"{label} must be an ISO date.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise APIError(400, f"{label} must be an ISO date.") from None
    if parsed.isoformat() != value:
        raise APIError(400, f"{label} must use YYYY-MM-DD format.")
    return value


def request_context(connection: sqlite3.Connection, request_id: str) -> sqlite3.Row:
    row = connection.execute(
        """SELECT pr.*, rl.request_line_id, rl.description, rl.category, rl.quantity,
                  rl.unit_of_measure, rl.estimated_unit_cost, rl.estimated_total AS line_total,
                  rl.currency AS line_currency, a.approval_id, a.approval_level,
                  a.decision, a.approver_id, a.threshold_amount
           FROM purchase_requests pr
           JOIN request_lines rl ON rl.request_id = pr.request_id
           JOIN approvals a ON a.request_id = pr.request_id
           WHERE pr.request_id = ?""",
        (request_id,),
    ).fetchone()
    if not row:
        raise APIError(404, "Purchase request not found.")
    return row


def create_request(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    requester_id = required_text(payload, "requester_id", "Requester")
    department_id = required_text(payload, "department_id", "Department")
    budget_id = required_text(payload, "budget_id", "Budget")
    description = required_text(payload, "description", "Item description")
    category = required_text(payload, "category", "Category")
    justification = required_text(payload, "business_justification", "Business justification")
    if category not in CATEGORIES:
        raise APIError(400, "Select a valid request category.")
    quantity = positive_int(payload, "quantity", "Quantity")
    unit_cost = nonnegative_money(payload, "estimated_unit_cost", "Estimated unit cost")
    if unit_cost <= 0:
        raise APIError(400, "Estimated unit cost must be greater than zero.")
    required_by = valid_date(payload.get("required_by"), "Required-by date")
    today = date.today().isoformat()
    if required_by < today:
        raise APIError(400, "Required-by date cannot be in the past.")

    requester = connection.execute(
        "SELECT requester_id, department_id FROM requesters WHERE requester_id=? AND status='active'",
        (requester_id,),
    ).fetchone()
    if not requester:
        raise APIError(400, "Select an active requester.")
    if requester["department_id"] != department_id:
        raise APIError(400, "The selected requester does not belong to this department.")
    budget = connection.execute(
        """SELECT budget_id, currency FROM budgets
           WHERE budget_id=? AND department_id=? AND status='active'""",
        (budget_id, department_id),
    ).fetchone()
    if not budget:
        raise APIError(400, "Select an active budget for the chosen department.")
    currency = budget["currency"]
    total = money(quantity * unit_cost)

    duplicate = connection.execute(
        """SELECT pr.request_id FROM purchase_requests pr
           JOIN request_lines rl ON rl.request_id=pr.request_id
           WHERE pr.requester_id=? AND pr.department_id=? AND pr.request_date=?
             AND rl.description=? AND rl.category=? AND rl.quantity=?
             AND rl.estimated_unit_cost=? AND rl.currency=?""",
        (requester_id, department_id, today, description, category, quantity, unit_cost, currency),
    ).fetchone()
    if duplicate:
        raise APIError(409, f"This appears to duplicate request {duplicate['request_id']}.")

    if total < APPROVAL_AUTO_LIMIT:
        level, decision, threshold = "automatic", "not_required", 0.0
        approver_id = None
    elif total <= APPROVAL_MANAGER_LIMIT:
        level, decision, threshold = "manager", "pending", APPROVAL_AUTO_LIMIT
        approver = connection.execute(
            """SELECT requester_id FROM requesters
               WHERE department_id=? AND role='manager' AND status='active'
               ORDER BY requester_id LIMIT 1""",
            (department_id,),
        ).fetchone()
        if not approver:
            raise APIError(409, "No active manager is configured for this department.")
        approver_id = approver["requester_id"]
    else:
        level, decision, threshold = "director", "pending", APPROVAL_MANAGER_LIMIT
        approver = connection.execute(
            """SELECT requester_id FROM requesters
               WHERE role='director' AND status='active'
               ORDER BY requester_id LIMIT 1"""
        ).fetchone()
        if not approver:
            raise APIError(409, "No active director is configured.")
        approver_id = approver["requester_id"]
    request_id = next_id(connection, "PR", "purchase_requests", "request_id")
    line_id = next_id(connection, "RL", "request_lines", "request_line_id")
    approval_id = next_id(connection, "APR", "approvals", "approval_id")
    request_state = "approved" if decision == "not_required" else "pending_approval"
    connection.execute(
        """INSERT INTO purchase_requests
           (request_id, requester_id, department_id, budget_id, request_date, required_by,
            estimated_total, currency, status, business_justification)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (request_id, requester_id, department_id, budget_id, today, required_by,
         total, currency, request_state, justification),
    )
    connection.execute(
        """INSERT INTO request_lines
           (request_line_id, request_id, line_number, description, category, quantity,
            unit_of_measure, estimated_unit_cost, estimated_total, currency)
           VALUES (?, ?, 1, ?, ?, ?, 'each', ?, ?, ?)""",
        (line_id, request_id, description, category, quantity, unit_cost, total, currency),
    )
    connection.execute(
        """INSERT INTO approvals
           (approval_id, request_id, approval_level, threshold_amount, decision, approver_id,
            requested_at, decided_at, decision_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (approval_id, request_id, level, threshold, decision, approver_id, today,
         today if decision == "not_required" else None,
         "Below approval threshold" if decision == "not_required" else "Awaiting required approval"),
    )
    add_event(connection, "request_events", "request_id", request_id, "submitted",
              None, "submitted", "Purchase request submitted", requester_id)
    if decision == "not_required":
        add_event(connection, "request_events", "request_id", request_id, "approval_recorded",
                  "submitted", "approved", "Automatically approved below the threshold")
    else:
        add_event(connection, "request_events", "request_id", request_id, "approval_requested",
                  "submitted", "pending_approval", f"{level.title()} approval required", approver_id)
    return {"request_id": request_id, "status": request_state, "approval_level": level,
            "estimated_total": total, "currency": currency}


def decide_request(
    connection: sqlite3.Connection, request_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    context = request_context(connection, request_id)
    if context["decision"] != "pending" or context["status"] != "pending_approval":
        raise APIError(409, "This request is not awaiting approval.")
    decision = payload.get("decision")
    if decision not in ("approved", "rejected"):
        raise APIError(400, "Decision must be approved or rejected.")
    reason = required_text(payload, "reason", "Decision reason")
    today = date.today().isoformat()
    connection.execute(
        """UPDATE approvals SET decision=?, decided_at=?, decision_reason=?
           WHERE approval_id=? AND decision='pending'""",
        (decision, today, reason, context["approval_id"]),
    )
    connection.execute(
        "UPDATE purchase_requests SET status=? WHERE request_id=?",
        (decision, request_id),
    )
    add_event(connection, "request_events", "request_id", request_id, "approval_decided",
              "pending_approval", decision, reason, context["approver_id"])
    return {"request_id": request_id, "status": decision}


def create_rfq(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    request_id = required_text(payload, "request_id", "Purchase request")
    context = request_context(connection, request_id)
    if context["status"] != "approved" or context["decision"] not in ("approved", "not_required"):
        raise APIError(409, "Only approved requests can be sent for supplier quotes.")
    if connection.execute("SELECT 1 FROM rfqs WHERE request_id=?", (request_id,)).fetchone():
        raise APIError(409, "This request already has an RFQ.")
    competition_type = payload.get("competition_type", "competitive")
    if competition_type not in ("competitive", "single_source"):
        raise APIError(400, "Select competitive or single-source purchasing.")
    single_reason = payload.get("single_source_reason")
    if competition_type == "single_source":
        single_reason = required_text(
            {"reason": single_reason}, "reason", "Single-source justification"
        )
    issue_date = date.today()
    close_date = valid_date(payload.get("close_date"), "RFQ close date")
    if close_date <= issue_date.isoformat():
        raise APIError(400, "RFQ close date must be after today.")
    if close_date > date.fromisoformat(context["required_by"]).isoformat():
        raise APIError(400, "RFQ close date must be on or before the purchase request's required-by date.")
    rfq_id = next_id(connection, "RFQ", "rfqs", "rfq_id")
    connection.execute(
        """INSERT INTO rfqs
           (rfq_id, request_id, issue_date, close_date, required_by,
            competition_type, single_source_reason, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'open')""",
        (rfq_id, request_id, issue_date.isoformat(), close_date, context["required_by"],
         competition_type, single_reason),
    )
    add_event(connection, "rfq_events", "rfq_id", rfq_id, "issued", None, "open",
              "RFQ issued to suppliers" if competition_type == "competitive"
              else f"Single-source exception: {single_reason}")
    return {"rfq_id": rfq_id, "status": "open"}


def add_quote(
    connection: sqlite3.Connection, rfq_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    rfq = connection.execute("SELECT * FROM rfqs WHERE rfq_id=?", (rfq_id,)).fetchone()
    if not rfq:
        raise APIError(404, "RFQ not found.")
    if rfq["status"] != "open":
        raise APIError(409, "Quotes can only be added to an open RFQ.")
    if date.today().isoformat() > rfq["close_date"]:
        raise APIError(409, "The RFQ has passed its closing date.")
    supplier_id = required_text(payload, "supplier_id", "Supplier")
    supplier = connection.execute(
        "SELECT * FROM suppliers WHERE supplier_id=? AND status='active'", (supplier_id,)
    ).fetchone()
    if not supplier:
        raise APIError(400, "Select an active supplier.")
    if connection.execute(
        "SELECT 1 FROM supplier_quotes WHERE rfq_id=? AND supplier_id=?",
        (rfq_id, supplier_id),
    ).fetchone():
        raise APIError(409, "This supplier has already submitted a quote for this RFQ.")
    if rfq["competition_type"] == "single_source" and connection.execute(
        "SELECT 1 FROM supplier_quotes WHERE rfq_id=?", (rfq_id,)
    ).fetchone():
        raise APIError(409, "A single-source RFQ accepts one supplier quote.")
    context = request_context(connection, rfq["request_id"])
    unit_price = nonnegative_money(payload, "unit_price", "Unit price")
    if unit_price <= 0:
        raise APIError(400, "Unit price must be greater than zero.")
    shipping = nonnegative_money(payload, "shipping_cost", "Shipping cost")
    tax = nonnegative_money(payload, "tax_amount", "Tax amount")
    lead_time = positive_int(payload, "lead_time_days", "Lead time")
    quote_currency = payload.get("currency")
    if quote_currency not in CURRENCIES:
        raise APIError(400, "Select a supported quote currency.")
    valid_until = valid_date(payload.get("valid_until"), "Quote expiry date")
    today = date.today().isoformat()
    if valid_until < today:
        raise APIError(400, "Quote expiry date cannot be in the past.")
    existing_quote_ids = connection.execute(
        "SELECT quote_id FROM supplier_quotes WHERE supplier_id=?", (supplier_id,)
    ).fetchall()
    sequence = max(
        (
            int(match.group(1))
            for row in existing_quote_ids
            if (match := re.search(r"(\d+)$", row["quote_id"]))
        ),
        default=0,
    )
    quote_id = f"QUO-{supplier['supplier_code']}-{date.today().year}-{sequence + 1:06d}"
    status = "submitted"
    exclusion_reason = None
    if quote_currency != context["currency"]:
        status = "excluded_currency"
        exclusion_reason = "Currency differs from the purchase request; excluded from comparison"
    if date.today() + timedelta(days=lead_time) > date.fromisoformat(context["required_by"]):
        if status == "submitted":
            status = "not_selected"
        exclusion_reason = (exclusion_reason + "; " if exclusion_reason else "")
        exclusion_reason += "Lead time misses the request required-by date; excluded from comparison"
    landed_total = money(unit_price * context["quantity"] + shipping + tax)
    connection.execute(
        """INSERT INTO supplier_quotes
           (quote_id, rfq_id, supplier_id, quote_date, valid_until, currency, unit_price,
            shipping_cost, tax_amount, landed_total, lead_time_days, status, exclusion_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (quote_id, rfq_id, supplier_id, today, valid_until, quote_currency, unit_price,
         shipping, tax, landed_total, lead_time, status, exclusion_reason),
    )
    add_event(connection, "rfq_events", "rfq_id", rfq_id, "quote_received",
              "open", "open", f"Quote {quote_id} received; landed total {landed_total:.2f}"
              + ("; excluded due to currency mismatch" if exclusion_reason else ""),
              supplier_id)
    return {"quote_id": quote_id, "status": status, "landed_total": landed_total}


def award_rfq(
    connection: sqlite3.Connection, rfq_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    rfq = connection.execute("SELECT * FROM rfqs WHERE rfq_id=?", (rfq_id,)).fetchone()
    if not rfq:
        raise APIError(404, "RFQ not found.")
    if rfq["status"] != "open":
        raise APIError(409, "This RFQ is not open for an award.")
    today = date.today().isoformat()
    if today > rfq["close_date"]:
        raise APIError(409, "This RFQ has closed.")
    context = request_context(connection, rfq["request_id"])
    quotes = connection.execute(
        """SELECT q.*, s.status AS supplier_status FROM supplier_quotes q
           JOIN suppliers s ON s.supplier_id=q.supplier_id WHERE q.rfq_id=?""",
        (rfq_id,),
    ).fetchall()
    if rfq["competition_type"] == "competitive" and len(quotes) < 3:
        raise APIError(409, "Competitive awards require at least three supplier quotes.")
    eligible = [
        quote for quote in quotes
        if quote["status"] == "submitted" and quote["supplier_status"] == "active"
        and quote["currency"] == context["currency"] and quote["valid_until"] >= today
    ]
    if not eligible:
        raise APIError(409, "No unexpired, active-supplier quote in the request currency can be awarded.")

    allocations = payload.get("allocations")
    if not isinstance(allocations, list) or not allocations:
        raise APIError(400, "Choose at least one quote and its awarded quantity.")
    quotes_by_id = {quote["quote_id"]: quote for quote in eligible}
    quantities: dict[str, int] = {}
    for allocation in allocations:
        if not isinstance(allocation, dict):
            raise APIError(400, "Each quote allocation must include a quote ID and quantity.")
        quote_id = required_text(allocation, "quote_id", "Quote")
        if quote_id not in quotes_by_id:
            raise APIError(400, f"Quote {quote_id} is expired, mismatched, inactive, or not part of this RFQ.")
        if quote_id in quantities:
            raise APIError(400, f"Quote {quote_id} is listed more than once.")
        quantities[quote_id] = positive_int(allocation, "quantity", "Award quantity")
    if sum(quantities.values()) != context["quantity"]:
        raise APIError(400, f"Award quantities must total the requested {context['quantity']} units.")
    cheapest = min(eligible, key=lambda quote: (quote["landed_total"], quote["lead_time_days"]))
    rationale = payload.get("rationale")
    if any(quotes_by_id[qid]["quote_id"] != cheapest["quote_id"] for qid in quantities):
        rationale = required_text(
            {"rationale": rationale}, "rationale", "Non-lowest award rationale"
        )
    elif rationale is not None:
        rationale = str(rationale).strip() or None

    total_quantity = context["quantity"]
    amounts: dict[str, float] = {}
    for quote_id, quantity in quantities.items():
        quote = quotes_by_id[quote_id]
        shipping_part = money(quote["shipping_cost"] * quantity / total_quantity)
        tax_part = money(quote["tax_amount"] * quantity / total_quantity)
        amounts[quote_id] = money(quote["unit_price"] * quantity + shipping_part + tax_part)
    award_total = money(sum(amounts.values()))
    budget = connection.execute(
        "SELECT allocation_amount FROM budgets WHERE budget_id=?", (context["budget_id"],)
    ).fetchone()
    already_reserved = connection.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM budget_ledger WHERE budget_id=?",
        (context["budget_id"],),
    ).fetchone()[0]
    available = money(budget["allocation_amount"] - already_reserved)
    if award_total > available:
        raise APIError(
            409,
            f"Budget has {available:.2f} available but this award needs {award_total:.2f}. "
            "Reduce the award or select a different budget/request.",
        )

    po_ids: list[str] = []
    for quote_id, quantity in quantities.items():
        quote = quotes_by_id[quote_id]
        fraction = quantity / total_quantity
        shipping_part = money(quote["shipping_cost"] * fraction)
        tax_part = money(quote["tax_amount"] * fraction)
        line_total = money(quote["unit_price"] * quantity + shipping_part + tax_part)
        award_id = next_id(connection, "AWD", "rfq_awards", "award_id")
        po_id = next_id(connection, "PO", "purchase_orders", "po_id")
        po_line_id = next_id(connection, "POL", "purchase_order_lines", "po_line_id")
        delivery_id = f"DEL-{po_id}-01"
        expected_date = (date.today() + timedelta(days=quote["lead_time_days"])).isoformat()
        connection.execute(
            """INSERT INTO rfq_awards
               (award_id, rfq_id, quote_id, awarded_at, quantity_awarded, award_total,
                rationale, decision_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'awarded')""",
            (award_id, rfq_id, quote_id, today, quantity, line_total,
             rationale or "Lowest eligible landed-cost quote selected"),
        )
        connection.execute(
            """INSERT INTO purchase_orders
               (po_id, request_id, rfq_id, award_id, supplier_id, approval_id,
                issued_date, currency, total_amount, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'in_transit')""",
            (po_id, context["request_id"], rfq_id, award_id, quote["supplier_id"],
             context["approval_id"], today, context["currency"], line_total),
        )
        connection.execute(
            """INSERT INTO purchase_order_lines
               (po_line_id, po_id, request_line_id, quote_id, quantity, unit_price,
                shipping_allocated, tax_allocated, line_total)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (po_line_id, po_id, context["request_line_id"], quote_id, quantity,
             quote["unit_price"], shipping_part, tax_part, line_total),
        )
        remaining_after = money(available - line_total)
        ledger_id = next_id(connection, "LED", "budget_ledger", "ledger_id")
        connection.execute(
            """INSERT INTO budget_ledger
               (ledger_id, budget_id, po_id, event_type, event_date, amount, balance_after, reason)
               VALUES (?, ?, ?, 'reserve', ?, ?, ?, ?)""",
            (ledger_id, context["budget_id"], po_id, today, line_total, remaining_after,
             "Budget reserved for purchase order"),
        )
        available = remaining_after
        connection.execute(
            """INSERT INTO deliveries
               (delivery_id, po_id, shipment_number, expected_date, shipped_date, received_date,
                quantity_shipped, quantity_received, status, exception_reason)
               VALUES (?, ?, 1, ?, ?, NULL, ?, 0, 'in_transit', NULL)""",
            (delivery_id, po_id, expected_date, today, quantity),
        )
        add_event(connection, "purchase_order_events", "po_id", po_id, "issued",
                  None, "issued", f"PO issued from RFQ {rfq_id}; quote {quote_id}")
        add_event(connection, "purchase_order_events", "po_id", po_id, "delivery_status_updated",
                  "issued", "in_transit", "Supplier shipment is in transit")
        add_event(connection, "delivery_events", "delivery_id", delivery_id, "shipment_created",
                  None, "in_transit", f"{quantity} units shipped by supplier", quote["supplier_id"])
        connection.execute(
            "UPDATE supplier_quotes SET status=? WHERE quote_id=?",
            ("awarded", quote_id),
        )
        add_event(connection, "rfq_events", "rfq_id", rfq_id, "quote_awarded",
                  "open", "open", f"Quote {quote_id} awarded; PO {po_id} created")
        po_ids.append(po_id)

    for quote in quotes:
        if quote["status"] == "submitted" and quote["quote_id"] not in quantities:
            new_status = "expired" if quote["valid_until"] < today else "not_selected"
            connection.execute(
                "UPDATE supplier_quotes SET status=? WHERE quote_id=?",
                (new_status, quote["quote_id"]),
            )
    final_rfq_status = "single_source_awarded" if rfq["competition_type"] == "single_source" else "awarded"
    connection.execute("UPDATE rfqs SET status=? WHERE rfq_id=?", (final_rfq_status, rfq_id))
    add_event(connection, "rfq_events", "rfq_id", rfq_id, "award_recorded",
              "open", "awarded", f"RFQ awarded; purchase orders: {', '.join(po_ids)}")
    add_event(connection, "rfq_events", "rfq_id", rfq_id, "closed",
              "awarded", final_rfq_status, "RFQ closed after supplier award")
    connection.execute(
        "UPDATE purchase_requests SET status='ordered' WHERE request_id=?",
        (context["request_id"],),
    )
    add_event(connection, "request_events", "request_id", context["request_id"],
              "order_created", "approved", "ordered",
              f"Purchase order(s) created: {', '.join(po_ids)}")
    return {"rfq_id": rfq_id, "purchase_order_ids": po_ids, "award_total": award_total}


def receive_delivery(
    connection: sqlite3.Connection, delivery_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    delivery = connection.execute(
        "SELECT * FROM deliveries WHERE delivery_id=?", (delivery_id,)
    ).fetchone()
    if not delivery:
        raise APIError(404, "Delivery not found.")
    if delivery["status"] == "delivered":
        raise APIError(409, "This delivery is already fully received.")
    quantity = positive_int(payload, "quantity", "Receipt quantity")
    remaining = delivery["quantity_shipped"] - delivery["quantity_received"]
    if quantity > remaining:
        raise APIError(400, f"Only {remaining} units remain to be received.")
    received_total = delivery["quantity_received"] + quantity
    status = "delivered" if received_total == delivery["quantity_shipped"] else "partially_received"
    today = date.today().isoformat()
    connection.execute(
        """UPDATE deliveries SET quantity_received=?, received_date=?, status=?
           WHERE delivery_id=?""",
        (received_total, today, status, delivery_id),
    )
    add_event(connection, "delivery_events", "delivery_id", delivery_id, "receipt_recorded",
              delivery["status"], status, f"Received {quantity} units; cumulative {received_total}")

    po_id = delivery["po_id"]
    totals = connection.execute(
        """SELECT COALESCE(SUM(quantity), 0) AS ordered,
                  COALESCE(SUM(quantity_received), 0) AS received
           FROM purchase_order_lines pol
           JOIN deliveries d ON d.po_id=pol.po_id WHERE pol.po_id=?""",
        (po_id,),
    ).fetchone()
    po_status = "delivered" if totals["received"] == totals["ordered"] else "partially_received"
    current_po = connection.execute(
        "SELECT status FROM purchase_orders WHERE po_id=?", (po_id,)
    ).fetchone()["status"]
    connection.execute(
        "UPDATE purchase_orders SET status=? WHERE po_id=?", (po_status, po_id)
    )
    add_event(connection, "purchase_order_events", "po_id", po_id, "delivery_status_updated",
              current_po, po_status, f"Receipt progress {totals['received']} of {totals['ordered']} units")
    return {"delivery_id": delivery_id, "status": status, "received": received_total,
            "remaining": remaining - quantity, "purchase_order_status": po_status}


def get_masters(connection: sqlite3.Connection) -> dict[str, Any]:
    departments = as_dicts(connection.execute(
        "SELECT department_id, name FROM departments WHERE status='active' ORDER BY name"
    ).fetchall())
    requesters = as_dicts(connection.execute(
        """SELECT requester_id, display_name, department_id, role FROM requesters
           WHERE status='active' ORDER BY display_name"""
    ).fetchall())
    suppliers = as_dicts(connection.execute(
        """SELECT supplier_id, supplier_code, legal_name, currency, risk_flag FROM suppliers
           WHERE status='active' ORDER BY legal_name"""
    ).fetchall())
    budgets = as_dicts(connection.execute(
        """SELECT b.budget_id, b.department_id, b.currency, b.allocation_amount,
                  b.allocation_amount-COALESCE(SUM(l.amount),0) AS available_amount
           FROM budgets b LEFT JOIN budget_ledger l ON l.budget_id=b.budget_id
           WHERE b.status='active' GROUP BY b.budget_id ORDER BY b.budget_id"""
    ).fetchall())
    return {"departments": departments, "requesters": requesters,
            "suppliers": suppliers, "budgets": budgets,
            "categories": CATEGORIES, "currencies": CURRENCIES}


def get_requests(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return as_dicts(connection.execute(
        """SELECT pr.request_id, pr.request_date, pr.required_by, pr.estimated_total,
                  pr.currency, pr.status, pr.business_justification, pr.requester_id,
                  r.display_name AS requester_name, d.name AS department_name,
                  rl.description, rl.category, rl.quantity, rl.estimated_unit_cost,
                  a.approval_id, a.approval_level, a.decision AS approval_decision,
                  a.approver_id, approver.display_name AS approver_name, a.decision_reason,
                  f.rfq_id
           FROM purchase_requests pr
           JOIN requesters r ON r.requester_id=pr.requester_id
           JOIN departments d ON d.department_id=pr.department_id
           JOIN request_lines rl ON rl.request_id=pr.request_id
           JOIN approvals a ON a.request_id=pr.request_id
           LEFT JOIN requesters approver ON approver.requester_id=a.approver_id
           LEFT JOIN rfqs f ON f.request_id=pr.request_id
           ORDER BY pr.request_id DESC LIMIT 300"""
    ).fetchall())


def get_rfqs(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rfqs = as_dicts(connection.execute(
        """SELECT f.*, pr.description, pr.currency, pr.quantity, pr.request_id
           FROM rfqs f
           JOIN purchase_requests p ON p.request_id=f.request_id
           JOIN request_lines pr ON pr.request_id=p.request_id
           ORDER BY f.issue_date DESC, f.rfq_id DESC LIMIT 300"""
    ).fetchall())
    for rfq in rfqs:
        rfq["quotes"] = as_dicts(connection.execute(
            """SELECT q.quote_id, q.supplier_id, s.legal_name AS supplier_name,
                      s.status AS supplier_status, s.risk_flag, q.quote_date, q.valid_until,
                      q.currency, q.unit_price, q.shipping_cost, q.tax_amount, q.landed_total,
                      q.lead_time_days, q.status, q.exclusion_reason,
                      EXISTS(SELECT 1 FROM rfq_awards a WHERE a.quote_id=q.quote_id) AS selected
               FROM supplier_quotes q JOIN suppliers s ON s.supplier_id=q.supplier_id
               WHERE q.rfq_id=? ORDER BY q.landed_total, q.lead_time_days""",
            (rfq["rfq_id"],),
        ).fetchall())
    return rfqs


def get_orders(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    orders = as_dicts(connection.execute(
        """SELECT po.po_id, po.request_id, po.rfq_id, po.issued_date, po.currency,
                  po.total_amount, po.status, s.legal_name AS supplier_name,
                  rl.description, pol.quantity, pol.unit_price, pol.quote_id,
                  (SELECT COALESCE(SUM(d.quantity_shipped),0) FROM deliveries d
                   WHERE d.po_id=po.po_id) AS quantity_shipped,
                  (SELECT COALESCE(SUM(d.quantity_received),0) FROM deliveries d
                   WHERE d.po_id=po.po_id) AS quantity_received,
                  (SELECT MIN(d.expected_date) FROM deliveries d
                   WHERE d.po_id=po.po_id) AS expected_date
           FROM purchase_orders po
           JOIN suppliers s ON s.supplier_id=po.supplier_id
           JOIN purchase_order_lines pol ON pol.po_id=po.po_id
           JOIN request_lines rl ON rl.request_line_id=pol.request_line_id
           ORDER BY po.issued_date DESC, po.po_id DESC LIMIT 300"""
    ).fetchall())
    for order in orders:
        order["deliveries"] = as_dicts(connection.execute(
            """SELECT delivery_id, expected_date, quantity_shipped,
                      quantity_received, status
               FROM deliveries WHERE po_id=? ORDER BY shipment_number""",
            (order["po_id"],),
        ).fetchall())
    return orders


def get_events(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return as_dicts(connection.execute(
        """SELECT * FROM (
             SELECT event_id, request_id AS record_id, 'Request' AS record_type,
                    event_type, from_state, to_state, event_at, actor_type, actor_id, reason
             FROM request_events
             UNION ALL
             SELECT event_id, rfq_id, 'RFQ', event_type, from_state, to_state,
                    event_at, actor_type, actor_id, reason FROM rfq_events
             UNION ALL
             SELECT event_id, po_id, 'Purchase order', event_type, from_state, to_state,
                    event_at, actor_type, actor_id, reason FROM purchase_order_events
             UNION ALL
             SELECT event_id, delivery_id, 'Delivery', event_type, from_state, to_state,
                    event_at, actor_type, actor_id, reason FROM delivery_events
           ) events ORDER BY event_at DESC, event_id DESC LIMIT 100"""
    ).fetchall())


def get_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    def count(table: str) -> int:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    allocation = connection.execute(
        "SELECT COALESCE(SUM(allocation_amount),0) FROM budgets WHERE status='active'"
    ).fetchone()[0]
    reserved = connection.execute(
        "SELECT COALESCE(SUM(amount),0) FROM budget_ledger"
    ).fetchone()[0]
    return {
        "row_counts": {
            "requesters": count("requesters"), "suppliers": count("suppliers"),
            "purchase_requests": count("purchase_requests"), "rfqs": count("rfqs"),
            "supplier_quotes": count("supplier_quotes"), "purchase_orders": count("purchase_orders"),
            "deliveries": count("deliveries"),
        },
        "metrics": {
            "pending_approvals": connection.execute(
                "SELECT COUNT(*) FROM approvals WHERE decision='pending'"
            ).fetchone()[0],
            "competitive_rfqs": connection.execute(
                "SELECT COUNT(*) FROM rfqs WHERE competition_type='competitive'"
            ).fetchone()[0],
            "single_source_rfqs": connection.execute(
                "SELECT COUNT(*) FROM rfqs WHERE competition_type='single_source'"
            ).fetchone()[0],
            "open_rfqs": connection.execute(
                "SELECT COUNT(*) FROM rfqs WHERE status='open'"
            ).fetchone()[0],
            "awarded_rfqs": connection.execute(
                "SELECT COUNT(*) FROM rfqs WHERE status IN ('awarded','single_source_awarded')"
            ).fetchone()[0],
            "in_transit_orders": connection.execute(
                "SELECT COUNT(*) FROM purchase_orders WHERE status='in_transit'"
            ).fetchone()[0],
            "partial_orders": connection.execute(
                "SELECT COUNT(*) FROM purchase_orders WHERE status='partially_received'"
            ).fetchone()[0],
            "budget_utilization_percent": round(reserved / allocation * 100, 1) if allocation else 0,
            "reserved_amount": money(reserved),
            "available_amount": money(allocation - reserved),
        },
        "database": "SQLite",
    }


def api_get(connection: sqlite3.Connection, path: str, read_only: bool = False) -> Any:
    routes = {
        "/api/health": lambda: {"status": "ok", "read_only": read_only},
        "/api/summary": lambda: get_summary(connection),
        "/api/masters": lambda: get_masters(connection),
        "/api/requests": lambda: get_requests(connection),
        "/api/rfqs": lambda: get_rfqs(connection),
        "/api/orders": lambda: get_orders(connection),
        "/api/events": lambda: get_events(connection),
    }
    if path not in routes:
        raise APIError(404, "API endpoint not found.")
    return routes[path]()


class ProcurementHandler(BaseHTTPRequestHandler):
    server_version = "ProcurementDemo/1.0"
    db_path: Path
    read_only = False

    def send_json(self, status: int, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_payload(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError(400, "Invalid content length.") from None
        if length <= 0 or length > 1_000_000:
            raise APIError(400, "Request body must be between 1 byte and 1 MB.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise APIError(400, "Request body must be valid JSON.") from None
        if not isinstance(payload, dict):
            raise APIError(400, "Request body must be a JSON object.")
        return payload

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path.startswith("/api/"):
            connection = connect_database(self.db_path)
            try:
                self.send_json(200, api_get(connection, path, self.read_only))
            except APIError as error:
                self.send_json(error.status, {"error": str(error)})
            except sqlite3.Error as error:
                self.log_error("Database error: %s", error)
                self.send_json(500, {"error": "A database error occurred. Check the server log."})
            finally:
                connection.close()
            return
        if path in ("/", "/index.html"):
            path = "/dashboard.html"
        allowed = {"/dashboard.html", "/styles.css", "/dashboard.js"}
        if path not in allowed:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        file_path = ROOT / path.lstrip("/")
        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = file_path.read_bytes()
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.read_only:
            self.send_json(HTTPStatus.FORBIDDEN, {
                "error": "This public demo is read-only."
            })
            return
        path = unquote(urlsplit(self.path).path)
        try:
            payload = self.read_payload()
            connection = connect_database(self.db_path)
            try:
                with connection:
                    result = self.route_post(connection, path, payload)
                self.send_json(201, result)
            finally:
                connection.close()
        except APIError as error:
            self.send_json(error.status, {"error": str(error)})
        except sqlite3.IntegrityError as error:
            self.log_error("Integrity constraint: %s", error)
            self.send_json(409, {"error": "The change conflicts with an existing record or relationship."})
        except sqlite3.Error as error:
            self.log_error("Database error: %s", error)
            self.send_json(500, {"error": "A database error occurred. Check the server log."})

    def route_post(
        self, connection: sqlite3.Connection, path: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if path == "/api/requests":
            return create_request(connection, payload)
        if path == "/api/rfqs":
            return create_rfq(connection, payload)
        match = re.fullmatch(r"/api/requests/([^/]+)/approval", path)
        if match:
            return decide_request(connection, match.group(1), payload)
        match = re.fullmatch(r"/api/rfqs/([^/]+)/quotes", path)
        if match:
            return add_quote(connection, match.group(1), payload)
        match = re.fullmatch(r"/api/rfqs/([^/]+)/award", path)
        if match:
            return award_rfq(connection, match.group(1), payload)
        match = re.fullmatch(r"/api/deliveries/([^/]+)/receive", path)
        if match:
            return receive_delivery(connection, match.group(1), payload)
        raise APIError(404, "API endpoint not found.")

    def log_message(self, format_string: str, *args: Any) -> None:
        sys.stderr.write(
            f"[{date.today().isoformat()}] {self.address_string()} "
            f"{format_string % args}\n"
        )


def serve(host: str, port: int, db_path: Path, reset: bool, read_only: bool) -> None:
    initialize_database(db_path, reset)
    ProcurementHandler.db_path = db_path
    ProcurementHandler.read_only = read_only
    server = ThreadingHTTPServer((host, port), ProcurementHandler)
    server.daemon_threads = True
    print(f"Procurement app ready at http://{host}:{port}/")
    print(f"SQLite database: {db_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping procurement app.")
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                        help="Bind locally by default; do not expose this demo server to a network.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--reset", action="store_true",
                        help="Delete and recreate the application database from the synthetic seed.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    read_only_value = os.environ.get("READ_ONLY_DEMO", "0").lower()
    if read_only_value not in ("0", "1", "false", "true", "no", "yes"):
        parser.error("READ_ONLY_DEMO must be set to 1 or 0.")
    read_only = read_only_value in ("1", "true", "yes")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not read_only:
        parser.error("Non-local binding requires READ_ONLY_DEMO=1.")
    db_path = args.db
    if db_path is None:
        db_path = Path(os.environ["DATABASE_PATH"]) if os.environ.get("DATABASE_PATH") else None
    if db_path is None and os.environ.get("RAILWAY_VOLUME_MOUNT_PATH"):
        db_path = Path(os.environ["RAILWAY_VOLUME_MOUNT_PATH"]) / "procurement-app.sqlite3"
    if db_path is None and os.environ.get("RAILWAY_ENVIRONMENT_NAME"):
        db_path = Path(os.environ.get("TMPDIR", "/tmp")) / "procurement-app.sqlite3"
    if db_path is None:
        db_path = DEFAULT_DB
    serve(args.host, args.port, db_path.resolve(), args.reset, read_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
