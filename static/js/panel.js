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
        coins.addEventListener("input", (event) => saveCoins(event.detail?.value ?? coins.value));
        coins.addEventListener("change", () => saveCoins(coins.value));
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
    if (costInput && costBtn) {
        void (async () => {
            try {
                const res = await fetch("/api/user/memealerts/reward", { credentials: "same-origin" });
                if (!res.ok) return;
                const data = await res.json();
                if (Number.isFinite(data.cost)) {
                    costInput.value = data.cost;
                    costInput.disabled = false;
                    costBtn.disabled = false;
                }
            } catch (e) {
                // Тихо: поле остаётся заблокированным, статус-карточка покажет проблему.
            }
        })();
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
                if (ok) costBtn.textContent = "Сохранено!";
                else costBtn.disabled = false;
                setTimeout(() => {
                    costBtn.textContent = "Сохранить";
                    costBtn.disabled = false;
                }, 1500);
            });
        });
    }
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
