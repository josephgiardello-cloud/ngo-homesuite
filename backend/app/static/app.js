"use strict";
let csrf = "";
let user = null;
const el = (id) => document.getElementById(id);

function message(text, error = false) {
  el("message").textContent = text;
  el("message").className = error ? "error" : "";
}

async function api(path, method = "GET", body) {
  const response = await fetch(`/api/v1/${path}`, {
    method, credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) showLogin();
    throw new Error(typeof data.detail === "string" ? data.detail : "Invalid request; check all fields.");
  }
  return data;
}

function showLogin() {
  user = null;
  csrf = "";
  el("workspace").hidden = true;
  el("login-panel").hidden = false;
  ["donors", "donations", "projects", "grants", "audit"].forEach((id) => el(id).replaceChildren());
}

function table(target, rows, columns, actions) {
  const result = document.createElement("table");
  const head = result.createTHead().insertRow();
  columns.forEach(([title]) => { const th = document.createElement("th"); th.textContent = title; head.append(th); });
  if (actions) { const th = document.createElement("th"); th.textContent = "Actions"; head.append(th); }
  const body = result.createTBody();
  rows.forEach((row) => {
    const tr = body.insertRow();
    columns.forEach(([, key]) => { tr.insertCell().textContent = String(row[key] ?? ""); });
    if (actions) actions(row, tr.insertCell());
  });
  el(target).replaceChildren(result);
}

function options(target, rows) {
  el(target).replaceChildren(...rows.map((row) => {
    const option = document.createElement("option");
    option.value = row.id;
    option.textContent = `${row.id}: ${row.name}`;
    return option;
  }));
}

async function refresh() {
  const [donors, donations, projects, grants] = await Promise.all(
    ["donors", "donations", "projects", "grants"].map((path) => api(path))
  );
  table("donors", donors, [["ID", "id"], ["Name", "name"], ["Email", "email"]]);
  table("donations", donations, [["ID", "id"], ["Donor", "donor_id"], ["Amount", "amount"], ["Purpose", "purpose"]]);
  table("projects", projects, [["ID", "id"], ["Name", "name"], ["Budget", "budget"], ["Spent", "spent"]]);
  table("grants", grants, [["ID", "id"], ["Name", "name"], ["Cents", "amount_minor"], ["Status", "status"]], (grant, cell) => {
    if (user.role !== "admin") return;
    const actions = grant.status === "draft" ? ["approve", "cancel"] :
      grant.status === "approved" ? ["pay", "cancel"] : [];
    actions.forEach((action) => {
      const button = document.createElement("button");
      button.textContent = action;
      button.setAttribute("aria-label", `${action} grant ${grant.id}`);
      button.disabled = action === "pay" && grant.approved_by === user.user_id;
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await api(`grants/${grant.id}/transition`, "POST", { action });
          await refresh();
          message(`Grant ${action} succeeded.`);
        } catch (error) { button.disabled = false; message(error.message, true); }
      });
      cell.append(button);
    });
  });
  options("donor-select", donors);
  options("project-select", projects);
  if (user.role === "admin") {
    table("audit", await api("audit_log"), [["Event", "action"], ["User", "username"], ["Entity", "entity_id"]]);
  }
}

async function enter() {
  user = await api("session");
  csrf = user.csrf_token;
  el("login-panel").hidden = true;
  el("workspace").hidden = false;
  el("identity").textContent = `${user.username} (${user.role}) - ${user.tenant}`;
  document.querySelectorAll(".writer").forEach((node) => { node.hidden = user.role === "viewer"; });
  document.querySelectorAll(".admin").forEach((node) => { node.hidden = user.role !== "admin"; });
  await refresh();
}

el("login").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const result = await api("login", "POST", Object.fromEntries(new FormData(form)));
    csrf = result.csrf_token;
    form.elements.password.value = "";
    await enter();
    message("Signed in.");
  } catch (error) { message(error.message, true); }
});

function bind(id, path, convert = (body) => body, method = "POST") {
  el(id).addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    button.disabled = true;
    message(`${id} saving...`);
    try {
      const body = convert(Object.fromEntries(new FormData(form)));
      const url = typeof path === "function" ? path(body) : path;
      await api(url, method, id === "allocation" ? { allocations: body.allocations } : body);
      if (id !== "donation") form.reset();
      await refresh();
      message(`${id} saved.`);
    } catch (error) { message(error.message, true); }
    finally { button.disabled = false; }
  });
}
bind("donor", "donors");
bind("donation", "donations", (body) => ({ ...body, donor_id: Number(body.donor_id) }));
bind("project", "projects");
bind("grant", "grants", (body) => ({ ...body, project_id: Number(body.project_id) }));
bind("user", "users");
bind("allocation", (body) => `donations/${body.donation_id}/allocations`, (body) => ({
  donation_id: Number(body.donation_id),
  allocations: [{ category: body.category, project_id: body.project_id ? Number(body.project_id) : null, amount: body.amount }],
}), "PUT");
el("refresh").addEventListener("click", () => refresh().catch((error) => message(error.message, true)));
el("logout").addEventListener("click", async () => {
  try { await api("logout", "POST"); showLogin(); message("Signed out."); }
  catch (error) { message(error.message, true); }
});
enter().catch((error) => { showLogin(); if (error.message !== "Authentication required") message(error.message, true); });
