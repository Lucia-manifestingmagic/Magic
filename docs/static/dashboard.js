/* Charts, tooltips and table sorting.
 *
 * Hand-written SVG rather than a charting library: it is a few hundred lines,
 * it ships nothing to install, and it hits the mark specs exactly — 2px lines,
 * hairline solid gridlines, 4px rounded column tops square at the baseline,
 * 8px end markers with a 2px surface ring.
 *
 * Two rules enforced here as well as in Python: a null is drawn as a gap, never
 * as a zero; and no chart ever gets a second y-axis.
 */
(function () {
  "use strict";

  var VIEW = JSON.parse(document.getElementById("view-data").textContent);
  var NS = "http://www.w3.org/2000/svg";

  var COLORS = {
    meta: "#2a78d6",
    youtube: "#eb6834",
    blended: "#52514e"
  };
  var SURFACE = "#fcfcfb";
  var GRID = "#e1e0d9";
  var AXIS = "#c3c2b7";
  var INK2 = "#52514e";

  var BLOCKS = {};
  VIEW.channels.forEach(function (channel) { BLOCKS[channel.key] = channel; });
  BLOCKS.blended = VIEW.blended;

  var MODES = {
    compare: VIEW.channels.map(function (c) { return c.key; }),
    blended: ["blended"]
  };
  VIEW.channels.forEach(function (c) { MODES[c.key] = [c.key]; });

  var CHARTS = {
    cac: {
      label: "Cost per new account",
      pick: function (point) { return point.cac; },
      format: money,
      reference: { value: VIEW.benchmarks.cac, label: "$" + fmtInt(VIEW.benchmarks.cac) + " cold-sales benchmark" },
      type: "line"
    },
    roas: {
      label: "ROAS",
      pick: function (point) { return point.roas; },
      format: function (n) { return n.toFixed(2) + "x"; },
      reference: { value: VIEW.benchmarks.breakeven_roas, label: VIEW.benchmarks.breakeven_roas.toFixed(1) + "x break-even" },
      type: "line"
    },
    spend: {
      label: "Daily spend",
      pick: function (point) { return { value: point.spend, reason: null }; },
      format: money,
      reference: null,
      type: "column"
    }
  };

  var mode = "compare";

  // ── formatting ─────────────────────────────────────────────────────────

  function fmtInt(n) { return Math.round(n).toLocaleString("en-US"); }

  function money(n) {
    if (n === null || n === undefined) return "—";
    if (Math.abs(n) >= 1000) return "$" + fmtInt(n);
    return "$" + n.toFixed(Math.abs(n) < 10 ? 2 : 0);
  }

  function parseDay(iso) {
    var parts = iso.split("-");
    return new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  }

  function shortDate(iso) {
    return parseDay(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }

  function el(name, attrs, parent) {
    var node = document.createElementNS(NS, name);
    for (var key in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, key)) node.setAttribute(key, attrs[key]);
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  /* Round a domain up to a readable tick step, so the axis reads
   * 0 / 200 / 400 rather than 0 / 173 / 346. */
  function niceTicks(max, count) {
    if (!isFinite(max) || max <= 0) return [0, 1];
    var rough = max / count;
    var magnitude = Math.pow(10, Math.floor(Math.log(rough) / Math.LN10));
    var candidates = [1, 2, 2.5, 5, 10];
    var step = magnitude * 10;
    for (var i = 0; i < candidates.length; i++) {
      if (magnitude * candidates[i] >= rough) { step = magnitude * candidates[i]; break; }
    }
    var ticks = [];
    for (var value = 0; value <= max + step * 0.001; value += step) ticks.push(value);
    return ticks;
  }

  // ── chart drawing ──────────────────────────────────────────────────────

  function seriesFor(chartKey) {
    var config = CHARTS[chartKey];
    return MODES[mode].map(function (key) {
      var block = BLOCKS[key];
      return {
        key: key,
        label: block.label,
        color: COLORS[key] || COLORS.blended,
        points: block.series.map(function (point) {
          var metric = config.pick(point);
          return { date: point.date, value: metric ? metric.value : null, reason: metric ? metric.reason : null };
        })
      };
    });
  }

  function draw(card) {
    var chartKey = card.getAttribute("data-chart");
    var config = CHARTS[chartKey];
    var plot = card.querySelector(".chart-plot");
    var legendBox = card.querySelector(".chart-legend");
    var series = seriesFor(chartKey);

    plot.textContent = "";

    var dates = series.length ? series[0].points.map(function (p) { return p.date; }) : [];
    if (!dates.length) {
      plot.innerHTML = '<p class="empty">No data in this period.</p>';
      legendBox.hidden = true;
      return;
    }

    // Legend: always present for two or more series; a single series is named
    // by the chart title instead.
    if (series.length > 1) {
      legendBox.hidden = false;
      legendBox.innerHTML = series.map(function (s) {
        return '<span class="legend-item"><span class="legend-key" style="background:' + s.color +
          '"></span>' + s.label + "</span>";
      }).join("");
    } else {
      legendBox.hidden = true;
    }

    var width = Math.max(plot.clientWidth || 640, 320);
    var plotHeight = width < 520 ? 150 : 190;
    var pad = { top: 14, right: width < 520 ? 12 : 96, bottom: 24, left: 52 };
    var height = plotHeight + pad.top + pad.bottom;
    var innerWidth = width - pad.left - pad.right;

    var svg = el("svg", {
      viewBox: "0 0 " + width + " " + height,
      width: width, height: height, role: "img",
      "aria-label": config.label + " over " + VIEW.window.pretty
    }, plot);

    var stacked = config.type === "column" && series.length > 1;
    var maxValue = 0;
    if (stacked) {
      dates.forEach(function (_, index) {
        var total = 0;
        series.forEach(function (s) { total += s.points[index].value || 0; });
        if (total > maxValue) maxValue = total;
      });
    } else {
      series.forEach(function (s) {
        s.points.forEach(function (p) { if (p.value !== null && p.value > maxValue) maxValue = p.value; });
      });
    }
    if (config.reference && config.reference.value > maxValue) maxValue = config.reference.value;
    if (maxValue <= 0) maxValue = 1;

    var ticks = niceTicks(maxValue * 1.08, 4);
    var domainMax = ticks[ticks.length - 1];
    function y(value) { return pad.top + plotHeight - (value / domainMax) * plotHeight; }
    function x(index) {
      if (dates.length === 1) return pad.left + innerWidth / 2;
      return pad.left + (index / (dates.length - 1)) * innerWidth;
    }

    // Gridlines: hairline, solid, one step off the surface.
    ticks.forEach(function (tick) {
      el("line", { x1: pad.left, x2: pad.left + innerWidth, y1: y(tick), y2: y(tick), stroke: GRID, "stroke-width": 1 }, svg);
      var label = el("text", { x: pad.left - 8, y: y(tick) + 3.5, "text-anchor": "end", class: "tick" }, svg);
      label.textContent = chartKey === "roas" ? tick.toFixed(1) + "x" : money(tick);
    });
    el("line", { x1: pad.left, x2: pad.left + innerWidth, y1: y(0), y2: y(0), stroke: AXIS, "stroke-width": 1 }, svg);

    // X labels: first, middle, last only — a label per day is unreadable.
    [0, Math.floor((dates.length - 1) / 2), dates.length - 1].forEach(function (index, position) {
      if (index < 0 || (position > 0 && index === 0)) return;
      var label = el("text", {
        x: x(index), y: height - 7, class: "tick",
        "text-anchor": position === 0 ? "start" : (position === 2 ? "end" : "middle")
      }, svg);
      label.textContent = shortDate(dates[index]);
    });

    if (config.type === "column") {
      drawColumns(svg, series, dates, x, y, innerWidth, stacked, pad);
    } else {
      drawLines(svg, series, dates, x, y, pad, innerWidth, width, config);
    }

    // Reference line last, so it sits above the fills but stays quiet: solid
    // hairline in ink, labelled, never dashed and never a gridline colour.
    if (config.reference && config.reference.value > 0) {
      var refY = y(config.reference.value);
      el("line", { x1: pad.left, x2: pad.left + innerWidth, y1: refY, y2: refY, stroke: INK2, "stroke-width": 1 }, svg);
      var refLabel = el("text", {
        x: width < 520 ? pad.left + 2 : pad.left + innerWidth + 8,
        y: refY - (width < 520 ? 5 : -3.5),
        class: "ref-label",
        "text-anchor": "start"
      }, svg);
      refLabel.textContent = config.reference.label;
    }

    attachHover(card, svg, series, dates, x, pad, innerWidth, config);
    renderTable(card, series, dates, config);
  }

  function drawColumns(svg, series, dates, x, y, innerWidth, stacked, pad) {
    var slot = innerWidth / Math.max(dates.length, 1);
    var barWidth = Math.max(2, Math.min(24, slot * 0.62));
    var GAP = 2; // surface gap between stacked segments

    dates.forEach(function (_, index) {
      var cursor = y(0);
      series.forEach(function (s) {
        var value = s.points[index].value;
        if (value === null || value <= 0) return;
        var top = stacked ? cursor - (y(0) - y(value)) : y(value);
        var barHeight = y(0) - y(value) - (stacked && cursor < y(0) ? GAP : 0);
        if (barHeight <= 0) return;
        var barTop = stacked ? cursor - barHeight : top;
        el("path", {
          d: roundedTop(x(index) - barWidth / 2, barTop, barWidth, barHeight, 4),
          fill: s.color
        }, svg);
        cursor = barTop - (stacked ? GAP : 0);
      });
    });
  }

  /* Column with a 4px rounded data-end and square corners at the baseline. */
  function roundedTop(left, top, width, height, radius) {
    var r = Math.min(radius, width / 2, height);
    return "M" + left + "," + (top + height) +
      "L" + left + "," + (top + r) +
      "Q" + left + "," + top + " " + (left + r) + "," + top +
      "L" + (left + width - r) + "," + top +
      "Q" + (left + width) + "," + top + " " + (left + width) + "," + (top + r) +
      "L" + (left + width) + "," + (top + height) + "Z";
  }

  function drawLines(svg, series, dates, x, y, pad, innerWidth, width, config) {
    series.forEach(function (s) {
      // Nulls break the path rather than being drawn through: a week with no
      // conversions has no cost per account, and a line across the gap would
      // invent one.
      var segments = [];
      var current = [];
      s.points.forEach(function (point, index) {
        if (point.value === null) {
          if (current.length) segments.push(current);
          current = [];
        } else {
          current.push([x(index), y(point.value)]);
        }
      });
      if (current.length) segments.push(current);

      segments.forEach(function (segment) {
        if (segment.length === 1) {
          el("circle", { cx: segment[0][0], cy: segment[0][1], r: 2.5, fill: s.color }, svg);
          return;
        }
        el("path", {
          d: segment.map(function (point, i) { return (i ? "L" : "M") + point[0] + "," + point[1]; }).join(""),
          fill: "none", stroke: s.color, "stroke-width": 2,
          "stroke-linejoin": "round", "stroke-linecap": "round"
        }, svg);
      });

      // End marker with a 2px surface ring, and a direct label for the last
      // known value — labelled selectively, never on every point.
      var last = null;
      for (var i = s.points.length - 1; i >= 0; i--) {
        if (s.points[i].value !== null) { last = { index: i, value: s.points[i].value }; break; }
      }
      if (last) {
        el("circle", { cx: x(last.index), cy: y(last.value), r: 4.5, fill: s.color, stroke: SURFACE, "stroke-width": 2 }, svg);
        if (width >= 520) {
          var label = el("text", {
            x: x(last.index) + 10, y: y(last.value) + 4, class: "tick",
            style: "font-weight:600;fill:" + INK2
          }, svg);
          label.textContent = config.format(last.value);
        }
      }
    });
  }

  // ── hover ──────────────────────────────────────────────────────────────

  function attachHover(card, svg, series, dates, x, pad, innerWidth, config) {
    var plot = card.querySelector(".chart-plot");
    var tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    plot.appendChild(tooltip);

    var box = svg.viewBox.baseVal;
    var crosshair = el("line", {
      y1: pad.top, y2: pad.top + (box.height - pad.top - pad.bottom),
      stroke: AXIS, "stroke-width": 1, opacity: 0
    }, svg);

    function nearest(clientX) {
      var rect = svg.getBoundingClientRect();
      var scale = box.width / rect.width;
      var localX = (clientX - rect.left) * scale;
      var best = 0, bestDistance = Infinity;
      for (var i = 0; i < dates.length; i++) {
        var distance = Math.abs(x(i) - localX);
        if (distance < bestDistance) { bestDistance = distance; best = i; }
      }
      return best;
    }

    function show(clientX) {
      var index = nearest(clientX);
      var rect = svg.getBoundingClientRect();
      var scale = rect.width / box.width;

      crosshair.setAttribute("x1", x(index));
      crosshair.setAttribute("x2", x(index));
      crosshair.setAttribute("opacity", 1);

      var rows = series.map(function (s) {
        var point = s.points[index];
        var value = point.value === null
          ? '<span class="unknown" title="' + (point.reason || "No data.") + '">—</span>'
          : config.format(point.value);
        return '<div class="tt-row"><span class="tt-name"><span class="legend-key" style="background:' +
          s.color + '"></span>' + s.label + '</span><span class="tt-value">' + value + "</span></div>";
      }).join("");

      tooltip.innerHTML = '<div class="tt-date">' + shortDate(dates[index]) + "</div>" + rows;
      tooltip.classList.add("is-visible");
      var left = Math.min(Math.max(x(index) * scale, 90), rect.width - 90);
      tooltip.style.left = left + "px";
      tooltip.style.top = (pad.top * scale) + "px";
    }

    function hide() {
      crosshair.setAttribute("opacity", 0);
      tooltip.classList.remove("is-visible");
    }

    svg.addEventListener("mousemove", function (event) { show(event.clientX); });
    svg.addEventListener("mouseleave", hide);
    svg.addEventListener("touchstart", function (event) { show(event.touches[0].clientX); }, { passive: true });
    svg.addEventListener("touchmove", function (event) { show(event.touches[0].clientX); }, { passive: true });
    svg.addEventListener("touchend", hide);
  }

  // ── the table twin ─────────────────────────────────────────────────────

  function renderTable(card, series, dates, config) {
    var target = card.querySelector(".chart-table");
    var head = "<tr><th>Date</th>" + series.map(function (s) {
      return "<th>" + s.label + "</th>";
    }).join("") + "</tr>";

    var body = dates.map(function (date, index) {
      var cells = series.map(function (s) {
        var point = s.points[index];
        return '<td class="num">' + (point.value === null
          ? '<span class="unknown" title="' + (point.reason || "No data.") + '">—</span>'
          : config.format(point.value)) + "</td>";
      }).join("");
      return "<tr><th>" + shortDate(date) + "</th>" + cells + "</tr>";
    }).join("");

    target.innerHTML = "<table><thead>" + head + "</thead><tbody>" + body + "</tbody></table>";
  }

  // ── sparklines ─────────────────────────────────────────────────────────

  function sparklines() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-spark]"), function (cell) {
      var values = cell.getAttribute("data-spark").split(",").map(Number).filter(function (n) { return !isNaN(n); });
      if (values.length < 2) return;
      var w = 74, h = 20, max = Math.max.apply(null, values) || 1;
      var svg = el("svg", { viewBox: "0 0 " + w + " " + h, width: w, height: h, "aria-hidden": "true" });
      var points = values.map(function (value, index) {
        return [(index / (values.length - 1)) * (w - 4) + 2, h - 3 - (value / max) * (h - 6)];
      });
      el("path", {
        d: points.map(function (p, i) { return (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(""),
        fill: "none", stroke: AXIS, "stroke-width": 1.5, "stroke-linejoin": "round", "stroke-linecap": "round"
      }, svg);
      var last = points[points.length - 1];
      el("circle", {
        cx: last[0].toFixed(1), cy: last[1].toFixed(1), r: 2.5,
        fill: COLORS[cell.getAttribute("data-channel")] || COLORS.blended
      }, svg);
      cell.appendChild(svg);
    });
  }

  // ── interaction wiring ─────────────────────────────────────────────────

  function drawAll() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-chart]"), draw);
  }

  document.querySelectorAll(".toggle-btn").forEach(function (button) {
    button.addEventListener("click", function () {
      document.querySelectorAll(".toggle-btn").forEach(function (other) {
        other.classList.toggle("is-selected", other === button);
      });
      mode = button.getAttribute("data-mode");
      drawAll();
    });
  });

  document.querySelectorAll(".table-toggle").forEach(function (button) {
    button.addEventListener("click", function () {
      var card = button.closest("[data-chart]");
      var table = card.querySelector(".chart-table");
      var open = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", String(!open));
      table.hidden = open;
    });
  });

  // Tap-to-open tooltips, so the explanations work on a phone too.
  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-tip]");
    document.querySelectorAll(".tip-open").forEach(function (open) {
      if (open !== trigger) open.classList.remove("tip-open");
    });
    if (trigger) trigger.classList.toggle("tip-open");
  });

  // Sortable campaign table.
  var table = document.getElementById("campaign-table");
  if (table) {
    table.querySelectorAll("thead th[data-sort]").forEach(function (header, columnIndex) {
      header.addEventListener("click", function () {
        var body = table.tBodies[0];
        var rows = Array.prototype.slice.call(body.rows);
        var descending = header.getAttribute("aria-sort") === "ascending";
        var numeric = header.getAttribute("data-sort") === "number";

        table.querySelectorAll("thead th").forEach(function (other) { other.removeAttribute("aria-sort"); });
        header.setAttribute("aria-sort", descending ? "descending" : "ascending");

        rows.sort(function (a, b) {
          var cellA = a.cells[columnIndex], cellB = b.cells[columnIndex];
          if (numeric) {
            var numberA = Number(cellA.getAttribute("data-value"));
            var numberB = Number(cellB.getAttribute("data-value"));
            return descending ? numberB - numberA : numberA - numberB;
          }
          var textA = cellA.textContent.trim(), textB = cellB.textContent.trim();
          return descending ? textB.localeCompare(textA) : textA.localeCompare(textB);
        });
        rows.forEach(function (row) { body.appendChild(row); });
      });
    });
  }

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(drawAll, 140);
  });

  drawAll();
  sparklines();
})();
