/* Страница «Сервер»: графики нагрузки хоста (диск/CPU/RAM/сеть/load) + алерт-баннер.
 * Данные: GET /api/user/server-metrics (только модераторы). */
(function () {
    "use strict";

    var API_URL = "/api/user/server-metrics";
    var RANGE_HOURS = { "1d": 24, "3d": 72, "7d": 168, "30d": 720 };
    var METRIC_LABELS = { disk: "Диск", cpu: "CPU", mem: "RAM", load5: "Load avg (5 мин)" };

    var els = {
        range: document.getElementById("server-range"),
        from: document.getElementById("server-from"),
        to: document.getElementById("server-to"),
        autorefresh: document.getElementById("server-autorefresh"),
        refresh: document.getElementById("server-refresh"),
        empty: document.getElementById("serverEmpty"),
        charts: document.getElementById("serverCharts"),
        banner: document.getElementById("serverAlertBanner")
    };

    var charts = {};
    var autoRefreshTimer = null;

    function themeColors() {
        var style = getComputedStyle(document.documentElement);
        return {
            text: style.getPropertyValue("--color-text-muted").trim() || "#666",
            grid: style.getPropertyValue("--color-border").trim() || "#ddd",
            primary: style.getPropertyValue("--color-primary").trim() || "#4CAF50",
            danger: style.getPropertyValue("--color-danger").trim() || "#e53935"
        };
    }

    function pad(n) { return n < 10 ? "0" + n : String(n); }

    function formatLabel(iso) {
        var d = new Date(iso);
        return pad(d.getDate()) + "." + pad(d.getMonth() + 1) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
    }

    function toLocalInputValue(date) {
        return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) +
            "T" + pad(date.getHours()) + ":" + pad(date.getMinutes());
    }

    function isoFromInput(input) {
        if (!input.value) return null;
        var d = new Date(input.value);
        return isNaN(d.getTime()) ? null : d.toISOString();
    }

    function applyRangePreset() {
        if (els.range.value === "custom") return;
        var hours = RANGE_HOURS[els.range.value] || 168;
        var from = new Date(Date.now() - hours * 3600 * 1000);
        els.from.value = toLocalInputValue(from);
        els.to.value = "";
    }

    function buildUrl() {
        var params = new URLSearchParams();
        var from = isoFromInput(els.from);
        var to = isoFromInput(els.to);
        if (from) params.set("from", from);
        if (to) params.set("to", to);
        return API_URL + (params.toString() ? "?" + params.toString() : "");
    }

    function writeUrlState() {
        var params = new URLSearchParams();
        var from = isoFromInput(els.from);
        var to = isoFromInput(els.to);
        if (from) params.set("from", from);
        if (to) params.set("to", to);
        if (els.range.value !== "custom") params.set("range", els.range.value);
        if (els.autorefresh.value !== "0") params.set("autorefresh", els.autorefresh.value);
        var url = location.pathname + (params.toString() ? "?" + params.toString() : "");
        history.replaceState(null, "", url);
    }

    function readUrlState() {
        var params = new URLSearchParams(location.search);
        var from = params.get("from");
        var to = params.get("to");
        if (from) { var fd = new Date(from); if (!isNaN(fd.getTime())) els.from.value = toLocalInputValue(fd); }
        if (to) { var td = new Date(to); if (!isNaN(td.getTime())) els.to.value = toLocalInputValue(td); }
        var range = params.get("range");
        if (range && RANGE_HOURS[range]) els.range.value = range;
        var ar = params.get("autorefresh");
        if (ar !== null) els.autorefresh.value = ar === "600" ? "600" : "0";
    }

    function renderBanner(alerts) {
        var hasCritical = false;
        var lines = [];
        alerts.forEach(function (a) {
            var label = METRIC_LABELS[a.metric] || a.metric;
            var unit = a.metric === "load5" ? "" : "%";
            lines.push("⚠ " + label + ": " + a.value + unit + " (лимит " + a.limit + unit + ")");
            if (a.severity === "critical") hasCritical = true;
        });
        if (!lines.length) {
            els.banner.classList.remove("show", "warning");
            els.banner.textContent = "";
            return;
        }
        els.banner.textContent = lines.join("\n");
        els.banner.classList.toggle("warning", !hasCritical);
        els.banner.classList.add("show");
    }

    function destroyCharts() {
        Object.keys(charts).forEach(function (key) {
            if (charts[key]) { charts[key].destroy(); charts[key] = null; }
        });
    }

    function baseOptions(colors, percentScale) {
        var scales = {
            x: {
                ticks: { color: colors.text, maxRotation: 0, autoSkipPadding: 20 },
                grid: { color: colors.grid }
            },
            y: {
                ticks: { color: colors.text },
                grid: { color: colors.grid }
            }
        };
        if (percentScale) {
            scales.y.suggestedMin = 0;
            scales.y.suggestedMax = 100;
        }
        return {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            scales: scales,
            plugins: { legend: { labels: { color: colors.text } } }
        };
    }

    function makeChart(canvasId, datasets, percentScale) {
        var ctx = document.getElementById(canvasId);
        if (!ctx) return null;
        var colors = themeColors();
        datasets.forEach(function (ds, i) {
            ds.borderColor = ds.borderColor || (i === 0 ? colors.primary : colors.danger);
            ds.backgroundColor = ds.borderColor;
            ds.tension = 0.25;
            ds.pointRadius = 1.5;
            ds.fill = false;
            ds.spanGaps = true;
        });
        return new Chart(ctx, {
            type: "line",
            data: { datasets: datasets },
            options: baseOptions(colors, percentScale)
        });
    }

    function labelsOf(points) {
        return points.map(function (p) { return formatLabel(p.datetime); });
    }

    function withLabels(dataset, points) {
        // Категорийная ось по меткам времени (без адаптера time scale).
        dataset.data = points.map(function (p) { return p[dataset.metaField]; });
        return dataset;
    }

    function renderCharts(points) {
        destroyCharts();
        if (!points.length) return;

        function ds(label, field, color) {
            return { label: label, metaField: field, borderColor: color, data: [] };
        }

        var configs = [
            ["serverDiskChart", [ds("Занято", "disk_used_percent")], true],
            ["serverCpuChart", [ds("avg", "cpu_avg"), ds("max", "cpu_max")], true],
            ["serverRamChart", [ds("Занято", "mem_used_percent")], true],
            [
                "serverNetChart",
                [ds("rx", "net_rx_mb"), ds("tx", "net_tx_mb")],
                false
            ],
            [
                "serverLoadChart",
                [ds("1 мин", "load1_avg"), ds("5 мин", "load5_avg"), ds("15 мин", "load15_avg")],
                false
            ],
            ["serverDiskFreeChart", [ds("Свободно, ГБ", "disk_free_gb")], false]
        ];

        configs.forEach(function (item) {
            var canvasId = item[0];
            var datasets = item[1];
            var percentScale = item[2];
            datasets.forEach(function (dataset) { withLabels(dataset, points); });
            charts[canvasId] = makeChart(canvasId, datasets, percentScale);
        });

        // Единые подписи оси X для всех графиков.
        var labels = labelsOf(points);
        Object.keys(charts).forEach(function (key) {
            if (charts[key]) {
                charts[key].data.labels = labels;
                charts[key].update();
            }
        });
    }

    function renderEmpty(text) {
        els.empty.textContent = text;
        els.empty.hidden = false;
        els.charts.style.display = "none";
        destroyCharts();
    }

    function refresh() {
        var btn = els.refresh;
        btn.disabled = true;
        btn.textContent = "Загрузка…";
        fetch(buildUrl(), { credentials: "same-origin" })
            .then(function (resp) {
                if (resp.status === 401) throw new Error("Нужна авторизация.");
                if (!resp.ok) throw new Error("Ошибка загрузки (" + resp.status + ").");
                // Пустое тело (прокси/сбой) без проверки падало бы внутри resp.json()
                // с невнятным «Unexpected end of JSON input».
                return resp.text().then(function (text) {
                    if (!text.trim()) throw new Error("Пустой ответ сервера.");
                    return JSON.parse(text);
                });
            })
            .then(function (data) {
                renderBanner(data.alerts || []);
                if (!data.points || !data.points.length) {
                    renderEmpty("Нет данных за период — коллектор недоступен (нет /proc или bind-директории) либо метрики ещё не собраны.");
                    return;
                }
                els.empty.hidden = true;
                els.charts.style.display = "";
                renderCharts(data.points);
            })
            .catch(function (err) {
                renderEmpty(String(err.message || err));
            })
            .finally(function () {
                btn.disabled = false;
                btn.textContent = "Обновить";
            });
    }

    function startAutoRefresh() {
        stopAutoRefresh();
        var sec = Number(els.autorefresh.value);
        if (sec > 0) autoRefreshTimer = setInterval(refresh, sec * 1000);
    }

    function stopAutoRefresh() {
        if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
    }

    function onChange(fromUser) {
        if (fromUser && els.range.value !== "custom") applyRangePreset();
        writeUrlState();
        refresh();
    }

    els.range.addEventListener("change", function () { onChange(true); });
    els.from.addEventListener("change", function () { els.range.value = "custom"; onChange(false); });
    els.to.addEventListener("change", function () { els.range.value = "custom"; onChange(false); });
    els.autorefresh.addEventListener("change", function () { startAutoRefresh(); writeUrlState(); });
    els.refresh.addEventListener("click", function () { refresh(); });

    // Перерисовать графики при смене темы (цвета берутся из CSS-переменных).
    var themeToggleBtn = document.getElementById("themeToggle");
    if (themeToggleBtn) {
        themeToggleBtn.addEventListener("click", function () {
            setTimeout(refresh, 50);
        });
    }

    readUrlState();
    if (els.range.value !== "custom" && !els.from.value) applyRangePreset();
    startAutoRefresh();
    writeUrlState();
    refresh();
})();
