/* ============================================================
   MTa Admin — icon-map.js
   Lucide-icons convenience wrapper + per-page icon catalog.
   Depends on Lucide UMD being loaded first (creates window.lucide).
   ============================================================ */
(function () {
  "use strict";

  // Wait until Lucide UMD has attached window.lucide
  function ready() {
    return typeof window !== "undefined" && window.lucide && typeof window.lucide.createIcons === "function";
  }

  /**
   * Swap every <i data-lucide="name"> in the given root for an SVG.
   * Lucide's API is: lucide.createIcons({ icons, attrs, nameAttr });
   */
  function refresh(root) {
    try {
      if (!ready()) return;
      const opts = { attrs: { "stroke-width": 1.75, width: "16", height: "16" }, nameAttr: "data-lucide" };
      window.lucide.createIcons(opts);
    } catch (e) {
      // Lucide may throw on unknown names; swallow — non-critical.
      // eslint-disable-next-line no-console
      console.warn("[icons] refresh failed", e && e.message);
    }
  }

  // Per-page icon catalog. Each page uses the icons it needs.
  const ICON_SETS = {
    common: [
      "search", "settings", "user", "log-out", "moon", "sun",
      "check", "x", "chevron-right", "chevron-down", "chevron-left",
      "chevron-up", "menu", "bell", "loader", "refresh-cw", "plus",
      "minus", "edit", "trash-2", "copy", "external-link", "filter",
      "download", "upload", "info", "alert-triangle", "alert-circle",
      "check-circle-2", "x-circle",
    ],
    sidebar: [
      "layout-dashboard", "mail", "send", "users", "key-round",
      "webhook", "shield", "globe", "sparkles", "activity",
      "zap", "credit-card", "history", "plug",
    ],
    dashboard: [
      "trending-up", "trending-down", "gauge", "clock",
      "inbox", "circle-dot",
    ],
    messages: [
      "mail", "mail-open", "mail-plus", "eye", "eye-off",
    ],
    queues: [
      "list", "layers", "pause", "play", "rotate-cw",
    ],
    send: ["send", "paperclip", "image"],
    customers: ["users", "user-plus", "circle-dollar-sign"],
    apiKeys: ["key", "copy", "rotate-ccw"],
    webhooks: ["webhook", "zap", "activity"],
    reputation: ["shield", "shield-check", "shield-alert"],
    dns: ["globe", "file-text", "server"],
    ai: ["sparkles", "bot", "brain", "message-circle"],
    settings: ["settings", "moon", "key", "lock"],
  };

  /**
   * Convenience: build an icon element.
   * Usage: icon("mail", 18) → <i data-lucide="mail" style="..."></i>
   *         icon("check", 14, '"color: var(--success)"') → styled icon.
   */
  function icon(name, size = 16, extraStyle = "") {
    const style = `width:${size}px;height:${size}px;${extraStyle}`;
    // Escape any double-quotes in user-provided style.
    return `<i data-lucide="${name}" style="${style.replace(/"/g, "&quot;")}"></i>`;
  }

  // Expose globally.
  window.MTaIcons = {
    refresh,
    icon,
    sets: ICON_SETS,
  };

  // Auto-refresh on DOMContentLoaded (DOM is ready before <script> ends parsing
  // for DOMContentLoaded listeners; here we attach safely).
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => refresh(document));
  } else {
    queueMicrotask(() => refresh(document));
  }
})();
