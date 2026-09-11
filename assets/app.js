/* =========================================================================
   multipage · app.js   (vanilla, no build, no chart lib)

   The PAGE-LEVEL LAYOUT ENGINE. shell.js has already injected the shared
   chrome and published window.LDW. This script:

     1. reads the current page from <body data-page="..."> (via LDW),
     2. picks a renderer from RENDERERS by that page's `layout`,
     3. paints it into <main id="page"> and wires its interactions,
     4. registers an onLang() callback so a language switch repaints the body.

   RENDERERS is the LAYOUT REGISTRY — one entry per supported page layout:
     field | gallery | article | dashboard | timeline | table |
     bento | kanban | faq | comparison | leaderboard | scrolly | map
   To add a layout, add one renderer (returns the inner HTML for #page) and,
   if it needs interaction, one matching WIRE entry. Nothing else changes.
   ========================================================================= */
(function () {
  "use strict";

  function boot() {
    // Wait until shell.js has injected the chrome (app bar, nav, footer, #dialog)
    // and published LDW. End-of-body scripts run while readyState === "loading",
    // so the shell defers its injection to DOMContentLoaded — we must wait for it.
    if (!window.LDW || !window.LDW.ready) {
      document.addEventListener("ldw:shell-ready", boot, { once: true });
      return;
    }
    var L = window.LDW;

    var t = L.t, esc = L.escapeHtml, r = L.r;
    var pageEl = document.getElementById("page");
    var teardowns = [];   // observers / listeners to disconnect before each repaint

    /* ---------- shared bits ---------- */
    function head(p) {
      var sub = t(p.subtitle)
        ? '<p class="page-head__sub">' + esc(t(p.subtitle)) + "</p>" : "";
      return '<header class="page-head"><h1>' + esc(t(p.title)) + "</h1>" + sub + "</header>";
    }

    /* ---------- field 版型的共用片段 ----------
       renderer(初始畫面)與 wire(點擊後重畫)都呼叫這兩個函式,而不是各寫一份 ——
       兩份實作只要有一點不一樣,prerender 存進靜態檔的初始畫面就會跟瀏覽器重畫的
       不同,載入時閃一下。共用一個來源就不可能漂移。 */
    function fieldReadout(F, ci) {
      var c = ci < 0 ? null : F.cats[ci];
      return '<b class="readout__n">' + (c ? c.n : F.order.length) + "</b>" +
        '<span class="readout__label">' + esc(c ? t(c.name) : t(F.all)) + "</span>";
    }
    function fieldTops(F, ci) {
      var c = ci < 0 ? null : F.cats[ci];
      var list = (c ? c.top : F.top) || [];
      var label = (c ? t(F.more) : t(F.moreAll)).replace("{n}", c ? c.n : F.order.length);
      return list.map(function (row) {
        return '<a class="tops__row" data-item href="' + esc(row.href) + '">' +
          '<span class="tops__text">' +
            '<span class="tops__name">' + esc(row.repo) + "</span>" +
            '<span class="tops__what">' + esc(t(row.what)) + "</span>" +
          "</span>" +
          '<span class="tops__stars">\u2605 ' + esc(row.stars) + "</span></a>";
      }).join("") +
      '<a class="tops__more" href="' + esc(c ? c.href : F.allHref) + '">' + esc(label) + "</a>";
    }

    function barChart(series, accent) {
      var W = 520, H = 240, padL = 16, padR = 16, padT = 16, padB = 44;
      var plotW = W - padL - padR, plotH = H - padT - padB;
      var max = Math.max.apply(null, series.map(function (d) { return d.value; }).concat([1]));
      var n = series.length || 1, gap = 14, bw = (plotW - gap * (n - 1)) / n, baseY = padT + plotH;
      var bars = series.map(function (d, i) {
        var x = padL + i * (bw + gap), h = (d.value / max) * plotH, y = baseY - h;
        var label = esc(t(d.label)), val = esc(String(d.value));
        /* 有 d.key 就掛 .k-<分類>,顏色交給 CSS 的 --k(跟首頁點陣同一套色)。
           沒有 key 的圖表照舊吃 accent 或 --primary。 */
        var cls = "bar-rect" + (d.key ? " k-" + d.key : "");
        return '<rect class="' + cls + '" x="' + r(x) + '" y="' + r(y) + '" width="' + r(bw) +
          '" height="' + r(h) + '" rx="5"' + (accent ? ' style="fill:' + esc(accent) + '"' : "") +
          '><title>' + label + ": " + val + "</title></rect>" +
          '<text class="bar-value" x="' + r(x + bw / 2) + '" y="' + r(y - 6) + '" text-anchor="middle">' + val + "</text>" +
          '<text class="bar-label" x="' + r(x + bw / 2) + '" y="' + r(baseY + 18) + '" text-anchor="middle">' + label + "</text>";
      }).join("");
      return '<svg viewBox="0 0 ' + W + " " + H + '" role="img" preserveAspectRatio="xMidYMid meet" aria-label="bar chart">' +
        '<line class="axis-line" x1="' + padL + '" y1="' + r(baseY) + '" x2="' + r(W - padR) + '" y2="' + r(baseY) + '" />' +
        bars + "</svg>";
    }

    function lineChart(points) {
      var W = 520, H = 240, padL = 28, padR = 16, padT = 16, padB = 32;
      var plotW = W - padL - padR, plotH = H - padT - padB;
      var ys = points.map(function (d) { return d.y; });
      var max = Math.max.apply(null, ys.concat([1])), min = Math.min.apply(null, ys.concat([0]));
      var span = (max - min) || 1, n = points.length || 1;
      var xy = points.map(function (d, i) {
        var x = padL + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
        var y = padT + plotH - ((d.y - min) / span) * plotH;
        return { x: x, y: y, d: d };
      });
      var path = xy.map(function (pt, i) { return (i ? "L" : "M") + r(pt.x) + " " + r(pt.y); }).join(" ");
      var area = path + " L" + r(xy[xy.length - 1].x) + " " + r(padT + plotH) + " L" + r(xy[0].x) + " " + r(padT + plotH) + " Z";
      var dots = xy.map(function (pt) {
        return '<circle class="line-dot" cx="' + r(pt.x) + '" cy="' + r(pt.y) + '" r="3"><title>' +
          esc(String(pt.d.x)) + ": " + esc(String(pt.d.y)) + "</title></circle>";
      }).join("");
      // viewBox 內的擁擠是絕對的(SVG 等比縮放,螢幕變大也不會鬆開)——
      // 每個標籤至少要 62 單位才不會黏在一起,不夠就隔幾個才畫一個(頭尾一定畫)。
      var every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(plotW / 62))));
      // 從最後一點往回推算要標哪幾個 —— 若改成「i % every === 0 再補畫最後一個」,
      // 最後一個可能緊鄰前一個被畫的標籤(12 個點、every=2 時 index 10 與 11 相鄰),照樣疊字。
      var labels = xy.map(function (pt, i) {
        if ((n - 1 - i) % every !== 0) return "";
        return '<text class="bar-label" x="' + r(pt.x) + '" y="' + r(padT + plotH + 20) +
          '" text-anchor="' + (i === 0 ? "start" : i === n - 1 ? "end" : "middle") + '">' +
          esc(String(pt.d.x)) + "</text>";
      }).join("");
      return '<svg viewBox="0 0 ' + W + " " + H + '" role="img" preserveAspectRatio="xMidYMid meet" aria-label="line chart">' +
        '<path class="line-area" d="' + area + '" />' +
        '<path class="line-path" d="' + path + '" fill="none" />' + dots + labels + "</svg>";
    }

    /* =====================================================================
       LAYOUT REGISTRY
       ===================================================================== */
    var RENDERERS = {

      /* ---- field: 可篩選的點陣場(首頁)----
         一個 repo 一個點,顏色是主題、位置是收藏順序。點某個主題就把不屬於它的
         點淡出去。順序由 build_data.py 算好寫在 p.field.order(一個字元一個點的
         分類索引),不在這裡隨機產生 —— 否則 prerender 存的排列與瀏覽器重畫的
         不一致,而且每次重跑都是一份無意義的 diff。 */
      field: function (p) {
        var F = p.field;
        if (!F) return head(p);
        var total = F.order.length;

        /* 資料裡用 *…* 標一段強調色。順序一定是「先 escape,再換標記」——
           反過來的話,我們插進去的 <b> 會被接著的 escape 變成 &lt;b&gt;,
           在畫面上顯示成字面的標籤。 */
        var h1 = esc(t(F.headline)).replace(/\*([^*]+)\*/, '<b class="hl">$1</b>');

        var tabs =
          '<button class="cat-tab" type="button" data-c="-1" aria-pressed="true" data-item>' +
            '<span class="cat-tab__dot"></span>' + esc(t(F.allShort)) +
            '<span class="cat-tab__n">' + total + "</span></button>" +
          F.cats.map(function (c, i) {
            return '<button class="cat-tab k-' + esc(c.key) + '" type="button" data-c="' + i +
                '" aria-pressed="false" data-item>' +
              '<span class="cat-tab__dot"></span>' + esc(t(c.short)) +
              '<span class="cat-tab__n">' + c.n + "</span></button>";
          }).join("");

        /* 點的顏色走 class(styles.css 的 .k-<key> 只設 --k),不用 inline style:
           inline style 蓋得過 CSS,深淺色就切不動;559 段 style 也讓 prerender
           產出的靜態檔白白多三萬多個位元組。 */
        var dots = "";
        for (var n = 0; n < total; n++) {
          var ci = +F.order.charAt(n);
          var c = F.cats[ci];
          if (!c) continue;
          dots += '<i class="k-' + esc(c.key) + '" data-c="' + ci + '"></i>';
        }

        /* 數字條:原本是四張導覽卡旁邊的四個大數字方塊,這個版面上一行就夠。
           文字直接寫最終值(不是 0)—— 靜態檔裡的數字任何情況下都是對的,
           count-up 只是進到畫面時的加分動畫,不是數字的唯一來源。 */
        var stats = (p.stats || []).map(function (st) {
          var v = esc(String(st.value));
          return '<span class="statline__item" data-item>' +
            '<b class="statline__v" data-count="' + v + '">' + v + "</b>" +
            '<span class="statline__l">' + esc(t(st.label)) + "</span></span>";
        }).join("");

        var axisFrom = F.axis && F.axis.from ? esc(F.axis.from) : "";
        var axisTo = F.axis && F.axis.to ? esc(F.axis.to) : "";
        var axis = axisFrom
          ? '<p class="field-axis"><span>' + axisFrom + '</span><span>' + axisTo + "</span></p>"
          : "";

        return '<header class="field-head"><h1>' + h1 + "</h1>" +
            '<p class="field-head__lede">' + esc(t(F.lede)) + "</p></header>" +
          '<div class="cat-tabs" id="catTabs">' + tabs + "</div>" +
          '<div class="readout" id="readout">' + fieldReadout(F, -1) + "</div>" +
          /* 點陣是一張統計圖,不是 559 個可讀項目 —— 給整塊一個 role="img"
             與一句摘要,讀屏就不用逐點念過去。 */
          '<div class="field" id="field" role="img" aria-label="' +
            esc(t(F.fieldAlt).replace("{n}", total)) + '">' + dots + "</div>" +
          axis +
          '<div class="tops" id="tops">' + fieldTops(F, -1) + "</div>" +
          (stats ? '<div class="statline">' + stats + "</div>" : "");
      },

      /* ---- gallery: search + chips + card grid + dialog ---- */
      gallery: function (p) {
        var cats = (p.categories || []).map(function (c) {
          return '<button class="chip" type="button" data-cat="' + esc(c.key) + '">' +
            esc(c[L.state.lang] || c.en) + "</button>";
        }).join("");
        var allLabel = L.state.lang === "en" ? "All" : "全部";
        return head(p) +
          '<div class="toolbar">' +
            '<input id="search" class="search" type="search" autocomplete="off" ' +
              'placeholder="' + (L.state.lang === "en" ? "Search…" : "搜尋…") + '" ' +
              'aria-label="' + (L.state.lang === "en" ? "Search" : "搜尋") + '" />' +
            (cats ? '<div class="chips"><button class="chip chip--active" type="button" data-cat="">' + esc(allLabel) + "</button>" + cats + "</div>" : "") +
          "</div>" +
          '<p class="result-count" id="resultCount" aria-live="polite"></p>' +
          '<div class="grid" id="grid"></div>';
      },

      /* ---- article: sticky TOC + prose + reading progress ---- */
      article: function (p) {
        var toc = (p.sections || []).map(function (s) {
          return '<a class="toc-link" href="#' + esc(s.id) + '" data-toc="' + esc(s.id) + '">' + esc(t(s.heading)) + "</a>";
        }).join("");
        var body = (p.sections || []).map(function (s) {
          var blocks = (s.blocks || []).map(function (b) {
            if (b.type === "h3") return "<h3>" + esc(t(b.text)) + "</h3>";
            if (b.type === "quote") return "<blockquote>" + esc(t(b.text)) + "</blockquote>";
            if (b.type === "code") return "<pre><code>" + esc(t(b.text)) + "</code></pre>";
            if (b.type === "ul") {
              var arr = (b.items && (b.items[L.state.lang] || b.items.en || b.items.zh)) || [];
              return "<ul>" + arr.map(function (li) { return "<li>" + esc(li) + "</li>"; }).join("") + "</ul>";
            }
            return "<p>" + esc(t(b.text)) + "</p>";
          }).join("");
          return '<section class="article-section" id="' + esc(s.id) + '" data-item ' +
            'aria-labelledby="' + esc(s.id) + '-h"><h2 id="' + esc(s.id) + '-h">' + esc(t(s.heading)) + "</h2>" + blocks + "</section>";
        }).join("");
        return '<div class="reading-progress" id="readingProgress" aria-hidden="true"></div>' +
          head(p) +
          '<div class="article-layout">' +
            '<nav class="toc" aria-label="Contents"><div class="toc__inner">' + toc + "</div></nav>" +
            '<div class="article-body prose">' + body + "</div>" +
          "</div>";
      },

      /* ---- dashboard: stat cards + bar + line + table ---- */
      dashboard: function (p) {
        var stats = (p.stats || []).map(function (s) {
          var d = s.delta;
          var deltaHtml = (d === 0 || d) ?
            '<span class="stat-delta stat-delta--' + (d >= 0 ? "up" : "down") + '">' +
              (d >= 0 ? "▲ " : "▼ ") + esc(String(Math.abs(d))) + "%</span>" : "";
          return '<div class="stat-card" data-item>' +
            '<span class="stat-label">' + esc(t(s.label)) + "</span>" +
            '<b class="stat-value">' + esc(String(s.value)) +
              (t(s.unit) ? ' <span class="stat-unit">' + esc(t(s.unit)) + "</span>" : "") + "</b>" +
            deltaHtml + "</div>";
        }).join("");
        var bars = p.bars ? '<figure class="panel" data-item><figcaption>' + esc(t(p.bars.title)) + "</figcaption>" +
          '<div class="chart-wrap">' + barChart(p.bars.series || []) + "</div></figure>" : "";
        var line = p.line ? '<figure class="panel" data-item><figcaption>' + esc(t(p.line.title)) + "</figcaption>" +
          '<div class="chart-wrap">' + lineChart(p.line.points || []) + "</div></figure>" : "";
        var table = "";
        if (p.table) {
          var thead = (p.table.columns || []).map(function (c) { return "<th>" + esc(t(c.label)) + "</th>"; }).join("");
          var tbody = (p.table.rows || []).map(function (row) {
            return "<tr data-item>" + (p.table.columns || []).map(function (c) {
              var v = row[c.key];
              return "<td>" + esc(typeof v === "object" ? t(v) : String(v == null ? "" : v)) + "</td>";
            }).join("") + "</tr>";
          }).join("");
          table = '<div class="panel panel--wide" data-item><div class="table-wrap"><table class="data-table">' +
            "<thead><tr>" + thead + "</tr></thead><tbody>" + tbody + "</tbody></table></div></div>";
        }
        return head(p) +
          '<div class="stat-grid">' + stats + "</div>" +
          '<div class="panel-grid">' + bars + line + "</div>" + table;
      },

      /* ---- timeline: dated event cards down a rail ---- */
      timeline: function (p) {
        var items = (p.events || []).map(function (ev) {
          return '<li class="tl-item" data-item><div class="tl-dot" aria-hidden="true"></div>' +
            '<div class="tl-card"><span class="tl-date">' + esc(t(ev.date)) + "</span>" +
            '<h3 class="tl-title">' + esc(t(ev.title)) + "</h3>" +
            '<p class="tl-body">' + esc(t(ev.body)) + "</p></div></li>";
        }).join("");
        return head(p) + '<ol class="timeline">' + items + "</ol>";
      },

      /* ---- table: searchable + sortable ---- */
      table: function (p) {
        return head(p) +
          '<div class="toolbar">' +
            '<input id="search" class="search" type="search" autocomplete="off" ' +
              'placeholder="' + (L.state.lang === "en" ? "Search…" : "搜尋…") + '" ' +
              'aria-label="' + (L.state.lang === "en" ? "Search" : "搜尋") + '" />' +
            '<div class="chips-stack" id="tableChips"></div>' +
          "</div>" +
          '<p class="result-count" id="resultCount" aria-live="polite"></p>' +
          '<div class="table-wrap"><table class="data-table" id="dataTable"><thead></thead><tbody></tbody></table></div>';
      },

      /* ---- bento: asymmetric tile grid ---- */
      bento: function (p) {
        var tiles = (p.tiles || []).map(function (tile) {
          return '<article class="tile tile--' + esc(tile.size || "sm") + (tile.accent ? " tile--accent" : "") + '" data-item>' +
            (tile.icon ? '<span class="material-symbols-rounded tile__icon" aria-hidden="true">' + esc(tile.icon) + "</span>" : "") +
            (tile.value ? '<b class="tile__value">' + esc(tile.value) + "</b>" : "") +
            '<h3 class="tile__title">' + esc(t(tile.title)) + "</h3>" +
            (t(tile.body) ? '<p class="tile__body">' + esc(t(tile.body)) + "</p>" : "") + "</article>";
        }).join("");
        return head(p) + '<div class="bento">' + tiles + "</div>";
      },

      /* ---- kanban: cards grouped by status column ---- */
      kanban: function (p) {
        var cols = (p.columns || []).map(function (col) {
          var cards = (p.cards || []).filter(function (c) { return c.column === col.key; }).map(function (c) {
            var tags = (c.tags || []).map(function (g) { return '<span class="tag">' + esc(g) + "</span>"; }).join("");
            return '<article class="kb-card" data-item><h3 class="kb-card__title">' + esc(t(c.title)) + "</h3>" +
              (t(c.body) ? '<p class="kb-card__body">' + esc(t(c.body)) + "</p>" : "") +
              (tags ? '<div class="card__tags">' + tags + "</div>" : "") + "</article>";
          }).join("");
          var count = (p.cards || []).filter(function (c) { return c.column === col.key; }).length;
          return '<div class="kb-col"><div class="kb-col__head">' + esc(t(col.label)) +
            ' <span class="kb-col__count">' + count + "</span></div>" +
            '<div class="kb-col__body">' + cards + "</div></div>";
        }).join("");
        return head(p) + '<div class="kanban">' + cols + "</div>";
      },

      /* ---- faq: searchable accordion ---- */
      faq: function (p) {
        var items = (p.qa || []).map(function (row) {
          return '<details class="acc-item" data-item data-q="' + esc((t(row.q) + " " + t(row.a)).toLowerCase()) + '">' +
            '<summary class="acc-q"><span>' + esc(t(row.q)) + "</span>" +
            '<span class="material-symbols-rounded acc-chevron" aria-hidden="true">expand_more</span></summary>' +
            '<div class="acc-a">' + esc(t(row.a)) + "</div></details>";
        }).join("");
        return head(p) +
          '<div class="toolbar"><input id="search" class="search" type="search" autocomplete="off" ' +
            'placeholder="' + (L.state.lang === "en" ? "Search…" : "搜尋…") + '" ' +
            'aria-label="' + (L.state.lang === "en" ? "Search" : "搜尋") + '" /></div>' +
          '<div class="accordion" id="accordion">' + items + "</div>";
      },

      /* ---- comparison: plans (cols) x features (rows) ---- */
      comparison: function (p) {
        var plans = p.plans || [], feats = p.features || [];
        var thead = '<th scope="col"></th>' + plans.map(function (pl) {
          return '<th scope="col" class="' + (pl.highlight ? "cmp-col--hl" : "") + '">' +
            '<div class="cmp-plan">' + esc(t(pl.name)) + "</div>" +
            '<div class="cmp-price">' + esc(t(pl.price)) + "</div>" +
            (t(pl.note) ? '<div class="cmp-note">' + esc(t(pl.note)) + "</div>" : "") + "</th>";
        }).join("");
        var rows = feats.map(function (f) {
          var cells = plans.map(function (pl) {
            var v = f.values ? f.values[pl.key] : undefined;
            var cell;
            if (v === true) cell = '<span class="cmp-yes material-symbols-rounded" aria-label="yes">check</span>';
            else if (v === false || v == null) cell = '<span class="cmp-no" aria-label="no">—</span>';
            else cell = esc(t(v));
            return '<td class="' + (pl.highlight ? "cmp-col--hl" : "") + '">' + cell + "</td>";
          }).join("");
          return '<tr data-item><th scope="row" class="cmp-feat">' + esc(t(f.label)) + "</th>" + cells + "</tr>";
        }).join("");
        return head(p) + '<div class="table-wrap"><table class="cmp-table">' +
          "<thead><tr>" + thead + "</tr></thead><tbody>" + rows + "</tbody></table></div>";
      },

      /* ---- leaderboard: ranked list + tier grouping toggle ---- */
      leaderboard: function (p) {
        var listLabel = L.state.lang === "en" ? "List" : "排名";
        var tierLabel = L.state.lang === "en" ? "Tiers" : "階級";
        return head(p) +
          '<div class="seg" role="tablist">' +
            '<button class="seg__btn seg__btn--active" type="button" data-view="list">' + esc(listLabel) + "</button>" +
            '<button class="seg__btn" type="button" data-view="tier">' + esc(tierLabel) + "</button>" +
          "</div>" +
          '<div id="lbView"></div>';
      },

      /* ---- scrolly: sticky visual + stepped narrative ---- */
      scrolly: function (p) {
        var steps = (p.steps || []).map(function (s, i) {
          return '<div class="scrolly-step" data-item data-step="' + i + '"><p>' + esc(t(s.text)) + "</p></div>";
        }).join("");
        return head(p) +
          '<div class="scrolly">' +
            '<div class="scrolly-sticky"><div class="scrolly-visual" id="scrollyVisual"></div></div>' +
            '<div class="scrolly-steps">' + steps + "</div>" +
          "</div>";
      },

      /* ---- map: Leaflet map + list (needs Leaflet on the page) ---- */
      map: function (p) {
        return head(p) +
          '<div class="map-layout">' +
            '<div class="map-box" id="map" role="application" aria-label="Map"></div>' +
            '<ul class="map-list" id="mapList"></ul>' +
          "</div>";
      }
    };

    /* =====================================================================
       WIRING (interactions) — keyed by layout, run after innerHTML is set
       ===================================================================== */
    var WIRE = {
      field: function (p) {
        animateCounters();
        var F = p.field;
        if (!F) return;
        var tabsEl = document.getElementById("catTabs");
        var fieldEl = document.getElementById("field");
        var readEl = document.getElementById("readout");
        var topsEl = document.getElementById("tops");
        if (!tabsEl || !fieldEl) return;
        var tabs = [].slice.call(tabsEl.children);
        var dots = [].slice.call(fieldEl.children);
        var cur = -1;

        function paint() {
          var c = cur < 0 ? null : F.cats[cur];
          var k = c ? " k-" + c.key : "";
          for (var i = 0; i < dots.length; i++) {
            dots[i].classList.toggle("is-dim", cur >= 0 && +dots[i].dataset.c !== cur);
          }
          tabs.forEach(function (b) {
            b.setAttribute("aria-pressed", String(+b.dataset.c === cur));
          });
          readEl.className = "readout" + k;
          readEl.innerHTML = fieldReadout(F, cur);
          topsEl.className = "tops" + k;
          topsEl.innerHTML = fieldTops(F, cur);
          fieldEl.setAttribute("aria-label",
            (c ? t(F.fieldAltOne).replace("{name}", t(c.name)).replace("{n}", c.n)
               : t(F.fieldAlt).replace("{n}", F.order.length)));
        }

        tabs.forEach(function (b) {
          b.addEventListener("click", function () {
            var want = +b.dataset.c;
            /* 再點一次同一個分類就回到全部 —— 不然選了之後沒有回頭路,
               只能去點「全部」那一格,多一步。 */
            cur = (cur === want) ? -1 : want;
            paint();
          });
        });
      },

      gallery: function (p) {
        var grid = document.getElementById("grid");
        var search = document.getElementById("search");
        var count = document.getElementById("resultCount");
        var chips = [].slice.call(pageEl.querySelectorAll(".chip"));
        var st = { q: "", cat: "" };

        function matches(item) {
          if (st.cat && item.category !== st.cat) return false;
          if (!st.q) return true;
          var hay = (t(item.title) + " " + t(item.summary) + " " + (item.tags || []).join(" ")).toLowerCase();
          return hay.indexOf(st.q) !== -1;
        }
        function paint() {
          var rows = (p.items || []).filter(matches);
          grid.innerHTML = rows.map(function (item) {
            var tags = (item.tags || []).map(function (g) { return '<span class="tag">' + esc(g) + "</span>"; }).join("");
            var meta = t(item.meta)
              ? '<p class="card__meta">' + esc(t(item.meta)) + "</p>" : "";
            return '<article class="card" tabindex="0" role="button" data-item data-slug="' + esc(item.slug) + '" ' +
              'aria-label="' + esc(t(item.title)) + '">' +
              '<h3 class="card__title">' + esc(t(item.title)) + "</h3>" +
              '<p class="card__summary">' + esc(t(item.summary)) + "</p>" +
              meta +
              (tags ? '<div class="card__tags">' + tags + "</div>" : "") + "</article>";
          }).join("");
          if (count) count.textContent = rows.length + (L.state.lang === "en" ? " result(s)" : " 筆結果");
          wireCards();
        }
        function wireCards() {
          [].forEach.call(grid.querySelectorAll(".card[data-slug]"), function (card) {
            var slug = card.dataset.slug;
            card.addEventListener("click", function () { openItem(slug); });
            card.addEventListener("keydown", function (e) {
              if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openItem(slug); }
            });
          });
        }
        function findItem(slug) {
          return (p.items || []).filter(function (it) { return it.slug === slug; })[0] || null;
        }
        function openItem(slug) {
          var item = findItem(slug); if (!item) return;
          var dlg = L.dialog(), body = document.getElementById("dialogBody");
          var tags = (item.tags || []).map(function (g) { return '<span class="tag">' + esc(g) + "</span>"; }).join("");
          var links = "";
          if (item.url) {
            links += '<a class="dialog__link" href="' + esc(item.url) + '" target="_blank" rel="noopener">' +
              '<span class="material-symbols-rounded" aria-hidden="true">open_in_new</span>' +
              (L.state.lang === "en" ? "View on GitHub" : "在 GitHub 上開啟") + "</a>";
          }
          if (item.homepage) {
            links += '<a class="dialog__link" href="' + esc(item.homepage) + '" target="_blank" rel="noopener">' +
              '<span class="material-symbols-rounded" aria-hidden="true">language</span>' +
              (L.state.lang === "en" ? "Project site" : "專案網站") + "</a>";
          }
          body.innerHTML = '<h2 id="dialogTitle">' + esc(t(item.title)) + "</h2>" +
            (t(item.meta) ? '<p class="dialog__meta">' + esc(t(item.meta)) + "</p>" : "") +
            (tags ? '<div class="card__tags">' + tags + "</div>" : "") +
            "<p>" + esc(t(item.overview) || t(item.summary)) + "</p>" +
            (links ? '<div class="dialog__links">' + links + "</div>" : "");
          if (!dlg.open) dlg.showModal();
          if (location.hash.slice(1) !== slug) history.replaceState(null, "", "#" + slug);
        }
        function syncHash() {
          var slug = location.hash.slice(1);
          if (slug && findItem(slug)) openItem(slug);
        }
        if (search) search.addEventListener("input", function () { st.q = this.value.trim().toLowerCase(); paint(); });
        chips.forEach(function (chip) {
          chip.addEventListener("click", function () {
            chips.forEach(function (c) { c.classList.remove("chip--active"); });
            chip.classList.add("chip--active");
            st.cat = chip.dataset.cat || "";
            paint();
          });
        });
        /* closing the dialog clears the #slug so the URL returns to clean state
           and a later deep link to the SAME slug fires hashchange again */
        var dlg = L.dialog();
        function onClose() {
          var slug = location.hash.slice(1);
          if (slug && findItem(slug)) history.replaceState(null, "", location.pathname + location.search);
        }
        dlg.addEventListener("close", onClose);
        var onHash = function () { syncHash(); };
        window.addEventListener("hashchange", onHash);
        teardowns.push(function () {
          window.removeEventListener("hashchange", onHash);
          dlg.removeEventListener("close", onClose);
        });
        paint();
        syncHash();
      },

      article: function () {
        var prog = document.getElementById("readingProgress");
        var links = [].slice.call(pageEl.querySelectorAll(".toc-link"));
        var secs = [].slice.call(pageEl.querySelectorAll(".article-section"));
        function onScroll() {
          var h = document.documentElement;
          var max = h.scrollHeight - h.clientHeight;
          if (prog) prog.style.width = (max > 0 ? (h.scrollTop / max) * 100 : 0) + "%";
        }
        window.addEventListener("scroll", onScroll, { passive: true });
        onScroll();
        teardowns.push(function () { window.removeEventListener("scroll", onScroll); });
        if ("IntersectionObserver" in window) {
          var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (en) {
              if (!en.isIntersecting) return;
              links.forEach(function (a) {
                var on = a.dataset.toc === en.target.id;
                a.classList.toggle("toc-link--active", on);
              });
            });
          }, { rootMargin: "-30% 0px -60% 0px" });
          secs.forEach(function (s) { io.observe(s); });
          teardowns.push(function () { io.disconnect(); });
        }
      },

      dashboard: function () { /* static charts; nothing to wire */ },

      table: function (p) {
        var table = document.getElementById("dataTable");
        var thead = table.querySelector("thead"), tbody = table.querySelector("tbody");
        var search = document.getElementById("search");
        var chipsBox = document.getElementById("tableChips");
        var cols = p.columns || [];
        // 多軸篩選:scaffold 原本只吃第一個 filter 欄。559 筆資料光靠一個軸不夠用
        // ——「主題」跟「語言」要能同時收斂,才找得到「Python 寫的 RAG 工具」。
        var filterCols = cols.filter(function (c) { return c.filter; });
        var st = { q: "", filters: {}, sortKey: null, dir: 1 };

        function cellText(row, c) { var v = row[c.key]; return typeof v === "object" ? t(v) : String(v == null ? "" : v); }
        function rowMatches(row) {
          for (var i = 0; i < filterCols.length; i++) {
            var c = filterCols[i], want = st.filters[c.key];
            if (want && cellText(row, c) !== want) return false;
          }
          if (!st.q) return true;
          return cols.some(function (c) { return cellText(row, c).toLowerCase().indexOf(st.q) !== -1; });
        }
        function paintHead() {
          thead.innerHTML = "<tr>" + cols.map(function (c) {
            var arrow = st.sortKey === c.key ? (st.dir > 0 ? " ▲" : " ▼") : "";
            return '<th class="th-sort" data-key="' + esc(c.key) + '" role="button" tabindex="0" aria-label="Sort by ' +
              esc(t(c.label)) + '">' + esc(t(c.label)) + esc(arrow) + "</th>";
          }).join("") + "</tr>";
          [].forEach.call(thead.querySelectorAll(".th-sort"), function (th) {
            th.addEventListener("click", function () { sortBy(th.dataset.key); });
            th.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sortBy(th.dataset.key); } });
          });
        }
        function sortBy(key) {
          if (st.sortKey === key) st.dir = -st.dir; else { st.sortKey = key; st.dir = 1; }
          paint();
        }
        function paint() {
          paintHead();
          var col = cols.filter(function (c) { return c.key === st.sortKey; })[0];
          var rows = (p.rows || []).filter(rowMatches).slice();
          if (col) {
            rows.sort(function (a, b) {
              var va = a[col.key], vb = b[col.key];
              if (col.type === "num") return (Number(va) - Number(vb)) * st.dir;
              return String(typeof va === "object" ? t(va) : va).localeCompare(String(typeof vb === "object" ? t(vb) : vb)) * st.dir;
            });
          }
          tbody.innerHTML = rows.map(function (row) {
            return "<tr data-item>" + cols.map(function (c) {
              // repo:顯示名稱、連結藏在後面。原本的 link 型別會把整串網址印出來,
              // 559 列下來就是一面 URL 牆,既難讀也搜不到重點。
              if (c.type === "repo" && row.url) {
                return '<td><a class="row-link" href="' + esc(row.url) + '" target="_blank" rel="noopener">' +
                  esc(cellText(row, c)) + "</a></td>";
              }
              if (c.type === "link") {
                var u = row[c.key];
                return '<td><a class="row-link" href="' + esc(u) + '" target="_blank" rel="noopener">' + esc(u) + "</a></td>";
              }
              // 數值欄存的是原始數字(排序要用),顯示時補千分位。
              // 表格是拿來精確比較的,所以用 546,005 而不是卡片上那種掃視用的 546k。
              if (c.type === "num") {
                var nv = Number(row[c.key]);
                return "<td>" + esc(isNaN(nv) ? cellText(row, c) : nv.toLocaleString("en-US")) + "</td>";
              }
              return "<td>" + esc(cellText(row, c)) + "</td>";
            }).join("") + "</tr>";
          }).join("");
          if (countEl) {
            countEl.textContent = L.state.lang === "en"
              ? rows.length + " of " + (p.rows || []).length + " repositories"
              : (p.rows || []).length + " 筆中的 " + rows.length + " 筆";
          }
        }

        /* 每個 filter 欄一列 chips,各自獨立收斂 */
        var countEl = null;
        if (filterCols.length) {
          var allLabel = L.state.lang === "en" ? "All" : "全部";
          chipsBox.innerHTML = filterCols.map(function (c) {
            var vals = [];
            (p.rows || []).forEach(function (row) {
              var v = cellText(row, c);
              if (v && vals.indexOf(v) === -1) vals.push(v);
            });
            vals.sort();
            return '<div class="chips chips--row" data-key="' + esc(c.key) + '">' +
              '<span class="chips__label">' + esc(t(c.label)) + "</span>" +
              '<button class="chip chip--active" type="button" data-v="">' + esc(allLabel) + "</button>" +
              vals.map(function (v) {
                return '<button class="chip" type="button" data-v="' + esc(v) + '">' + esc(v) + "</button>";
              }).join("") + "</div>";
          }).join("");
          [].forEach.call(chipsBox.querySelectorAll(".chips--row"), function (rowEl) {
            var key = rowEl.dataset.key;
            [].forEach.call(rowEl.querySelectorAll(".chip"), function (chip) {
              chip.addEventListener("click", function () {
                [].forEach.call(rowEl.querySelectorAll(".chip"), function (c) { c.classList.remove("chip--active"); });
                chip.classList.add("chip--active");
                st.filters[key] = chip.dataset.v || "";
                paint();
              });
            });
          });
        }
        countEl = document.getElementById("resultCount");
        if (search) {
          // 559 列 × 6 欄 = 每次重畫 3000+ 個儲存格,逐鍵重畫會頓。
          // 120ms 去抖動:低於一般打字間隔,使用者感覺不到延遲,但省掉大部分重畫。
          var timer = null;
          search.addEventListener("input", function () {
            var v = this.value.trim().toLowerCase();
            clearTimeout(timer);
            timer = setTimeout(function () { st.q = v; paint(); }, 120);
          });
          teardowns.push(function () { clearTimeout(timer); });
        }
        paint();
      },

      bento: function () { /* static */ },
      kanban: function () { /* static */ },

      faq: function () {
        var search = document.getElementById("search");
        var items = [].slice.call(pageEl.querySelectorAll(".acc-item"));
        if (search) search.addEventListener("input", function () {
          var q = this.value.trim().toLowerCase();
          items.forEach(function (it) {
            var hit = !q || (it.dataset.q || "").indexOf(q) !== -1;
            it.style.display = hit ? "" : "none";
          });
        });
      },

      comparison: function () { /* static */ },

      leaderboard: function (p) {
        var view = document.getElementById("lbView");
        var btns = [].slice.call(pageEl.querySelectorAll(".seg__btn"));
        var entries = (p.entries || []).slice().sort(function (a, b) { return b.score - a.score; });
        function row(e, rank) {
          return '<li class="lb-row" data-item>' +
            (rank ? '<span class="lb-rank">' + rank + "</span>" : "") +
            '<span class="lb-tier lb-tier--' + esc(e.tier || "") + '">' + esc(e.tier || "") + "</span>" +
            '<span class="lb-name">' + esc(t(e.name)) + "</span>" +
            '<span class="lb-meta">' + esc(t(e.meta)) + "</span>" +
            '<span class="lb-score">' + esc(String(e.score)) + "</span></li>";
        }
        function listView() {
          view.innerHTML = '<ol class="lb-list">' + entries.map(function (e, i) { return row(e, i + 1); }).join("") + "</ol>";
        }
        function tierView() {
          var tiers = [];
          entries.forEach(function (e) { if (tiers.indexOf(e.tier) === -1) tiers.push(e.tier); });
          view.innerHTML = tiers.map(function (tier) {
            var rows = entries.filter(function (e) { return e.tier === tier; }).map(function (e) { return row(e); }).join("");
            return '<div class="lb-tier-group"><div class="lb-tier-head lb-tier--' + esc(tier) + '">' + esc(tier) + "</div>" +
              '<ol class="lb-list">' + rows + "</ol></div>";
          }).join("");
        }
        btns.forEach(function (b) {
          b.addEventListener("click", function () {
            btns.forEach(function (x) { x.classList.remove("seg__btn--active"); });
            b.classList.add("seg__btn--active");
            if (b.dataset.view === "tier") tierView(); else listView();
          });
        });
        listView();
      },

      scrolly: function (p) {
        var visual = document.getElementById("scrollyVisual");
        var steps = [].slice.call(pageEl.querySelectorAll(".scrolly-step"));
        function paintVisual(i) {
          var s = (p.steps || [])[i]; if (!s) return;
          var v = s.visual || {};
          if (v.type === "bars") {
            visual.innerHTML = '<div class="chart-wrap">' + barChart(v.bars || [], v.color) + "</div>";
          } else {
            visual.innerHTML = '<div class="scrolly-stat" style="color:' + esc(v.color || "var(--primary)") + '">' +
              esc(t(v.value)) + "</div>";
          }
        }
        paintVisual(0);
        if ("IntersectionObserver" in window) {
          var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (en) {
              if (en.isIntersecting) {
                steps.forEach(function (s) { s.classList.remove("scrolly-step--active"); });
                en.target.classList.add("scrolly-step--active");
                paintVisual(parseInt(en.target.dataset.step, 10) || 0);
              }
            });
          }, { rootMargin: "-45% 0px -45% 0px" });
          steps.forEach(function (s) { io.observe(s); });
          teardowns.push(function () { io.disconnect(); });
        }
      },

      map: function (p) {
        var listEl = document.getElementById("mapList");
        var places = p.places || [];
        listEl.innerHTML = places.map(function (pl) {
          return '<li class="place" data-item data-slug="' + esc(pl.slug) + '" tabindex="0" role="button" ' +
            'aria-label="' + esc(t(pl.name)) + '"><b>' + esc(t(pl.name)) + "</b>" +
            '<span>' + esc(t(pl.body)) + "</span></li>";
        }).join("");

        if (typeof window.L === "undefined" || !window.L.map) {
          // Leaflet not loaded (offline / blocked): list-only graceful fallback.
          document.getElementById("map").innerHTML =
            '<div class="map-fallback">' + esc(L.state.lang === "en" ? "Map unavailable offline — see the list." : "離線時地圖無法載入 — 請看清單。") + "</div>";
          return;
        }
        var map = window.L.map("map", { scrollWheelZoom: false });
        window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          attribution: "© OpenStreetMap", maxZoom: 19
        }).addTo(map);
        var markers = {}, group = [];
        places.forEach(function (pl) {
          /* alt + title give Leaflet's marker <img role="button"> an accessible name */
          var m = window.L.marker([pl.lat, pl.lng], { alt: t(pl.name), title: t(pl.name), keyboard: true })
            .addTo(map).bindPopup("<b>" + esc(t(pl.name)) + "</b><br>" + esc(t(pl.body)));
          markers[pl.slug] = m; group.push([pl.lat, pl.lng]);
        });
        if (group.length) map.fitBounds(group, { padding: [30, 30] });
        [].forEach.call(listEl.querySelectorAll(".place"), function (li) {
          function go() { var m = markers[li.dataset.slug]; if (m) { map.panTo(m.getLatLng()); m.openPopup(); } }
          li.addEventListener("click", go);
          li.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
        });
        teardowns.push(function () { try { map.remove(); } catch (e) {} });
      }
    };

    /* ---- 數字 count-up(hero 方塊與首頁的數字條共用)---- */
    function animateCounters() {
      var els = [].slice.call(pageEl.querySelectorAll("[data-count]"));
      if (!els.length) return;
      function run(el) {
        // data-count 可能帶單位後綴("14.6M"、"504.2k")。parseFloat 只吃前面的數字,
        // 後綴要自己留著補回去,否則動畫跑完 14.6M 會變成 14.6。
        var raw = String(el.dataset.count || "");
        var num = parseFloat(raw);
        if (isNaN(num)) { el.textContent = raw; return; }
        var suffix = raw.slice(String(num).length);
        var decimals = (String(num).split(".")[1] || "").length;
        var dur = 1000, start = null;
        function fmt(v) { return (decimals ? v.toFixed(decimals) : String(Math.round(v))) + suffix; }
        function step(ts) {
          if (start === null) start = ts;
          var pr = Math.min(1, (ts - start) / dur), eased = 1 - Math.pow(1 - pr, 3);
          el.textContent = fmt(num * eased);
          if (pr < 1) requestAnimationFrame(step); else el.textContent = raw;
        }
        requestAnimationFrame(step);
      }
      if (!("IntersectionObserver" in window)) { els.forEach(run); return; }
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) { if (en.isIntersecting) { run(en.target); io.unobserve(en.target); } });
      }, { threshold: 0.4 });
      els.forEach(function (el) { io.observe(el); });
      teardowns.push(function () { io.disconnect(); });
    }

    /* =====================================================================
       RENDER the current page; re-runnable on language switch
       ===================================================================== */
    function render() {
      teardowns.forEach(function (fn) { try { fn(); } catch (e) {} });
      teardowns = [];
      var p = L.currentPage();
      if (!p) { pageEl.innerHTML = '<p class="empty">No page data.</p>'; return; }
      // data/data.js 只帶每頁的輕量欄位(nav 要用);items / rows / events 這些重的
      // 放在各自的 data/page-<slug>.js,由該頁自己載入 —— 在這裡併回 page 物件,
      // 底下所有 renderer 就跟資料沒拆過一樣。
      if (window.SITE_PAGE_DATA) {
        for (var k in window.SITE_PAGE_DATA) {
          if (Object.prototype.hasOwnProperty.call(window.SITE_PAGE_DATA, k)) p[k] = window.SITE_PAGE_DATA[k];
        }
      }
      var fn = RENDERERS[p.layout] || RENDERERS.gallery;
      pageEl.className = "page page--" + p.layout;
      pageEl.innerHTML = fn(p);
      var w = WIRE[p.layout];
      if (w) w(p);
    }

    L.onLang(render);
    render();
  }

  boot();
})();
