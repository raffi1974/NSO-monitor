// Renders DASHBOARD_DATA (from data.js). Vanilla JS, no build step, no dependencies - everything is
// loaded via <script src> rather than fetch() (which browsers block for file:// pages), so double-
// clicking index.html just works.
(function () {
  "use strict";
  var DATA = window.DASHBOARD_DATA || { summary: {}, nsos: [], changes_feed: [], themes: {} };

  var STATUS_LABEL = { ok: "Monitored", suspicious: "Suspicious", failed: "Failed", diagnosed_only: "Diagnosed only",
    not_diagnosed: "Not diagnosed yet" };
  var API_LABEL = { documented: "Documented API", undocumented: "Undocumented API", "content API only": "Content API only",
    none: "No API", "not checked": "Not checked" };
  var ACCESS_LABEL = { yes: "Python: yes", partial: "Python: partial", no: "Python: no", unknown: "Python: unknown" };
  var CHANGE_LABEL = { new: "New", updated: "Updated", removed: "Removed" };

  function h(tag, attrs, children) {
    var el = document.createElement(tag);
    for (var k in attrs || {}) {
      if (k === "class") el.className = attrs[k];
      else if (k.indexOf("on") === 0) el.addEventListener(k.slice(2), attrs[k]);
      else el.setAttribute(k, attrs[k]);
    }
    (children || []).forEach(function (c) { if (c != null) el.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return el;
  }
  function fmtNum(n) { return (n || 0).toLocaleString("en-US"); }
  function fmtDate(iso) { return iso ? String(iso).replace("T", " ").slice(0, 16) : ""; }
  function nsoById(id) { return DATA.nsos.filter(function (n) { return n.id === id; })[0]; }
  function badge(text, cls) { return h("span", { class: "badge badge-" + cls }, [text]); }

  function renderStatCards(container, stats) {
    var row = h("div", { class: "stat-row" });
    stats.forEach(function (s) { row.appendChild(h("div", { class: "stat-card" }, [h("div", { class: "value" }, [String(s[1])]), h("div", { class: "label" }, [s[0]])])); });
    container.appendChild(row);
  }

  function themeTotal(n, key) {
    var total = 0;
    Object.keys(n.themes || {}).forEach(function (t) { total += (n.themes[t][key] || 0); });
    return total;
  }

  function renderOverview(root) {
    var s = DATA.summary || {};
    renderStatCards(root, [
      ["NSOs tracked", fmtNum(s.total_nsos)], ["Diagnosed (Agent 1)", fmtNum(s.diagnosed)],
      ["Monitored daily (Agent 2)", fmtNum(s.monitored)], ["Items catalogued", fmtNum(s.total_items)],
      ["With an API", fmtNum(s.with_api)], ["Changes (" + s.changes_window_days + "d)", fmtNum(s.changes_recent)],
    ]);
    var grid = h("div", { class: "card-grid" });
    DATA.nsos.forEach(function (n) { grid.appendChild(renderNsoCard(n)); });
    root.appendChild(grid);
  }

  function renderNsoCard(n) {
    var card = h("div", { class: "nso-card", tabindex: "0", role: "button", onclick: function () { location.hash = "#/nso/" + n.id; } });
    card.addEventListener("keydown", function (e) { if (e.key === "Enter") location.hash = "#/nso/" + n.id; });
    card.appendChild(h("div", { class: "nso-card-head" }, [
      h("div", {}, [h("h3", {}, [n.country]), h("div", { class: "org" }, [n.nso])]),
      badge(STATUS_LABEL[n.status] || n.status, n.status),
    ]));
    card.appendChild(h("div", { class: "metrics" }, [
      h("div", {}, [h("b", {}, [fmtNum(themeTotal(n, "n_items"))]), "items"]),
      h("div", {}, [h("b", {}, [fmtNum(themeTotal(n, "n_fetched"))]), "with values fetched"]),
    ]));
    var badges = h("div", {}, []);
    badges.appendChild(badge(API_LABEL[n.api.status] || n.api.status, (n.api.status || "unknown").replace(/ /g, "-")));
    badges.appendChild(document.createTextNode(" "));
    badges.appendChild(badge(ACCESS_LABEL[n.api.python_access] || n.api.python_access, n.api.python_access));
    card.appendChild(badges);
    card.appendChild(h("div", { class: "muted" }, [n.diagnosed_at ? "Diagnosed " + fmtDate(n.diagnosed_at) : "Not yet diagnosed - run agent1_diagnose.py"]));
    return card;
  }

  function renderBarList(title, counts) {
    var box = h("div", {}, [h("h3", {}, [title])]);
    var entries = Object.keys(counts || {}).map(function (k) { return [k, counts[k]]; }).sort(function (a, b) { return b[1] - a[1]; });
    if (!entries.length) { box.appendChild(h("p", { class: "muted" }, ["No data."])); return box; }
    var list = h("div", { class: "bar-list" });
    var max = Math.max.apply(null, entries.map(function (e) { return e[1]; }));
    entries.forEach(function (e) {
      list.appendChild(h("div", { class: "bar-row" }, [h("span", {}, [e[0]]),
        h("div", { class: "bar-track" }, [h("div", { class: "bar-fill", style: "width:" + (100 * e[1] / max) + "%" })]),
        h("span", { class: "muted" }, [fmtNum(e[1])])]));
    });
    box.appendChild(list);
    return box;
  }

  function renderItemTable(container, theme) {
    var state = { q: "", kind: "" };
    var toolbar = h("div", { class: "toolbar" });
    var search = h("input", { type: "search", placeholder: "Search title or category...", oninput: function (e) { state.q = e.target.value.toLowerCase(); redraw(); } });
    var kinds = Array.from(new Set(theme.sample.map(function (r) { return r.kind; }))).sort();
    var kindSel = h("select", { onchange: function (e) { state.kind = e.target.value; redraw(); } },
      [h("option", { value: "" }, ["All kinds"])].concat(kinds.map(function (k) { return h("option", { value: k }, [k]); })));
    toolbar.appendChild(search); toolbar.appendChild(kindSel);
    container.appendChild(toolbar);
    var note = h("p", { class: "muted" }, []);
    container.appendChild(note);
    var wrap = h("div", {});
    container.appendChild(wrap);

    function redraw() {
      var rows = theme.sample.filter(function (r) {
        if (state.kind && r.kind !== state.kind) return false;
        if (state.q && ((r.title || "") + " " + (r.category || "")).toLowerCase().indexOf(state.q) < 0) return false;
        return true;
      });
      note.textContent = "Showing " + rows.length + " of " + theme.sample_shown + " sampled items" +
        (theme.sample_total > theme.sample_shown ? " (" + fmtNum(theme.sample_total) + " total - see the SQLite database for everything)." : ".");
      var table = h("table", {}, [h("thead", {}, [h("tr", {}, ["Item", "Category", "Coverage", "Format"].map(function (t) { return h("th", {}, [t]); }))])]);
      var tbody = h("tbody", {});
      rows.forEach(function (r) {
        tbody.appendChild(h("tr", {}, [
          h("td", {}, [r.url ? h("a", { href: r.url, target: "_blank", rel: "noopener" }, [r.title || "(untitled)"]) : (r.title || "(untitled)"),
            h("div", { class: "muted" }, [r.kind + (r.status && r.status !== "fetched" ? " · " + r.status : "")])]),
          h("td", {}, [r.category || ""]), h("td", {}, [[r.frequency, r.coverage].filter(Boolean).join(" · ")]),
          h("td", {}, [r.format || ""]),
        ]));
      });
      table.appendChild(tbody);
      wrap.innerHTML = "";
      wrap.appendChild(rows.length ? table : h("div", { class: "empty-state" }, ["No items match these filters."]));
    }
    redraw();
  }

  function renderApiSection(container, api) {
    var box = h("div", { class: "section" }, [h("h2", {}, ["API access"])]);
    box.appendChild(h("div", {}, [badge(API_LABEL[api.status] || api.status, (api.status || "unknown").replace(/ /g, "-")), " ",
      badge(ACCESS_LABEL[api.python_access] || api.python_access, api.python_access)]));
    if (api.summary) box.appendChild(h("p", {}, [api.summary]));
    if (api.docs && api.docs.length) {
      box.appendChild(h("h3", {}, ["Documentation"]));
      var ul = h("ul", {});
      api.docs.forEach(function (d) { ul.appendChild(h("li", {}, [h("a", { href: d, target: "_blank", rel: "noopener" }, [d])])); });
      box.appendChild(ul);
    }
    if (api.how_to && api.how_to.python) {
      box.appendChild(h("h3", {}, ["Python example"]));
      box.appendChild(h("div", { class: "code-block" }, [api.how_to.python]));
    }
    container.appendChild(box);
  }

  function renderNsoDetail(root, id) {
    var n = nsoById(id);
    if (!n) { root.appendChild(h("div", { class: "empty-state" }, ["Unknown NSO: " + id])); return; }
    root.appendChild(h("a", { class: "back-link", href: "#/" }, ["← Back to overview"]));
    root.appendChild(h("div", { class: "section" }, [
      h("div", { class: "nso-card-head" }, [
        h("div", {}, [h("h2", { style: "margin:0" }, [n.country]), h("div", { class: "muted" }, [n.nso])]),
        badge(STATUS_LABEL[n.status] || n.status, n.status),
      ]),
      h("p", {}, [h("a", { href: n.url, target: "_blank", rel: "noopener" }, [n.url])]),
      h("p", { class: "muted" }, ["Languages: " + (n.languages || []).join(", ") + " | English: " + n.english]),
      n.report_path ? h("p", {}, [h("a", { href: "../" + n.report_path, target: "_blank" }, ["Full Agent 1 diagnostics report"])]) : null,
    ]));

    Object.keys(n.themes).forEach(function (tid) {
      var theme = n.themes[tid];
      var section = h("div", { class: "section" }, [h("h2", {}, [(DATA.themes[tid] || tid) + " ", badge(STATUS_LABEL[theme.status] || theme.status, theme.status)])]);
      if (theme.status === "diagnosed_only") {
        section.appendChild(h("p", { class: "muted" }, ["Snapshot from Agent 1's one-time diagnosis (" + fmtDate(n.diagnosed_at) +
          "), not yet monitored daily. Enable a source for this NSO/theme in config/agent2_sources.yaml to track it."]));
      } else {
        section.appendChild(h("p", { class: "muted" }, ["Last checked " + fmtDate(theme.last_checked) + " | " + fmtNum(theme.n_items) +
          " item(s) | " + fmtNum(theme.n_fetched) + " with values fetched, " + fmtNum(theme.n_pending) + " pending | changes (" +
          theme.changes.window_days + "d): +" + theme.changes.new + " new, " + theme.changes.updated + " updated, " + theme.changes.removed + " removed"]));
      }
      var cols = h("div", { style: "display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:14px" },
        [renderBarList("By category", theme.by_category), renderBarList("By kind", theme.by_kind)]);
      section.appendChild(cols);
      renderItemTable(section, theme);
      root.appendChild(section);
    });
    renderApiSection(root, n.api);
  }

  function renderChangeItem(c) {
    return h("div", { class: "change-item" }, [
      h("div", {}, [badge(CHANGE_LABEL[c.change] || c.change, c.change === "removed" ? "failed" : c.change === "new" ? "ok" : "suspicious"),
        " ", h("a", { href: "#/nso/" + c.nso }, [c.country]), " — ", h("b", {}, [c.title || "(untitled)"])]),
      h("div", { class: "change-meta" }, [fmtDate(c.ts) + " | " + (DATA.themes[c.theme] || c.theme) + (c.detail ? " | " + c.detail : "")]),
    ]);
  }

  function renderChanges(root) {
    var state = { nso: "", type: "", q: "" };
    var toolbar = h("div", { class: "toolbar" });
    var ids = Array.from(new Set(DATA.changes_feed.map(function (c) { return c.nso; }))).sort();
    var nsoSel = h("select", { onchange: function (e) { state.nso = e.target.value; redraw(); } },
      [h("option", { value: "" }, ["All NSOs"])].concat(ids.map(function (id) { var n = nsoById(id); return h("option", { value: id }, [n ? n.country : id]); })));
    var typeSel = h("select", { onchange: function (e) { state.type = e.target.value; redraw(); } },
      [h("option", { value: "" }, ["All change types"]), h("option", { value: "new" }, ["New"]), h("option", { value: "updated" }, ["Updated"]), h("option", { value: "removed" }, ["Removed"])]);
    var search = h("input", { type: "search", placeholder: "Search title...", oninput: function (e) { state.q = e.target.value.toLowerCase(); redraw(); } });
    toolbar.appendChild(search); toolbar.appendChild(nsoSel); toolbar.appendChild(typeSel);
    root.appendChild(h("div", { class: "section" }, [h("h2", {}, ["What's new (last " + (DATA.summary.changes_window_days || 30) + " days)"]), toolbar]));
    var list = h("div", { class: "changes-list section" });
    root.appendChild(list);

    function redraw() {
      var rows = DATA.changes_feed.filter(function (c) {
        if (state.nso && c.nso !== state.nso) return false;
        if (state.type && c.change !== state.type) return false;
        if (state.q && (c.title || "").toLowerCase().indexOf(state.q) < 0) return false;
        return true;
      });
      list.innerHTML = "";
      list.appendChild(rows.length ? h("div", {}, rows.map(renderChangeItem)) : h("div", { class: "empty-state" }, ["No changes match these filters."]));
    }
    redraw();
  }

  function route() {
    var hash = location.hash || "#/";
    var app = document.getElementById("app");
    app.innerHTML = "";
    document.querySelectorAll(".tab").forEach(function (t) {
      t.classList.toggle("active", hash.indexOf(t.dataset.route) === 0 && (t.dataset.route !== "#/" || hash === "#/" || hash.indexOf("#/nso/") === 0));
    });
    var m = hash.match(/^#\/nso\/(.+)$/);
    if (m) renderNsoDetail(app, decodeURIComponent(m[1]));
    else if (hash === "#/changes") renderChanges(app);
    else renderOverview(app);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.getElementById("generated-at").textContent = DATA.generated_at ? "Generated " + fmtDate(DATA.generated_at) : "";
    document.querySelectorAll(".tab").forEach(function (t) {
      t.addEventListener("click", function () {
        if (location.hash === t.dataset.route) route(); else location.hash = t.dataset.route;
      });
    });
    window.addEventListener("hashchange", route);
    route();
  });
})();
