// График мониторинга сервиса. Берёт данные с /api/user/stats и рисует через Chart.js.
// Все даты на сервере в UTC; на фронте конвертируются в локальное время через dayjs-подобный форматтер Chart.js (адаптер date-fns не подключаем — Chart.js умеет Date напрямую).

(function () {
    "use strict";

    const SUBTYPES_BY_TYPE = {
        message_incoming: [""],
        message_outgoing: [""],
        reward_memecoins: ["", "received", "succeed", "failed"],
        reward_ai_stickers: ["", "received", "success", "failed_on_moderation"],
        command_handled: null, // subtype = имя команды; список динамический — оставляем пустым (все)
    };

    const SUBTYPE_LABELS = {
        "": "(все)",
        received: "получено",
        succeed: "успешно",
        success: "успешно",
        failed: "ошибки",
        failed_on_moderation: "отклонено модерацией",
    };

    const TYPE_LABELS = {
        message_incoming: "Входящие сообщения",
        message_outgoing: "Исходящие сообщения",
        reward_memecoins: "Награды: мемкоины",
        reward_ai_stickers: "Награды: ИИ-стикеры",
        command_handled: "Команды",
    };

    const PERIOD_DEFAULT_FROM_HOURS = {
        "10m": 24,
        "1h": 24 * 5,
        "3h": 24 * 14,
        "6h": 24 * 30,
        "1d": 24 * 90,
    };

    const typeSelect = document.getElementById("stats-type");
    const subtypeSelect = document.getElementById("stats-subtype");
    const periodSelect = document.getElementById("stats-period");
    const fromInput = document.getElementById("stats-from");
    const toInput = document.getElementById("stats-to");
    const refreshBtn = document.getElementById("stats-refresh");
    const emptyEl = document.getElementById("stats-empty");
    const canvas = document.getElementById("statsChart");

    let chart = null;

    function subtypeOptions(type) {
        const subs = SUBTYPES_BY_TYPE[type];
        subtypeSelect.innerHTML = "";
        if (subs === null) {
            subtypeSelect.appendChild(new Option("(все)", ""));
            return;
        }
        for (const s of subs) {
            subtypeSelect.appendChild(new Option(SUBTYPE_LABELS[s] || s, s));
        }
    }

    function toLocalInputValue(dt) {
        // Возвращает строку в формате YYYY-MM-DDTHH:MM для <input type="datetime-local"> в локальном времени.
        const pad = (n) => String(n).padStart(2, "0");
        return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
    }

    function defaultRange() {
        const now = new Date();
        const hours = PERIOD_DEFAULT_FROM_HOURS[periodSelect.value] || 24;
        const from = new Date(now.getTime() - hours * 3600 * 1000);
        fromInput.value = toLocalInputValue(from);
        toInput.value = "";
    }

    function isoFromInput(value) {
        if (!value) return null;
        // <input datetime-local> отдаёт локальное время; переводим в UTC ISO.
        const dt = new Date(value);
        if (isNaN(dt.getTime())) return null;
        return dt.toISOString();
    }

    function buildUrl() {
        const params = new URLSearchParams();
        params.set("type", typeSelect.value);
        const sub = subtypeSelect.value;
        if (sub) params.set("subtype", sub);
        params.set("period", periodSelect.value);
        const from = isoFromInput(fromInput.value);
        const to = isoFromInput(toInput.value);
        if (from) params.set("from", from);
        if (to) params.set("to", to);
        return `/api/user/stats?${params.toString()}`;
    }

    function destroyChart() {
        if (chart) {
            chart.destroy();
            chart = null;
        }
    }

    function renderEmpty(text) {
        emptyEl.style.display = "block";
        emptyEl.textContent = text;
        canvas.style.display = "none";
    }

    function formatLocal(dt) {
        const pad = (n) => String(n).padStart(2, "0");
        return `${pad(dt.getDate())}.${pad(dt.getMonth() + 1)} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
    }

    function renderChart(points, typeLabel, subtypeLabel, period) {
        if (!points || points.length === 0) {
            renderEmpty("Нет данных за выбранный период.");
            return;
        }
        emptyEl.style.display = "none";
        canvas.style.display = "block";

        const labels = points.map((p) => formatLocal(new Date(p.datetime + "Z")));
        const data = points.map((p) => p.value);

        destroyChart();
        chart = new Chart(canvas, {
            type: "line",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: `${typeLabel}${subtypeLabel ? " / " + subtypeLabel : ""}`,
                        data: data,
                        borderColor: "#6c4dff",
                        backgroundColor: "rgba(108, 77, 255, 0.15)",
                        fill: true,
                        tension: 0.25,
                        pointRadius: 1.5,
                        borderWidth: 2,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        ticks: { maxRotation: 45, autoSkip: true, maxTicksLimit: 24 },
                    },
                    y: { beginAtZero: true, ticks: { precision: 0 } },
                },
                plugins: {
                    tooltip: {
                        callbacks: {
                            title: (items) => items[0].label,
                        },
                    },
                },
            },
        });
    }

    async function refresh() {
        refreshBtn.disabled = true;
        refreshBtn.textContent = "Загрузка…";
        try {
            const resp = await fetch(buildUrl(), { credentials: "same-origin" });
            if (resp.status === 401) {
                renderEmpty("Нужна авторизация. Войдите на сайт и вернитесь на эту страницу.");
                return;
            }
            if (!resp.ok) {
                const txt = await resp.text();
                renderEmpty(`Ошибка ${resp.status}: ${txt}`);
                return;
            }
            const payload = await resp.json();
            const typeLabel = TYPE_LABELS[payload.type] || payload.type;
            const subtypeLabel = payload.subtype ? SUBTYPE_LABELS[payload.subtype] || payload.subtype : "";
            renderChart(payload.points, typeLabel, subtypeLabel, payload.period);
        } catch (e) {
            console.error(e);
            renderEmpty("Ошибка сети при загрузке данных.");
        } finally {
            refreshBtn.disabled = false;
            refreshBtn.textContent = "Обновить";
        }
    }

    typeSelect.addEventListener("change", () => {
        subtypeOptions(typeSelect.value);
        defaultRange();
        refresh();
    });
    subtypeSelect.addEventListener("change", refresh);
    periodSelect.addEventListener("change", () => {
        defaultRange();
        refresh();
    });
    refreshBtn.addEventListener("click", refresh);

    // Инициализация.
    subtypeOptions(typeSelect.value);
    defaultRange();
    refresh();
})();
