from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
DEFAULT_SEED = 20261022
GENERATOR_VERSION = "1.0.0"
FULL_COUNTS = {
    "requesters": 300,
    "suppliers": 120,
    "purchase_requests": 1200,
    "rfqs": 500,
    "supplier_quotes": 1500,
    "purchase_orders": 900,
}

DEPARTMENTS = [
    "Finance", "Engineering", "Operations", "Information Technology",
    "People", "Marketing", "Legal", "Customer Support", "Facilities",
    "Research", "Sales", "Innovation Lab",
]
CATEGORIES = [
    "IT Hardware", "Office Supplies", "Facilities", "Professional Services",
    "Software", "Safety Equipment",
]
PRODUCTS = {
    "IT Hardware": ["Workstation", "Network switch", "Monitor", "Laptop"],
    "Office Supplies": ["Printer paper", "Ergonomic chair", "Filing cabinet"],
    "Facilities": ["LED fixture", "Air filter", "Access-control reader"],
    "Professional Services": ["Implementation services", "Audit support"],
    "Software": ["Analytics license", "Security license", "Design license"],
    "Safety Equipment": ["Protective kit", "First-aid station", "Safety monitor"],
}
CURRENCIES = ["USD", "EUR", "GBP", "CAD"]
COUNTRIES = ["US", "CA", "GB", "DE", "AU", "JP"]

# Ordered by foreign-key dependency. These definitions are also used to make
# the SQL schema, CSV headers, seed statements, and field-level dictionary.
TABLE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "departments": [
        ("department_id", "TEXT"), ("name", "TEXT"), ("cost_center", "TEXT"),
        ("status", "TEXT"), ("effective_from", "TEXT"), ("effective_to", "TEXT"),
    ],
    "requesters": [
        ("requester_id", "TEXT"), ("display_name", "TEXT"), ("email", "TEXT"),
        ("department_id", "TEXT"), ("role", "TEXT"), ("status", "TEXT"),
        ("effective_from", "TEXT"), ("effective_to", "TEXT"),
    ],
    "budgets": [
        ("budget_id", "TEXT"), ("department_id", "TEXT"), ("fiscal_year", "INTEGER"),
        ("currency", "TEXT"), ("allocation_amount", "REAL"), ("effective_from", "TEXT"),
        ("effective_to", "TEXT"), ("status", "TEXT"),
    ],
    "suppliers": [
        ("supplier_id", "TEXT"), ("supplier_code", "TEXT"), ("legal_name", "TEXT"),
        ("country", "TEXT"), ("currency", "TEXT"), ("status", "TEXT"),
        ("risk_flag", "INTEGER"), ("risk_reason", "TEXT"),
        ("effective_from", "TEXT"), ("effective_to", "TEXT"),
    ],
    "purchase_requests": [
        ("request_id", "TEXT"), ("requester_id", "TEXT"), ("department_id", "TEXT"),
        ("budget_id", "TEXT"), ("request_date", "TEXT"), ("required_by", "TEXT"),
        ("estimated_total", "REAL"), ("currency", "TEXT"), ("status", "TEXT"),
        ("business_justification", "TEXT"),
    ],
    "request_lines": [
        ("request_line_id", "TEXT"), ("request_id", "TEXT"), ("line_number", "INTEGER"),
        ("description", "TEXT"), ("category", "TEXT"), ("quantity", "INTEGER"),
        ("unit_of_measure", "TEXT"), ("estimated_unit_cost", "REAL"),
        ("estimated_total", "REAL"), ("currency", "TEXT"),
    ],
    "approvals": [
        ("approval_id", "TEXT"), ("request_id", "TEXT"), ("approval_level", "TEXT"),
        ("threshold_amount", "REAL"), ("decision", "TEXT"), ("approver_id", "TEXT"),
        ("requested_at", "TEXT"), ("decided_at", "TEXT"), ("decision_reason", "TEXT"),
    ],
    "rfqs": [
        ("rfq_id", "TEXT"), ("request_id", "TEXT"), ("issue_date", "TEXT"),
        ("close_date", "TEXT"), ("required_by", "TEXT"), ("competition_type", "TEXT"),
        ("single_source_reason", "TEXT"), ("status", "TEXT"),
    ],
    "supplier_quotes": [
        ("quote_id", "TEXT"), ("rfq_id", "TEXT"), ("supplier_id", "TEXT"),
        ("quote_date", "TEXT"), ("valid_until", "TEXT"), ("currency", "TEXT"),
        ("unit_price", "REAL"), ("shipping_cost", "REAL"), ("tax_amount", "REAL"),
        ("landed_total", "REAL"), ("lead_time_days", "INTEGER"), ("status", "TEXT"),
        ("exclusion_reason", "TEXT"),
    ],
    "rfq_awards": [
        ("award_id", "TEXT"), ("rfq_id", "TEXT"), ("quote_id", "TEXT"),
        ("awarded_at", "TEXT"), ("quantity_awarded", "INTEGER"),
        ("award_total", "REAL"), ("rationale", "TEXT"), ("decision_status", "TEXT"),
    ],
    "purchase_orders": [
        ("po_id", "TEXT"), ("request_id", "TEXT"), ("rfq_id", "TEXT"),
        ("award_id", "TEXT"), ("supplier_id", "TEXT"), ("approval_id", "TEXT"),
        ("issued_date", "TEXT"), ("currency", "TEXT"), ("total_amount", "REAL"),
        ("status", "TEXT"),
    ],
    "purchase_order_lines": [
        ("po_line_id", "TEXT"), ("po_id", "TEXT"), ("request_line_id", "TEXT"),
        ("quote_id", "TEXT"), ("quantity", "INTEGER"), ("unit_price", "REAL"),
        ("shipping_allocated", "REAL"), ("tax_allocated", "REAL"), ("line_total", "REAL"),
    ],
    "deliveries": [
        ("delivery_id", "TEXT"), ("po_id", "TEXT"), ("shipment_number", "INTEGER"),
        ("expected_date", "TEXT"), ("shipped_date", "TEXT"), ("received_date", "TEXT"),
        ("quantity_shipped", "INTEGER"), ("quantity_received", "INTEGER"),
        ("status", "TEXT"), ("exception_reason", "TEXT"),
    ],
    "budget_ledger": [
        ("ledger_id", "TEXT"), ("budget_id", "TEXT"), ("po_id", "TEXT"),
        ("event_type", "TEXT"), ("event_date", "TEXT"), ("amount", "REAL"),
        ("balance_after", "REAL"), ("reason", "TEXT"),
    ],
    "request_events": [
        ("event_id", "TEXT"), ("request_id", "TEXT"), ("event_type", "TEXT"),
        ("from_state", "TEXT"), ("to_state", "TEXT"), ("event_at", "TEXT"),
        ("actor_type", "TEXT"), ("actor_id", "TEXT"), ("reason", "TEXT"),
    ],
    "rfq_events": [
        ("event_id", "TEXT"), ("rfq_id", "TEXT"), ("event_type", "TEXT"),
        ("from_state", "TEXT"), ("to_state", "TEXT"), ("event_at", "TEXT"),
        ("actor_type", "TEXT"), ("actor_id", "TEXT"), ("reason", "TEXT"),
    ],
    "purchase_order_events": [
        ("event_id", "TEXT"), ("po_id", "TEXT"), ("event_type", "TEXT"),
        ("from_state", "TEXT"), ("to_state", "TEXT"), ("event_at", "TEXT"),
        ("actor_type", "TEXT"), ("actor_id", "TEXT"), ("reason", "TEXT"),
    ],
    "delivery_events": [
        ("event_id", "TEXT"), ("delivery_id", "TEXT"), ("event_type", "TEXT"),
        ("from_state", "TEXT"), ("to_state", "TEXT"), ("event_at", "TEXT"),
        ("actor_type", "TEXT"), ("actor_id", "TEXT"), ("reason", "TEXT"),
    ],
}

PRIMARY_KEYS = {
    "departments": "department_id", "requesters": "requester_id", "budgets": "budget_id",
    "suppliers": "supplier_id", "purchase_requests": "request_id",
    "request_lines": "request_line_id", "approvals": "approval_id", "rfqs": "rfq_id",
    "supplier_quotes": "quote_id", "rfq_awards": "award_id",
    "purchase_orders": "po_id", "purchase_order_lines": "po_line_id",
    "deliveries": "delivery_id", "budget_ledger": "ledger_id",
    "request_events": "event_id", "rfq_events": "event_id",
    "purchase_order_events": "event_id", "delivery_events": "event_id",
}

FOREIGN_KEYS = {
    "requesters": {"department_id": ("departments", "department_id")},
    "budgets": {"department_id": ("departments", "department_id")},
    "purchase_requests": {
        "requester_id": ("requesters", "requester_id"),
        "department_id": ("departments", "department_id"),
        "budget_id": ("budgets", "budget_id"),
    },
    "request_lines": {"request_id": ("purchase_requests", "request_id")},
    "approvals": {
        "request_id": ("purchase_requests", "request_id"),
        "approver_id": ("requesters", "requester_id"),
    },
    "rfqs": {"request_id": ("purchase_requests", "request_id")},
    "supplier_quotes": {
        "rfq_id": ("rfqs", "rfq_id"), "supplier_id": ("suppliers", "supplier_id"),
    },
    "rfq_awards": {
        "rfq_id": ("rfqs", "rfq_id"), "quote_id": ("supplier_quotes", "quote_id"),
    },
    "purchase_orders": {
        "request_id": ("purchase_requests", "request_id"),
        "rfq_id": ("rfqs", "rfq_id"), "award_id": ("rfq_awards", "award_id"),
        "supplier_id": ("suppliers", "supplier_id"), "approval_id": ("approvals", "approval_id"),
    },
    "purchase_order_lines": {
        "po_id": ("purchase_orders", "po_id"),
        "request_line_id": ("request_lines", "request_line_id"),
        "quote_id": ("supplier_quotes", "quote_id"),
    },
    "deliveries": {"po_id": ("purchase_orders", "po_id")},
    "budget_ledger": {
        "budget_id": ("budgets", "budget_id"), "po_id": ("purchase_orders", "po_id"),
    },
    "request_events": {"request_id": ("purchase_requests", "request_id")},
    "rfq_events": {"rfq_id": ("rfqs", "rfq_id")},
    "purchase_order_events": {"po_id": ("purchase_orders", "po_id")},
    "delivery_events": {"delivery_id": ("deliveries", "delivery_id")},
}

NULLABLE = {
    ("departments", "effective_to"), ("requesters", "effective_to"),
    ("suppliers", "risk_reason"), ("suppliers", "effective_to"),
    ("approvals", "approver_id"), ("approvals", "decided_at"),
    ("rfqs", "single_source_reason"), ("supplier_quotes", "exclusion_reason"),
    ("rfq_awards", "rationale"), ("purchase_orders", "approval_id"),
    ("deliveries", "shipped_date"), ("deliveries", "received_date"),
    ("deliveries", "exception_reason"),
    *{
        (table, field)
        for table in ("request_events", "rfq_events", "purchase_order_events", "delivery_events")
        for field in ("from_state", "actor_id")
    },
}

ENUMS = {
    ("departments", "status"): ["active"],
    ("requesters", "role"): ["requester", "manager", "director"],
    ("requesters", "status"): ["active"],
    ("budgets", "status"): ["active"],
    ("budgets", "currency"): CURRENCIES,
    ("suppliers", "status"): ["active", "inactive"],
    ("suppliers", "currency"): CURRENCIES,
    ("suppliers", "risk_flag"): [0, 1],
    ("suppliers", "country"): COUNTRIES,
    ("purchase_requests", "status"): [
        "pending_approval", "approved", "rejected", "ordered", "rfq_closed",
        "rfq_expired", "rejected_duplicate",
    ],
    ("purchase_requests", "currency"): CURRENCIES,
    ("request_lines", "category"): CATEGORIES,
    ("request_lines", "currency"): CURRENCIES,
    ("approvals", "approval_level"): ["automatic", "manager", "director"],
    ("approvals", "decision"): ["approved", "pending", "not_required", "rejected"],
    ("rfqs", "competition_type"): ["competitive", "single_source"],
    ("rfqs", "status"): ["open", "awarded", "single_source_awarded", "closed_no_award", "expired"],
    ("supplier_quotes", "status"): [
        "submitted", "awarded", "expired", "excluded_currency", "not_selected",
    ],
    ("supplier_quotes", "currency"): CURRENCIES,
    ("rfq_awards", "decision_status"): ["awarded"],
    ("purchase_orders", "status"): ["issued", "in_transit", "partially_received", "delivered"],
    ("purchase_orders", "currency"): CURRENCIES,
    ("deliveries", "status"): ["in_transit", "partially_received", "delivered", "delayed"],
    ("budget_ledger", "event_type"): ["reserve"],
}

UNIQUE_GROUPS = {
    "departments": [("cost_center",)],
    "requesters": [("email",)],
    "budgets": [("department_id", "fiscal_year")],
    "suppliers": [("supplier_code",)],
    "approvals": [("request_id",)],
    "rfqs": [("request_id",)],
    "request_lines": [("request_id", "line_number")],
    "supplier_quotes": [("rfq_id", "supplier_id")],
    "rfq_awards": [("rfq_id", "quote_id")],
    "purchase_orders": [("award_id",)],
    "purchase_order_lines": [("po_id", "request_line_id")],
    "deliveries": [("po_id", "shipment_number")],
    "budget_ledger": [("po_id",)],
}

EVENT_TABLES = {
    "request_events": ("request_id", "REQ"),
    "rfq_events": ("rfq_id", "RFQ"),
    "purchase_order_events": ("po_id", "PO"),
    "delivery_events": ("delivery_id", "DEL"),
}


def money(value: float | int | Decimal) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def iso_day(value: date) -> str:
    return value.isoformat()


def add_days(value: str | date, days: int) -> str:
    base = date.fromisoformat(value) if isinstance(value, str) else value
    return iso_day(base + timedelta(days=days))


def identifier(prefix: str, sequence: int) -> str:
    return f"{prefix}-2026-{sequence:06d}"


def empty_tables() -> dict[str, list[dict[str, Any]]]:
    return {name: [] for name in TABLE_COLUMNS}


def row(**values: Any) -> dict[str, Any]:
    return values


def build_dataset(seed: int = DEFAULT_SEED, scale: float = 1.0) -> dict[str, Any]:
    if not 0 < scale <= 1:
        raise ValueError("scale must be greater than 0 and no greater than 1")
    rng = random.Random(seed)
    tables = empty_tables()
    truth: list[dict[str, str]] = []
    counters: Counter[str] = Counter()

    def add_event(table: str, parent_id: str, event_type: str, before: str | None,
                  after: str, event_date: str, actor_type: str, actor_id: str | None,
                  reason: str) -> None:
        counters[table] += 1
        fk_field, _ = EVENT_TABLES[table]
        tables[table].append(row(
            event_id=f"{EVENT_TABLES[table][1]}E-{counters[table]:06d}",
            **{fk_field: parent_id}, event_type=event_type, from_state=before,
            to_state=after, event_at=event_date, actor_type=actor_type,
            actor_id=actor_id, reason=reason,
        ))

    def add_truth(issue_type: str, entity_type: str, entity_id: str,
                  related_entity_id: str, severity: str, expected: str,
                  observed: str, reason: str) -> None:
        truth.append(row(
            issue_type=issue_type, entity_type=entity_type, entity_id=entity_id,
            related_entity_id=related_entity_id, severity=severity,
            expected_outcome=expected, observed_outcome=observed, audit_reason=reason,
        ))

    n_requesters = max(15, round(FULL_COUNTS["requesters"] * scale))
    n_suppliers = max(4, round(FULL_COUNTS["suppliers"] * scale))
    n_requests = max(12, round(FULL_COUNTS["purchase_requests"] * scale))
    n_rfqs = max(5, round(FULL_COUNTS["rfqs"] * scale))
    n_pos = max(9, round(FULL_COUNTS["purchase_orders"] * scale))
    n_departments = 12 if scale >= 0.1 else max(3, min(12, round(12 * scale)))

    for i, name in enumerate(DEPARTMENTS[:n_departments], 1):
        tables["departments"].append(row(
            department_id=f"DEP-{i:03d}", name=name, cost_center=f"CC-{i:04d}",
            status="active", effective_from="2020-01-01", effective_to=None,
        ))

    # Assign one requester per department as manager and the next three as directors.
    manager_ids = {dept["department_id"]: f"USR-{i:04d}"
                   for i, dept in enumerate(tables["departments"], 1)}
    for i in range(1, n_requesters + 1):
        dept = tables["departments"][(i - 1) % n_departments]
        role = "manager" if i <= n_departments else ("director" if i <= n_departments + 3 else "requester")
        tables["requesters"].append(row(
            requester_id=f"USR-{i:04d}", display_name=f"Synthetic User {i:04d}",
            email=f"user{i:04d}@example.invalid", department_id=dept["department_id"],
            role=role, status="active", effective_from="2021-01-01", effective_to=None,
        ))

    special_budget_department = tables["departments"][-1]["department_id"]
    for dept in tables["departments"]:
        allocation = 10_000.00 if dept["department_id"] == special_budget_department else 5_000_000.00
        tables["budgets"].append(row(
            budget_id=f"BUD-{dept['department_id']}-2026",
            department_id=dept["department_id"], fiscal_year=2026, currency="USD",
            allocation_amount=allocation, effective_from="2026-01-01",
            effective_to="2026-12-31", status="active",
        ))

    risky_supplier_numbers = set(rng.sample(range(1, n_suppliers), k=max(1, n_suppliers // 6))) if n_suppliers > 1 else set()
    for i in range(1, n_suppliers + 1):
        inactive = (i == n_suppliers and n_suppliers >= 4)
        risk = i in risky_supplier_numbers
        tables["suppliers"].append(row(
            supplier_id=f"SUP-{i:04d}", supplier_code=f"S{i:04d}",
            legal_name=f"Synthetic Supplier {i:04d}", country=COUNTRIES[(i - 1) % len(COUNTRIES)],
            currency=CURRENCIES[(i - 1) % len(CURRENCIES)],
            status="inactive" if inactive else "active", risk_flag=int(risk),
            risk_reason="Synthetic elevated delivery/financial risk; enhanced review required" if risk else None,
            effective_from="2022-01-01", effective_to="2026-12-31" if inactive else None,
        ))

    request_target_count = max(2, n_requests - 1)
    regular_count = request_target_count - 1
    regular_departments = tables["departments"][:-1] if n_departments > 1 else tables["departments"]
    regular_requesters_by_dept: dict[str, list[str]] = defaultdict(list)
    for requester in tables["requesters"]:
        regular_requesters_by_dept[requester["department_id"]].append(requester["requester_id"])

    request_ids: list[str] = []
    target_request_id = identifier("PR", request_target_count)
    duplicate_source_id = identifier("PR", 1)
    approval_target_id = identifier("PR", 1)
    approval_records_by_request: dict[str, dict[str, Any]] = {}
    request_line_by_request: dict[str, dict[str, Any]] = {}

    def create_request(seq: int, dept: dict[str, Any], force_special: bool = False) -> dict[str, Any]:
        request_id = identifier("PR", seq)
        dept_id = dept["department_id"]
        budget_id = f"BUD-{dept_id}-2026"
        requesters = regular_requesters_by_dept[dept_id]
        requester_id = rng.choice(requesters)
        requested = date(2026, 1, 5) + timedelta(days=rng.randrange(0, 320))
        required = requested + timedelta(days=rng.randrange(20, 91))
        if force_special:
            category, description, quantity, unit_cost = (
                "IT Hardware", "Synthetic network switch", 10, 1000.00
            )
        else:
            category = rng.choice(CATEGORIES)
            description = f"Synthetic {rng.choice(PRODUCTS[category])}"
            quantity = rng.randint(8, 60)
            unit_cost = money(rng.uniform(35, 450))
            if seq == 1:
                category, description, quantity, unit_cost = (
                    "IT Hardware", "Synthetic workstation", 20, 500.00
                )
        total = money(quantity * unit_cost)
        request = row(
            request_id=request_id, requester_id=requester_id, department_id=dept_id,
            budget_id=budget_id, request_date=iso_day(requested),
            required_by=iso_day(required), estimated_total=total, currency="USD",
            status="approved",
            business_justification=f"Operational need for {description.lower()}",
        )
        line = row(
            request_line_id=f"RL-{seq:06d}", request_id=request_id, line_number=1,
            description=description, category=category, quantity=quantity,
            unit_of_measure="each", estimated_unit_cost=unit_cost,
            estimated_total=total, currency="USD",
        )
        tables["purchase_requests"].append(request)
        tables["request_lines"].append(line)
        request_ids.append(request_id)
        request_line_by_request[request_id] = line
        level = "automatic" if total < 5000 else ("manager" if total <= 25000 else "director")
        decision = "not_required" if level == "automatic" else "approved"
        approver_id = None
        if level == "manager":
            approver_id = manager_ids[dept_id]
        elif level == "director":
            directors = [r["requester_id"] for r in tables["requesters"] if r["role"] == "director"]
            approver_id = directors[(seq - 1) % len(directors)] if directors else manager_ids[dept_id]
        requested_at = iso_day(requested + timedelta(days=1))
        decided_at = None if request_id == approval_target_id else iso_day(requested + timedelta(days=2))
        if request_id == approval_target_id:
            decision = "pending"
        approval = row(
            approval_id=f"APR-{seq:06d}", request_id=request_id, approval_level=level,
            threshold_amount=5000.00 if level == "manager" else (25000.00 if level == "director" else 0.00),
            decision=decision, approver_id=None if decision in ("pending", "not_required") else approver_id,
            requested_at=requested_at, decided_at=decided_at,
            decision_reason="Awaiting required approval" if decision == "pending" else
            ("Below approval threshold" if decision == "not_required" else "Approved within delegated authority"),
        )
        tables["approvals"].append(approval)
        approval_records_by_request[request_id] = approval
        add_event("request_events", request_id, "submitted", None, "submitted",
                  iso_day(requested), "requester", requester_id, "Request submitted")
        if decision == "pending":
            add_event("request_events", request_id, "approval_requested", "submitted",
                      "pending_approval", requested_at, "system", None,
                      "Required approval is pending")
        else:
            add_event("request_events", request_id, "approval_recorded", "submitted",
                      "approved", decided_at or requested_at, "system", None,
                      approval["decision_reason"])
        return request

    for seq in range(1, regular_count + 1):
        dept = regular_departments[(seq - 1) % len(regular_departments)]
        create_request(seq, dept)
    special_request = create_request(request_target_count, tables["departments"][-1], True)

    # A fully identical business request is retained, assigned a fresh immutable ID,
    # and explicitly rejected by the workflow's duplicate-detection event.
    duplicate = dict(tables["purchase_requests"][0])
    duplicate["request_id"] = identifier("PR", n_requests)
    duplicate["status"] = "rejected_duplicate"
    duplicate_line = dict(tables["request_lines"][0])
    duplicate_line["request_line_id"] = f"RL-{n_requests:06d}"
    duplicate_line["request_id"] = duplicate["request_id"]
    tables["purchase_requests"].append(duplicate)
    tables["request_lines"].append(duplicate_line)
    request_ids.append(duplicate["request_id"])
    duplicate_approval = dict(tables["approvals"][0])
    duplicate_approval.update(
        approval_id=f"APR-{n_requests:06d}", request_id=duplicate["request_id"],
        decided_at=add_days(duplicate["request_date"], 2),
        decision="rejected", decision_reason="Rejected after exact duplicate detection",
    )
    tables["approvals"].append(duplicate_approval)
    approval_records_by_request[duplicate["request_id"]] = duplicate_approval
    request_line_by_request[duplicate["request_id"]] = duplicate_line
    add_event("request_events", duplicate["request_id"], "submitted", None, "submitted",
              duplicate["request_date"], "requester", duplicate["requester_id"],
              "Duplicate request submitted")
    add_event("request_events", duplicate["request_id"], "duplicate_rejected",
              "submitted", "rejected_duplicate", duplicate["request_date"],
              "system", None, f"Exact duplicate of {duplicate_source_id}")

    eligible_requests = [r["request_id"] for r in tables["purchase_requests"][:-1]
                         if r["request_id"] != duplicate["request_id"]]
    target_rfq_count = min(n_rfqs, len(eligible_requests))
    if target_rfq_count < 1:
        raise ValueError("scale produces too few requests for an RFQ")
    single_count = max(1, round(target_rfq_count * 0.1))
    competitive_count = target_rfq_count - single_count
    if competitive_count < 1:
        single_count = max(0, target_rfq_count - 1)
        competitive_count = target_rfq_count - single_count
    mandatory_requests = [approval_target_id]
    if single_count and target_request_id != approval_target_id:
        mandatory_requests.append(target_request_id)
    mandatory_requests = [rid for rid in mandatory_requests if rid in eligible_requests]
    if len(mandatory_requests) > target_rfq_count:
        mandatory_requests = mandatory_requests[:target_rfq_count]
    remaining = [rid for rid in eligible_requests if rid not in mandatory_requests]
    selected_requests = [approval_target_id] if approval_target_id in mandatory_requests else []
    selected_requests.extend(rng.sample(remaining, target_rfq_count - len(mandatory_requests)))
    if target_request_id in mandatory_requests:
        selected_requests.append(target_request_id)
    if len(selected_requests) < target_rfq_count:
        available = [rid for rid in remaining if rid not in selected_requests]
        selected_requests.extend(rng.sample(available, target_rfq_count - len(selected_requests)))
    competitive_awards = round(competitive_count * 400 / 450)
    competitive_awards = min(competitive_count, max(1, competitive_awards))
    multi_split_count = round(competitive_awards * 50 / 400) + 2
    four_quote_count = round(competitive_count * 100 / 450)
    single_source_awards = single_count
    quote_seq_by_supplier: Counter[str] = Counter()
    rfq_quote_rows: dict[str, list[dict[str, Any]]] = {}
    quote_by_id: dict[str, dict[str, Any]] = {}
    request_to_rfq: dict[str, str] = {}
    rfq_decision_dates: dict[str, str] = {}
    special_rfq_id: str | None = None
    expired_quote_id: str | None = None
    inactive_award_quote_id: str | None = None
    undocumented_award_id: str | None = None
    currency_mismatch_quote_id: str | None = None
    generated_po_ids: list[str] = []
    award_counter = 0

    for i, request_id in enumerate(selected_requests, 1):
        rfq_id = identifier("RFQ", i)
        request = next(r for r in tables["purchase_requests"] if r["request_id"] == request_id)
        request_line = request_line_by_request[request_id]
        is_special = request_id == target_request_id and target_request_id in selected_requests
        is_single = (i > competitive_count)
        competition_type = "single_source" if is_single else "competitive"
        issue_date = add_days(request["request_date"], 3)
        close_date = add_days(issue_date, 7)
        single_reason = (
            "Documented sole-compatible maintenance part; market search recorded in RFQ event"
            if is_single else None
        )
        rfq = row(
            rfq_id=rfq_id, request_id=request_id, issue_date=issue_date,
            close_date=close_date, required_by=request["required_by"],
            competition_type=competition_type, single_source_reason=single_reason,
            status="closed_no_award",
        )
        tables["rfqs"].append(rfq)
        request_to_rfq[request_id] = rfq_id
        add_event("rfq_events", rfq_id, "issued", None, "open", issue_date,
                  "buyer", "BUYER-001",
                  "RFQ issued to invited suppliers" if not is_single else
                  f"Single-source exception: {single_reason}")

        if is_single:
            quote_count = 1
        elif i <= competitive_count:
            quote_count = 4 if i <= four_quote_count else 3
        else:
            quote_count = 3
        supplier_pool = range(1, n_suppliers + 1) if i == 2 else range(1, n_suppliers)
        supplier_numbers = rng.sample(supplier_pool, min(quote_count, len(supplier_pool)))
        if not is_single and i == 2 and n_suppliers >= 4:
            supplier_numbers[0] = n_suppliers
            supplier_numbers = list(dict.fromkeys(supplier_numbers))
            while len(supplier_numbers) < quote_count:
                candidate = rng.randint(1, n_suppliers - 1)
                if candidate not in supplier_numbers:
                    supplier_numbers.append(candidate)
        qrows: list[dict[str, Any]] = []
        for quote_index, supplier_number in enumerate(supplier_numbers, 1):
            supplier = tables["suppliers"][supplier_number - 1]
            quote_seq_by_supplier[supplier["supplier_code"]] += 1
            quote_id = (
                f"QUO-{supplier['supplier_code']}-2026-"
                f"{quote_seq_by_supplier[supplier['supplier_code']]:06d}"
            )
            total_qty = request_line["quantity"]
            base_unit = request_line["estimated_unit_cost"]
            unit_price = money(base_unit * rng.uniform(0.82, 1.18))
            shipping = money(rng.uniform(0, max(25, total_qty * 3)))
            tax = money(unit_price * total_qty * rng.uniform(0.00, 0.08))
            if i == 2 and supplier_number == n_suppliers and n_suppliers >= 4:
                unit_price, shipping, tax = money(base_unit * 0.5), 0.00, 0.00
            if is_special:
                unit_price, shipping, tax = 1100.00, 100.00, 100.00
            if request_id == approval_target_id and quote_index == 1:
                unit_price = money(max(unit_price, base_unit * 1.01))
            currency = request["currency"]
            exclusion_reason = None
            if i == 4 and quote_index == len(supplier_numbers) and len(supplier_numbers) >= 3:
                currency = "EUR" if request["currency"] != "EUR" else "GBP"
                exclusion_reason = "Currency differs from RFQ currency; excluded from comparison"
                currency_mismatch_quote_id = quote_id
            expired_case = (not is_single and i == max(1, competitive_awards + 1))
            quote_date = add_days(close_date, -3) if expired_case else close_date
            validity_days = 2 if i == max(1, competitive_awards + 1) else rng.randint(14, 45)
            valid_until = close_date if expired_case else add_days(quote_date, validity_days)
            landed = money(unit_price * total_qty + shipping + tax)
            quote = row(
                quote_id=quote_id, rfq_id=rfq_id, supplier_id=supplier["supplier_id"],
                quote_date=quote_date, valid_until=valid_until, currency=currency,
                unit_price=unit_price, shipping_cost=shipping, tax_amount=tax,
                landed_total=landed, lead_time_days=rng.randint(5, 50),
                status="excluded_currency" if exclusion_reason else "submitted",
                exclusion_reason=exclusion_reason,
            )
            qrows.append(quote)
            quote_by_id[quote_id] = quote
            tables["supplier_quotes"].append(quote)
        rfq_quote_rows[rfq_id] = qrows

        is_awarded = (i <= competitive_awards) if not is_single else True
        if is_special:
            special_rfq_id = rfq_id
        decision_date = add_days(close_date, 1)
        rfq_decision_dates[rfq_id] = decision_date
        if not is_awarded:
            rfq["status"] = "expired" if expired_case else "closed_no_award"
            add_event("rfq_events", rfq_id, "closed", "open", rfq["status"],
                      decision_date, "buyer", "BUYER-001",
                      "All quotes expired before award" if expired_case else "No award decision recorded")
            for quote in qrows:
                quote["status"] = "expired" if expired_case else (
                    quote["status"] if quote["status"] == "excluded_currency" else "not_selected"
                )
            if expired_case and qrows:
                expired_quote_id = qrows[0]["quote_id"]
                add_truth(
                    "quote_expired_before_award", "SupplierQuote", expired_quote_id, rfq_id,
                    "medium", "Do not award an expired quote",
                    f"Quote validity ended {qrows[0]['valid_until']} before RFQ decision {decision_date}",
                    "RFQ was closed without award and the expiry was retained in the RFQ event history",
                )
            continue

        eligible_quotes = [
            q for q in qrows
            if q["currency"] == request["currency"] and q["valid_until"] >= decision_date
        ]
        if not eligible_quotes:
            raise RuntimeError(f"No valid quote remains for awarded RFQ {rfq_id}")
        cheapest = min(eligible_quotes, key=lambda q: q["landed_total"])
        if is_single:
            selected = [eligible_quotes[0]]
            rfq["status"] = "single_source_awarded"
            add_truth(
                "single_source_exception", "RFQ", rfq_id, selected[0]["quote_id"],
                "low", "Allow one quote only when a sole-source reason is documented",
                "Single-source award has a documented compatibility reason",
                single_reason or "Sole-source exception recorded",
            )
        else:
            multi_split = 2 <= i <= multi_split_count
            selected_count = 1 if i == 1 else (3 if multi_split else 2)
            available = sorted(eligible_quotes, key=lambda q: (q["landed_total"], q["lead_time_days"]))
            if i == 2 and len(available) >= 3:
                # This known negative case deliberately omits the rationale.
                selected = [available[1], available[0]]
                if multi_split and len(available) >= 3:
                    selected.append(available[2])
                undocumented_award_id = f"AWD-{award_counter + 1:06d}"
            else:
                selected = available[:min(selected_count, len(available))]
            rfq["status"] = "awarded"

        quantities = [request_line["quantity"] // len(selected)] * len(selected)
        for part in range(request_line["quantity"] % len(selected)):
            quantities[part] += 1
        for quote, quantity_awarded in zip(selected, quantities):
            award_counter += 1
            award_id = f"AWD-{award_counter:06d}"
            fraction = quantity_awarded / request_line["quantity"]
            allocated_shipping = money(quote["shipping_cost"] * fraction)
            allocated_tax = money(quote["tax_amount"] * fraction)
            award_total = money(
                quote["unit_price"] * quantity_awarded + allocated_shipping + allocated_tax
            )
            if i == 2 and quote is selected[0] and quote != cheapest:
                rationale = None
                undocumented_award_id = award_id
            elif quote["supplier_id"] == f"SUP-{n_suppliers:04d}":
                rationale = "Known supplier-inactive exception injected for control evaluation"
                inactive_award_quote_id = quote["quote_id"]
            elif quote != cheapest:
                rationale = "Documented delivery-time advantage offsets the landed-cost premium"
            else:
                rationale = "Lowest eligible landed cost; delivery time met the required date"
            tables["rfq_awards"].append(row(
                award_id=award_id, rfq_id=rfq_id, quote_id=quote["quote_id"],
                awarded_at=decision_date, quantity_awarded=quantity_awarded,
                award_total=award_total, rationale=rationale, decision_status="awarded",
            ))
            quote["status"] = "awarded"
            if quote["supplier_id"] == f"SUP-{n_suppliers:04d}":
                inactive_award_quote_id = quote["quote_id"]
            add_event("rfq_events", rfq_id, "award_recorded", "open", "awarded",
                      decision_date, "buyer", "BUYER-001",
                      rationale or "Noncompliant selection: rationale missing; review required")

            po_seq = len(generated_po_ids) + 1
            po_id = identifier("PO", po_seq)
            generated_po_ids.append(po_id)
            approval = approval_records_by_request[request_id]
            approval_id = approval["approval_id"]
            if request_id == approval_target_id:
                approval_id = None
            po_date = add_days(decision_date, 1)
            po_status = "issued"
            tables["purchase_orders"].append(row(
                po_id=po_id, request_id=request_id, rfq_id=rfq_id,
                award_id=award_id, supplier_id=quote["supplier_id"],
                approval_id=approval_id, issued_date=po_date, currency=quote["currency"],
                total_amount=award_total, status=po_status,
            ))
            po_line_id = f"POL-{po_seq:06d}"
            line_total = money(quote["unit_price"] * quantity_awarded
                               + allocated_shipping + allocated_tax)
            tables["purchase_order_lines"].append(row(
                po_line_id=po_line_id, po_id=po_id,
                request_line_id=request_line["request_line_id"], quote_id=quote["quote_id"],
                quantity=quantity_awarded, unit_price=quote["unit_price"],
                shipping_allocated=allocated_shipping, tax_allocated=allocated_tax,
                line_total=line_total,
            ))
            add_event("purchase_order_events", po_id, "issued", None, "issued",
                      po_date, "buyer", "BUYER-001",
                      "PO generated from approved quote" if approval_id else
                      "PO issued while required approval remained pending; control exception")

            budget = next(b for b in tables["budgets"] if b["budget_id"] == request["budget_id"])
            counters["ledger"] += 1
            prior_reserved = sum(
                l["amount"] for l in tables["budget_ledger"] if l["budget_id"] == budget["budget_id"]
            )
            balance_after = money(budget["allocation_amount"] - prior_reserved - award_total)
            tables["budget_ledger"].append(row(
                ledger_id=f"LED-{counters['ledger']:06d}", budget_id=budget["budget_id"],
                po_id=po_id, event_type="reserve", event_date=po_date,
                amount=award_total, balance_after=balance_after,
                reason="Purchase order commitment reserved against department budget",
            ))

            quantity_shipped = quantity_awarded
            expected_date = add_days(po_date, quote["lead_time_days"])
            delivery_base = f"DEL-{po_id}-"
            partial = (po_seq % 4 == 0 and quantity_awarded >= 2)
            shipment_specs = (
                [(quantity_awarded // 2, quantity_awarded // 2, "partially_received"),
                 (quantity_awarded - quantity_awarded // 2, 0, "in_transit")]
                if partial else [(quantity_awarded, quantity_awarded, "delivered")]
            )
            for shipment_no, (shipped, received, delivery_status) in enumerate(shipment_specs, 1):
                delivery_id = f"{delivery_base}{shipment_no:02d}"
                shipped_date = add_days(expected_date, rng.randint(-2, 4))
                received_date = add_days(shipped_date, 2) if received else None
                delivery = row(
                    delivery_id=delivery_id, po_id=po_id, shipment_number=shipment_no,
                    expected_date=expected_date, shipped_date=shipped_date,
                    received_date=received_date, quantity_shipped=shipped,
                    quantity_received=received, status=delivery_status,
                    exception_reason="Second shipment pending receipt" if not received else None,
                )
                tables["deliveries"].append(delivery)
                add_event("delivery_events", delivery_id, "shipment_created", None,
                          "in_transit", shipped_date, "supplier", quote["supplier_id"],
                          "Supplier shipment notice recorded")
                if received:
                    add_event("delivery_events", delivery_id, "receipt_recorded",
                              "in_transit", delivery_status, received_date or shipped_date,
                              "operations", "OPS-001", "Quantity received and recorded")
                if delivery_status == "in_transit":
                    add_event("delivery_events", delivery_id, "receipt_pending",
                              "in_transit", "in_transit", shipped_date, "system", None,
                              "Shipment is in transit; receipt is pending")
            final_status = (
                "partially_received" if partial else "delivered"
            )
            tables["purchase_orders"][-1]["status"] = final_status
            add_event("purchase_order_events", po_id, "delivery_status_updated",
                      "issued", final_status, add_days(expected_date, 4),
                      "system", None, "PO status derived from delivery receipt events")
            if balance_after < 0:
                add_truth(
                    "budget_overrun", "PurchaseOrder", po_id, budget["budget_id"],
                    "high", "Do not reserve a PO that exceeds its available budget",
                    f"Reservation of {award_total:.2f} leaves budget balance {balance_after:.2f}",
                    "Budget ledger records the negative balance and linked PO for remediation",
                )
            if request_id == approval_target_id:
                add_truth(
                    "po_issued_without_approval", "PurchaseOrder", po_id, request_id,
                    "critical", "Do not issue a PO until required approval is approved",
                    "PO was issued with no approval_id while the linked approval remained pending",
                    "PO issuance event explicitly records the pending-approval control exception",
                )

        if is_special:
            special_rfq_id = rfq_id
        if rfq["status"] in ("awarded", "single_source_awarded"):
            add_event("rfq_events", rfq_id, "closed", "awarded", rfq["status"],
                      decision_date, "system", None, "Award decision persisted")

    # Set current request states from the workflow records, not random labels.
    awarded_requests = {r["request_id"] for r in tables["rfqs"]
                        if r["status"] in ("awarded", "single_source_awarded")}
    expired_requests = {r["request_id"] for r in tables["rfqs"] if r["status"] == "expired"}
    rfq_closed_requests = {r["request_id"] for r in tables["rfqs"]
                           if r["status"] == "closed_no_award"}
    for request in tables["purchase_requests"]:
        rid = request["request_id"]
        if rid == duplicate["request_id"]:
            continue
        request["status"] = (
            "ordered" if rid in awarded_requests else
            "rfq_expired" if rid in expired_requests else
            "rfq_closed" if rid in rfq_closed_requests else "approved"
        )
        if rid in awarded_requests:
            add_event("request_events", rid, "order_created",
                      "pending_approval" if rid == approval_target_id else "approved",
                      "ordered",
                      request_to_rfq[rid] and rfq_decision_dates[request_to_rfq[rid]],
                      "system", None, "Awarded request has purchase order records")
        elif rid in expired_requests:
            add_event("request_events", rid, "rfq_expired", "approved", "rfq_expired",
                      rfq_decision_dates[request_to_rfq[rid]], "system", None,
                      "Request RFQ expired before an award")
        elif rid in rfq_closed_requests:
            add_event("request_events", rid, "rfq_closed", "approved", "rfq_closed",
                      rfq_decision_dates[request_to_rfq[rid]], "system", None,
                      "RFQ closed without an award")

    if undocumented_award_id:
        award = next(a for a in tables["rfq_awards"] if a["award_id"] == undocumented_award_id)
        add_truth(
            "selected_quote_not_cheapest_without_rationale", "RFQAward",
            award["award_id"], award["quote_id"], "high",
            "Document a defensible award rationale for any non-lowest selection",
            "Non-lowest quote selected with a blank rationale field",
            "Award decision event records the missing-rationale exception",
        )
    if inactive_award_quote_id:
        award = next(a for a in tables["rfq_awards"] if a["quote_id"] == inactive_award_quote_id)
        add_truth(
            "inactive_supplier_awarded", "SupplierQuote", inactive_award_quote_id,
            award["award_id"], "high",
            "Block awards to inactive suppliers or record an approved exception",
            "Supplier status is inactive although its quote was awarded",
            "Linked award and PO preserve the supplier-status exception trail",
        )
    if currency_mismatch_quote_id:
        quote = quote_by_id[currency_mismatch_quote_id]
        add_truth(
            "quote_currency_mismatch", "SupplierQuote", currency_mismatch_quote_id,
            quote["rfq_id"], "medium",
            "Exclude quotes whose currency differs from the RFQ currency",
            f"Quote currency {quote['currency']} differs from its RFQ/request currency USD",
            quote["exclusion_reason"] or "Currency mismatch exclusion recorded",
        )
    add_truth(
        "duplicate_request", "PurchaseRequest", duplicate["request_id"],
        duplicate_source_id, "medium",
        "Reject an exact duplicate business request while preserving its immutable identifier",
        "Request duplicates an earlier request on requester, date, line, quantity and cost",
        "Duplicate-detection request event records the source request and rejection",
    )
    if len(tables["purchase_orders"]) != n_pos and scale == 1.0:
        raise RuntimeError(f"Expected {n_pos} POs, generated {len(tables['purchase_orders'])}")

    # Rebuild ledger running balances in event date / PO order for stable auditability.
    budgets_by_id = {b["budget_id"]: b for b in tables["budgets"]}
    running: Counter[str] = Counter()
    ledger_by_po = {entry["po_id"]: entry for entry in tables["budget_ledger"]}
    for entry in sorted(tables["budget_ledger"], key=lambda x: (x["event_date"], x["po_id"])):
        running[entry["budget_id"]] += entry["amount"]
        entry["balance_after"] = money(
            budgets_by_id[entry["budget_id"]]["allocation_amount"] - running[entry["budget_id"]]
        )

    return {
        "tables": tables,
        "ground_truth": truth,
        "data_dictionary": build_data_dictionary(),
        "seed": seed,
        "scale": scale,
        "counts": FULL_COUNTS,
    }


def build_data_dictionary() -> list[dict[str, str]]:
    units = {
        "allocation_amount": "currency units", "estimated_total": "currency units",
        "estimated_unit_cost": "currency units per unit", "threshold_amount": "currency units",
        "unit_price": "currency units per unit", "shipping_cost": "currency units",
        "tax_amount": "currency units", "landed_total": "currency units",
        "award_total": "currency units", "total_amount": "currency units",
        "shipping_allocated": "currency units", "tax_allocated": "currency units",
        "line_total": "currency units", "amount": "currency units",
        "balance_after": "currency units", "quantity": "units",
        "quantity_awarded": "units", "quantity_shipped": "units",
        "quantity_received": "units", "lead_time_days": "days",
    }
    date_fields = {
        "effective_from", "effective_to", "request_date", "required_by",
        "requested_at", "decided_at", "issue_date", "close_date", "quote_date",
        "valid_until", "awarded_at", "issued_date", "expected_date", "shipped_date",
        "received_date", "event_at", "event_date",
    }
    unique_fields: dict[tuple[str, str], str] = {}
    for table, groups in UNIQUE_GROUPS.items():
        for group in groups:
            for field in group:
                unique_fields[(table, field)] = (
                    "Yes" if len(group) == 1 else f"Yes (composite: {', '.join(group)})"
                )
    dictionary = []
    for table, columns in TABLE_COLUMNS.items():
        for field, sql_type in columns:
            if field == PRIMARY_KEYS[table]:
                unique, validation = "Yes (primary key)", "Required; immutable and unique"
            elif field in FOREIGN_KEYS.get(table, {}):
                parent, parent_key = FOREIGN_KEYS[table][field]
                unique = unique_fields.get((table, field), "No")
                validation = f"Must reference {parent}.{parent_key}"
            elif (table, field) in NULLABLE:
                unique = unique_fields.get((table, field), "No")
                validation = "Optional; when supplied, must pass the field domain rule"
            else:
                unique = unique_fields.get((table, field), "No")
                validation = "Required; values are validated against the field domain"
            if (table, field) in ENUMS:
                allowed = "|".join(str(v) for v in ENUMS[(table, field)])
                validation = f"Must be one of: {allowed}"
            elif sql_type == "REAL":
                if field == "balance_after":
                    allowed = "Signed currency decimal"
                    validation = "Must reconcile to allocation less cumulative reservations; negative only for a labeled overrun"
                else:
                    allowed = "Non-negative; finite decimal"
                    validation = "Must be finite and non-negative; derived totals must reconcile"
            elif sql_type == "INTEGER" and field == "quantity_received":
                allowed = "Non-negative integer"
                validation = "Must be a whole number between zero and quantity shipped"
            elif sql_type == "INTEGER" and field in {
                "quantity", "quantity_awarded", "quantity_shipped",
                "lead_time_days", "line_number", "shipment_number",
            }:
                allowed = "Positive integer"
                validation = "Must be a positive whole number"
            elif sql_type == "INTEGER" and field == "fiscal_year":
                allowed = "Four-digit year"
                validation = "Must match the budget effective dates"
            elif field in date_fields:
                allowed = "ISO 8601 date"
                validation = "Must be a valid ISO date; related lifecycle dates are ordered"
            else:
                allowed = "Non-empty text" if sql_type == "TEXT" else "See field validation"
            dictionary.append(row(
                table=table, field=field, type=sql_type,
                required="No" if (table, field) in NULLABLE else "Yes",
                allowed_values=allowed, units=units.get(field, "not applicable"),
                uniqueness=unique, validation=validation,
            ))
    for field in (
        "issue_type", "entity_type", "entity_id", "related_entity_id", "severity",
        "expected_outcome", "observed_outcome", "audit_reason",
    ):
        dictionary.append(row(
            table="ground_truth", field=field, type="TEXT", required="Yes",
            allowed_values="Documented label/value", units="not applicable",
            uniqueness="No",
            validation="Required; evaluation-only label kept separate from operational inputs",
        ))
    return dictionary


def validate_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    tables = dataset["tables"]
    errors: list[str] = []
    checks = Counter()
    indexes: dict[str, set[Any]] = {}
    for table, records in tables.items():
        columns = {name for name, _ in TABLE_COLUMNS[table]}
        for index, record in enumerate(records):
            if set(record) != columns:
                errors.append(f"{table}[{index}] has unexpected or missing columns")
        pk = PRIMARY_KEYS[table]
        values = [record[pk] for record in records]
        if len(values) != len(set(values)):
            errors.append(f"{table}.{pk} contains duplicate keys")
        checks["primary_key_tables"] += 1
        indexes[table] = set(values)
        for unique_group in UNIQUE_GROUPS.get(table, []):
            keys = [tuple(record[field] for field in unique_group) for record in records]
            if len(keys) != len(set(keys)):
                errors.append(f"{table} violates unique constraint {unique_group}")
            checks["unique_constraints"] += 1
        for record in records:
            for field, value in record.items():
                if (table, field) not in NULLABLE and (value is None or value == ""):
                    errors.append(f"{table}.{field} is required")
            for field, (parent, parent_key) in FOREIGN_KEYS.get(table, {}).items():
                value = record[field]
                if value is None and (table, field) in NULLABLE:
                    continue
                if value not in indexes[parent]:
                    errors.append(f"{table}.{field} references missing {parent} key {value}")
                    break
                checks["foreign_key_references"] += 1
            for field, allowed in ENUMS.items():
                if field[0] == table and record[field[1]] not in allowed:
                    errors.append(f"{table}.{field[1]} has invalid value {record[field[1]]}")
            for field, value in record.items():
                if isinstance(value, (float, int)) and not isinstance(value, bool):
                    if not math.isfinite(value):
                        errors.append(f"{table}.{field} is not finite")
                    if field.endswith(("amount", "cost", "price", "total", "balance_after")) and value < 0 and table != "budget_ledger":
                        errors.append(f"{table}.{field} is negative")

    by_id = {table: {r[PRIMARY_KEYS[table]]: r for r in records}
             for table, records in tables.items()}
    lines_by_request: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in tables["request_lines"]:
        lines_by_request[line["request_id"]].append(line)
    for department in tables["departments"]:
        if department["effective_to"] and department["effective_from"] > department["effective_to"]:
            errors.append(f"{department['department_id']} has reversed effective dates")
    for requester in tables["requesters"]:
        if requester["effective_to"] and requester["effective_from"] > requester["effective_to"]:
            errors.append(f"{requester['requester_id']} has reversed effective dates")
    for budget in tables["budgets"]:
        if budget["effective_from"] > budget["effective_to"]:
            errors.append(f"{budget['budget_id']} has reversed effective dates")
    for supplier in tables["suppliers"]:
        if supplier["effective_to"] and supplier["effective_from"] > supplier["effective_to"]:
            errors.append(f"{supplier['supplier_id']} has reversed effective dates")

    approval_by_request = {a["request_id"]: a for a in tables["approvals"]}
    truth_keys = {
        (item["issue_type"], item["entity_id"]) for item in dataset["ground_truth"]
    }
    for request in tables["purchase_requests"]:
        if request["request_date"] > request["required_by"]:
            errors.append(f"{request['request_id']} is required before it was requested")
        approval = approval_by_request[request["request_id"]]
        total = request["estimated_total"]
        expected_level = "automatic" if total < 5000 else ("manager" if total <= 25000 else "director")
        if approval["approval_level"] != expected_level:
            errors.append(f"{request['request_id']} has the wrong approval threshold")
        if approval["requested_at"] < request["request_date"]:
            errors.append(f"{approval['approval_id']} was requested before the purchase request")
        if approval["decided_at"] and approval["decided_at"] < approval["requested_at"]:
            errors.append(f"{approval['approval_id']} was decided before it was requested")
        if approval["decision"] == "pending" and approval["decided_at"] is not None:
            errors.append(f"{approval['approval_id']} is pending but has a decision date")
        if approval["decision"] == "not_required" and expected_level != "automatic":
            errors.append(f"{approval['approval_id']} bypasses a required approval threshold")
    request_signatures: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    for request in tables["purchase_requests"]:
        request_lines = lines_by_request[request["request_id"]]
        if not request_lines:
            continue
        line = request_lines[0]
        signature = (
            request["requester_id"], request["department_id"], request["request_date"],
            line["description"], line["category"], line["quantity"],
            line["unit_of_measure"], line["estimated_unit_cost"], line["currency"],
        )
        request_signatures[signature].append(request["request_id"])
    duplicate_request_ids = {
        request_id
        for ids in request_signatures.values() if len(ids) > 1
        for request_id in sorted(ids)[1:]
    }
    labeled_duplicate_ids = {
        item["entity_id"] for item in dataset["ground_truth"]
        if item["issue_type"] == "duplicate_request"
    }
    if duplicate_request_ids != labeled_duplicate_ids:
        errors.append("Duplicate requests and duplicate ground-truth labels do not reconcile")
    checks["duplicate_request_checks"] += len(request_signatures)

    for rfq in tables["rfqs"]:
        request = by_id["purchase_requests"][rfq["request_id"]]
        if rfq["issue_date"] < request["request_date"] or rfq["close_date"] < rfq["issue_date"]:
            errors.append(f"{rfq['rfq_id']} has invalid issue/close ordering")
        if rfq["required_by"] < rfq["close_date"]:
            errors.append(f"{rfq['rfq_id']} closes after its required-by date")
        awards = [a for a in tables["rfq_awards"] if a["rfq_id"] == rfq["rfq_id"]]
        if rfq["status"] in ("awarded", "single_source_awarded") and not awards:
            errors.append(f"{rfq['rfq_id']} is marked awarded without an award")
        if rfq["status"] in ("closed_no_award", "expired") and awards:
            errors.append(f"{rfq['rfq_id']} has awards despite a no-award lifecycle")
        quote_count = sum(q["rfq_id"] == rfq["rfq_id"] for q in tables["supplier_quotes"])
        if rfq["competition_type"] == "single_source":
            if quote_count != 1 or not rfq["single_source_reason"]:
                errors.append(f"{rfq['rfq_id']} lacks a justified single-source quote")
        elif quote_count < 3:
            errors.append(f"{rfq['rfq_id']} has fewer than three competitive quotes")

    for line in tables["request_lines"]:
        if line["quantity"] <= 0:
            errors.append(f"{line['request_line_id']} quantity must be positive")
        if money(line["quantity"] * line["estimated_unit_cost"]) != line["estimated_total"]:
            errors.append(f"{line['request_line_id']} estimate does not reconcile")
        checks["request_line_arithmetic"] += 1
    for request in tables["purchase_requests"]:
        total = money(sum(line["estimated_total"] for line in lines_by_request[request["request_id"]]))
        if total != request["estimated_total"]:
            errors.append(f"{request['request_id']} total differs from its lines")
        if not lines_by_request[request["request_id"]]:
            errors.append(f"{request['request_id']} has no request line")
    for quote in tables["supplier_quotes"]:
        rfq = by_id["rfqs"][quote["rfq_id"]]
        request_id = rfq["request_id"]
        if quote["quote_date"] < rfq["issue_date"] or quote["valid_until"] < quote["quote_date"]:
            errors.append(f"{quote['quote_id']} has invalid quote-date ordering")
        qty = lines_by_request[request_id][0]["quantity"]
        expected = money(quote["unit_price"] * qty + quote["shipping_cost"] + quote["tax_amount"])
        if quote["landed_total"] != expected:
            errors.append(f"{quote['quote_id']} landed total does not reconcile")
        if quote["valid_until"] < quote["quote_date"]:
            errors.append(f"{quote['quote_id']} expires before quote date")
        checks["quote_arithmetic"] += 1

    po_lines_by_po: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in tables["purchase_order_lines"]:
        po_lines_by_po[line["po_id"]].append(line)
        po = by_id["purchase_orders"][line["po_id"]]
        award = by_id["rfq_awards"][po["award_id"]]
        request_line = by_id["request_lines"][line["request_line_id"]]
        if line["quote_id"] != award["quote_id"]:
            errors.append(f"{line['po_line_id']} does not use its awarded quote")
        if request_line["request_id"] != po["request_id"]:
            errors.append(f"{line['po_line_id']} request line belongs to another request")
        expected = money(line["quantity"] * line["unit_price"]
                         + line["shipping_allocated"] + line["tax_allocated"])
        if expected != line["line_total"]:
            errors.append(f"{line['po_line_id']} line total does not reconcile")
        checks["po_line_arithmetic"] += 1
    award_po_total: Counter[str] = Counter()
    for po in tables["purchase_orders"]:
        calculated = money(sum(line["line_total"] for line in po_lines_by_po[po["po_id"]]))
        if calculated != po["total_amount"]:
            errors.append(f"{po['po_id']} does not reconcile to PO lines")
        if not po_lines_by_po[po["po_id"]]:
            errors.append(f"{po['po_id']} has no PO line")
        award = by_id["rfq_awards"][po["award_id"]]
        award_po_total[award["award_id"]] += po["total_amount"]
        if po["request_id"] != by_id["rfqs"][po["rfq_id"]]["request_id"]:
            errors.append(f"{po['po_id']} request differs from its RFQ request")
        if po["supplier_id"] != by_id["supplier_quotes"][
            next(line["quote_id"] for line in po_lines_by_po[po["po_id"]])
        ]["supplier_id"]:
            errors.append(f"{po['po_id']} supplier differs from the awarded quote")
        approval = by_id["approvals"].get(po["approval_id"]) if po["approval_id"] else None
        if approval and approval["request_id"] != po["request_id"]:
            errors.append(f"{po['po_id']} approval references a different request")
        if po["issued_date"] < award["awarded_at"]:
            errors.append(f"{po['po_id']} was issued before its award")
        if approval and approval["decision"] not in ("approved", "not_required"):
            errors.append(f"{po['po_id']} is linked to a non-approved decision")
        if not approval:
            request_approval = approval_by_request[po["request_id"]]
            if (
                request_approval["decision"] != "pending"
                or ("po_issued_without_approval", po["po_id"]) not in truth_keys
            ):
                errors.append(f"{po['po_id']} has no approved approval record")
        checks["po_reconciliations"] += 1
    for award in tables["rfq_awards"]:
        if money(award_po_total[award["award_id"]]) != award["award_total"]:
            errors.append(f"{award['award_id']} award total differs from its PO(s)")
        award_quote = by_id["supplier_quotes"][award["quote_id"]]
        if award_quote["rfq_id"] != award["rfq_id"]:
            errors.append(f"{award['award_id']} quote belongs to a different RFQ")
        if award["awarded_at"] > award_quote["valid_until"]:
            errors.append(f"{award['award_id']} was made after its quote expired")
        if (
            by_id["suppliers"][award_quote["supplier_id"]]["status"] == "inactive"
            and ("inactive_supplier_awarded", award_quote["quote_id"]) not in truth_keys
        ):
            errors.append(f"{award['award_id']} has an unlabeled inactive-supplier award")
        checks["award_reconciliations"] += 1

    delivery_qty: Counter[str] = Counter()
    for delivery in tables["deliveries"]:
        if delivery["quantity_received"] > delivery["quantity_shipped"]:
            errors.append(f"{delivery['delivery_id']} received more than was shipped")
        po = by_id["purchase_orders"][delivery["po_id"]]
        if delivery["expected_date"] < po["issued_date"]:
            errors.append(f"{delivery['delivery_id']} is expected before its PO was issued")
        if delivery["shipped_date"] and delivery["shipped_date"] < delivery["expected_date"]:
            # Early supplier shipments are permitted, so only verify valid date encodings.
            date.fromisoformat(delivery["shipped_date"])
        if delivery["received_date"] and delivery["received_date"] < delivery["shipped_date"]:
            errors.append(f"{delivery['delivery_id']} received before shipment")
        delivery_qty[delivery["po_id"]] += delivery["quantity_shipped"]
        checks["delivery_date_and_quantity"] += 1
    for po_id, lines in po_lines_by_po.items():
        if delivery_qty[po_id] > sum(line["quantity"] for line in lines):
            errors.append(f"{po_id} shipped quantity exceeds ordered quantity")

    running = Counter()
    budget_by_id = by_id["budgets"]
    for event in sorted(tables["budget_ledger"], key=lambda x: (x["event_date"], x["po_id"])):
        running[event["budget_id"]] += event["amount"]
        expected_balance = money(budget_by_id[event["budget_id"]]["allocation_amount"]
                                 - running[event["budget_id"]])
        if event["balance_after"] != expected_balance:
            errors.append(f"{event['ledger_id']} running balance does not reconcile")
        if event["amount"] != by_id["purchase_orders"][event["po_id"]]["total_amount"]:
            errors.append(f"{event['ledger_id']} differs from its PO commitment")
        if event["balance_after"] < 0 and ("budget_overrun", event["po_id"]) not in truth_keys:
            errors.append(f"{event['ledger_id']} has an unlabeled negative budget balance")
        checks["budget_ledger_reconciliations"] += 1

    for event_table, parent_field in (
        ("request_events", "request_id"), ("rfq_events", "rfq_id"),
        ("purchase_order_events", "po_id"), ("delivery_events", "delivery_id"),
    ):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in tables[event_table]:
            grouped[event[parent_field]].append(event)
        for parent_id, parent_events in grouped.items():
            dates = [event["event_at"] for event in parent_events]
            if dates != sorted(dates):
                errors.append(f"{event_table} for {parent_id} is not chronologically ordered")
            for event in parent_events:
                valid_transitions = {
                    "request_events": {
                        "submitted": {(None, "submitted")},
                        "approval_requested": {("submitted", "pending_approval")},
                        "approval_recorded": {("submitted", "approved")},
                        "approval_decided": {
                            ("pending_approval", "approved"),
                            ("pending_approval", "rejected"),
                        },
                        "duplicate_rejected": {("submitted", "rejected_duplicate")},
                        "order_created": {("approved", "ordered"), ("pending_approval", "ordered")},
                        "rfq_expired": {("approved", "rfq_expired")},
                        "rfq_closed": {("approved", "rfq_closed")},
                    },
                    "rfq_events": {
                        "issued": {(None, "open")},
                        "quote_received": {("open", "open")},
                        "quote_awarded": {("open", "open")},
                        "award_recorded": {("open", "awarded")},
                        "closed": {
                            ("open", "closed_no_award"), ("open", "expired"),
                            ("awarded", "awarded"), ("awarded", "single_source_awarded"),
                        },
                    },
                    "purchase_order_events": {
                        "issued": {(None, "issued")},
                        "delivery_status_updated": {
                            ("issued", "delivered"), ("issued", "partially_received"),
                            ("issued", "in_transit"), ("in_transit", "delivered"),
                            ("in_transit", "partially_received"),
                            ("partially_received", "delivered"),
                            ("partially_received", "partially_received"),
                        },
                    },
                    "delivery_events": {
                        "shipment_created": {(None, "in_transit")},
                        "receipt_recorded": {
                            ("in_transit", "delivered"), ("in_transit", "partially_received"),
                            ("partially_received", "delivered"),
                            ("partially_received", "partially_received"),
                        },
                        "receipt_pending": {("in_transit", "in_transit")},
                    },
                }[event_table]
                if (event["from_state"], event["to_state"]) not in valid_transitions.get(
                    event["event_type"], set()
                ):
                    errors.append(
                        f"{event_table} for {parent_id} has invalid transition "
                        f"{event['from_state']} -> {event['to_state']}"
                    )
            latest_state = parent_events[-1]["to_state"]
            state_table, state_key = {
                "request_events": ("purchase_requests", "status"),
                "rfq_events": ("rfqs", "status"),
                "purchase_order_events": ("purchase_orders", "status"),
                "delivery_events": ("deliveries", "status"),
            }[event_table]
            entity = by_id[state_table].get(parent_id)
            if entity and latest_state != entity[state_key]:
                errors.append(f"{event_table} for {parent_id} does not match the current lifecycle state")
        checks["event_history_tables"] += 1

    truth_entity_tables = {
        "PurchaseRequest": "purchase_requests", "SupplierQuote": "supplier_quotes",
        "RFQAward": "rfq_awards", "PurchaseOrder": "purchase_orders", "RFQ": "rfqs",
    }
    for label in dataset["ground_truth"]:
        entity_table = truth_entity_tables.get(label["entity_type"])
        if not entity_table or label["entity_id"] not in by_id[entity_table]:
            errors.append(f"Ground-truth entity does not exist: {label['entity_type']} {label['entity_id']}")
        if label["severity"] not in ("low", "medium", "high", "critical"):
            errors.append(f"Ground-truth severity is invalid: {label['severity']}")
        if not all(label[field] for field in (
            "issue_type", "entity_type", "entity_id", "related_entity_id",
            "expected_outcome", "observed_outcome", "audit_reason",
        )):
            errors.append(f"Ground-truth record for {label['entity_id']} is incomplete")

        issue_type = label["issue_type"]
        if issue_type == "inactive_supplier_awarded":
            quote = by_id["supplier_quotes"].get(label["entity_id"])
            if quote and by_id["suppliers"][quote["supplier_id"]]["status"] != "inactive":
                errors.append(f"{quote['quote_id']} is not from an inactive supplier")
        elif issue_type == "quote_currency_mismatch":
            quote = by_id["supplier_quotes"].get(label["entity_id"])
            if quote:
                rfq_request = by_id["purchase_requests"][
                    by_id["rfqs"][quote["rfq_id"]]["request_id"]
                ]
                if quote["currency"] == rfq_request["currency"] or quote["status"] != "excluded_currency":
                    errors.append(f"{quote['quote_id']} is not a correctly excluded currency mismatch")
        elif issue_type == "selected_quote_not_cheapest_without_rationale":
            award = by_id["rfq_awards"].get(label["entity_id"])
            if award:
                quote = by_id["supplier_quotes"][award["quote_id"]]
                eligible = [
                    q for q in tables["supplier_quotes"]
                    if q["rfq_id"] == award["rfq_id"]
                    and q["currency"] == quote["currency"]
                    and q["valid_until"] >= award["awarded_at"]
                ]
                if award["rationale"] or quote["landed_total"] <= min(
                    q["landed_total"] for q in eligible
                ):
                    errors.append(f"{award['award_id']} is not a missing-rationale non-lowest award")

    truth_types = {item["issue_type"] for item in dataset["ground_truth"]}
    required_labels = {
        "po_issued_without_approval", "quote_expired_before_award",
        "selected_quote_not_cheapest_without_rationale", "inactive_supplier_awarded",
        "budget_overrun", "duplicate_request", "quote_currency_mismatch",
    }
    missing = required_labels - truth_types
    if dataset["scale"] == 1 and missing:
        errors.append(f"Required ground-truth labels missing: {sorted(missing)}")
    checks["ground_truth_label_types"] = len(truth_types)
    if errors:
        raise ValueError("Validation failed:\n- " + "\n- ".join(errors[:50]))

    budget_reserved = Counter()
    for entry in tables["budget_ledger"]:
        budget_reserved[entry["budget_id"]] += entry["amount"]
    return {
        "validation_status": "passed",
        "checks": dict(checks),
        "row_counts": {name: len(records) for name, records in tables.items()},
        "ground_truth_count": len(dataset["ground_truth"]),
        "ground_truth_labels": dict(Counter(item["issue_type"] for item in dataset["ground_truth"])),
        "metrics": {
            "requests_with_rfqs": len(tables["rfqs"]),
            "competitive_rfqs": sum(r["competition_type"] == "competitive" for r in tables["rfqs"]),
            "single_source_rfqs": sum(r["competition_type"] == "single_source" for r in tables["rfqs"]),
            "awarded_rfqs": sum(r["status"] in ("awarded", "single_source_awarded") for r in tables["rfqs"]),
            "split_awards": sum(
                1 for rfq in tables["rfqs"]
                if sum(a["rfq_id"] == rfq["rfq_id"] for a in tables["rfq_awards"]) > 1
            ),
            "partial_or_in_transit_deliveries": sum(
                d["status"] in ("partially_received", "in_transit") for d in tables["deliveries"]
            ),
            "budget_reserved_by_budget": {
                budget["budget_id"]: money(budget_reserved[budget["budget_id"]])
                for budget in tables["budgets"]
            },
            "approval_thresholds_usd": {
                "automatic_below": 5000, "manager_up_to_inclusive": 25000,
                "director_above": 25000,
            },
        },
    }


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def schema_sql() -> str:
    statements = ["PRAGMA foreign_keys = ON;", ""]
    for table, columns in TABLE_COLUMNS.items():
        definitions = []
        for field, sql_type in columns:
            definition = f"  {field} {sql_type}"
            if (table, field) not in NULLABLE:
                definition += " NOT NULL"
            if PRIMARY_KEYS[table] == field:
                definition += " PRIMARY KEY"
            if (table, field) in ENUMS:
                allowed = ", ".join(sql_literal(v) for v in ENUMS[(table, field)])
                definition += f" CHECK ({field} IN ({allowed}))"
            elif sql_type == "REAL" and field != "balance_after":
                definition += f" CHECK ({field} >= 0)"
            elif field == "quantity_received":
                definition += f" CHECK ({field} >= 0)"
            elif field in {
                "quantity", "quantity_awarded", "quantity_shipped",
                "lead_time_days", "line_number", "shipment_number",
            }:
                definition += f" CHECK ({field} > 0)"
            definitions.append(definition)
        for field, (parent, parent_field) in FOREIGN_KEYS.get(table, {}).items():
            definitions.append(f"  FOREIGN KEY ({field}) REFERENCES {parent}({parent_field})")
        for group in UNIQUE_GROUPS.get(table, []):
            definitions.append(f"  UNIQUE ({', '.join(group)})")
        statements.append(f"CREATE TABLE IF NOT EXISTS {table} (\n" + ",\n".join(definitions) + "\n);")
    return "\n\n".join(statements) + "\n"


def seed_sql(tables: dict[str, list[dict[str, Any]]]) -> str:
    statements = ["PRAGMA foreign_keys = ON;", ""]
    for table, records in tables.items():
        if not records:
            continue
        columns = [name for name, _ in TABLE_COLUMNS[table]]
        for record in records:
            values = ", ".join(sql_literal(record[column]) for column in columns)
            statements.append(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values});"
            )
    return "\n".join(statements) + "\n"


def ground_truth_sql(records: list[dict[str, Any]]) -> str:
    columns = [
        "issue_type", "entity_type", "entity_id", "related_entity_id", "severity",
        "expected_outcome", "observed_outcome", "audit_reason",
    ]
    statements = [
        "CREATE TABLE IF NOT EXISTS ground_truth (",
        "  issue_type TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,",
        "  related_entity_id TEXT NOT NULL, severity TEXT NOT NULL,",
        "  expected_outcome TEXT NOT NULL, observed_outcome TEXT NOT NULL,",
        "  audit_reason TEXT NOT NULL,",
        "  PRIMARY KEY (issue_type, entity_type, entity_id, related_entity_id)",
        ");",
    ]
    for record in records:
        values = ", ".join(sql_literal(record[column]) for column in columns)
        statements.append(
            f"INSERT INTO ground_truth ({', '.join(columns)}) VALUES ({values});"
        )
    return "\n".join(statements) + "\n"


def stable_json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=False, ensure_ascii=True) + "\n").encode("utf-8")


def csv_bytes(records: list[dict[str, Any]], columns: list[str]) -> bytes:
    import io
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return buffer.getvalue().encode("utf-8")


def render_files(dataset: dict[str, Any]) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    tables = dataset["tables"]
    for table, records in tables.items():
        files[f"csv/{table}.csv"] = csv_bytes(records, [f for f, _ in TABLE_COLUMNS[table]])
    files["csv/ground_truth.csv"] = csv_bytes(
        dataset["ground_truth"],
        ["issue_type", "entity_type", "entity_id", "related_entity_id", "severity",
         "expected_outcome", "observed_outcome", "audit_reason"],
    )
    files["csv/data_dictionary.csv"] = csv_bytes(
        dataset["data_dictionary"],
        ["table", "field", "type", "required", "allowed_values", "units", "uniqueness", "validation"],
    )
    files["json/dataset.json"] = stable_json(tables)
    files["json/ground_truth.json"] = stable_json(dataset["ground_truth"])
    files["json/data_dictionary.json"] = stable_json(dataset["data_dictionary"])
    files["sql/schema.sql"] = schema_sql().encode("utf-8")
    files["sql/seed.sql"] = seed_sql(tables).encode("utf-8")
    files["sql/ground_truth.sql"] = ground_truth_sql(dataset["ground_truth"]).encode("utf-8")
    summary = validate_dataset(dataset)
    summary["seed"] = dataset["seed"]
    summary["scale"] = dataset["scale"]
    files["validation_summary.json"] = stable_json(summary)
    return files


def write_dataset(output_dir: Path, seed: int, scale: float) -> dict[str, Any]:
    dataset = build_dataset(seed, scale)
    files = render_files(dataset)
    hashes = {}
    for relative, content in files.items():
        target = output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        hashes[relative] = hashlib.sha256(content).hexdigest()
    manifest = {
        "generator": "generate_procurement.py",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "scale": scale,
        "provenance": {
            "type": "synthetic",
            "external_sources": [],
            "personal_data": False,
            "note": "All names, identifiers, transactions, risks and dates are synthetic.",
        },
        "row_counts": {
            **{name: len(records) for name, records in dataset["tables"].items()},
            "ground_truth": len(dataset["ground_truth"]),
            "data_dictionary": len(dataset["data_dictionary"]),
        },
        "sha256": hashes,
    }
    (output_dir / "source_manifest.json").write_bytes(stable_json(manifest))
    return manifest


def parse_scale(value: str) -> float:
    if value.casefold() == "full":
        return 1.0
    if value.casefold() == "starter":
        return 0.1
    try:
        scale = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("scale must be 'starter', 'full', or a number in (0, 1]") from exc
    if not 0 < scale <= 1:
        raise argparse.ArgumentTypeError("numeric scale must be greater than 0 and no greater than 1")
    return scale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--scale", type=parse_scale, default=1.0,
                        help="full, starter (10%%), or a numeric fraction in (0, 1]")
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--check-reproducibility", action="store_true",
                        help="generate twice in memory and compare every output checksum")
    args = parser.parse_args(argv)
    try:
        dataset = build_dataset(args.seed, args.scale)
        files = render_files(dataset)
        if args.check_reproducibility:
            second = render_files(build_dataset(args.seed, args.scale))
            if {k: hashlib.sha256(v).hexdigest() for k, v in files.items()} != {
                k: hashlib.sha256(v).hexdigest() for k, v in second.items()
            }:
                raise RuntimeError("Same-seed reproducibility check failed")
        manifest = write_dataset(args.output_dir, args.seed, args.scale)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "generated",
        "output_dir": str(args.output_dir.resolve()),
        "seed": args.seed,
        "scale": args.scale,
        "row_counts": manifest["row_counts"],
        "reproducibility_check": "passed" if args.check_reproducibility else "not requested",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
