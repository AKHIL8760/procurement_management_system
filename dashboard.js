const state = {
  summary: null,
  masters: null,
  requests: [],
  rfqs: [],
  orders: [],
  events: [],
  requestPage: 1,
  pendingDecision: null,
  readOnlyDemo: false,
};

const REQUEST_PAGE_SIZE = 30;

const pageTitles = {
  overview: "Overview",
  requests: "Purchase requests",
  sourcing: "Supplier sourcing",
  orders: "Orders & delivery",
  activity: "Audit trail",
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function money(value, currency = "USD") {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value) || 0);
}

function pretty(value) {
  return String(value ?? "").replaceAll("_", " ");
}

function badge(status) {
  const tone = ({
    approved: "good",
    delivered: "good",
    awarded: "good",
    single_source_awarded: "warning",
    pending: "warning",
    pending_approval: "warning",
    open: "blue",
    in_transit: "blue",
    partially_received: "warning",
    rejected: "danger",
    rejected_duplicate: "danger",
    expired: "danger",
    excluded_currency: "danger",
    closed_no_award: "muted",
  })[status] || "muted";
  return `<span class="status-badge ${tone}">${escapeHtml(pretty(status))}</span>`;
}

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(result.error || `The request failed (${response.status}).`);
  }
  return result;
}

function showMessage(message, isError = false) {
  const success = document.getElementById("app-message");
  const error = document.getElementById("app-error");
  success.hidden = isError;
  error.hidden = !isError;
  (isError ? error : success).textContent = message;
  window.clearTimeout(showMessage.timer);
  if (!isError) {
    showMessage.timer = window.setTimeout(() => {
      success.hidden = true;
    }, 5000);
  }
}

function setView(name) {
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === `view-${name}`);
  });
  document.querySelectorAll(".nav-link").forEach((link) => {
    link.classList.toggle("active", link.dataset.view === name);
  });
  document.getElementById("page-title").textContent = pageTitles[name] || "Overview";
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
}

function fillSelect(select, options, placeholder, getValue, getLabel) {
  const previous = select.value;
  select.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>` + options.map((item) => `
    <option value="${escapeHtml(getValue(item))}">${escapeHtml(getLabel(item))}</option>
  `).join("");
  if (options.some((item) => getValue(item) === previous)) select.value = previous;
}

function renderMasterSelects() {
  const masters = state.masters;
  fillSelect(
    document.getElementById("department-select"),
    masters.departments,
    "Choose department",
    (item) => item.department_id,
    (item) => item.name,
  );
  fillSelect(
    document.getElementById("requester-select"),
    masters.requesters,
    "Choose requester",
    (item) => item.requester_id,
    (item) => `${item.display_name} · ${pretty(item.role)}`,
  );
  fillSelect(
    document.getElementById("category-select"),
    masters.categories,
    "Choose category",
    (item) => item,
    (item) => item,
  );
  fillSelect(
    document.getElementById("supplier-select"),
    masters.suppliers,
    "Choose active supplier",
    (item) => item.supplier_id,
    (item) => `${item.legal_name}${item.risk_flag ? " · risk review" : ""}`,
  );
  fillSelect(
    document.getElementById("quote-currency"),
    masters.currencies,
    "Choose currency",
    (item) => item,
    (item) => item,
  );
  updateRequestersForDepartment();
  updateBudgetOptions();
}

function updateRequestersForDepartment() {
  const departmentId = document.getElementById("department-select").value;
  const currentRequester = document.getElementById("requester-select").value;
  const requesters = state.masters.requesters.filter(
    (requester) => requester.department_id === departmentId,
  );
  fillSelect(
    document.getElementById("requester-select"),
    requesters,
    requesters.length ? "Choose requester" : "Choose department first",
    (item) => item.requester_id,
    (item) => `${item.display_name} · ${pretty(item.role)}`,
  );
  if (requesters.some((requester) => requester.requester_id === currentRequester)) {
    document.getElementById("requester-select").value = currentRequester;
  }
}

function updateBudgetOptions() {
  const departmentId = document.getElementById("department-select").value;
  const budgets = state.masters.budgets.filter((budget) => budget.department_id === departmentId);
  fillSelect(
    document.getElementById("budget-select"),
    budgets,
    budgets.length ? "Choose budget" : "Choose department first",
    (item) => item.budget_id,
    (item) => `${money(item.available_amount, item.currency)} available`,
  );
}

function renderSummary() {
  const { row_counts: counts, metrics } = state.summary;
  const cards = [
    { label: "Purchase requests", value: counts.purchase_requests, note: "Across all departments", icon: "▤", color: "blue" },
    { label: "Awaiting approval", value: metrics.pending_approvals, note: "Needs an approver", icon: "◷", color: "amber" },
    { label: "Open RFQs", value: metrics.open_rfqs, note: "Waiting on a decision", icon: "⇄", color: "violet" },
    { label: "Orders in transit", value: metrics.in_transit_orders, note: "Supplier shipments underway", icon: "▣", color: "green" },
  ];
  document.getElementById("summary-cards").innerHTML = cards.map((card) => `
    <article class="stat-card">
      <div class="stat-top"><span class="stat-label">${card.label}</span><span class="stat-icon ${card.color}">${card.icon}</span></div>
      <strong class="stat-value">${Number(card.value).toLocaleString()}</strong>
      <span class="stat-note">${card.note}</span>
    </article>
  `).join("");
  document.getElementById("pending-nav-count").textContent =
    metrics.pending_approvals ? metrics.pending_approvals : "";
  document.getElementById("seed-tag").textContent =
    `${counts.requesters} requesters · ${counts.suppliers} suppliers · SQLite`;

  const workflow = [
    ["Competitive RFQs", metrics.competitive_rfqs, "blue"],
    ["Single-source RFQs", metrics.single_source_rfqs, "amber"],
    ["Awarded RFQs", metrics.awarded_rfqs, "green"],
    ["Open RFQs", metrics.open_rfqs, "violet"],
  ];
  const max = Math.max(...workflow.map(([, value]) => value), 1);
  document.getElementById("workflow-snapshot").innerHTML = workflow.map(([label, value, color]) => `
    <div class="workflow-row">
      <div class="workflow-label"><span>${label}</span><strong>${Number(value).toLocaleString()}</strong></div>
      <div class="progress-track"><div class="progress-value ${color}" style="width:${Math.max(1, value / max * 100)}%"></div></div>
    </div>
  `).join("");
  const percent = Math.max(0, Math.min(100, metrics.budget_utilization_percent));
  document.getElementById("budget-value").textContent = `${percent}% committed`;
  document.getElementById("budget-progress").style.width = `${percent}%`;
  document.getElementById("budget-caption").textContent =
    `${money(metrics.reserved_amount)} reserved · ${money(metrics.available_amount)} remaining across active budgets`;
}

function renderRequests() {
  const rows = state.requests;
  const search = document.getElementById("request-search").value.trim().toLowerCase();
  const filtered = rows.filter((request) => (
    `${request.request_id} ${request.description} ${request.requester_name} ${request.department_name}`
      .toLowerCase().includes(search)
  ));
  const pageCount = Math.max(1, Math.ceil(filtered.length / REQUEST_PAGE_SIZE));
  state.requestPage = Math.min(state.requestPage, pageCount);
  const start = (state.requestPage - 1) * REQUEST_PAGE_SIZE;
  document.getElementById("requests-table").innerHTML = filtered
    .slice(start, start + REQUEST_PAGE_SIZE).map((request) => {
    let action = `<span class="muted small">—</span>`;
    if (!state.readOnlyDemo && request.approval_decision === "pending") {
      action = `<div class="action-pair">
        <button class="button button-small button-success" data-action="approve" data-id="${escapeHtml(request.request_id)}">Approve</button>
        <button class="button button-small button-danger" data-action="reject" data-id="${escapeHtml(request.request_id)}">Reject</button>
      </div>`;
    } else if (!state.readOnlyDemo && request.status === "approved" && !request.rfq_id) {
      action = `<button class="button button-small button-secondary" data-action="create-rfq" data-id="${escapeHtml(request.request_id)}">Start RFQ</button>`;
    } else if (request.rfq_id) {
      action = `<button class="text-button" data-open-view="sourcing">${escapeHtml(request.rfq_id)} →</button>`;
    }
    return `<tr>
      <td><strong>${escapeHtml(request.request_id)}</strong><span class="table-sub">${escapeHtml(request.request_date)}</span></td>
      <td><strong>${escapeHtml(request.description)}</strong><span class="table-sub">${escapeHtml(request.quantity)} × ${money(request.estimated_unit_cost, request.currency)}</span></td>
      <td>${escapeHtml(request.requester_name)}</td>
      <td>${escapeHtml(request.department_name)}</td>
      <td class="amount">${money(request.estimated_total, request.currency)}</td>
      <td>${badge(request.approval_decision === "pending" ? "pending_approval" : request.status)}</td>
      <td>${action}</td>
    </tr>`;
    }).join("") || `<tr><td class="empty-cell" colspan="7">No matching requests.</td></tr>`;
  document.getElementById("request-page-info").textContent = filtered.length
    ? `${start + 1}–${Math.min(start + REQUEST_PAGE_SIZE, filtered.length)} of ${filtered.length} requests`
    : "0 requests";
  document.getElementById("request-previous").disabled = state.requestPage <= 1;
  document.getElementById("request-next").disabled = state.requestPage >= pageCount;

  const pending = rows.filter((request) => request.approval_decision === "pending").slice(0, 5);
  document.getElementById("pending-list").innerHTML = pending.map((request) => `
    <div class="compact-item">
      <div><strong>${escapeHtml(request.description)}</strong><span>${escapeHtml(request.request_id)} · ${money(request.estimated_total, request.currency)}</span></div>
      <button class="text-button" data-open-view="requests">Review →</button>
    </div>
  `).join("") || `<div class="empty-state"><span class="empty-icon">✓</span><strong>All caught up</strong><span>No requests are waiting for approval.</span></div>`;

  const approved = rows.filter((request) =>
    request.status === "approved" && !request.rfq_id
    && ["approved", "not_required"].includes(request.approval_decision)
  );
  fillSelect(
    document.getElementById("rfq-request-select"),
    approved,
    approved.length ? "Choose an approved request" : "No approved requests available",
    (item) => item.request_id,
    (item) => `${item.request_id} · ${item.description} · ${money(item.estimated_total, item.currency)}`,
  );
}

function renderRfqSelectors() {
  const openRfqs = state.rfqs.filter((rfq) => rfq.status === "open");
  fillSelect(
    document.getElementById("quote-rfq-select"),
    openRfqs,
    openRfqs.length ? "Choose open RFQ" : "No open RFQs",
    (item) => item.rfq_id,
    (item) => `${item.rfq_id} · ${item.description} · ${item.quantity} units`,
  );
}

function quoteSelectable(rfq, quote) {
  const expectedDate = new Date();
  expectedDate.setDate(expectedDate.getDate() + Number(quote.lead_time_days));
  const onTime = expectedDate.toISOString().slice(0, 10) <= rfq.required_by;
  return rfq.status === "open" && quote.status === "submitted"
    && quote.supplier_status === "active"
    && quote.currency === rfq.currency
    && onTime
    && quote.valid_until >= new Date().toISOString().slice(0, 10);
}

function renderRfqs() {
  const open = state.rfqs.filter((rfq) => rfq.status === "open");
  const closed = state.rfqs.filter((rfq) => rfq.status !== "open").slice(0, 8);
  const visible = [...open, ...closed];
  document.getElementById("rfq-list").innerHTML = visible.map((rfq) => {
    const selectable = rfq.quotes.filter((quote) => quoteSelectable(rfq, quote));
    const lowest = selectable.length
      ? Math.min(...selectable.map((quote) => Number(quote.landed_total))) : null;
    const awards = rfq.quotes.filter((quote) => quote.selected);
    const quoteRows = rfq.quotes.map((quote, index) => {
      const canChoose = !state.readOnlyDemo && quoteSelectable(rfq, quote);
      const chosenByDefault = canChoose && Number(quote.landed_total) === lowest
        && !rfq.quotes.slice(0, index).some((previous) =>
          quoteSelectable(rfq, previous) && Number(previous.landed_total) === lowest);
      return `<tr class="${quote.status === "excluded_currency" || quote.status === "expired" ? "row-muted" : ""}">
        <td>${canChoose ? `<input class="award-check" type="checkbox" data-quote="${escapeHtml(quote.quote_id)}" ${chosenByDefault ? "checked" : ""} aria-label="Select ${escapeHtml(quote.supplier_name)} for award" />` : ""}</td>
        <td><strong>${escapeHtml(quote.supplier_name)}</strong>${quote.risk_flag ? `<span class="table-sub risk-text">Supplier risk flag</span>` : ""}</td>
        <td>${money(quote.unit_price, quote.currency)}</td>
        <td>${money(quote.shipping_cost + quote.tax_amount, quote.currency)}</td>
        <td class="amount">${money(quote.landed_total, quote.currency)}${Number(quote.landed_total) === lowest ? `<span class="best-tag">Lowest</span>` : ""}</td>
        <td>${quote.lead_time_days} days</td>
        <td>${escapeHtml(quote.valid_until)}</td>
        <td>${quote.status === "submitted" && quote.supplier_status === "inactive" ? badge("inactive") : badge(quote.status)}
          ${quote.exclusion_reason ? `<span class="table-sub">${escapeHtml(quote.exclusion_reason)}</span>` : ""}
        </td>
        <td>${canChoose ? `<input class="award-quantity" type="number" min="0" max="${rfq.quantity}" value="${chosenByDefault ? rfq.quantity : 0}" data-quote="${escapeHtml(quote.quote_id)}" aria-label="Units from ${escapeHtml(quote.supplier_name)}" />` : ""}</td>
      </tr>`;
    }).join("");
    const awardControl = state.readOnlyDemo && rfq.status === "open"
      ? `<div class="rfq-closed-note">Open RFQ · Public demo is read-only.</div>`
      : rfq.status === "open"
      ? `<div class="award-bar">
          <label class="rationale-field">Award rationale <input class="award-rationale" maxlength="400" placeholder="Required if not choosing the lowest quote" /></label>
          <button class="button button-primary" data-action="award-rfq" data-id="${escapeHtml(rfq.rfq_id)}" ${selectable.length ? "" : "disabled"}>Award selected quantities</button>
        </div>`
      : `<div class="rfq-closed-note">${awards.length ? `${awards.length} supplier allocation(s) awarded.` : "No supplier award recorded."}</div>`;
    return `<article class="panel rfq-card">
      <div class="rfq-heading">
        <div><p class="eyebrow">${escapeHtml(rfq.rfq_id)} · ${escapeHtml(rfq.competition_type.replaceAll("_", " "))}</p>
          <h2>${escapeHtml(rfq.description)}</h2>
          <p class="muted">${escapeHtml(rfq.request_id)} · ${rfq.quantity} units · Need by ${escapeHtml(rfq.required_by)}</p>
          ${rfq.single_source_reason ? `<p class="note-box">${escapeHtml(rfq.single_source_reason)}</p>` : ""}
        </div>
        <div class="rfq-status">${badge(rfq.status)}<span class="table-sub">Closes ${escapeHtml(rfq.close_date)}</span></div>
      </div>
      <div class="table-wrap">
        <table class="comparison-table">
          <thead><tr><th></th><th>Supplier</th><th>Unit price</th><th>Ship + tax</th><th>Landed cost</th><th>Lead time</th><th>Valid through</th><th>Quote status</th><th>Units</th></tr></thead>
          <tbody>${quoteRows || `<tr><td colspan="9" class="empty-cell">No quotes received yet. Record supplier offers above.</td></tr>`}</tbody>
        </table>
      </div>
      ${awardControl}
    </article>`;
  }).join("") || `<article class="panel empty-state"><span class="empty-icon">⇄</span><strong>No RFQs yet</strong><span>Approve a request, then create an RFQ to start collecting supplier offers.</span></article>`;
}

function renderOrders() {
  const rows = state.orders;
  document.getElementById("orders-table").innerHTML = rows.map((order) => {
    const deliveryContent = (order.deliveries || []).map((delivery) => `
      <div class="delivery-line">
        <span>${escapeHtml(delivery.delivery_id)} · expected ${escapeHtml(delivery.expected_date)}</span>
        <span>${delivery.quantity_received}/${delivery.quantity_shipped} received ${badge(delivery.status)}</span>
      </div>
    `).join("");
    const receiveControls = state.readOnlyDemo
      ? `<span class="muted small">Read-only demo</span>`
      : (order.deliveries || [])
      .filter((delivery) => delivery.status !== "delivered")
      .map((delivery) => {
        const remaining = delivery.quantity_shipped - delivery.quantity_received;
        return `<div class="receive-control">
          <label class="visually-hidden" for="receive-${escapeHtml(delivery.delivery_id)}">Units to receive for ${escapeHtml(delivery.delivery_id)}</label>
          <input id="receive-${escapeHtml(delivery.delivery_id)}" type="number" min="1" max="${remaining}" value="${remaining}" />
          <button class="button button-small button-secondary" data-action="receive" data-id="${escapeHtml(delivery.delivery_id)}">Record receipt</button>
        </div>`;
      }).join("");
    return `<tr>
      <td><strong>${escapeHtml(order.po_id)}</strong><span class="table-sub">${escapeHtml(order.request_id)}</span></td>
      <td>${escapeHtml(order.supplier_name)}</td>
      <td>${escapeHtml(order.description)}<span class="table-sub">Quote ${escapeHtml(order.quote_id)}</span></td>
      <td>${order.quantity} units</td>
      <td class="amount">${money(order.total_amount, order.currency)}</td>
      <td>${badge(order.status)}<div class="delivery-list">${deliveryContent}</div></td>
      <td>${receiveControls || `<span class="muted small">All units received</span>`}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="7" class="empty-cell">No purchase orders found.</td></tr>`;
}

function renderEvents() {
  const rows = state.events;
  const html = rows.map((event) => `
    <tr>
      <td>${escapeHtml(event.event_at)}</td>
      <td>${escapeHtml(event.record_type)}</td>
      <td><strong>${escapeHtml(event.record_id)}</strong></td>
      <td>${escapeHtml(pretty(event.event_type))}</td>
      <td>${event.from_state ? `${badge(event.from_state)} <span class="arrow">→</span> ` : ""}${badge(event.to_state)}</td>
      <td>${escapeHtml(event.reason)}</td>
    </tr>
  `).join("") || `<tr><td colspan="6" class="empty-cell">No events recorded.</td></tr>`;
  document.getElementById("events-table").innerHTML = html;
  document.getElementById("recent-events").innerHTML = rows.slice(0, 7).map((event) => `
    <tr>
      <td><strong>${escapeHtml(pretty(event.event_type))}</strong><span class="table-sub">${escapeHtml(event.record_type)}</span></td>
      <td>${escapeHtml(event.record_id)}</td>
      <td>${escapeHtml(event.event_at)}</td>
      <td>${escapeHtml(event.reason)}</td>
    </tr>
  `).join("") || `<tr><td colspan="4" class="empty-cell">No recent updates.</td></tr>`;
}

function setDateDefaults() {
  const today = new Date();
  const todayString = today.toISOString().slice(0, 10);
  const nextWeek = new Date(today.getTime() + 7 * 86400000).toISOString().slice(0, 10);
  document.getElementById("required-by").min = todayString;
  if (!document.getElementById("required-by").value) {
    document.getElementById("required-by").value = nextWeek;
  }
  document.getElementById("rfq-close-date").min = nextWeek;
  if (!document.getElementById("rfq-close-date").value) {
    document.getElementById("rfq-close-date").value = nextWeek;
  }
  document.getElementById("quote-valid-until").min = todayString;
  if (!document.getElementById("quote-valid-until").value) {
    document.getElementById("quote-valid-until").value = nextWeek;
  }
}

async function refreshAll() {
  const [
    health, summary, masters, requests, rfqs, orders, events,
  ] = await Promise.all([
    api("/health"), api("/summary"), api("/masters"), api("/requests"), api("/rfqs"),
    api("/orders"), api("/events"),
  ]);
  state.readOnlyDemo = health.read_only;
  document.body.classList.toggle("read-only-demo", state.readOnlyDemo);
  document.getElementById("workspace-badge").innerHTML = state.readOnlyDemo
    ? "<span></span> Public read-only demo"
    : "<span></span> Local workspace";
  document.getElementById("workspace-note").textContent = state.readOnlyDemo
    ? "Synthetic data · Changes are disabled."
    : "Changes are saved to your local SQLite database.";
  document.querySelector(".app-footer").textContent = state.readOnlyDemo
    ? "Public read-only demo · Synthetic procurement data"
    : "Synthetic procurement workspace · Local demo data · No external services";
  state.summary = summary;
  state.masters = masters;
  state.requests = requests;
  state.rfqs = rfqs;
  state.orders = orders;
  state.events = events;
  renderSummary();
  renderMasterSelects();
  renderRequests();
  renderRfqSelectors();
  renderRfqs();
  renderOrders();
  renderEvents();
}

async function submitForm(form, path, successText) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const payload = Object.fromEntries(new FormData(form).entries());
    await api(path, { method: "POST", body: JSON.stringify(payload) });
    form.reset();
    document.getElementById("competition-type").dispatchEvent(new Event("change"));
    setDateDefaults();
    await refreshAll();
    showMessage(successText);
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function performAction(button) {
  const { action, id } = button.dataset;
  if (action === "approve" || action === "reject") {
    const decision = action === "approve" ? "approved" : "rejected";
    state.pendingDecision = { requestId: id, decision };
    document.getElementById("decision-title").textContent =
      action === "approve" ? "Approve this request?" : "Reject this request?";
    document.getElementById("decision-description").textContent =
      `${id} · Enter a reason to preserve the decision in the audit history.`;
    document.getElementById("decision-reason").value =
      action === "approve" ? "Approved within delegated authority" : "";
    document.getElementById("decision-dialog").showModal();
    return;
  }
  if (action === "create-rfq") {
    const closeDate = document.getElementById("rfq-close-date").value;
    await api("/rfqs", {
      method: "POST",
      body: JSON.stringify({
        request_id: id,
        competition_type: "competitive",
        close_date: closeDate,
      }),
    });
    await refreshAll();
    showMessage(`RFQ created for ${id}.`);
    setView("sourcing");
    return;
  }
  if (action === "award-rfq") {
    const card = button.closest(".rfq-card");
    const selected = [...card.querySelectorAll(".award-check:checked")];
    if (!selected.length) {
      showMessage("Select one or more supplier quotes to award.", true);
      return;
    }
    const allocations = selected.map((checkbox) => ({
      quote_id: checkbox.dataset.quote,
      quantity: Number(card.querySelector(`.award-quantity[data-quote="${CSS.escape(checkbox.dataset.quote)}"]`).value),
    }));
    const rationale = card.querySelector(".award-rationale").value.trim();
    button.disabled = true;
    try {
      const result = await api(`/rfqs/${encodeURIComponent(id)}/award`, {
        method: "POST",
        body: JSON.stringify({ allocations, rationale }),
      });
      await refreshAll();
      showMessage(`Award recorded. Created ${result.purchase_order_ids.join(", ")}.`);
    } finally {
      button.disabled = false;
    }
    return;
  }
  if (action === "receive") {
    const quantity = Number(document.getElementById(`receive-${CSS.escape(id)}`).value);
    button.disabled = true;
    try {
      const result = await api(`/deliveries/${encodeURIComponent(id)}/receive`, {
        method: "POST",
        body: JSON.stringify({ quantity }),
      });
      await refreshAll();
      showMessage(`Receipt recorded for ${id}. ${result.remaining} units remain.`);
    } finally {
      button.disabled = false;
    }
  }
}

function bindEvents() {
  document.querySelectorAll(".nav-link").forEach((link) => {
    link.addEventListener("click", () => setView(link.dataset.view));
  });
  document.addEventListener("click", async (event) => {
    const viewButton = event.target.closest("[data-open-view]");
    if (viewButton) {
      setView(viewButton.dataset.openView);
      return;
    }
    const actionButton = event.target.closest("[data-action]");
    if (!actionButton) return;
    try {
      await performAction(actionButton);
    } catch (error) {
      showMessage(error.message, true);
    }
  });
  document.getElementById("refresh-button").addEventListener("click", async () => {
    try {
      await refreshAll();
      showMessage("Workspace refreshed.");
    } catch (error) {
      showMessage(error.message, true);
    }
  });
  document.getElementById("department-select").addEventListener("change", () => {
    updateRequestersForDepartment();
    updateBudgetOptions();
  });
  document.getElementById("requester-select").addEventListener("change", (event) => {
    const requester = state.masters.requesters.find(
      (item) => item.requester_id === event.target.value,
    );
    if (requester) {
      document.getElementById("department-select").value = requester.department_id;
      updateBudgetOptions();
    }
  });
  document.getElementById("request-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, "/requests", "Purchase request submitted.");
  });
  document.getElementById("rfq-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, "/rfqs", "RFQ created.");
  });
  document.getElementById("quote-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const rfqId = new FormData(form).get("rfq_id");
    submitForm(form, `/rfqs/${encodeURIComponent(rfqId)}/quotes`, "Supplier quote saved.");
  });
  document.getElementById("request-search").addEventListener("input", () => {
    state.requestPage = 1;
    renderRequests();
  });
  document.getElementById("request-previous").addEventListener("click", () => {
    state.requestPage = Math.max(1, state.requestPage - 1);
    renderRequests();
  });
  document.getElementById("request-next").addEventListener("click", () => {
    state.requestPage += 1;
    renderRequests();
  });
  document.getElementById("decision-cancel").addEventListener("click", () => {
    document.getElementById("decision-dialog").close();
    state.pendingDecision = null;
  });
  document.getElementById("decision-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const pending = state.pendingDecision;
    const reason = document.getElementById("decision-reason").value.trim();
    if (!pending || !reason) return;
    const button = document.getElementById("decision-confirm");
    button.disabled = true;
    try {
      await api(`/requests/${encodeURIComponent(pending.requestId)}/approval`, {
        method: "POST",
        body: JSON.stringify({ decision: pending.decision, reason }),
      });
      document.getElementById("decision-dialog").close();
      state.pendingDecision = null;
      await refreshAll();
      showMessage(`Request ${pending.requestId} ${pending.decision}.`);
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
  document.getElementById("competition-type").addEventListener("change", (event) => {
    const single = event.target.value === "single_source";
    document.getElementById("single-source-field").hidden = !single;
    document.querySelector('[name="single_source_reason"]').required = single;
  });
  document.getElementById("quote-rfq-select").addEventListener("change", (event) => {
    const rfq = state.rfqs.find((item) => item.rfq_id === event.target.value);
    if (rfq) document.getElementById("quote-currency").value = rfq.currency;
  });
  document.getElementById("rfq-request-select").addEventListener("change", (event) => {
    const request = state.requests.find((item) => item.request_id === event.target.value);
    if (request) {
      const matchingOpen = state.rfqs.find((rfq) => rfq.request_id === request.request_id);
      if (matchingOpen) document.getElementById("quote-rfq-select").value = matchingOpen.rfq_id;
    }
  });
  document.getElementById("rfq-list").addEventListener("change", (event) => {
    const checkbox = event.target.closest(".award-check");
    if (!checkbox) return;
    const card = checkbox.closest(".rfq-card");
    const quantity = card.querySelector(`.award-quantity[data-quote="${CSS.escape(checkbox.dataset.quote)}"]`);
    if (checkbox.checked && Number(quantity.value) === 0) quantity.value = 1;
    if (!checkbox.checked) quantity.value = 0;
  });
  window.addEventListener("hashchange", () => {
    const name = location.hash.slice(1);
    if (pageTitles[name]) setView(name);
  });
}

async function init() {
  bindEvents();
  setDateDefaults();
  const initialView = location.hash.slice(1);
  if (pageTitles[initialView]) setView(initialView);
  try {
    await refreshAll();
  } catch (error) {
    showMessage(`${error.message} Start the app with python app.py.`, true);
  }
}

init();
