/* ============================================================
 * MTa Admin — app.js
 * Vanilla, single-file SPA. No React, no build.
 * Loads: lucide UMD (window.lucide), tokens.css+components.css+app.css.
 * Exposes: window.MTa for debug.
 * ============================================================ */
(function () {
  "use strict";

  // ----------------------------------------------------------------
  // 0. Tiny helpers
  // ----------------------------------------------------------------
  const $  = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const ce = (tag, props, html) => {
    const el = document.createElement(tag);
    if (props) for (const k of Object.keys(props)) {
      if (k === "class") el.className = props[k];
      else if (k === "style" && typeof props[k] === "object") Object.assign(el.style, props[k]);
      else if (k.startsWith("on") && typeof props[k] === "function") el.addEventListener(k.slice(2).toLowerCase(), props[k]);
      else if (k === "html") el.innerHTML = props[k];
      else el.setAttribute(k, props[k]);
    }
    if (html != null) el.innerHTML = html;
    return el;
  };
  const escapeHTML = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  const fmtNum = (n) => {
    if (n == null || isNaN(n)) return "—";
    return Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
  };
  const fmtMoney = (n) => {
    if (n == null || isNaN(n)) return "—";
    return "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  const fmtTime = (iso) => {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return String(iso);
      return d.toLocaleTimeString([], { hour12: false }) + " · " + d.toLocaleDateString();
    } catch { return String(iso); }
  };
  const fmtShortTime = (iso) => {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return String(iso);
      return d.toLocaleTimeString([], { hour12: false });
    } catch { return String(iso); }
  };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ----------------------------------------------------------------
  // 1. Auth + API helpers
  // ----------------------------------------------------------------
  const STORAGE_KEY = "mta.session.v1";
  const THEME_KEY   = "mta.theme.v1";
  const COMPACT_KEY = "mta.compact.v1";

  const state = {
    auth: loadToken(),
    theme: loadTheme(),
    compact: localStorage.getItem(COMPACT_KEY) === "1",
    currentPath: "/",
    cache: {},
    sse: null,
    sseRetries: 0,
    pageAbort: null,
  };

  function loadToken() {
    try { const t = localStorage.getItem(STORAGE_KEY); return t || null; } catch { return null; }
  }
  function saveToken(t) {
    state.auth = t;
    if (t) localStorage.setItem(STORAGE_KEY, t);
    else   localStorage.removeItem(STORAGE_KEY);
  }
  function loadTheme() {
    try {
      const t = localStorage.getItem(THEME_KEY);
      if (t === "light" || t === "dark") return t;
    } catch {}
    return "light";
  }

  async function api(method, path, body, opts) {
    opts = opts || {};
    const headers = Object.assign({}, opts.headers || {});
    headers["Content-Type"] = "application/json";
    if (state.auth) headers["Authorization"] = "Bearer " + state.auth;
    const init = { method, headers, credentials: "include" };
    if (body !== undefined && body !== null) init.body = JSON.stringify(body);
    const res = await fetch(path, init);
    if (res.status === 204) return null;
    const ct = res.headers.get("content-type") || "";
    let data;
    if (ct.indexOf("application/json") !== -1) data = await res.json();
    else                                       data = await res.text();
    if (!res.ok) {
      const err = new Error((data && data.detail) || ("HTTP " + res.status));
      err.status = res.status; err.body = data;
      if (res.status === 401) {
        saveToken(null);
        state.currentPath = "/login";
        route("/login", true);
      }
      throw err;
    }
    return data;
  }

  // ----------------------------------------------------------------
  // 2. Toasts
  // ----------------------------------------------------------------
  function toast(opts) {
    const o = Object.assign({ kind: "info", title: "", description: "", duration: 4000 }, opts || {});
    const host = $("#toast-container");
    if (!host) return;
    const wrap = ce("div", { class: "toast toast-" + o.kind, role: "status" });
    const iconFor = { success: "check-circle-2", error: "alert-circle",
                      warning: "alert-triangle", info: "info" };
    wrap.innerHTML = [
      '<span class="toast-icon">', window.MTaIcons.icon(iconFor[o.kind] || "info", 16), '</span>',
      '<div style="flex:1;min-width:0">',
        o.title   ? '<p class="toast-title">' + escapeHTML(o.title) + '</p>' : "",
        o.description ? '<p class="toast-description">' + escapeHTML(o.description) + '</p>' : "",
      '</div>',
      '<button class="toast-close" aria-label="Dismiss">', window.MTaIcons.icon("x", 14), '</button>',
    ].join("");
    host.appendChild(wrap);
    refreshIcons(wrap);
    const close = () => { wrap.style.opacity = "0"; setTimeout(() => wrap.remove(), 200); };
    wrap.querySelector(".toast-close").addEventListener("click", close);
    if (o.duration > 0) setTimeout(close, o.duration);
  }

  // ----------------------------------------------------------------
  // 3. Theme + compact-mode
  // ----------------------------------------------------------------
  function applyTheme(theme) {
    state.theme = theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch {}
    document.documentElement.classList.toggle("dark",   theme === "dark");
    document.documentElement.classList.toggle("light",  theme === "light");
    document.body.classList.toggle("dark",   theme === "dark");
    document.body.classList.toggle("light",  theme === "light");
    const meta = document.querySelector("meta[name=theme-color]");
    if (meta) meta.content = theme === "dark" ? "#171717" : "#ffffff";
    const icon = $("#theme-icon");
    if (icon) {
      icon.setAttribute("data-lucide", theme === "dark" ? "sun" : "moon");
      refreshIcons(icon.parentElement);
    }
  }

  function applyCompact(compact) {
    state.compact = !!compact;
    try { localStorage.setItem(COMPACT_KEY, state.compact ? "1" : "0"); } catch {}
    const sb = $("#sidebar");
    if (sb) sb.classList.toggle("collapsed", state.compact);
  }

  // ----------------------------------------------------------------
  // 4. Icon refresh
  // ----------------------------------------------------------------
  function refreshIcons(root) { try { window.MTaIcons && window.MTaIcons.refresh(root); } catch {} }

  // ----------------------------------------------------------------
  // 5. Page templates
  // ----------------------------------------------------------------
  function cloneTpl(id) {
    const tpl = document.getElementById("tpl-" + id);
    if (!tpl) return null;
    return tpl.content.cloneNode(true);
  }

  function setChrome() {
    // Show sidebar only when authenticated.
    const sb = $("#sidebar");
    if (sb) sb.style.display = state.auth ? "" : "none";
  }

  function setSidebarActive(path) {
    const nav = $("#sidebar-nav");
    if (!nav) return;
    $$(".sidebar-item", nav).forEach((el) => {
      const target = el.getAttribute("data-route");
      el.setAttribute("data-active", "false");
      if (target === path) el.setAttribute("data-active", "true");
      else if (path.indexOf(target + "/") === 0) el.setAttribute("data-active", "true");
    });
  }

  function mountPage(node) {
    const host = $("#app");
    if (!host) return;
    // Cancel any in-flight page work.
    if (state.pageAbort) { try { state.pageAbort.abort(); } catch {} state.pageAbort = null; }
    state.pageAbort = new AbortController();

    host.innerHTML = "";
    if (node) host.appendChild(node);
    host.focus();
    refreshIcons(host);
  }

  // ----------------------------------------------------------------
  // 6. Sidebar navigation
  // ----------------------------------------------------------------
  const NAV_ITEMS = [
    { path: "/",          label: "Dashboard",  icon: "layout-dashboard" },
    { path: "/messages",  label: "Messages",   icon: "mail" },
    { path: "/queues",    label: "Queues",     icon: "layers" },
    { path: "/send",      label: "Send",       icon: "send" },
    { path: "/customers", label: "Customers",  icon: "users" },
    { path: "/keys",      label: "API keys",   icon: "key-round" },
    { path: "/webhooks",  label: "Webhooks",   icon: "webhook" },
    { path: "/reputation",label: "Reputation", icon: "shield" },
    { path: "/dns",       label: "DNS",        icon: "globe" },
    { path: "/ai",        label: "AI",         icon: "sparkles" },
    { path: "/settings",  label: "Settings",   icon: "settings" },
  ];

  function buildSidebar() {
    const nav = $("#sidebar-nav");
    if (!nav) return;
    nav.innerHTML = "";
    const group = ce("div", { class: "sidebar-section" });
    const title = ce("div", { class: "sidebar-section-title" }, "Workspace");
    group.appendChild(title);
    NAV_ITEMS.forEach((item) => {
      const a = ce("a", {
        class: "sidebar-item",
        href: "#" + item.path,
        "data-route": item.path,
        title: item.label,
      });
      a.innerHTML = [
        window.MTaIcons.icon(item.icon, 16),
        '<span class="sidebar-item-label">', escapeHTML(item.label), '</span>',
      ].join("");
      group.appendChild(a);
    });
    nav.appendChild(group);
    refreshIcons(nav);
  }

  // ----------------------------------------------------------------
  // 7. Router
  // ----------------------------------------------------------------
  function parseHash() {
    let h = (location.hash || "").replace(/^#/, "");
    if (!h) h = "/";
    if (!h.startsWith("/")) h = "/" + h;
    return h;
  }

  async function route(path, force) {
    if (!force && state.currentPath === path && path !== "/login") return;
    state.currentPath = path;

    // Login gate.
    if (!state.auth && path !== "/login") { location.hash = "#/login"; return; }
    if (state.auth && path === "/login")  { location.hash = "#/";      return; }

    // Hide sidebar on login.
    setChrome();
    setSidebarActive(path);

    // Render.
    let node = null;
    try {
      if (path === "/login") node = renderLogin();
      else if (path === "/") node = renderDashboard();
      else if (path === "/messages") node = renderMessages();
      else if (path === "/queues")   node = renderQueues();
      else if (path === "/send")     node = renderSend();
      else if (path === "/customers")node = renderCustomers();
      else if (path.indexOf("/customers/") === 0) {
        const id = decodeURIComponent(path.slice("/customers/".length));
        node = renderCustomerDetail(id);
      }
      else if (path === "/keys")     node = renderApiKeys();
      else if (path === "/webhooks") node = renderWebhooks();
      else if (path === "/reputation")node = renderReputation();
      else if (path === "/dns")      node = renderDns();
      else if (path === "/ai")       node = renderAi();
      else if (path === "/settings") node = renderSettings();
      else node = renderNotFound(path);
    } catch (e) {
      console.error("[route] failed", e);
      node = renderError(e);
    }
    mountPage(node);
  }

  function renderNotFound(path) {
    const wrap = ce("div", { class: "page-fade-in" });
    wrap.innerHTML = [
      '<div class="empty-state">',
        window.MTaIcons.icon("alert-circle", 32),
        '<h3>Page not found</h3>',
        '<p>The path <code class="text-mono">' + escapeHTML(path) + '</code> does not match any route.</p>',
        '<a class="btn btn-outline mt-4" href="#/">← Back to dashboard</a>',
      '</div>',
    ].join("");
    return wrap;
  }

  function renderError(err) {
    const wrap = ce("div", { class: "page-fade-in" });
    wrap.innerHTML = [
      '<div class="alert alert-destructive">',
        window.MTaIcons.icon("alert-triangle", 16),
        '<div><p class="alert-title">Failed to render page</p>',
          '<p class="alert-description">' + escapeHTML(err && err.message || "Unknown error") + '</p></div>',
      '</div>',
      '<div class="mt-4"><a class="btn btn-outline" href="#/">← Dashboard</a></div>',
    ].join("");
    return wrap;
  }

  // ----------------------------------------------------------------
  // 8. Page renderers
  // ----------------------------------------------------------------

  // LOGIN
  function renderLogin() {
    const node = cloneTpl("login");
    setTimeout(() => {
      const form = $("#form-login");
      const errBox = $("#login-error");
      const btn = form && form.querySelector("button[type=submit]");
      if (!form) return;
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        errBox.classList.add("hidden");
        const pin = $("#login-pin").value.trim();
        if (!pin) return;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Signing in…';
        try {
          const data = await api("POST", "/api/auth/login", { pin });
          saveToken(data.token);
          location.hash = "#/";
          toast({ kind: "success", title: "Welcome back", description: "Session created." });
        } catch (err) {
          errBox.textContent = err && err.message || "Sign-in failed";
          errBox.classList.remove("hidden");
          btn.disabled = false;
          btn.innerHTML = '<i data-lucide="log-in"></i> Sign in';
          refreshIcons(btn);
        }
      });
      refreshIcons(form);
      $("#login-pin").focus();
    }, 0);
    return node;
  }

  // DASHBOARD
  function renderDashboard() {
    const node = cloneTpl("dashboard");
    setTimeout(async () => {
      $("#dash-engine-status").textContent = "Engine: …";
      try {
        const h = await api("GET", "/api/engine/health");
        const ok = h && h.status === "ok" && h.engine !== false;
        const badge = $("#dash-engine-status");
        badge.textContent = "Engine: " + (ok ? "online" : "offline");
        badge.classList.toggle("badge-success", !!ok);
        badge.classList.toggle("badge-destructive", !ok);
        $("#status-engine").textContent = ok ? "online" : "offline";
      } catch {
        $("#dash-engine-status").textContent = "Engine: offline";
        $("#dash-engine-status").classList.add("badge-destructive");
        $("#status-engine").textContent = "offline";
      }

      try {
        const metrics = await api("GET", "/api/engine/metrics", null, { headers: { Accept: "text/plain" } }).catch(() => null);
        const txt = typeof metrics === "string" ? metrics : (metrics && metrics.text) ? metrics.text : JSON.stringify(metrics, null, 2);
        $("#dash-metrics").textContent = (txt || "").slice(0, 2000) || "(no metrics returned)";
      } catch { $("#dash-metrics").textContent = "(failed to load metrics)"; }

      try {
        const tenants = await api("GET", "/api/v1/credits/tenants").catch(() => []);
        $("#stat-tenants").textContent = fmtNum(tenants.length);
        const wrap = $("#dash-credits");
        if (wrap) {
          if (!tenants || !tenants.length) {
            wrap.innerHTML = '<div class="empty-state"><p>No tenants yet.</p></div>';
          } else {
            const top = tenants.slice(0, 5).map((t) => {
              const name = t.display_name || t.name || t.id || t.tenant_id || "?";
              return '<tr><td>' + escapeHTML(name) + '</td>'
                + '<td class="text-mono text-right">' + fmtNum(t.balance) + '</td>'
                + '<td class="text-right"><a href="#/customers/' + encodeURIComponent(t.tenant_id || t.id) + '" class="btn btn-link">Open</a></td></tr>';
            }).join("");
            wrap.innerHTML = '<div class="table-wrap" style="border:0"><table class="table table-compact">'
              + '<thead><tr><th>Tenant</th><th class="text-right">Balance</th><th></th></tr></thead>'
              + '<tbody>' + top + '</tbody></table></div>';
          }
        }
      } catch { $("#stat-tenants").textContent = "—"; }

      try {
        const q = await api("GET", "/api/engine/ready-q-states").catch(() => ({}));
        const wrap = $("#dash-queues");
        const entries = q && typeof q === "object" ? Object.entries(q) : [];
        if (!entries.length) {
          wrap.innerHTML = '<div class="empty-state"><p>No active queues.</p></div>';
        } else {
          wrap.innerHTML = '<div class="stack-2">'
            + entries.slice(0, 8).map(([k, v]) => {
                const depth = (v && (v.size != null ? v.size : (v.depth != null ? v.depth : v.count))) || 0;
                const age = (v && (v.oldest_age_seconds != null ? v.oldest_age_seconds : 0)) || 0;
                return '<div class="flex items-center justify-between">'
                  + '<div><div class="text-sm text-bold">' + escapeHTML(k) + '</div>'
                  + '<div class="text-xs text-muted">oldest: ' + fmtNum(age) + 's</div></div>'
                  + '<div class="stat-value" style="font-size:var(--text-lg)">' + fmtNum(depth) + '</div>'
                  + '</div>';
              }).join("")
            + '</div>';
        }
      } catch { /* keep loading */ }

      try {
        const rec = state.cache.dash_recent || [];
        const wrap = $("#dash-recent");
        if (!rec.length) {
          wrap.innerHTML = '<div class="empty-state"><p>No recent messages yet.</p></div>';
        } else {
          wrap.innerHTML = rec.slice(0, 12).map((m) => liveLine(m)).join("");
        }
      } catch {}

      try {
        const stats = await api("GET", "/api/v1/credits/stats").catch(() => null);
        if (stats) {
          const tot = (stats.delivered || stats.sent || 0);
          const queued = (stats.queued || 0);
          const bounces = (stats.bounced || 0);
          $("#stat-messages").textContent = fmtNum(queued + tot);
          $("#stat-delivered").textContent = fmtNum(tot);
          const rate = tot > 0 ? (bounces / tot * 100) : 0;
          $("#stat-bounce").textContent = rate.toFixed(2) + "%";
        }
      } catch { /* keep dashes */ }

      // Refresh button
      const refresh = document.querySelector('[data-action="refresh"]');
      if (refresh) refresh.addEventListener("click", () => route("/", true));

      refreshIcons($("#app"));
    }, 0);
    return node;
  }

  // MESSAGES (live tail from SSE)
  function renderMessages() {
    const node = cloneTpl("messages");
    let paused = false;
    let rows = [];
    const MAX = 200;

    setTimeout(() => {
      const tbody = $("#messages-tbody");
      const countEl = $("#messages-count");
      const label = $("#messages-stream-label");

      function renderRows() {
        if (!tbody) return;
        if (!rows.length) {
          tbody.innerHTML = '<tr><td colspan="7" class="empty-state"><p>Waiting for messages…</p></td></tr>';
        } else {
          tbody.innerHTML = rows.map((m) => msgRow(m)).join("");
        }
        if (countEl) countEl.textContent = rows.length + " events";
        refreshIcons(tbody);
      }

      function pushRow(m) {
        if (paused) return;
        rows.unshift(m);
        if (rows.length > MAX) rows.length = MAX;
        renderRows();
      }

      function connect() {
        if (state.sse) { try { state.sse.close(); } catch {} state.sse = null; }
        if (paused) return;
        try {
          const es = new EventSource("/api/live/stream");
          state.sse = es;
          es.addEventListener("open", () => {
            state.sseRetries = 0;
            const t = tbody.querySelector(".empty-state");
            if (t) t.textContent = "Stream connected · waiting for events…";
          });
          const handler = (e) => {
            if (!e.data) return;
            try {
              const obj = JSON.parse(e.data);
              const norm = normalizeEvent(obj);
              if (norm) pushRow(norm);
            } catch {}
          };
          ["message", "delivery", "queue", "status", "stats"].forEach((n) => es.addEventListener(n, handler));
          es.onerror = () => {
            es.close();
            state.sse = null;
            if (state.sseRetries++ > 5) return;
            const backoff = Math.min(20000, 1000 * Math.pow(2, state.sseRetries));
            setTimeout(connect, backoff);
          };
        } catch (err) {
          console.warn("[messages] SSE failed", err);
          setTimeout(connect, 4000);
        }
      }

      const toggleBtn = document.querySelector('[data-action="toggle-stream"]');
      const clearBtn  = document.querySelector('[data-action="clear"]');
      if (toggleBtn) toggleBtn.addEventListener("click", () => {
        paused = !paused;
        if (label) label.textContent = paused ? "Resume" : "Pause";
        const ic = toggleBtn.querySelector("[data-lucide]");
        if (ic) {
          ic.setAttribute("data-lucide", paused ? "play" : "pause");
          refreshIcons(toggleBtn);
        }
        if (!paused) connect();
        else if (state.sse) { try { state.sse.close(); } catch {} state.sse = null; }
      });
      if (clearBtn) clearBtn.addEventListener("click", () => { rows = []; renderRows(); });

      // Also seed from non-SSE list if available.
      api("GET", "/api/v1/messages?limit=50").then((list) => {
        if (Array.isArray(list)) {
          rows = list.slice(0, MAX).map(normalizeEvent).filter(Boolean);
          renderRows();
        }
      }).catch(() => {});

      renderRows();
      connect();
      refreshIcons($("#app"));
    }, 0);
    return node;
  }

  function normalizeEvent(e) {
    if (!e) return null;
    const type = e.type || e.event || e.kind || (e.message ? "message" : "status");
    return {
      time: e.time || e.timestamp || e.ts || new Date().toISOString(),
      type,
      status: e.status || e.outcome || (type === "bounce" ? "bounced" : type),
      recipient: e.recipient || e.to || e.recipient_address || "",
      subject: e.subject || (e.content && e.content.subject) || "",
      queue: e.queue || e.queue_name || "",
      tenant: e.tenant || e.tenant_id || "",
      id: e.id || e.message_id || e.spool_id || e.event_id || "",
    };
  }

  function liveLine(m) {
    const status = (m.status || m.type || "").toString().toLowerCase();
    const cls = status.indexOf("deliver") !== -1 || status.indexOf("sent") !== -1
      ? "live-status-success"
      : status.indexOf("fail") !== -1 || status.indexOf("bounce") !== -1 || status.indexOf("reject") !== -1
      ? "live-status-error"
      : "live-status-pending";
    return '<div class="live-line">'
      + '<span class="live-time">' + escapeHTML(fmtShortTime(m.time)) + '</span>'
      + '<span class="live-status ' + cls + '">' + escapeHTML(m.status || m.type || "—") + '</span>'
      + '<span class="live-text">' + escapeHTML(m.recipient || m.subject || m.id || "—") + '</span>'
      + '</div>';
  }

  function msgRow(m) {
    const status = (m.status || m.type || "").toString().toLowerCase();
    const cls = status.indexOf("deliver") !== -1 || status.indexOf("sent") !== -1
      ? "badge-success"
      : status.indexOf("fail") !== -1 || status.indexOf("bounce") !== -1 || status.indexOf("reject") !== -1
      ? "badge-destructive"
      : "badge-warning";
    return '<tr>'
      + '<td>' + escapeHTML(fmtShortTime(m.time)) + '</td>'
      + '<td><span class="badge ' + cls + '">' + escapeHTML(m.status || m.type || "—") + '</span></td>'
      + '<td>' + escapeHTML(m.recipient || "—") + '</td>'
      + '<td>' + escapeHTML(m.subject || "—") + '</td>'
      + '<td>' + escapeHTML(m.queue || "—") + '</td>'
      + '<td>' + escapeHTML(m.tenant || "—") + '</td>'
      + '<td class="text-mono text-xs">' + escapeHTML((m.id || "").toString().slice(0, 12)) + '</td>'
      + '</tr>';
  }

  // QUEUES
  function renderQueues() {
    const node = cloneTpl("queues");
    setTimeout(async () => {
      const grid = $("#queues-grid");
      grid.innerHTML = '<div class="loading-block"><div class="spinner"></div> Loading queue states…</div>';
      try {
        const q = await api("GET", "/api/engine/ready-q-states").catch(() => ({}));
        const entries = q && typeof q === "object" ? Object.entries(q) : [];
        if (!entries.length) {
          grid.innerHTML = '<div class="empty-state"><p>No active queues detected.</p></div>';
          return;
        }
        grid.innerHTML = entries.map(([k, v]) => {
          const depth = (v && (v.size != null ? v.size : v.depth != null ? v.depth : v.count)) || 0;
          const age = (v && (v.oldest_age_seconds != null ? v.oldest_age_seconds : 0)) || 0;
          return '<div class="card card-compact">'
            + '<div class="card-header"><h3 class="card-title">' + escapeHTML(k) + '</h3></div>'
            + '<div class="card-content">'
            + '<div class="stat-tile">'
            + '<div class="stat-value">' + fmtNum(depth) + '</div>'
            + '<div class="stat-label">messages in queue</div>'
            + '<div class="stat-trend">' + (age > 0 ? 'oldest: ' + fmtNum(age) + 's' : 'idle') + '</div>'
            + '</div>'
            + '</div>'
            + '</div>';
        }).join("");
      } catch (e) {
        grid.innerHTML = '<div class="empty-state"><p>Failed to load queues: ' + escapeHTML(e.message) + '</p></div>';
      }
      const refresh = document.querySelector('[data-action="refresh"]');
      if (refresh) refresh.addEventListener("click", () => route("/queues", true));
      refreshIcons($("#app"));
    }, 0);
    return node;
  }

  // SEND
  function renderSend() {
    const node = cloneTpl("send");
    setTimeout(async () => {
      // Prefill tenant if only one tenant is known.
      try {
        const list = await api("GET", "/api/v1/credits/tenants").catch(() => []);
        if (Array.isArray(list) && list.length === 1) {
          const t = list[0];
          const tnEl = $("#send-tenant");
          if (tnEl) tnEl.value = t.tenant_id || t.id || "";
        }
      } catch {}

      const form = $("#form-send");
      if (!form) return;
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const body = {
          tenant: $("#send-tenant").value.trim(),
          from:   $("#send-from").value.trim(),
          to:     $("#send-to").value.trim(),
          subject:$("#send-subject").value.trim(),
          body:   $("#send-body").value,
        };
        const okBox  = $("#send-ok");
        const errBox = $("#send-err");
        okBox.classList.add("hidden");
        errBox.classList.add("hidden");
        const submit = form.querySelector('button[type=submit]');
        submit.disabled = true;
        submit.innerHTML = '<span class="spinner"></span> Sending…';
        try {
          const r = await api("POST", "/api/v1/send", body);
          okBox.textContent = "Sent! " + (r && r.message_id ? "message_id=" + r.message_id : "");
          okBox.classList.remove("hidden");
          toast({ kind: "success", title: "Message dispatched", description: "Into the queue." });
        } catch (err) {
          errBox.textContent = err.message || "Failed to send";
          errBox.classList.remove("hidden");
          toast({ kind: "error", title: "Send failed", description: err.message });
        } finally {
          submit.disabled = false;
          submit.innerHTML = '<i data-lucide="send"></i> Dispatch';
          refreshIcons(submit);
        }
      });
      refreshIcons($("#app"));
    }, 0);
    return node;
  }

  // CUSTOMERS
  function renderCustomers() {
    const node = cloneTpl("customers");
    setTimeout(async () => {
      const tbody = $("#customers-tbody");
      tbody.innerHTML = '<tr><td colspan="6" class="loading-block"><div class="spinner"></div> Loading tenants…</td></tr>';
      try {
        const list = await api("GET", "/api/v1/credits/tenants").catch(() => []);
        if (!list || !list.length) {
          tbody.innerHTML = '<tr><td colspan="6" class="empty-state"><p>No tenants yet.</p>'
            + '<p class="text-xs">Use the "New tenant" button or POST <code>/api/v1/credits/tenants</code> to create one.</p></td></tr>';
          return;
        }
        tbody.innerHTML = list.map((t) => {
          const id = t.tenant_id || t.id || "?";
          const name = t.display_name || t.name || id;
          const balance = (t.balance != null ? t.balance : t.credits);
          const sent = (t.sent || t.messages_sent || 0);
          const status = t.status || (t.active === false ? "suspended" : "active");
          const idEnc = encodeURIComponent(id);
          return '<tr>'
            + '<td class="text-mono text-xs">' + escapeHTML(id) + '</td>'
            + '<td>' + escapeHTML(name) + '</td>'
            + '<td class="text-mono text-right">' + fmtNum(balance) + '</td>'
            + '<td class="text-mono text-right">' + fmtNum(sent) + '</td>'
            + '<td><span class="badge ' + (status === "active" ? "badge-success" : "badge-warning") + '">' + escapeHTML(status) + '</span></td>'
            + '<td class="text-right"><a class="btn btn-link" href="#/customers/' + idEnc + '">Open</a></td>'
            + '</tr>';
        }).join("");
      } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-state"><p>Failed to load: ' + escapeHTML(e.message) + '</p></td></tr>';
      }
      const refresh = document.querySelector('[data-action="refresh"]');
      if (refresh) refresh.addEventListener("click", () => route("/customers", true));
      refreshIcons($("#app"));
    }, 0);
    return node;
  }

  function renderCustomerDetail(id) {
    const node = cloneTpl("customer-detail");
    setTimeout(async () => {
      $("#cd-title").textContent = "Tenant " + id;
      $("#cd-sub").textContent = "Loading…";
      try {
        const t = await api("GET", "/api/v1/credits/tenants/" + encodeURIComponent(id)).catch(() => null);
        if (t) {
          $("#cd-title").textContent = "Tenant " + (t.tenant_id || t.id || id);
          $("#cd-sub").textContent = t.display_name || t.name || "";
          $("#cd-balance").textContent = fmtNum(t.balance != null ? t.balance : t.credits);
          $("#cd-sent-today").textContent = fmtNum(t.sent_today != null ? t.sent_today : t.sent || 0);
          $("#cd-status").textContent = t.status || "active";
        }
      } catch (e) { $("#cd-sub").textContent = "Error: " + e.message; }

      try {
        const usage = await api("GET", "/api/v1/credits/tenants/" + encodeURIComponent(id) + "/usage?limit=20").catch(() => []);
        const wrap = $("#cd-usage");
        if (!usage || !usage.length) {
          wrap.innerHTML = '<div class="empty-state"><p>No recent usage events.</p></div>';
        } else {
          wrap.innerHTML = usage.map((u) => liveLine({
            time: u.time || u.ts || u.timestamp,
            status: u.event || u.kind || "sent",
            recipient: u.subject || u.message || u.event,
          })).join("");
        }
      } catch { /* keep */ }

      const refresh = document.querySelector('[data-action="refresh"]');
      if (refresh) refresh.addEventListener("click", () => route(state.currentPath, true));
      refreshIcons($("#app"));
    }, 0);
    return node;
  }

  // API KEYS
  function renderApiKeys() {
    const node = cloneTpl("apikeys");
    setTimeout(async () => {
      const tbody = $("#apikeys-tbody");
      tbody.innerHTML = '<tr><td colspan="5" class="loading-block"><div class="spinner"></div> Loading…</td></tr>';
      try {
        const list = await api("GET", "/api/v1/smtp-users?include_inactive=true").catch(() => []);
        if (!list || !list.length) {
          tbody.innerHTML = '<tr><td colspan="5" class="empty-state"><p>No SMTP users yet.</p></td></tr>';
          return;
        }
        tbody.innerHTML = list.map((u) => {
          const active = u.active !== false && u.status !== "revoked";
          return '<tr>'
            + '<td>' + escapeHTML(u.name || "—") + '</td>'
            + '<td class="text-mono">' + escapeHTML(u.username || "—") + '</td>'
            + '<td class="text-mono text-xs">' + escapeHTML(u.tenant_id || "—") + '</td>'
            + '<td><span class="badge ' + (active ? "badge-success" : "badge-warning") + '">' + (active ? "active" : "revoked") + '</span></td>'
            + '<td class="text-right"><button class="btn btn-outline btn-sm" data-rotate="' + escapeHTML(u.id) + '"><i data-lucide="rotate-ccw"></i> Rotate</button></td>'
            + '</tr>';
        }).join("");
        $$('[data-rotate]', tbody).forEach((btn) => {
          btn.addEventListener("click", async () => {
            const uid = btn.getAttribute("data-rotate");
            try {
              const r = await api("POST", "/api/v1/smtp-users/" + encodeURIComponent(uid) + "/rotate");
              toast({ kind: "success", title: "Password rotated", description: "Copy and store the new password." });
              alert("New password (one-time view):\n\n" + (r.password || "(not returned)"));
            } catch (err) {
              toast({ kind: "error", title: "Rotate failed", description: err.message });
            }
          });
        });
      } catch (e) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-state"><p>Failed to load: ' + escapeHTML(e.message) + '</p></td></tr>';
      }
      const refresh = document.querySelector('[data-action="refresh"]');
      if (refresh) refresh.addEventListener("click", () => route("/keys", true));
      refreshIcons($("#app"));
    }, 0);
    return node;
  }

  // WEBHOOKS
  function renderWebhooks() {
    const node = cloneTpl("webhooks");
    setTimeout(async () => {
      try {
        const hooks = await api("GET", "/api/v1/webhooks").catch(() => []);
        const tbody = $("#webhooks-tbody");
        if (!hooks || !hooks.length) {
          tbody.innerHTML = '<tr><td colspan="4" class="empty-state"><p>No webhooks registered.</p></td></tr>';
        } else {
          tbody.innerHTML = hooks.map((h) => '<tr>'
            + '<td class="text-mono text-xs">' + escapeHTML(h.url || "—") + '</td>'
            + '<td>' + escapeHTML((h.events || []).join(", ")) + '</td>'
            + '<td class="text-mono text-xs">' + escapeHTML(h.tenant_id || "—") + '</td>'
            + '<td><span class="badge ' + (h.active === false ? "badge-warning" : "badge-success") + '">' + (h.active === false ? "paused" : "active") + '</span></td>'
            + '</tr>').join("");
        }
      } catch {
        $("#webhooks-tbody").innerHTML = '<tr><td colspan="4" class="empty-state"><p>Failed to load webhooks.</p></td></tr>';
      }

      try {
        const deliveries = await api("GET", "/api/v1/webhooks/deliveries?limit=20").catch(() => []);
        const tbd = $("#webhook-deliveries-tbody");
        if (!deliveries || !deliveries.length) {
          tbd.innerHTML = '<tr><td colspan="4" class="empty-state"><p>No recent deliveries.</p></td></tr>';
        } else {
          tbd.innerHTML = deliveries.map((d) => '<tr>'
            + '<td>' + escapeHTML(fmtShortTime(d.time || d.ts)) + '</td>'
            + '<td class="text-mono text-xs">' + escapeHTML(d.url || "—") + '</td>'
            + '<td><span class="badge ' + ((d.response_code >= 200 && d.response_code < 300) ? "badge-success" : "badge-destructive") + '">' + escapeHTML(String(d.response_code || "—")) + '</span></td>'
            + '<td class="text-mono text-xs">' + escapeHTML(String(d.latency_ms != null ? d.latency_ms + "ms" : "—")) + '</td>'
            + '</tr>').join("");
        }
      } catch {
        $("#webhook-deliveries-tbody").innerHTML = '<tr><td colspan="4" class="empty-state"><p>Failed to load deliveries.</p></td></tr>';
      }

      const refresh = document.querySelector('[data-action="refresh"]');
      if (refresh) refresh.addEventListener("click", () => route("/webhooks", true));
      refreshIcons($("#app"));
    }, 0);
    return node;
  }

  // REPUTATION
  function renderReputation() {
    const node = cloneTpl("reputation");
    setTimeout(() => {
      $("#form-dbl").addEventListener("submit", async (e) => {
        e.preventDefault();
        const out = $("#dbl-result");
        const domain = $("#dbl-domain").value.trim();
        out.innerHTML = '<div class="loading-block"><div class="spinner"></div> Checking ' + escapeHTML(domain) + '…</div>';
        try {
          const r = await api("GET", "/api/reputation/dbl/" + encodeURIComponent(domain));
          out.innerHTML = renderRepResult("DBL", domain, r);
          refreshIcons(out);
        } catch (err) {
          out.innerHTML = '<div class="alert alert-destructive"><p class="alert-title">Failed</p><p>' + escapeHTML(err.message) + '</p></div>';
        }
      });
      $("#form-zen").addEventListener("submit", async (e) => {
        e.preventDefault();
        const out = $("#zen-result");
        const ip = $("#zen-ip").value.trim();
        out.innerHTML = '<div class="loading-block"><div class="spinner"></div> Checking ' + escapeHTML(ip) + '…</div>';
        try {
          const r = await api("GET", "/api/reputation/zen/" + encodeURIComponent(ip));
          out.innerHTML = renderRepResult("ZEN", ip, r);
          refreshIcons(out);
        } catch (err) {
          out.innerHTML = '<div class="alert alert-destructive"><p class="alert-title">Failed</p><p>' + escapeHTML(err.message) + '</p></div>';
        }
      });
      $("#form-surbl").addEventListener("submit", async (e) => {
        e.preventDefault();
        const out = $("#surbl-result");
        const u = $("#surbl-url").value.trim();
        out.innerHTML = '<div class="loading-block"><div class="spinner"></div> Checking URL…</div>';
        try {
          const r = await api("GET", "/api/reputation/surbl?url=" + encodeURIComponent(u));
          out.innerHTML = renderRepResult("SURBL", u, r);
          refreshIcons(out);
        } catch (err) {
          out.innerHTML = '<div class="alert alert-destructive"><p class="alert-title">Failed</p><p>' + escapeHTML(err.message) + '</p></div>';
        }
      });
      refreshIcons($("#app"));
    }, 0);
    return node;
  }

  function renderRepResult(kind, q, r) {
    const listed = !!(r && (r.listed === true || r.listed === "yes" || (r.lists && r.lists.length)));
    const status = listed
      ? '<span class="badge badge-destructive"><i data-lucide="shield-alert"></i> Listed</span>'
      : '<span class="badge badge-success"><i data-lucide="shield-check"></i> Clean</span>';
    let body = '';
    if (r && r.lists && r.lists.length) body = '<ul class="mt-3 stack-2" style="padding-left:18px">' + r.lists.map(l => '<li class="text-mono text-xs">' + escapeHTML(JSON.stringify(l)) + '</li>').join("") + '</ul>';
    return '<div class="alert ' + (listed ? "alert-destructive" : "alert-success") + '">'
      + '<div class="alert-title">' + kind + ' · ' + escapeHTML(q) + '</div>'
      + '<p class="alert-description">' + status + body + '</p>'
      + '</div>';
  }

  // DNS
  function renderDns() {
    const node = cloneTpl("dns");
    setTimeout(async () => {
      try {
        const zones = await api("GET", "/api/cf/zones").catch(() => []);
        const wrap = $("#dns-zones");
        if (!zones || !zones.length) {
          wrap.innerHTML = '<div class="empty-state"><p>No Cloudflare zones linked.</p></div>';
        } else {
          wrap.innerHTML = '<ul class="stack-2">' + zones.slice(0, 20).map((z) =>
            '<li class="flex items-center justify-between text-sm">'
            + '<span class="text-bold">' + escapeHTML(z.name || z.zone || z.id) + '</span>'
            + '<span class="text-xs text-muted">' + escapeHTML(z.status || z.plan || "active") + '</span>'
            + '</li>').join("") + '</ul>';
        }
      } catch {
        $("#dns-zones").innerHTML = '<div class="empty-state"><p>Cloudflare not configured.</p></div>';
      }

      $("#form-dns-lookup").addEventListener("submit", async (e) => {
        e.preventDefault();
        const out = $("#dns-lookup-result");
        const host = $("#dns-host").value.trim();
        out.innerHTML = '<div class="loading-block"><div class="spinner"></div> Looking up…</div>';
        try {
          const r = await api("GET", "/api/cf/dns-lookup?host=" + encodeURIComponent(host));
          out.innerHTML = '<pre class="code-block">' + escapeHTML(JSON.stringify(r, null, 2)) + '</pre>';
        } catch (err) {
          out.innerHTML = '<div class="alert alert-destructive"><p class="alert-title">Failed</p><p>' + escapeHTML(err.message) + '</p></div>';
        }
      });
      $("#form-dkim").addEventListener("submit", async (e) => {
        e.preventDefault();
        const out = $("#dkim-result");
        const d  = $("#dkim-domain").value.trim();
        const s  = $("#dkim-selector").value.trim();
        out.innerHTML = '<div class="loading-block"><div class="spinner"></div> Generating…</div>';
        try {
          const r = await api("POST", "/api/cf/dkim", { domain: d, selector: s });
          out.innerHTML = '<pre class="code-block">' + escapeHTML(JSON.stringify(r, null, 2)) + '</pre>';
        } catch (err) {
          out.innerHTML = '<div class="alert alert-destructive"><p class="alert-title">Failed</p><p>' + escapeHTML(err.message) + '</p></div>';
        }
      });

      const refresh = document.querySelector('[data-action="refresh"]');
      if (refresh) refresh.addEventListener("click", () => route("/dns", true));
      refreshIcons($("#app"));
    }, 0);
    return node;
  }

  // AI
  function renderAi() {
    const node = cloneTpl("ai");
    setTimeout(async () => {
      const out = $("#ai-output");
      const insights = $("#ai-insights");
      $("#form-ai-chat").addEventListener("submit", async (e) => {
        e.preventDefault();
        const prompt = $("#ai-prompt").value.trim();
        if (!prompt) return;
        out.innerHTML = '<div class="loading-block"><div class="spinner"></div> Thinking…</div>';
        try {
          const r = await api("POST", "/api/ai/chat", { prompt });
          out.innerHTML = '<div class="alert alert-success">'
            + '<div class="alert-title">Assistant</div>'
            + '<p class="alert-description" style="white-space:pre-wrap">' + escapeHTML((r && (r.text || r.answer || JSON.stringify(r))) || "—") + '</p>'
            + '</div>';
        } catch (err) {
          out.innerHTML = '<div class="alert alert-destructive"><p class="alert-title">Failed</p><p>' + escapeHTML(err.message) + '</p></div>';
        }
      });
      const golden = document.querySelector('[data-action="golden"]');
      if (golden) golden.addEventListener("click", async () => {
        out.innerHTML = '<div class="loading-block"><div class="spinner"></div> Generating…</div>';
        try {
          const r = await api("GET", "/api/ai/golden-qa");
          out.innerHTML = '<div class="alert alert-success"><div class="alert-title">Golden Q&amp;A</div>'
            + '<pre class="code-block">' + escapeHTML(JSON.stringify(r, null, 2)) + '</pre></div>';
        } catch (err) {
          out.innerHTML = '<div class="alert alert-destructive"><p class="alert-title">Failed</p><p>' + escapeHTML(err.message) + '</p></div>';
        }
      });
      const refresh = document.querySelector('button[data-action="refresh"]');
      if (refresh) refresh.addEventListener("click", () => route("/ai", true));
      const diagnoseBtn = document.querySelector('button[data-action="diagnose"]');
      if (diagnoseBtn) diagnoseBtn.addEventListener("click", async () => {
        insights.innerHTML = '<div class="loading-block"><div class="spinner"></div> Diagnosing…</div>';
        try {
          const r = await api("GET", "/api/ai/diagnose");
          insights.innerHTML = '<pre class="code-block">' + escapeHTML(JSON.stringify(r, null, 2)) + '</pre>';
        } catch (err) {
          insights.innerHTML = '<div class="alert alert-destructive"><p class="alert-title">Failed</p><p>' + escapeHTML(err.message) + '</p></div>';
        }
      });

      // Initial insights load.
      try {
        const r = await api("GET", "/api/ai/insights");
        if (!r || (Array.isArray(r) && !r.length)) {
          insights.innerHTML = '<div class="empty-state"><p>No insights yet — traffic is too low.</p></div>';
        } else if (Array.isArray(r)) {
          insights.innerHTML = '<ul class="stack-2">' + r.map((i) =>
            '<li class="alert alert-info"><div class="alert-title">' + escapeHTML(i.title || i.kind || "Insight") + '</div>'
            + '<p class="alert-description">' + escapeHTML(i.description || i.text || JSON.stringify(i)) + '</p></li>').join("") + '</ul>';
        } else {
          insights.innerHTML = '<pre class="code-block">' + escapeHTML(JSON.stringify(r, null, 2)) + '</pre>';
        }
      } catch (err) {
        insights.innerHTML = '<div class="alert alert-warning"><p class="alert-title">No insights</p><p class="alert-description">' + escapeHTML(err.message) + '</p></div>';
      }
      refreshIcons($("#app"));
    }, 0);
    return node;
  }

  // SETTINGS
  function renderSettings() {
    const node = cloneTpl("settings");
    setTimeout(async () => {
      const tbody = $("#settings-files-tbody");
      try {
        const files = await api("GET", "/api/config/files");
        const list = Array.isArray(files) ? files : (files && files.files) || [];
        if (!list.length) {
          tbody.innerHTML = '<tr><td colspan="4" class="empty-state"><p>No config files exposed.</p></td></tr>';
        } else {
          tbody.innerHTML = list.map((f) => {
            const name = f.name || f.path || "?";
            const fmt = f.format || f.type || "toml";
            const size = f.size != null ? f.size : (f.content ? f.content.length : 0);
            return '<tr><td class="text-mono text-xs">' + escapeHTML(name) + '</td>'
              + '<td>' + escapeHTML(fmt) + '</td>'
              + '<td class="text-right">' + fmtNum(size) + '</td>'
              + '<td class="text-right"><button class="btn btn-link text-xs" data-view="' + escapeHTML(name) + '">View</button></td></tr>';
          }).join("");
        }
      } catch (e) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty-state"><p>Failed to load: ' + escapeHTML(e.message) + '</p></td></tr>';
      }
      // Theme buttons
      $$('[data-action^="theme-"]').forEach((b) => {
        b.addEventListener("click", () => {
          const mode = b.getAttribute("data-action").slice(6);
          if (mode === "auto") {
            try { localStorage.removeItem(THEME_KEY); } catch {}
            applyTheme(window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
          } else applyTheme(mode);
        });
      });
      // Compact toggle
      const compact = $("#settings-compact");
      if (compact) {
        compact.checked = state.compact;
        compact.addEventListener("change", () => applyCompact(compact.checked));
      }
      // PIN change
      const changeBtn = document.querySelector('[data-action="change-pin"]');
      if (changeBtn) changeBtn.addEventListener("click", async () => {
        const pin = $("#settings-pin-new").value;
        const okBox = $("#settings-pin-ok");
        const errBox = $("#settings-pin-err");
        okBox.classList.add("hidden");
        errBox.classList.add("hidden");
        if (!pin || pin.length < 4) {
          errBox.textContent = "PIN must be at least 4 digits.";
          errBox.classList.remove("hidden");
          return;
        }
        try {
          await api("POST", "/api/auth/change-pin", { pin });
          okBox.textContent = "PIN updated.";
          okBox.classList.remove("hidden");
          $("#settings-pin-new").value = "";
          toast({ kind: "success", title: "PIN updated" });
        } catch (e) {
          errBox.textContent = e.message;
          errBox.classList.remove("hidden");
        }
      });
      refreshIcons($("#app"));
    }, 0);
    return node;
  }

  // ----------------------------------------------------------------
  // 9. Command palette (Cmd+K)
  // ----------------------------------------------------------------
  function openCommandPalette() {
    const host = $("#command-host");
    if (!host) return;
    const items = NAV_ITEMS.map((it) => ({
      kind: "route",
      icon: it.icon,
      label: it.label,
      sub: "Go to " + it.path,
      run: () => { location.hash = "#" + it.path; },
    })).concat([
      { kind: "act", icon: "moon", label: "Toggle theme", sub: "light / dark", run: () => applyTheme(state.theme === "dark" ? "light" : "dark") },
      { kind: "act", icon: "rotate-cw", label: "Refresh current page", sub: "re-render", run: () => route(state.currentPath, true) },
      { kind: "act", icon: "log-out", label: "Sign out", sub: "revoke session", run: async () => {
          try { await api("POST", "/api/auth/logout"); } catch {}
          saveToken(null);
          location.hash = "#/login";
        } },
      { kind: "act", icon: "sparkles", label: "Open AI assistant", sub: "/ai", run: () => { location.hash = "#/ai"; } },
      { kind: "act", icon: "send", label: "Compose new message", sub: "/send", run: () => { location.hash = "#/send"; } },
      { kind: "act", icon: "key-round", label: "Manage API keys", sub: "/keys", run: () => { location.hash = "#/keys"; } },
    ]);

    host.hidden = false;
    host.innerHTML = "";
    const overlay = ce("div", { class: "command-overlay", role: "dialog", "aria-modal": "true" });
    const box = ce("div", { class: "command" });

    let q = "";
    let activeIdx = 0;
    let filtered = items;

    function render() {
      const list = filtered.length ? filtered.map((it, i) =>
        '<div class="command-item" data-idx="' + i + '" data-selected="' + (i === activeIdx ? "true" : "false") + '">'
        + window.MTaIcons.icon(it.icon, 16)
        + '<span>' + escapeHTML(it.label) + '</span>'
        + '<span class="command-item-meta">' + escapeHTML(it.sub || "") + '</span>'
        + '</div>').join("") : '<div class="command-empty">No matches.</div>';
      box.innerHTML = [
        '<input class="command-input" id="cmd-input" placeholder="Type a command or search…" autocomplete="off" />',
        '<div class="command-list">' + list + '</div>',
        '<div class="command-footer">',
          '<span><kbd class="command-kbd">↑↓</kbd> navigate</span>',
          '<span><kbd class="command-kbd">↵</kbd> select</span>',
          '<span><kbd class="command-kbd">esc</kbd> close</span>',
          '<span class="text-muted" style="margin-left:auto">' + filtered.length + ' results</span>',
        '</div>',
      ].join("");
    }

    overlay.appendChild(box);
    host.appendChild(overlay);
    refreshIcons(box);
    const input = box.querySelector("#cmd-input");
    const list = box.querySelector(".command-list");
    if (input) input.focus();

    function refreshFiltered() {
      const term = q.toLowerCase().trim();
      filtered = term ? items.filter((it) => (it.label + " " + (it.sub || "")).toLowerCase().indexOf(term) !== -1) : items.slice();
      activeIdx = 0;
      render();
      refreshIcons(box);
    }

    function updateSelection() {
      $$(".command-item", list).forEach((el, i) => el.setAttribute("data-selected", String(i === activeIdx)));
      const sel = box.querySelector('.command-item[data-selected="true"]');
      if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: "nearest" });
    }

    function close() {
      host.hidden = true;
      host.innerHTML = "";
      document.removeEventListener("keydown", onKey, true);
    }
    function runIdx(i) {
      const it = filtered[i];
      if (!it) return;
      close();
      try { it.run(); } catch (e) { toast({ kind: "error", title: "Command failed", description: e.message }); }
    }

    function onKey(e) {
      if (!host || host.hidden) return;
      if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); activeIdx = Math.min(filtered.length - 1, activeIdx + 1); updateSelection(); }
      else if (e.key === "ArrowUp")   { e.preventDefault(); activeIdx = Math.max(0, activeIdx - 1); updateSelection(); }
      else if (e.key === "Enter")     { e.preventDefault(); runIdx(activeIdx); }
      else if (e.key === "Tab")       {
        // don't let tab escape.
        e.preventDefault();
      }
    }
    document.addEventListener("keydown", onKey, true);

    // Click outside closes.
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    // Delegate clicks inside list.
    list.addEventListener("click", (e) => {
      const el = e.target.closest(".command-item");
      if (!el) return;
      runIdx(Number(el.getAttribute("data-idx")));
    });
    input.addEventListener("input", (e) => { q = e.target.value || ""; refreshFiltered(); });

    render();
    refreshIcons(box);
  }

  // ----------------------------------------------------------------
  // 10. Wire up global chrome
  // ----------------------------------------------------------------
  function wireTopbar() {
    const cmdBtn = $("#btn-open-cmdk");
    if (cmdBtn) cmdBtn.addEventListener("click", openCommandPalette);
    const themeBtn = $("#btn-theme-toggle");
    if (themeBtn) themeBtn.addEventListener("click", () => applyTheme(state.theme === "dark" ? "light" : "dark"));
    const refreshBtn = $("#btn-refresh");
    if (refreshBtn) refreshBtn.addEventListener("click", () => route(state.currentPath, true));
    const logoutBtn = $("#btn-logout");
    if (logoutBtn) logoutBtn.addEventListener("click", async () => {
      try { await api("POST", "/api/auth/logout"); } catch {}
      saveToken(null);
      location.hash = "#/login";
    });
    const sidebarToggle = $("#btn-sidebar-toggle");
    if (sidebarToggle) sidebarToggle.addEventListener("click", () => {
      const sb = $("#sidebar");
      if (!sb) return;
      const open = sb.getAttribute("data-mobile-open") === "true";
      sb.setAttribute("data-mobile-open", open ? "false" : "true");
    });
  }

  // ----------------------------------------------------------------
  // 11. Boot
  // ----------------------------------------------------------------
  async function boot() {
    applyTheme(state.theme);
    applyCompact(state.compact);
    buildSidebar();
    wireTopbar();

    // Global keyboard: Cmd/Ctrl+K opens palette, "/" focuses search.
    document.addEventListener("keydown", (e) => {
      const metaK = (e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K");
      if (metaK) { e.preventDefault(); openCommandPalette(); return; }
      // Slash focuses topbar search if not in input.
      if (e.key === "/" && !(e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA"))) {
        const btn = $("#btn-open-cmdk");
        if (btn) { e.preventDefault(); btn.click(); }
      }
    });

    // Verify session if we have a token.
    if (state.auth) {
      try { await api("GET", "/api/me"); } catch { saveToken(null); }
    }

    route(parseHash());
    window.addEventListener("hashchange", () => route(parseHash()));
  }

  // Expose debug API
  window.MTa = {
    state, route, api, toast,
    openCommandPalette, applyTheme, applyCompact,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
