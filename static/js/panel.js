/**
 * Контроллер страниц панели настроек: /panel/<section> (ES-модуль).
 * Главная /panel — статичный хаб без JS; здесь — страницы разделов.
 *
 * Интерактивные контролы — библиотека q0ui (ui/index.js): тумблеры, слайдеры,
 * степпер мемкоинов, статус-карточки. Здесь — только логика страницы:
 * сохранение настроек, ссылки-конфиги оверлеев, кнопки-действия.
 * Инициализация защищена от отсутствия элементов — один модуль обслуживает
 * все разделы, на чужой странице каждый init — no-op.
 * Логика секции Telegram — panel-telegram.js.
 *
 * Конфиг страницы (id канала, секрет оверлеев) приходит из
 * templates/panel/base_section.html через window.__panel.
 */
import "./ui/index.js";
import "./panel-telegram.js";
import { bindSettingSwitches, createRewardButton } from "./ui/bindings.js";
import { debounce } from "./ui/utils.js";

const CFG = window.__panel ?? { channel_id: "", channel_name: "", slovotron_secret: "" };
const YM_ID = 108266334;
const HEAT_EXT_URL = "https://dashboard.twitch.tv/extensions/cr20njfkgll4okyrhag7xxph270sqk-2.1.1";

const byId = (id) => document.getElementById(id);
const reachGoal = (goal) => {
    if (typeof ym === "function") ym(YM_ID, "reachGoal", goal);
};

/** Обёртка fetch → {ok, data} с toast-уведомлением. */
async function fetchNotify(endpoint, options, fallbackTitle) {
    try {
        const res = await fetch(endpoint, { credentials: "same-origin", ...options });
        const data = await res.json();
        showNotification(data.title || fallbackTitle, data.message, !res.ok);
        return { ok: res.ok, data };
    } catch (e) {
        showNotification("Ошибка", e.message, true);
        return { ok: false, data: null };
    }
}

// ── Сохранение настроек (/api/user/update_settings) ─────────────────────────

// Очереди сохранений по имени настройки: быстрые переключения тумблера
// (вкл→выкл подряд) раньше уходили параллельными fetch'ами и могли
// примениться на сервере в обратном порядке. Теперь запросы одной настройки
// строго последовательны — сервер видит их в порядке кликов.
const settingSaveQueues = new Map();

function updateSetting(name, value) {
    const prev = settingSaveQueues.get(name) ?? Promise.resolve();
    const chain = prev.catch(() => {}).then(() => saveSetting(name, value));
    settingSaveQueues.set(name, chain);
    return chain;
}

async function saveSetting(name, value) {
    const delays = [1000, 2000, 4000, 7000];
    let attempt = 0;
    const encodedValue = encodeURIComponent(String(value));

    while (true) {
        try {
            const res = await fetch("/api/user/update_settings", {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: `${name}=${encodedValue}`,
                credentials: "same-origin",
            });

            // 500 и обрывы сети ретраим с нарастающей задержкой.
            if (res.status === 500 && attempt < delays.length) {
                await new Promise((resolve) => setTimeout(resolve, delays[attempt]));
                attempt++;
                continue;
            }

            const data = await res.json();
            showNotification(data.title || "Настройки", data.message, !res.ok);
            return;
        } catch (err) {
            if (attempt < delays.length) {
                await new Promise((resolve) => setTimeout(resolve, delays[attempt]));
                attempt++;
                continue;
            }
            showNotification("Ошибка", err.message, true);
            return;
        }
    }
}

/** Показ остальных настроек чат-бота — только когда бот включён. */
function updateDependentTogglesState() {
    const master = document.querySelector('q0ui-switch[name="enable_chat_bot"]');
    const enabled = master ? master.checked : false;
    ["dependent-toggles", "chatbot-extra-divider", "chatbot-extra-settings"].forEach((id) => {
        byId(id)?.classList.toggle("active", enabled);
    });
}

function initChatbotSection() {
    bindSettingSwitches(document, (name, checked) => {
        void updateSetting(name, checked);
        if (name === "enable_chat_bot") {
            if (checked) reachGoal("enableChatBot");
            updateDependentTogglesState();
        }
    });
    updateDependentTogglesState();

    document.querySelectorAll('input[name="chatbot_default_target_behaviour"]').forEach((radio) => {
        radio.addEventListener("change", (event) => {
            if (!event.target.checked) return;
            void updateSetting("chatbot_default_target_behaviour", event.target.value);
        });
    });
}

// ── Категории команд: кнопка «Включить все» ────────────────────────────────

function initCommandCategories() {
    // Программная установка checked не кидает событие change (его слушает
    // bindSettingSwitches) — сохраняем настройки напрямую, по одному запросу.
    document.querySelectorAll(".toggle-all-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            const grid = byId(btn.dataset.target ?? "");
            if (!grid) return;
            let enabled = 0;
            grid.querySelectorAll("q0ui-switch[name]").forEach((sw) => {
                if (sw.checked) return;
                sw.checked = true;
                enabled++;
                void updateSetting(sw.name, true);
            });
            if (enabled === 0) showNotification("Настройки", "Все команды категории уже включены.");
        });
    });
}

// ── Мемалёртс: мемкоины, кнопка награды, отключение интеграции ──────────────

async function memealertsAction(endpoint, method) {
    const btn = byId("memealerts-reward-btn");
    if (btn) btn.disabled = true;
    await fetchNotify(endpoint, { method }, "Memealerts");
    // Обновляем обе статус-карточки после действия.
    byId("memealerts-token-card")?.refresh();
    await byId("memealerts-reward-card")?.refresh();
}

async function disconnectMemealerts() {
    const btn = byId("disconnect-memealerts-btn");
    if (btn) btn.disabled = true;
    const { ok } = await fetchNotify("/api/user/memealerts", { method: "DELETE" }, "Memealerts");
    if (ok) {
        // Скрываем интерфейс подключённой интеграции, показываем кнопку «Подключить».
        // Останавливаем периодические опросы статус-карточек.
        const connected = byId("memealerts-v2-connected");
        const disconnected = byId("memealerts-v2-disconnected");
        connected?.querySelectorAll("q0ui-status-card").forEach((card) => card.stop());
        if (connected) connected.style.display = "none";
        if (disconnected) disconnected.style.display = "";
    } else if (btn) {
        btn.disabled = false;
    }
}

function initMemealertsSection() {
    // Курс «1 <валюта> = N баллов канала» вычисляется на клиенте из полей
    // «Количество мемкоинов» и «Стоимость награды»: обновляется при изменении
    // любого из них и после первой загрузки цены из API.
    // q0ui-number при живом вводе обновляет .value только на Enter/блюр
    // (_commitField), поэтому слушатели input передают свежее число событием.
    const syncCoinRate = (costValue = null, coinsValue = null) => {
        const line = byId("coin-rate-line");
        const valueEl = byId("coin-rate-value");
        if (!line || !valueEl) return;
        const costInputEl = byId("reward-cost");
        // Поле цены включается только после успешной загрузки из API; до этого
        // .value держит placeholder (=min=1) — курс по нему был бы выдуманным.
        if (!costInputEl || costInputEl.disabled) {
            line.hidden = true;
            return;
        }
        const cost = Number(costValue ?? costInputEl.value);
        const coinCount = Number(coinsValue ?? byId("coin-count")?.value);
        if (!Number.isFinite(cost) || cost < 1 || !Number.isFinite(coinCount) || coinCount < 1) {
            line.hidden = true;
            return;
        }
        const rate = cost / coinCount;
        const rounded = Math.round(rate);
        // Дробный/субединичный курс: «≈ 0 баллов» — бессмыслица, честнее подписать.
        valueEl.textContent =
            rounded < 1 ? "меньше 1 балла канала" : `${Number.isInteger(rate) ? "" : "≈ "}${rounded} ${pluralPoints(rounded)} канала`;
        line.hidden = false;
    };

    // Из shadow-DOM компонента до host-а доходит и нативный composed input —
    // без detail и со старым .value. Игнорируем его: свежее число приходит
    // кастомным событием, закоммиченное — событием change.
    const isFreshValueEvent = (event) => event.detail?.value !== undefined;

    const coins = byId("coin-count");
    if (coins) {
        const saveCoins = debounce((count) => {
            // Значение приходит из события компонента: при живом вводе до
            // «коммита» (Enter/блюр) coins.value ещё держит прошлое число.
            if (!Number.isFinite(count) || count < 1 || count > 100) {
                showNotification("Ошибка", "Введите число от 1 до 100", true);
                return;
            }
            void fetchNotify(
                "/api/user/memealerts/coins",
                { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ count }) },
                "Мемкоины",
            );
        }, 3000);
        coins.addEventListener("input", (event) => {
            if (!isFreshValueEvent(event)) return;
            const value = event.detail.value;
            saveCoins(value);
            syncCoinRate(null, value);
        });
        coins.addEventListener("change", () => {
            saveCoins(coins.value);
            syncCoinRate();
        });
    }

    const rewardCard = byId("memealerts-reward-card");
    const rewardBtn = byId("memealerts-reward-btn");
    if (rewardCard && rewardBtn) {
        createRewardButton(rewardBtn, rewardCard, {
            create: () => memealertsAction("/api/user/memealerts/reward", "PUT"),
            fix: () => memealertsAction("/api/user/memealerts/reward", "PATCH"),
            delete: () => memealertsAction("/api/user/memealerts/reward", "DELETE"),
        });
    }

    byId("disconnect-memealerts-btn")?.addEventListener("click", () => void disconnectMemealerts());

    // Стоимость награды в баллах канала: подтягиваем текущую из Twitch при
    // загрузке, сохраняем отдельной кнопкой (не debounce — цена важна).
    const costInput = byId("reward-cost");
    const costBtn = byId("reward-cost-save-btn");
    const costHint = byId("reward-cost-hint");
    if (costInput && costBtn) {
        // Последнее сохранённое значение: пока поле отличается от него,
        // под ценой мигает подсказка «не забудь сохранить!».
        let savedCost = null;
        const syncCostHint = (value = null) => {
            if (costHint) costHint.hidden = savedCost === null || Number(value ?? costInput.value) === savedCost;
        };

        void (async () => {
            try {
                const res = await fetch("/api/user/memealerts/reward", { credentials: "same-origin" });
                if (!res.ok) return;
                const data = await res.json();
                if (Number.isFinite(data.cost)) {
                    costInput.value = data.cost;
                    savedCost = data.cost;
                    costInput.disabled = false;
                    costBtn.disabled = false;
                    syncCoinRate();
                }
            } catch (e) {
                // Тихо: поле остаётся заблокированным, статус-карточка покажет проблему.
                // Курс не считаем — без реальной цены он был бы выдуманным.
            }
        })();
        costInput.addEventListener("input", (event) => {
            if (!isFreshValueEvent(event)) return;
            const value = event.detail.value;
            syncCostHint(value);
            syncCoinRate(value, null);
        });
        costInput.addEventListener("change", () => {
            syncCostHint();
            syncCoinRate();
        });
        costBtn.addEventListener("click", () => {
            const cost = Number(costInput.value);
            if (!Number.isFinite(cost) || cost < 1) {
                showNotification("Ошибка", "Введите число от 1 и больше", true);
                return;
            }
            costBtn.disabled = true;
            void fetchNotify(
                "/api/user/memealerts/reward",
                { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cost }) },
                "Memealerts",
            ).then(({ ok }) => {
                if (ok) {
                    savedCost = cost;
                    syncCostHint();
                    costBtn.textContent = "Сохранено!";
                } else {
                    costBtn.disabled = false;
                }
                setTimeout(() => {
                    costBtn.textContent = "Сохранить";
                    costBtn.disabled = false;
                }, 1500);
            });
        });
    }

    // Склонения названия валюты: ручной override над (кривыми) значениями
    // Memealerts. После сохранения — перезагрузка: склонения вшиты в несколько
    // серверных мест страницы (строка курса, шапка истории, placeholder-ы).
    const currencyForm = byId("memealerts-currency-form");
    const currencyEditBtn = byId("memealerts-currency-edit-btn");
    if (currencyForm && currencyEditBtn) {
        const saveBtn = byId("memealerts-currency-save-btn");
        const cancelBtn = byId("memealerts-currency-cancel-btn");
        const resetBtn = byId("memealerts-currency-reset-btn");

        currencyEditBtn.addEventListener("click", () => {
            currencyForm.hidden = !currencyForm.hidden;
        });
        cancelBtn?.addEventListener("click", () => {
            currencyForm.hidden = true;
        });

        const patchCurrency = async (body) => {
            const { ok } = await fetchNotify(
                "/api/user/memealerts/currency",
                { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
                "Memealerts",
            );
            if (ok) window.location.reload();
        };

        saveBtn?.addEventListener("click", () => {
            const body = {};
            // Что в поле — то и сохранится: непустое = override, пустое = сброс
            // поля к значению Memealerts (нетронутые override предзаполнены).
            currencyForm.querySelectorAll("input[data-currency-key]").forEach((input) => {
                body[input.dataset.currencyKey] = input.value;
            });
            void patchCurrency(body);
        });
        resetBtn?.addEventListener("click", () => {
            void patchCurrency({ genitive: "", accusative: "", genitive_multiple: "", accusative_multiple: "" });
        });
    }

    initMemealertsHistory();
}

// ── История пополнений мемкоинов (/api/user/memealerts/deposits) ─────────────

const MEME_DEPOSITS_PAGE_SIZE = 30;

const DEPOSIT_ERROR_LABELS = {
    user_not_found: "получатель не найден",
    not_supporter: "зритель ещё не саппортер канала",
    duplicate_user: "несколько пользователей с таким именем",
    token_expired: "истёк токен интеграции",
    token_invalid: "токен интеграции не принят",
    ma_unavailable: "Memealerts недоступен",
    unexpected: "непредвиденная ошибка",
};

function pluralPoints(n) {
    const abs = Math.abs(n) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return "баллов";
    if (last > 1 && last < 5) return "балла";
    if (last === 1) return "балл";
    return "баллов";
}

function escDeposit(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// API отдаёт naive-UTC ISO без суффикса Z — дописываем его перед парсингом
// (как в balance_history.js), иначе Date() прочитает как локальное время.
function formatDepositDate(value) {
    if (!value) return "—";
    const s = String(value);
    const d = new Date(/Z|[+-]\d{2}:?\d{2}$/.test(s) ? s : `${s}Z`);
    if (isNaN(d.getTime())) return "—";
    return d.toLocaleString("ru-RU", {
        day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit",
    });
}

function renderDepositRow(d) {
    const points = d.points_spent == null ? "—" : String(d.points_spent);
    // «Получено»: количество показываем только при успехе — при провале зритель
    // ничего не получил (баллы вернулись).
    const coins = d.status === "succeeded" ? String(d.memecoins) : "—";
    let statusCell;
    if (d.status === "succeeded") {
        statusCell = '<span class="meme-deposit-status succeeded">Начислено</span>';
    } else if (d.status === "pending") {
        statusCell = '<span class="meme-deposit-status pending">Обрабатывается</span>';
    } else {
        const reason = DEPOSIT_ERROR_LABELS[d.error] || "ошибка начисления";
        statusCell =
            '<span class="meme-deposit-status failed">Не начислено</span>' +
            `<div class="meme-deposit-reason" title="${escDeposit(reason)}">${escDeposit(reason)}</div>`;
    }
    return `<tr>
        <td>${escDeposit(formatDepositDate(d.created_at))}</td>
        <td>${escDeposit(d.chatter_login)}</td>
        <td>${escDeposit(d.memealerts_username)}</td>
        <td>${escDeposit(points)}</td>
        <td>${escDeposit(coins)}</td>
        <td>${statusCell}</td>
    </tr>`;
}

function initMemealertsHistory() {
    const tbody = byId("memeDepositsBody");
    if (!tbody) return;
    const table = byId("memeDepositsTable");
    const emptyEl = byId("memeDepositsEmpty");
    const loadingEl = byId("memeDepositsLoading");
    const moreBtn = byId("memeDepositsMore");
    let nextCursor = null;
    let loading = false;

    const loadPage = async () => {
        if (loading) return; // двойной клик не должен дублировать страницу
        loading = true;
        moreBtn.disabled = true;
        const params = new URLSearchParams({ limit: String(MEME_DEPOSITS_PAGE_SIZE) });
        if (nextCursor) params.set("before_id", String(nextCursor));
        try {
            const resp = await fetch(`/api/user/memealerts/deposits?${params}`, {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();

            loadingEl.hidden = true;
            table.hidden = false;
            tbody.insertAdjacentHTML("beforeend", data.items.map(renderDepositRow).join(""));
            emptyEl.hidden = tbody.children.length > 0;
            nextCursor = data.next_cursor;
            moreBtn.hidden = nextCursor == null;
        } catch (e) {
            loadingEl.hidden = true;
            if (tbody.children.length === 0) {
                emptyEl.textContent = "Не удалось загрузить историю.";
                emptyEl.hidden = false;
            } else {
                showNotification("Ошибка", "Не удалось загрузить следующую страницу истории.", true);
            }
        } finally {
            loading = false;
            moreBtn.disabled = false;
        }
    };

    moreBtn.addEventListener("click", () => void loadPage());
    void loadPage();
}

// ── Оверлеи: сборка URL-конфига для OBS из [data-param] ─────────────────────
// Настройки оверлеев не персистятся на сервере: конфиг живёт в URL, который
// пользователь копирует в OBS. Значения сбрасываются к разметке при перезагрузке,
// поэтому дополнительно запоминаем их в localStorage — вернувшись на страницу,
// не нужно выставлять всё заново.

const overlayCfgKey = (card) => `overlay_cfg:${(card.dataset.base || "").split("/overlay/").pop()}`;

function readOverlayCfg(card) {
    try {
        return JSON.parse(localStorage.getItem(overlayCfgKey(card)) || "null");
    } catch (e) {
        return null;
    }
}

function restoreOverlayCfg(card) {
    const params = readOverlayCfg(card);
    if (!params) return;
    card.querySelectorAll("[data-param]").forEach((el) => {
        const key = el.dataset.param;
        if (!(key in params)) return;
        const value = params[key];
        if (el.tagName === "Q0UI-SWITCH") el.checked = Boolean(value);
        else if (el.type === "radio") el.checked = String(value) === el.value;
        else el.value = value;
    });
}

function saveOverlayCfg(card) {
    const params = {};
    card.querySelectorAll("[data-param]").forEach((el) => {
        const key = el.dataset.param;
        if (el.tagName === "Q0UI-SWITCH") params[key] = el.checked;
        else if (el.type === "radio") {
            if (el.checked) params[key] = el.value;
        } else params[key] = el.value;
    });
    try {
        localStorage.setItem(overlayCfgKey(card), JSON.stringify(params));
    } catch (e) {
        // Приватный режим/переполнение — молча живём без персиста.
    }
}

function updateOverlayLink(card) {
    const base = card.dataset.base;
    const baseDock = card.dataset.base_dock;
    const params = new URLSearchParams();

    params.set("channel_id", CFG.channel_id);
    params.set("channel_name", CFG.channel_name);

    card.querySelectorAll("[data-param]").forEach((el) => {
        const key = el.dataset.param;
        let value;
        if (el.tagName === "Q0UI-SWITCH") value = el.checked;
        else if (el.tagName === "Q0UI-SLIDER") value = el.value;
        else if (el.type === "radio" && !el.checked) return;
        else value = el.value;
        params.set(key, value);
    });

    if (params.get("chat_control") === "true") {
        params.set("secret", CFG.slovotron_secret);
    }

    const linkDiv = card.querySelector(".overlay-link");
    if (linkDiv) linkDiv.textContent = base + "?" + params.toString();

    if (baseDock) {
        params.set("secret", CFG.slovotron_secret);
        const dockLinkDiv = card.querySelector(".dock-panel-link");
        if (dockLinkDiv) dockLinkDiv.textContent = baseDock + "?" + params.toString();
    }
}

function initOverlays() {
    document.querySelectorAll(".overlay-card").forEach((card) => {
        // Восстанавливаем сохранённые значения до первой сборки ссылок.
        restoreOverlayCfg(card);

        const linkDiv = card.querySelector(".overlay-link");
        const linkDockDiv = card.querySelector(".dock-panel-link");

        card.querySelectorAll("[data-param]").forEach((el) => {
            // q0ui-slider кричит input, q0ui-switch — change; нативным хватит input+change.
            el.addEventListener("input", () => {
                updateOverlayLink(card);
                saveOverlayCfg(card);
            });
            el.addEventListener("change", () => {
                updateOverlayLink(card);
                saveOverlayCfg(card);
            });
        });

        // Показ/скрытие настроек по выбранному radio (data-visible-when-param / -value):
        // например, цвета «своей» темы видны только при теме «Своя».
        const syncConditionalControls = () => {
            card.querySelectorAll("[data-visible-when-param]").forEach((el) => {
                const checked = card.querySelector(
                    `input[type="radio"][data-param="${el.dataset.visibleWhenParam}"]:checked`,
                );
                el.hidden = !checked || checked.value !== el.dataset.visibleWhenValue;
            });
        };
        card.querySelectorAll("input[type='radio'][data-param]").forEach((el) => {
            el.addEventListener("change", syncConditionalControls);
        });
        syncConditionalControls();

        card.querySelector(".overlay-toggle-settings")?.addEventListener("click", () => {
            card.querySelector(".overlay-settings")?.classList.toggle("active");
        });

        const bindCopy = (div, withGoal) => {
            if (!div) return;
            div.addEventListener("click", () => {
                navigator.clipboard.writeText(div.textContent);
                div.classList.add("copied");
                div.textContent = "Скопировано!";
                setTimeout(() => {
                    updateOverlayLink(card);
                    div.classList.remove("copied");
                }, 1000);
                if (withGoal) reachGoal("copiedOverlayLink");
            });
        };
        bindCopy(linkDiv, true);
        bindCopy(linkDockDiv, false);

        updateOverlayLink(card);
    });

    byId("wtf-is-heat-link")?.addEventListener("click", () => {
        const clip = byId("wtf-is-heat-clip");
        if (clip) clip.style.display = "flex";
    });
}

// ── Плагин Heat: установка и скрытие предупреждений ─────────────────────────

async function setupHeat() {
    reachGoal("installHeat");
    try {
        const res = await fetch("/api/user/install-heat", {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
        });
        const data = await res.json();
        if (res.ok) {
            showNotification("Настройки", "Плагин Heat успешно установлен!", false);
            document.querySelectorAll("div.plugin-required").forEach((el) => el.remove());
        } else {
            showNotification(
                "Ошибка",
                "Ошибка автоматической установки плагина Heat. Открываем страничку плагина.",
                true,
            );
            window.open(HEAT_EXT_URL, "_blank", "noopener,noreferrer");
        }
    } catch (e) {
        window.open(HEAT_EXT_URL, "_blank", "noopener,noreferrer");
        showNotification("Ошибка", "Ошибка автоматической установки плагина Heat.", true);
    }
}

function initHeat() {
    // Heat установлен → предупреждения «обязательно установите» больше не нужны.
    const heatCard = byId("heat-status-card");
    if (heatCard) {
        const applyHeatStatus = (detail) => {
            if (detail.state === "ok" || detail.state === "warn") {
                document.querySelectorAll(".plugin-required").forEach((el) => {
                    el.style.display = "none";
                });
            }
        };
        heatCard.addEventListener("q0ui-status", (event) => applyHeatStatus(event.detail));
        // Первый опрос мог пройти до подвешивания слушателя.
        if (heatCard.lastStatus) applyHeatStatus(heatCard.lastStatus);
    }

    document.querySelectorAll(".plugin-required .install-btn").forEach((btn) => {
        btn.addEventListener("click", () => void setupHeat());
    });
}

// ── Прочие кнопки-действия ──────────────────────────────────────────────────

function initActionButtons() {
    byId("reset-overlay-secret-btn")?.addEventListener("click", () => {
        void fetchNotify("/api/user/overlay/reset-secret", { method: "POST" }, "Оверлеи");
    });

    byId("tg-link-btn")?.addEventListener("click", () => {
        window.location.href = "/auth/telegram";
    });

    byId("tg-unlink-btn")?.addEventListener("click", async () => {
        const { ok } = await fetchNotify("/api/user/telegram/unlink-account", { method: "POST" }, "Telegram");
        if (ok) {
            byId("tg-username-line")?.remove();
            byId("tg-unlink-btn")?.setAttribute("hidden", "");
            byId("tg-link-btn")?.removeAttribute("hidden");
        }
    });

    byId("steam-unlink-btn")?.addEventListener("click", async () => {
        const { ok } = await fetchNotify("/api/user/steam/unlink", { method: "POST" }, "Steam");
        if (ok) {
            byId("steam-unlink-btn")?.setAttribute("hidden", "");
            byId("steam-link")?.removeAttribute("hidden");
        }
    });

    document.querySelectorAll("[data-nav]").forEach((btn) => {
        btn.addEventListener("click", () => {
            window.location.href = btn.dataset.nav;
        });
    });
}

// ── Инициализация ───────────────────────────────────────────────────────────

function initPanel() {
    initChatbotSection();
    initCommandCategories();
    initMemealertsSection();
    initOverlays();
    initHeat();
    initActionButtons();

    // Неудачная привязка Telegram: /panel?tg_link=failed (редирект из callback).
    const linkParams = new URLSearchParams(window.location.search);
    if (linkParams.get("tg_link") === "failed") {
        showNotification("Telegram", "Не удалось привязать Telegram-аккаунт. Попробуйте позже.", true);
        linkParams.delete("tg_link");
        const rest = linkParams.toString();
        window.history.replaceState({}, "", window.location.pathname + (rest ? `?${rest}` : ""));
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initPanel);
} else {
    initPanel();
}
