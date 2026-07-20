// График мониторинга сервиса. Берёт данные с /api/user/stats и рисует через Chart.js.
// Все даты на сервере в UTC; на фронте конвертируются в локальное время.
// Выбранные фильтры синхронизируются с URL (?type=...&period=...&from=...&to=...&subtype=...),
// чтобы ссылку можно было поделиться.

(function () {
    "use strict";

    const SUBTYPES_BY_TYPE = {
        message_incoming: [""],
        message_outgoing: [""],
        reward_memecoins: ["", "received", "succeed", "failed"],
        reward_ai_stickers: ["", "received", "success", "failed_on_moderation"],
        command_handled: null, // subtype = имя команды; список динамический — оставляем пустым (все)
        message_processing_time: [""],
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
        message_processing_time: "Время обработки сообщения",
    };

    // Типы метрик, для которых значение — это «среднее» (мс), а не «количество».
    // Для них тултип показывает «Avg: X ms», а не RPS.
    const TIMING_TYPES = new Set(["message_processing_time"]);

    // Дефолтный показываемый диапазон при отсутствии ?from= в URL.
    // Не равен максимальному разрешённому окну API — выбран поменьше, чтобы
    // сразу видеть читаемый график, а не «растянутую плоскую линию».
    const PERIOD_DEFAULT_HOURS = {
        "10m": 2,        // 2 часа
        "1h": 24,        // сутки
        "3h": 72,        // 3 дня
        "6h": 24 * 7,    // неделя
        "1d": 24 * 30,   // 30 дней
    };

    // Длительность одного бакета в секундах — для расчёта Average RPS в тултипе.
    const PERIOD_SECONDS = {
        "10m": 10 * 60,
        "1h": 60 * 60,
        "3h": 3 * 60 * 60,
        "6h": 6 * 60 * 60,
        "1d": 24 * 60 * 60,
    };

    const typeSelect = document.getElementById("stats-type");
    const subtypeSelect = document.getElementById("stats-subtype");
    const periodSelect = document.getElementById("stats-period");
    const fromInput = document.getElementById("stats-from");
    const toInput = document.getElementById("stats-to");
    const refreshBtn = document.getElementById("stats-refresh");
    const emptyEl = document.getElementById("stats-empty");
    const canvas = document.getElementById("statsChart");
    const chartWrap = canvas.parentElement;

    let chart = null;

    // ------------------------------------------------------------------
    // Вспомогательные функции для дат
    // ------------------------------------------------------------------

    function pad(n) {
        return String(n).padStart(2, "0");
    }

    function toLocalInputValue(dt) {
        // Возвращает строку YYYY-MM-DDTHH:MM для <input type="datetime-local"> в локальном времени.
        return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
    }

    function isoToLocalInput(iso) {
        if (!iso) return "";
        const dt = new Date(iso);
        if (isNaN(dt.getTime())) return "";
        return toLocalInputValue(dt);
    }

    function isoFromInput(value) {
        if (!value) return null;
        // <input datetime-local> отдаёт локальное время; переводим в UTC ISO.
        const dt = new Date(value);
        if (isNaN(dt.getTime())) return null;
        return dt.toISOString();
    }

    function formatLocal(dt) {
        return `${pad(dt.getDate())}.${pad(dt.getMonth() + 1)} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
    }

    function defaultRange() {
        // Дефолтный диапазон: now-PERIOD_DEFAULT_HOURS → now.
        const now = new Date();
        const hours = PERIOD_DEFAULT_HOURS[periodSelect.value] || 2;
        const from = new Date(now.getTime() - hours * 3600 * 1000);
        fromInput.value = toLocalInputValue(from);
        toInput.value = "";
    }

    // ------------------------------------------------------------------
    // Синхронизация состояния с URL
    // ------------------------------------------------------------------

    function readUrlState() {
        const params = new URLSearchParams(window.location.search);

        const type = params.get("type");
        if (type && TYPE_LABELS[type]) {
            typeSelect.value = type;
        }

        subtypeOptions(typeSelect.value);

        const subtype = params.get("subtype");
        if (subtype) {
            // Проверяем, что подтип валиден для текущего типа (иначе селект его не покажет).
            const allowed = SUBTYPES_BY_TYPE[typeSelect.value];
            if (allowed === null || allowed.includes(subtype)) {
                subtypeSelect.value = subtype;
            }
        }

        const period = params.get("period");
        if (period && PERIOD_DEFAULT_HOURS[period]) {
            periodSelect.value = period;
        }

        const from = params.get("from");
        const to = params.get("to");
        if (from || to) {
            fromInput.value = isoToLocalInput(from);
            toInput.value = isoToLocalInput(to);
        } else {
            defaultRange();
        }
    }

    function writeUrlState() {
        const params = new URLSearchParams();
        params.set("type", typeSelect.value);
        const sub = subtypeSelect.value;
        if (sub) params.set("subtype", sub);
        params.set("period", periodSelect.value);
        const from = isoFromInput(fromInput.value);
        const to = isoFromInput(toInput.value);
        if (from) params.set("from", from);
        if (to) params.set("to", to);
        const url = `${window.location.pathname}?${params.toString()}`;
        history.replaceState(null, "", url);
    }

    // ------------------------------------------------------------------
    // Управление подтипами
    // ------------------------------------------------------------------

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

    // ------------------------------------------------------------------
    // Построение запроса и отрисовка
    // ------------------------------------------------------------------

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

    function themeColors() {
        const css = getComputedStyle(document.documentElement);
        const get = (name) => css.getPropertyValue(name).trim();
        return {
            primary: get("--color-primary") || "#4CAF50",
            text: get("--color-text") || "#333",
            textMuted: get("--color-text-muted") || "#666",
            border: get("--color-border") || "#ccc",
        };
    }

    function renderEmpty(text) {
        emptyEl.style.display = "block";
        emptyEl.textContent = text;
        chartWrap.style.display = "none";
    }

    function formatRps(rps) {
        if (rps >= 1) return rps.toFixed(2);
        if (rps >= 0.01) return rps.toFixed(3);
        return rps.toFixed(4);
    }

    function renderChart(points, typeLabel, subtypeLabel, period, typeValue) {
        if (!points || points.length === 0) {
            renderEmpty("Нет данных за выбранный период.");
            return;
        }
        emptyEl.style.display = "none";
        chartWrap.style.display = "block";

        // Pydantic отдаёт datetime как ISO-строку с таймзоной (+00:00 или Z);
        // дописывать "Z" нельзя — получится невалидная дата (NaN).
        const labels = points.map((p) => formatLocal(new Date(p.datetime)));
        const data = points.map((p) => p.value);

        // Длительность бакета для расчёта RPS. Если точки идут неравномерно
        // (server вернул с zero-fill'ом — должен идти равномерно), берём
        // разницу между соседними точками; для единственной точки — длительность периода.
        const bucketSeconds = PERIOD_SECONDS[period] || 600;
        const isTiming = TIMING_TYPES.has(typeValue);

        destroyChart();
        const colors = themeColors();
        chart = new Chart(canvas, {
            type: "line",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: `${typeLabel}${subtypeLabel ? " / " + subtypeLabel : ""}`,
                        data: data,
                        borderColor: colors.primary,
                        backgroundColor: colors.primary + "22",
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
                color: colors.text,
                scales: {
                    x: {
                        ticks: {
                            color: colors.textMuted,
                            maxRotation: 45,
                            autoSkip: true,
                            maxTicksLimit: 24,
                        },
                        grid: { color: colors.border + "55" },
                    },
                    y: {
                        beginAtZero: true,
                        ticks: { color: colors.textMuted, precision: 0 },
                        grid: { color: colors.border + "55" },
                    },
                },
                plugins: {
                    legend: { labels: { color: colors.text } },
                    tooltip: {
                        callbacks: {
                            title: (items) => items[0].label,
                            label: (item) => {
                                const value = item.parsed.y;
                                if (isTiming) {
                                    // value = avg ms за бакет (уже усреднённое сервером).
                                    return [
                                        `${item.dataset.label}: ${value} ms`,
                                    ];
                                }
                                const rps = value / bucketSeconds;
                                return [
                                    `${item.dataset.label}: ${value}`,
                                    `Average RPS: ${formatRps(rps)}`,
                                ];
                            },
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
            renderChart(payload.points, typeLabel, subtypeLabel, payload.period, payload.type);
        } catch (e) {
            console.error(e);
            renderEmpty("Ошибка сети при загрузке данных.");
        } finally {
            refreshBtn.disabled = false;
            refreshBtn.textContent = "Обновить";
        }
    }

    // Обработчики: при смене типа сбрасываем только подтип (если стал невалиден),
    // но НЕ даты. При смене периода — тоже не трогаем даты.
    function onChange(shouldWriteUrl) {
        if (shouldWriteUrl) writeUrlState();
        refresh();
    }

    typeSelect.addEventListener("change", () => {
        const prevSubtype = subtypeSelect.value;
        subtypeOptions(typeSelect.value);
        // Если прежний подтип не подходит новому типу — он уже сброшен селектом.
        if (subtypeSelect.value !== prevSubtype) {
            // ничего дополнительно делать не надо — subtypeOptions уже выставил дефолт
        }
        onChange(true);
    });
    subtypeSelect.addEventListener("change", () => onChange(true));
    periodSelect.addEventListener("change", () => onChange(true));
    fromInput.addEventListener("change", () => onChange(true));
    toInput.addEventListener("change", () => onChange(true));
    refreshBtn.addEventListener("click", () => onChange(true));

    // Перерисовка при смене темы — цвета читаются из CSS-переменных.
    const themeToggle = document.getElementById("themeToggle");
    if (themeToggle) {
        themeToggle.addEventListener("click", () => setTimeout(refresh, 50));
    }

    // ------------------------------------------------------------------
    // Инициализация
    // ------------------------------------------------------------------
    readUrlState();
    writeUrlState();
    refresh();
})();
