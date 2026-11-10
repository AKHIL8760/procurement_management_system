PRAGMA foreign_keys = ON;



CREATE TABLE IF NOT EXISTS departments (
  department_id TEXT NOT NULL PRIMARY KEY,
  name TEXT NOT NULL,
  cost_center TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active')),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  UNIQUE (cost_center)
);

CREATE TABLE IF NOT EXISTS requesters (
  requester_id TEXT NOT NULL PRIMARY KEY,
  display_name TEXT NOT NULL,
  email TEXT NOT NULL,
  department_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('requester', 'manager', 'director')),
  status TEXT NOT NULL CHECK (status IN ('active')),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  FOREIGN KEY (department_id) REFERENCES departments(department_id),
  UNIQUE (email)
);

CREATE TABLE IF NOT EXISTS budgets (
  budget_id TEXT NOT NULL PRIMARY KEY,
  department_id TEXT NOT NULL,
  fiscal_year INTEGER NOT NULL,
  currency TEXT NOT NULL CHECK (currency IN ('USD', 'EUR', 'GBP', 'CAD')),
  allocation_amount REAL NOT NULL CHECK (allocation_amount >= 0),
  effective_from TEXT NOT NULL,
  effective_to TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active')),
  FOREIGN KEY (department_id) REFERENCES departments(department_id),
  UNIQUE (department_id, fiscal_year)
);

CREATE TABLE IF NOT EXISTS suppliers (
  supplier_id TEXT NOT NULL PRIMARY KEY,
  supplier_code TEXT NOT NULL,
  legal_name TEXT NOT NULL,
  country TEXT NOT NULL CHECK (country IN ('US', 'CA', 'GB', 'DE', 'AU', 'JP')),
  currency TEXT NOT NULL CHECK (currency IN ('USD', 'EUR', 'GBP', 'CAD')),
  status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
  risk_flag INTEGER NOT NULL CHECK (risk_flag IN (0, 1)),
  risk_reason TEXT,
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  UNIQUE (supplier_code)
);

CREATE TABLE IF NOT EXISTS purchase_requests (
  request_id TEXT NOT NULL PRIMARY KEY,
  requester_id TEXT NOT NULL,
  department_id TEXT NOT NULL,
  budget_id TEXT NOT NULL,
  request_date TEXT NOT NULL,
  required_by TEXT NOT NULL,
  estimated_total REAL NOT NULL CHECK (estimated_total >= 0),
  currency TEXT NOT NULL CHECK (currency IN ('USD', 'EUR', 'GBP', 'CAD')),
  status TEXT NOT NULL CHECK (status IN ('pending_approval', 'approved', 'rejected', 'ordered', 'rfq_closed', 'rfq_expired', 'rejected_duplicate')),
  business_justification TEXT NOT NULL,
  FOREIGN KEY (requester_id) REFERENCES requesters(requester_id),
  FOREIGN KEY (department_id) REFERENCES departments(department_id),
  FOREIGN KEY (budget_id) REFERENCES budgets(budget_id)
);

CREATE TABLE IF NOT EXISTS request_lines (
  request_line_id TEXT NOT NULL PRIMARY KEY,
  request_id TEXT NOT NULL,
  line_number INTEGER NOT NULL CHECK (line_number > 0),
  description TEXT NOT NULL,
  category TEXT NOT NULL CHECK (category IN ('IT Hardware', 'Office Supplies', 'Facilities', 'Professional Services', 'Software', 'Safety Equipment')),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_of_measure TEXT NOT NULL,
  estimated_unit_cost REAL NOT NULL CHECK (estimated_unit_cost >= 0),
  estimated_total REAL NOT NULL CHECK (estimated_total >= 0),
  currency TEXT NOT NULL CHECK (currency IN ('USD', 'EUR', 'GBP', 'CAD')),
  FOREIGN KEY (request_id) REFERENCES purchase_requests(request_id),
  UNIQUE (request_id, line_number)
);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT NOT NULL PRIMARY KEY,
  request_id TEXT NOT NULL,
  approval_level TEXT NOT NULL CHECK (approval_level IN ('automatic', 'manager', 'director')),
  threshold_amount REAL NOT NULL CHECK (threshold_amount >= 0),
  decision TEXT NOT NULL CHECK (decision IN ('approved', 'pending', 'not_required', 'rejected')),
  approver_id TEXT,
  requested_at TEXT NOT NULL,
  decided_at TEXT,
  decision_reason TEXT NOT NULL,
  FOREIGN KEY (request_id) REFERENCES purchase_requests(request_id),
  FOREIGN KEY (approver_id) REFERENCES requesters(requester_id),
  UNIQUE (request_id)
);

CREATE TABLE IF NOT EXISTS rfqs (
  rfq_id TEXT NOT NULL PRIMARY KEY,
  request_id TEXT NOT NULL,
  issue_date TEXT NOT NULL,
  close_date TEXT NOT NULL,
  required_by TEXT NOT NULL,
  competition_type TEXT NOT NULL CHECK (competition_type IN ('competitive', 'single_source')),
  single_source_reason TEXT,
  status TEXT NOT NULL CHECK (status IN ('open', 'awarded', 'single_source_awarded', 'closed_no_award', 'expired')),
  FOREIGN KEY (request_id) REFERENCES purchase_requests(request_id),
  UNIQUE (request_id)
);

CREATE TABLE IF NOT EXISTS supplier_quotes (
  quote_id TEXT NOT NULL PRIMARY KEY,
  rfq_id TEXT NOT NULL,
  supplier_id TEXT NOT NULL,
  quote_date TEXT NOT NULL,
  valid_until TEXT NOT NULL,
  currency TEXT NOT NULL CHECK (currency IN ('USD', 'EUR', 'GBP', 'CAD')),
  unit_price REAL NOT NULL CHECK (unit_price >= 0),
  shipping_cost REAL NOT NULL CHECK (shipping_cost >= 0),
  tax_amount REAL NOT NULL CHECK (tax_amount >= 0),
  landed_total REAL NOT NULL CHECK (landed_total >= 0),
  lead_time_days INTEGER NOT NULL CHECK (lead_time_days > 0),
  status TEXT NOT NULL CHECK (status IN ('submitted', 'awarded', 'expired', 'excluded_currency', 'not_selected')),
  exclusion_reason TEXT,
  FOREIGN KEY (rfq_id) REFERENCES rfqs(rfq_id),
  FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id),
  UNIQUE (rfq_id, supplier_id)
);

CREATE TABLE IF NOT EXISTS rfq_awards (
  award_id TEXT NOT NULL PRIMARY KEY,
  rfq_id TEXT NOT NULL,
  quote_id TEXT NOT NULL,
  awarded_at TEXT NOT NULL,
  quantity_awarded INTEGER NOT NULL CHECK (quantity_awarded > 0),
  award_total REAL NOT NULL CHECK (award_total >= 0),
  rationale TEXT,
  decision_status TEXT NOT NULL CHECK (decision_status IN ('awarded')),
  FOREIGN KEY (rfq_id) REFERENCES rfqs(rfq_id),
  FOREIGN KEY (quote_id) REFERENCES supplier_quotes(quote_id),
  UNIQUE (rfq_id, quote_id)
);

CREATE TABLE IF NOT EXISTS purchase_orders (
  po_id TEXT NOT NULL PRIMARY KEY,
  request_id TEXT NOT NULL,
  rfq_id TEXT NOT NULL,
  award_id TEXT NOT NULL,
  supplier_id TEXT NOT NULL,
  approval_id TEXT,
  issued_date TEXT NOT NULL,
  currency TEXT NOT NULL CHECK (currency IN ('USD', 'EUR', 'GBP', 'CAD')),
  total_amount REAL NOT NULL CHECK (total_amount >= 0),
  status TEXT NOT NULL CHECK (status IN ('issued', 'in_transit', 'partially_received', 'delivered')),
  FOREIGN KEY (request_id) REFERENCES purchase_requests(request_id),
  FOREIGN KEY (rfq_id) REFERENCES rfqs(rfq_id),
  FOREIGN KEY (award_id) REFERENCES rfq_awards(award_id),
  FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id),
  FOREIGN KEY (approval_id) REFERENCES approvals(approval_id),
  UNIQUE (award_id)
);

CREATE TABLE IF NOT EXISTS purchase_order_lines (
  po_line_id TEXT NOT NULL PRIMARY KEY,
  po_id TEXT NOT NULL,
  request_line_id TEXT NOT NULL,
  quote_id TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price REAL NOT NULL CHECK (unit_price >= 0),
  shipping_allocated REAL NOT NULL CHECK (shipping_allocated >= 0),
  tax_allocated REAL NOT NULL CHECK (tax_allocated >= 0),
  line_total REAL NOT NULL CHECK (line_total >= 0),
  FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id),
  FOREIGN KEY (request_line_id) REFERENCES request_lines(request_line_id),
  FOREIGN KEY (quote_id) REFERENCES supplier_quotes(quote_id),
  UNIQUE (po_id, request_line_id)
);

CREATE TABLE IF NOT EXISTS deliveries (
  delivery_id TEXT NOT NULL PRIMARY KEY,
  po_id TEXT NOT NULL,
  shipment_number INTEGER NOT NULL CHECK (shipment_number > 0),
  expected_date TEXT NOT NULL,
  shipped_date TEXT,
  received_date TEXT,
  quantity_shipped INTEGER NOT NULL CHECK (quantity_shipped > 0),
  quantity_received INTEGER NOT NULL CHECK (quantity_received >= 0),
  status TEXT NOT NULL CHECK (status IN ('in_transit', 'partially_received', 'delivered', 'delayed')),
  exception_reason TEXT,
  FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id),
  UNIQUE (po_id, shipment_number)
);

CREATE TABLE IF NOT EXISTS budget_ledger (
  ledger_id TEXT NOT NULL PRIMARY KEY,
  budget_id TEXT NOT NULL,
  po_id TEXT NOT NULL,
  event_type TEXT NOT NULL CHECK (event_type IN ('reserve')),
  event_date TEXT NOT NULL,
  amount REAL NOT NULL CHECK (amount >= 0),
  balance_after REAL NOT NULL,
  reason TEXT NOT NULL,
  FOREIGN KEY (budget_id) REFERENCES budgets(budget_id),
  FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id),
  UNIQUE (po_id)
);

CREATE TABLE IF NOT EXISTS request_events (
  event_id TEXT NOT NULL PRIMARY KEY,
  request_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  event_at TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  reason TEXT NOT NULL,
  FOREIGN KEY (request_id) REFERENCES purchase_requests(request_id)
);

CREATE TABLE IF NOT EXISTS rfq_events (
  event_id TEXT NOT NULL PRIMARY KEY,
  rfq_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  event_at TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  reason TEXT NOT NULL,
  FOREIGN KEY (rfq_id) REFERENCES rfqs(rfq_id)
);

CREATE TABLE IF NOT EXISTS purchase_order_events (
  event_id TEXT NOT NULL PRIMARY KEY,
  po_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  event_at TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  reason TEXT NOT NULL,
  FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id)
);

CREATE TABLE IF NOT EXISTS delivery_events (
  event_id TEXT NOT NULL PRIMARY KEY,
  delivery_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  event_at TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  reason TEXT NOT NULL,
  FOREIGN KEY (delivery_id) REFERENCES deliveries(delivery_id)
);
