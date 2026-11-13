/**
 * Логика секции Telegram-интеграции на /panel (вынесена из инлайн-скрипта
 * templates/panel/telegram.html).
 *
 * Загрузка/сохранение настроек, подключение/отключение чатов. Каждый блок
 * (стрим/клипы/стикеры) опционален: у не-бета пользователей блока клипов нет —
 * отсутствующие элементы просто пропускаются, а не роняют всю инициализацию
 * (так вёл себя старый инлайн-скрипт: TypeError внутри try глотался, и
 * остальные контролы оставались мёртвыми).
 *
 * Тумблеры — q0ui-switch, радио и шаблоны — нативные элементы.
 */

const TG_DEFAULT_TEMPLATE = "🔴 {streamer} начинает стрим!\n{title}\n{category}\n\n{link}";
const TG_DEFAULT_OFFLINE_TEMPLATE = "⚪️ Стрим завершён.";
const TG_DEFAULT_RESTART_TEMPLATE = "🟠 Стрим упал, но был перезапущен.";

const byId = (id) => document.getElementById(id);

/** Название подключённого чата или статус «не подключён». */
function chatTitle(data, prefix) {
    return data[`${prefix}_chat_title`] || (data[`${prefix}_chat_id`] ? "подключён" : "не подключён");
}

/** Заполнить строку чата и кнопки подключения/отключения одной секции. */
function applyChatSection(prefix, data) {
    const chatLabel = byId(`tg-${prefix}-chat`);
    if (chatLabel) chatLabel.textContent = chatTitle(data, prefix);

    const connectBtns = byId(`tg-${prefix}-connect-btns`);
    if (connectBtns) connectBtns.hidden = Boolean(data[`${prefix}_chat_id`]);

    const disconnectBtn = byId(`tg-${prefix}-disconnect-btn`);
    if (disconnectBtn) disconnectBtn.hidden = !data[`${prefix}_chat_id`];
}

function applySwitch(id, checked) {
    const control = byId(id);
    if (control) control.checked = checked;
}

function setRadio(name, value) {
    const input = document.querySelector(`input[name="${name}"][value="${value}"]`);
    if (input) input.checked = true;
}

function setTemplate(id, value, fallback) {
    const field = byId(id);
    if (field) field.value = value || fallback;
}

async function loadSettings() {
    let data;
    try {
        const resp = await fetch("/api/user/telegram/settings", { credentials: "same-origin" });
        if (!resp.ok) return;
        data = await resp.json();
    } catch (e) {
        console.error("telegram loadSettings error:", e);
        return;
    }

    const main = byId("telegram-main");
    if (main) main.hidden = false;

    // ── Чаты: выводим название, не айдишник ──
    applyChatSection("stream", data);
    applyChatSection("clips", data);
    applyChatSection("stickers", data);

    // ── Тумблеры ──
    applySwitch("tg-stream-notif-toggle", Boolean(data.stream_notification_enabled));
    applySwitch("tg-link-preview-toggle", data.stream_link_preview_enabled !== false);
    applySwitch("tg-clips-toggle", Boolean(data.clips_enabled));
    applySwitch("tg-stickers-toggle", Boolean(data.stickers_enabled));

    // ── Radio-группы ──
    setRadio("tg-offline-behavior", data.stream_offline_behavior || "keep");
    setRadio("tg-restart-behavior", data.stream_restart_behavior || "edit");
    setRadio("tg-clips-mode", data.clips_mode || "all");
    setRadio("tg-clips-delivery", data.clips_delivery || "link");
    setRadio("tg-stickers-mode", data.stickers_mode || "photo");

    // ── Шаблоны текста ──
    setTemplate("tg-stream-template", data.stream_message_template, TG_DEFAULT_TEMPLATE);
    setTemplate("tg-offline-template", data.stream_offline_message_template, TG_DEFAULT_OFFLINE_TEMPLATE);
    setTemplate("tg-restart-template", data.stream_restart_message_template, TG_DEFAULT_RESTART_TEMPLATE);
}

/** Значение шаблона к отправке: undefined, если совпадает с дефолтом. */
function templatePayload(id, fallback) {
    const field = byId(id);
    if (!field) return undefined;
    const value = field.value;
    return value === undefined || value.trim() === fallback.trim() ? undefined : value;
}

async function saveSettings() {
    const checkedOf = (id) => {
        const el = byId(id);
        return el ? el.checked : undefined;
    };
    const radioValue = (name) => document.querySelector(`input[name="${name}"]:checked`)?.value;

    const body = {};
    const streamEnabled = checkedOf("tg-stream-notif-toggle");
    const linkPreviewEnabled = checkedOf("tg-link-preview-toggle");
    const clipsEnabled = checkedOf("tg-clips-toggle");
    const stickersEnabled = checkedOf("tg-stickers-toggle");
    if (streamEnabled !== undefined) body.stream_notification_enabled = streamEnabled;
    if (linkPreviewEnabled !== undefined) body.stream_link_preview_enabled = linkPreviewEnabled;
    if (clipsEnabled !== undefined) body.clips_enabled = clipsEnabled;
    if (stickersEnabled !== undefined) body.stickers_enabled = stickersEnabled;

    const offlineBehavior = radioValue("tg-offline-behavior");
    const restartBehavior = radioValue("tg-restart-behavior");
    const clipsMode = radioValue("tg-clips-mode");
    const clipsDelivery = radioValue("tg-clips-delivery");
    const stickersMode = radioValue("tg-stickers-mode");
    if (offlineBehavior) body.stream_offline_behavior = offlineBehavior;
    if (restartBehavior) body.stream_restart_behavior = restartBehavior;
    if (clipsMode) body.clips_mode = clipsMode;
    if (clipsDelivery) body.clips_delivery = clipsDelivery;
    if (stickersMode) body.stickers_mode = stickersMode;

    const streamTemplate = templatePayload("tg-stream-template", TG_DEFAULT_TEMPLATE);
    const offlineTemplate = templatePayload("tg-offline-template", TG_DEFAULT_OFFLINE_TEMPLATE);
    const restartTemplate = templatePayload("tg-restart-template", TG_DEFAULT_RESTART_TEMPLATE);
    if (streamTemplate !== undefined) body.stream_message_template = streamTemplate;
    if (offlineTemplate !== undefined) body.stream_offline_message_template = offlineTemplate;
    if (restartTemplate !== undefined) body.stream_restart_message_template = restartTemplate;

    try {
        const resp = await fetch("/api/user/telegram/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            credentials: "same-origin",
        });
        const result = await resp.json();
        showNotification(result.title, result.message, !resp.ok);
    } catch (e) {
        showNotification("Ошибка", "Не удалось сохранить настройки", true);
    }
}

async function connectChat(scope, chatType) {
    try {
        const resp = await fetch("/api/user/telegram/connect", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scope, chat_type: chatType }),
            credentials: "same-origin",
        });
        const data = await resp.json();
        if (resp.ok && data.url) {
            window.open(data.url, "_blank");
        } else {
            showNotification(data.title || "Ошибка", data.message || "Не удалось создать ссылку подключения", true);
        }
    } catch (e) {
        showNotification("Ошибка", "Не удалось создать ссылку подключения", true);
    }
}

async function disconnectChat(scope) {
    if (!confirm(`Вы уверены, что хотите отключить чат для «${scope}»? Бот покинет чат.`)) return;
    try {
        const resp = await fetch(`/api/user/telegram/disconnect/${scope}`, {
            method: "POST",
            credentials: "same-origin",
        });
        const data = await resp.json();
        showNotification(data.title, data.message, !resp.ok);
        await loadSettings();
    } catch (e) {
        showNotification("Ошибка", "Не удалось отключить чат", true);
    }
}

function initTelegramSection() {
    // Страницы разделов панели грузят этот модуль все вместе — без TG-секции
    // выходим сразу, не дёргая settings-API впустую.
    if (!document.getElementById("telegram-panel")) return;

    // Тумблеры и радио автосохраняются, шаблоны — кнопкой «Сохранить шаблон».
    document.querySelectorAll("#telegram-panel q0ui-switch").forEach((control) => {
        control.addEventListener("change", () => void saveSettings());
    });
    document.querySelectorAll("#telegram-panel input[type='radio']").forEach((radio) => {
        radio.addEventListener("change", () => void saveSettings());
    });

    document.querySelectorAll("[data-tg-connect]").forEach((btn) => {
        btn.addEventListener("click", () => void connectChat(btn.dataset.tgConnect, btn.dataset.tgChat));
    });
    document.querySelectorAll("[data-tg-disconnect]").forEach((btn) => {
        btn.addEventListener("click", () => void disconnectChat(btn.dataset.tgDisconnect));
    });

    const saveTemplatesBtn = byId("tg-save-templates-btn");
    if (saveTemplatesBtn) saveTemplatesBtn.addEventListener("click", () => void saveSettings());

    void loadSettings();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTelegramSection);
} else {
    initTelegramSection();
}
