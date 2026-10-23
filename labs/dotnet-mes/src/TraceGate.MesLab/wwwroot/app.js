"use strict";

// No browser storage or client-side production rules: the API owns execution state.
const $ = (selector, scope = document) => scope.querySelector(selector);
const state = {
  route: [{ code: "ASSEMBLY", name: "Assembly" }, { code: "TEST", name: "Functional test" }, { code: "PACK", name: "Packing" }],
  orders: [], unitsByOrder: new Map(), events: [], selectedOrder: null,
  stationTrace: null, traceSerial: null, focusedUnit: null, view: "overview", refresh: 0,
};
const pendingRequests = new Map();
let toastTimeout;
const viewMeta = {
  overview: ["Work orders", "Production overview", "From work order to finished unit. Follow every step."],
  station: ["Station desk", "Execute with confidence", "Register a unit, report a result, and keep the route in order."],
  trace: ["Serial trace", "A history for every unit", "Follow the complete execution trail behind a serial number."],
  events: ["Event log", "Accepted unit events, recorded", "Inspect accepted unit execution events and export a traceable record."],
};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}
function cell(text, className) { return node("td", className, text); }
function clear(element) { element.replaceChildren(); }
function text(selector, value) { $(selector).textContent = String(value ?? "—"); }
function tag(status) {
  const labels = { Ready: "Ready", Held: "On hold", Completed: "Completed", InProgress: "In progress", Planned: "Planned", Open: "Open", Active: "Active" };
  const type = status === "Held" ? "held" : status === "Completed" ? "completed" : "ready";
  return node("span", `tag tag-${type}`, labels[status] || status || "Open");
}
function stationName(code) { return state.route.find(step => step.code === code)?.name || code || "—"; }
function completedCount(unit) {
  if (Array.isArray(unit.completedStations)) return unit.completedStations.length;
  if (typeof unit.completedStations === "number") return unit.completedStations;
  if (unit.status === "Completed") return state.route.length;
  const next = state.route.findIndex(step => step.code === unit.nextStation);
  return Math.max(0, next);
}
function progress(unit) {
  const wrapper = node("span", "progress-track");
  const done = completedCount(unit);
  wrapper.setAttribute("aria-label", `${done} of ${state.route.length} stations passed`);
  state.route.forEach((step, index) => {
    let className = "progress-segment";
    if (index < done) className += " done";
    else if (step.code === unit.nextStation) className += unit.status === "Held" ? " held" : " current";
    wrapper.append(node("span", className));
  });
  return wrapper;
}
function utcTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "—" : date.toISOString().replace("T", " ").slice(0, 19);
}
function eventLabel(event) {
  return ({ Registered: "Unit registered", StationPassed: "Station passed", StationFailed: "Station failed", HoldReleased: "Hold released" })[event.eventType] || event.eventType || "Event";
}
function eventGlyph(event) { return event.eventType === "StationFailed" ? "Ⅱ" : event.eventType === "HoldReleased" ? "↗" : event.eventType === "Registered" ? "+" : "✓"; }
function notify(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => { toast.hidden = true; }, 5500);
}
function showGlobalError(message) {
  const error = $("#global-error");
  error.textContent = message;
  error.hidden = !message;
}
function formError(form, message) {
  const error = $(".form-error", form);
  if (error) { error.textContent = message || ""; error.hidden = !message; }
}
async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } }); }
  catch { throw new Error("Cannot reach the local API. Check that TraceGate MES Lab is running, then retry. Your request ID is retained."); }
  let payload;
  try { payload = await response.json(); }
  catch { throw new Error(`The server returned an unreadable response (HTTP ${response.status}). Refresh and try again.`); }
  if (!response.ok) throw new Error(payload?.error?.message || `Request failed (HTTP ${response.status}).`);
  return payload;
}
function formValues(form) { return Object.fromEntries(new FormData(form).entries()); }
async function mutate(form, path, payload, successMessage, afterSuccess) {
  formError(form, "");
  const signature = JSON.stringify({ path, payload });
  let request = pendingRequests.get(form.id);
  if (!request || request.signature !== signature) {
    request = { signature, id: crypto.randomUUID() };
    pendingRequests.set(form.id, request);
  }
  const button = $("button[type=submit]", form);
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    const result = await api(path, { method: "POST", body: JSON.stringify({ ...payload, requestId: request.id }) });
    pendingRequests.delete(form.id);
    if (afterSuccess) afterSuccess(result);
    notify(successMessage);
    await refreshAll();
  } catch (error) { formError(form, error.message); }
  finally { button.disabled = false; button.textContent = oldText; }
}
function switchView(view) {
  if (!viewMeta[view]) view = "overview";
  state.view = view;
  document.querySelectorAll(".view-panel").forEach(panel => { panel.hidden = panel.id !== `view-${view}`; });
  document.querySelectorAll(".nav-item").forEach(button => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  text("#breadcrumb-current", viewMeta[view][0]);
  $("#page-title").replaceChildren(document.createTextNode(viewMeta[view][1]), node("span", "title-dot", "."));
  text("#page-description", viewMeta[view][2]);
  $("#create-order-toggle").hidden = view !== "overview";
  if (view === "events") loadEvents().catch(error => showGlobalError(error.message));
}
function emptyRow(body, colSpan, message) {
  const row = node("tr");
  const td = cell(message, "empty-state");
  td.colSpan = colSpan;
  row.append(td); body.append(row);
}
function renderOrders() {
  const body = $("#orders-body"); clear(body);
  text("#order-count", `${state.orders.length} ${state.orders.length === 1 ? "order" : "orders"}`);
  if (!state.orders.length) emptyRow(body, 5, "Create a work order to start your execution workflow.");
  state.orders.forEach(order => {
    const row = node("tr");
    const identity = cell(null, "primary-cell");
    identity.append(document.createTextNode(order.workOrderNo), node("span", "sub-cell", order.productCode));
    const quantity = cell(null, "cell-number");
    quantity.append(document.createTextNode(order.registeredQuantity ?? 0), node("span", "muted", ` / ${order.plannedQuantity}`));
    const status = cell(); status.append(tag(order.status));
    const action = cell(); const button = node("button", "text-button", "View →"); button.type = "button";
    button.setAttribute("aria-label", `View units in ${order.workOrderNo}`);
    button.addEventListener("click", () => { state.selectedOrder = order.workOrderNo; renderUnits(); $("#register-work-order").value = order.workOrderNo; });
    action.append(button);
    row.append(identity, quantity, cell(order.completedQuantity ?? 0, "cell-number"), status, action);
    body.append(row);
  });
  updateOrderSelects();
}
function updateOrderSelects() {
  const register = $("#register-work-order");
  const oldRegister = register.value || state.selectedOrder;
  clear(register);
  if (!state.orders.length) { const option = node("option", null, "Create a work order first"); option.value = ""; register.append(option); }
  state.orders.forEach(order => { const option = node("option", null, `${order.workOrderNo} · ${order.productCode}`); option.value = order.workOrderNo; register.append(option); });
  if (state.orders.some(order => order.workOrderNo === oldRegister)) register.value = oldRegister;
  const filter = $("#events-filter"); const oldFilter = filter.value;
  clear(filter); const all = node("option", null, "All work orders"); all.value = ""; filter.append(all);
  state.orders.forEach(order => { const option = node("option", null, order.workOrderNo); option.value = order.workOrderNo; filter.append(option); });
  if (state.orders.some(order => order.workOrderNo === oldFilter)) filter.value = oldFilter;
  updateExportLink();
}
function renderUnits() {
  const body = $("#units-body"); clear(body);
  const units = state.unitsByOrder.get(state.selectedOrder) || [];
  text("#units-order-label", state.selectedOrder ? `${state.selectedOrder} · ${units.length} registered ${units.length === 1 ? "unit" : "units"}` : "Select a work order to inspect its units.");
  if (!units.length) emptyRow(body, 5, state.selectedOrder ? "No serial numbers registered yet. Register one at the station desk." : "No work orders yet.");
  units.forEach(unit => {
    const row = node("tr");
    const route = cell(); route.append(progress(unit), node("span", "progress-caption", `${completedCount(unit)}/${state.route.length} passed`));
    const status = cell(); status.append(tag(unit.status));
    const action = cell(); const button = node("button", "text-button", "Trace ↗"); button.type = "button";
    button.setAttribute("aria-label", `Trace ${unit.serialNumber}`);
    button.addEventListener("click", () => openTrace(unit.serialNumber)); action.append(button);
    row.append(cell(unit.serialNumber, "primary-cell"), route, cell(unit.nextStation ? stationName(unit.nextStation) : "Route complete"), status, action);
    body.append(row);
  });
  const allUnits = [...state.unitsByOrder.values()].flat();
  state.focusedUnit = allUnits.find(unit => unit.status === "Held") || null;
  const focus = $("#focus-unit"); clear(focus);
  if (state.focusedUnit) {
    text("#focus-title", "A unit needs attention");
    text("#focus-description", "A failed station result put this unit on hold. Review, release, then retest.");
    focus.append(node("strong", "focus-serial", state.focusedUnit.serialNumber), node("span", "focus-station", `${state.focusedUnit.workOrderNo} · ${stationName(state.focusedUnit.nextStation)}`));
    $("#focus-action").hidden = false;
  } else {
    text("#focus-title", "No holds to review"); text("#focus-description", "Every registered unit is ready to continue or has completed its route."); $("#focus-action").hidden = true;
  }
}
function renderActivity() {
  const list = $("#activity-list"); clear(list);
  if (!state.events.length) { list.append(node("div", "empty-state", "Accepted execution events will appear here.")); return; }
  [...state.events].sort((a, b) => new Date(b.createdAtUtc) - new Date(a.createdAtUtc) || Number(b.eventId) - Number(a.eventId)).slice(0, 3).forEach(event => {
    const item = node("article", "activity-item"); const copy = node("div", "activity-copy");
    copy.append(node("strong", null, eventLabel(event)), document.createTextNode(` · ${event.serialNumber || event.workOrderNo || ""}`), node("span", null, `${event.station ? stationName(event.station) + " · " : ""}${utcTime(event.createdAtUtc)} UTC`));
    item.append(node("span", "activity-icon", eventGlyph(event)), copy); list.append(item);
  });
}
function renderStationUnit(trace) {
  state.stationTrace = trace;
  const container = $("#station-unit-card"); clear(container); container.classList.remove("empty-state");
  const header = node("div", "summary-header"); header.append(node("h3", null, trace.unit.serialNumber), tag(trace.unit.status));
  const hint = trace.unit.status === "Held" ? `On hold at ${stationName(trace.unit.nextStation)}. Release the hold before reporting another result.` : trace.unit.status === "Completed" ? "All required stations passed. This unit is complete." : `Next required station: ${stationName(trace.unit.nextStation)}.`;
  container.append(header, node("p", null, `${trace.unit.workOrderNo} · ${trace.unit.productCode || trace.workOrder?.productCode || ""}`), node("p", null, hint), progress(trace.unit));
  const form = $("#station-form");
  if (trace.unit.nextStation) form.elements.station.value = trace.unit.nextStation;
}
async function loadStationUnit(serial) {
  const normalized = serial.trim().toUpperCase();
  if (!normalized) throw new Error("Enter a serial number first.");
  const trace = await api(`/api/units/${encodeURIComponent(normalized)}/trace`);
  $("#station-serial").value = normalized;
  $("#release-serial").value = normalized;
  renderStationUnit(trace);
  return trace;
}
function renderTrace(trace) {
  const result = $("#trace-result"); clear(result);
  const unit = trace.unit;
  const overview = node("section", "panel trace-header");
  const top = node("div", "trace-header-top"); top.append(node("h2", null, unit.serialNumber), tag(unit.status));
  overview.append(top, node("p", null, `${unit.workOrderNo} · ${unit.productCode || trace.workOrder?.productCode || ""} · ${unit.nextStation ? "Next: " + stationName(unit.nextStation) : "All stations passed"}`));
  const route = node("div", "trace-route"); const done = completedCount(unit);
  state.route.forEach((step, index) => {
    const passed = index < done; const current = step.code === unit.nextStation;
    const stage = node("div", `trace-step${passed ? " is-complete" : current ? (unit.status === "Held" ? " is-held" : " is-current") : ""}`);
    const copy = node("div"); copy.append(node("strong", null, step.name), node("small", null, passed ? "Passed" : current ? (unit.status === "Held" ? "On hold · retry after release" : "Ready to report") : "Waiting for previous station"));
    stage.append(node("span", "step-num", passed ? "✓" : String(index + 1).padStart(2, "0")), copy); route.append(stage);
  });
  overview.append(route); result.append(overview);
  const panel = node("section", "panel"); const heading = node("div", "panel-heading"); const headingCopy = node("div");
  headingCopy.append(node("h2", null, "Unit event history"), node("p", null, "Oldest to newest · UTC timestamps · Accepted events only"));
  heading.append(headingCopy, node("span", "count-label", `${trace.events.length} events`)); panel.append(heading);
  const timeline = node("ol", "timeline");
  [...trace.events].sort((a, b) => new Date(a.createdAtUtc) - new Date(b.createdAtUtc) || Number(a.eventId) - Number(b.eventId)).forEach(event => {
    const item = node("li", `timeline-item${event.eventType === "StationFailed" ? " failed" : ""}`);
    item.append(node("span", "timeline-dot", eventGlyph(event)), node("div", "timeline-event-title", `${eventLabel(event)}${event.station ? " · " + stationName(event.station) : ""}`));
    item.append(node("div", "timeline-detail", `Operator: ${event.operator || "—"}${event.reason ? " · " + event.reason : ""}`), node("div", "timeline-time", `${utcTime(event.createdAtUtc)} UTC · Event ${event.eventId ?? "—"}`)); timeline.append(item);
  });
  if (!trace.events.length) timeline.append(node("li", "empty-state", "No events recorded for this unit."));
  panel.append(timeline); result.append(panel);
}
async function openTrace(serial) {
  switchView("trace"); $("#trace-serial").value = serial;
  formError($("#trace-form"), "");
  try { const trace = await api(`/api/units/${encodeURIComponent(serial.trim().toUpperCase())}/trace`); state.traceSerial = trace.unit.serialNumber; renderTrace(trace); }
  catch (error) { formError($("#trace-form"), error.message); }
}
function updateExportLink() {
  const filter = $("#events-filter").value;
  $("#export-link").href = `/api/events/export.csv${filter ? "?workOrderNo=" + encodeURIComponent(filter) : ""}`;
}
async function loadEvents() {
  const filter = $("#events-filter").value;
  const payload = await api(`/api/events?limit=100${filter ? "&workOrderNo=" + encodeURIComponent(filter) : ""}`);
  const body = $("#events-body"); clear(body); updateExportLink();
  const events = [...payload.items].sort((a, b) => new Date(b.createdAtUtc) - new Date(a.createdAtUtc) || Number(b.eventId) - Number(a.eventId));
  text("#events-count", `${events.length} events loaded · max 100`);
  if (!events.length) emptyRow(body, 5, "No accepted events for this filter.");
  events.forEach(event => {
    const row = node("tr"); const identity = cell(null, "primary-cell");
    identity.append(document.createTextNode(event.serialNumber || "—"), node("span", "sub-cell", event.workOrderNo));
    const eventCell = cell(); eventCell.append(node("span", `tag ${event.eventType === "StationFailed" ? "tag-held" : "tag-ready"}`, eventLabel(event)));
    const operator = cell(event.operator || "—"); if (event.reason) operator.append(node("span", "event-reason", event.reason));
    row.append(cell(utcTime(event.createdAtUtc), "utc-time"), identity, eventCell, cell(stationName(event.station)), operator); body.append(row);
  });
}
async function refreshAll() {
  const currentRefresh = ++state.refresh;
  const refresh = $("#refresh-button"); refresh.disabled = true;
  try {
    const [meta, totals, orders, events] = await Promise.all([api("/api/meta"), api("/api/dashboard"), api("/api/work-orders"), api("/api/events?limit=100")]);
    const unitResults = await Promise.all(orders.items.map(async order => [order.workOrderNo, (await api(`/api/work-orders/${encodeURIComponent(order.workOrderNo)}/units`)).items]));
    if (currentRefresh !== state.refresh) return;
    if (Array.isArray(meta.route) && meta.route.length) state.route = meta.route;
    state.orders = orders.items; state.events = events.items; state.unitsByOrder = new Map(unitResults);
    if (!state.orders.some(order => order.workOrderNo === state.selectedOrder)) state.selectedOrder = state.orders[0]?.workOrderNo || null;
    text("#metric-orders", totals.workOrders); text("#metric-units", totals.units); text("#metric-completed", totals.completedUnits); text("#metric-held", totals.heldUnits);
    renderOrders(); renderUnits(); renderActivity();
    text("#connection-state", "Workspace synced"); showGlobalError("");
    if (state.stationTrace) {
      try { renderStationUnit(await api(`/api/units/${encodeURIComponent(state.stationTrace.unit.serialNumber)}/trace`)); } catch { /* The workspace refresh still succeeded. */ }
    }
    if (state.traceSerial) {
      try { renderTrace(await api(`/api/units/${encodeURIComponent(state.traceSerial)}/trace`)); } catch { /* Preserve the last successfully loaded trace. */ }
    }
    if (state.view === "events") await loadEvents();
    return true;
  } catch (error) {
    if (currentRefresh === state.refresh) { showGlobalError(error.message); text("#connection-state", "Connection unavailable"); }
    return false;
  } finally { if (currentRefresh === state.refresh) refresh.disabled = false; }
}

document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
document.querySelectorAll("[data-go]").forEach(button => button.addEventListener("click", () => switchView(button.dataset.go)));
$(".brand").addEventListener("click", event => { event.preventDefault(); switchView("overview"); });
$("#refresh-button").addEventListener("click", refreshAll);
$("#create-order-toggle").addEventListener("click", () => { $("#create-order-panel").hidden = !$("#create-order-panel").hidden; if (!$("#create-order-panel").hidden) $("#create-order-form").elements.workOrderNo.focus(); });
$("#close-order-form").addEventListener("click", () => { $("#create-order-panel").hidden = true; });
$("#create-order-form").addEventListener("submit", event => {
  event.preventDefault(); const form = event.currentTarget; const value = formValues(form);
  mutate(form, "/api/work-orders", { workOrderNo: value.workOrderNo.trim(), productCode: value.productCode.trim(), plannedQuantity: Number(value.plannedQuantity), operator: value.operator.trim() }, "Work order created. Register a serial number at the station desk.", result => { state.selectedOrder = result.workOrderNo; form.reset(); $("#create-order-panel").hidden = true; });
});
$("#register-form").addEventListener("submit", event => {
  event.preventDefault(); const form = event.currentTarget; const value = formValues(form); const serial = value.serialNumber.trim().toUpperCase();
  mutate(form, "/api/units/register", { workOrderNo: value.workOrderNo, serialNumber: serial, operator: value.operator.trim() }, `${serial} registered. Ready for Assembly.`, () => { state.selectedOrder = value.workOrderNo; $("#station-serial").value = serial; $("#release-serial").value = serial; form.elements.serialNumber.value = ""; loadStationUnit(serial).catch(error => formError($("#station-form"), error.message)); });
});
$("#load-station-unit").addEventListener("click", async () => {
  const form = $("#station-form"); const button = $("#load-station-unit"); formError(form, ""); button.disabled = true;
  try { await loadStationUnit($("#station-serial").value); } catch (error) { formError(form, error.message); } finally { button.disabled = false; }
});
$("#station-form").addEventListener("submit", event => {
  event.preventDefault(); const form = event.currentTarget; const value = formValues(form); const serial = value.serialNumber.trim().toUpperCase();
  if (value.result === "FAIL" && value.reason.trim().length < 3) { formError(form, "Enter a reason of at least 3 characters for a failed station result."); return; }
  mutate(form, `/api/units/${encodeURIComponent(serial)}/station-events`, { station: value.station, result: value.result, operator: value.operator.trim(), reason: value.reason.trim() || null }, value.result === "FAIL" ? `${serial} put on hold. Release is required before a retry.` : `Station pass recorded for ${serial}.`, result => { if (result.unit) renderStationUnit({ ...state.stationTrace, unit: result.unit }); form.elements.reason.value = ""; });
});
$("#release-form").addEventListener("submit", event => {
  event.preventDefault(); const form = event.currentTarget; const value = formValues(form); const serial = value.serialNumber.trim().toUpperCase();
  mutate(form, `/api/units/${encodeURIComponent(serial)}/release-hold`, { operator: value.operator.trim(), reason: value.reason.trim() }, `${serial} released. Retry its current station before continuing.`, result => { if (state.stationTrace?.unit.serialNumber === serial && result.unit) renderStationUnit({ ...state.stationTrace, unit: result.unit }); form.elements.reason.value = ""; });
});
$("#focus-action").addEventListener("click", async () => {
  if (!state.focusedUnit) return; switchView("station");
  try { await loadStationUnit(state.focusedUnit.serialNumber); } catch (error) { formError($("#station-form"), error.message); }
});
$("#trace-form").addEventListener("submit", event => { event.preventDefault(); openTrace($("#trace-serial").value); });
$("#demo-trace-button").addEventListener("click", () => { openTrace("SN-DEMO-0002"); });
$("#events-filter").addEventListener("change", () => { updateExportLink(); loadEvents().catch(error => showGlobalError(error.message)); });

// If an operator edits a request after an uncertain result, it is a new command.
document.querySelectorAll("form").forEach(form => form.addEventListener("input", () => { pendingRequests.delete(form.id); formError(form, ""); }));
switchView("overview");
refreshAll();
