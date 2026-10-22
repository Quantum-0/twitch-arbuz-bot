/* Управление банами: стример — свой канал, стафф — все баны + global-управление. */
(function () {
    "use strict";

    const FEATURE_LABELS = {
        chat_bot: "Чат-бот",
        memecoins: "Мемкоины",
        ai_stickers: "ИИ-стикеры",
        tts: "TTS",
        site: "Сайт",
    };

    let state = { staff: false, bans: [] };

    const el = (id) => document.getElementById(id);

    function esc(text) {
        const div = document.createElement("div");
        div.textContent = text == null ? "" : String(text);
        return div.innerHTML;
    }

    // API отдаёт naive-UTC ISO без суффикса Z; new Date() парсит такое как
    // локальное время — дописываем Z, иначе даты сдвинуты на таймзону браузера.
    function parseDate(value) {
        if (!value) return null;
        const s = String(value);
        return new Date(/Z|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + "Z");
    }

    function formatDate(value) {
        const d = parseDate(value);
        if (!d || isNaN(d.getTime())) return "—";
        return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
    }

    function formatExpiry(ban) {
        if (!ban.expires_at) return "навсегда";
        if (isExpired(ban)) return "истёк";
        return formatDate(ban.expires_at);
    }

    function isExpired(ban) {
        const d = parseDate(ban.expires_at);
        return Boolean(d) && !isNaN(d.getTime()) && d.getTime() < Date.now();
    }

    function render() {
        const scopeFilter = el("bansFilterScope") ? el("bansFilterScope").value : "";
        const search = el("bansFilterSearch") ? el("bansFilterSearch").value.trim().toLowerCase() : "";
        const rows = state.bans.filter((ban) => {
            if (scopeFilter && ban.scope !== scopeFilter) return false;
            if (search && !ban.target_login.toLowerCase().includes(search)) return false;
            return true;
        });

        const body = el("bansTableBody");
        body.innerHTML = rows.map((ban) => {
            const scopeBadge = ban.scope === "global"
                ? '<span class="ban-scope-badge global">Весь сервис</span>'
                : '<span class="ban-scope-badge channel">Канал</span>';
            const channel = ban.scope === "channel" && ban.channel_login
                ? ` <span class="ban-target-login">(@${esc(ban.channel_login)})</span>` : "";
            const features = ban.features.map((f) => `<span class="ban-feature-badge">${FEATURE_LABELS[f] || f}</span>`).join("");
            const reason = ban.reason ? esc(ban.reason) : '<span class="ban-muted">—</span>';
            const manageBtn = ban.can_manage
                ? `<button class="ban-unban-btn" data-ban-id="${ban.id}">${isExpired(ban) ? "Удалить" : "Разбанить"}</button>`
                : '<span class="ban-muted">только просмотр</span>';
            return `<tr class="${isExpired(ban) ? "ban-expired" : ""}">
                <td><b>${esc(ban.target_name)}</b><div class="ban-target-login">${esc(ban.target_login)}</div></td>
                <td>${scopeBadge}${channel}</td>
                <td>${features}</td>
                <td>${reason}</td>
                <td>${esc(ban.created_by_login)}<div class="ban-target-muted ban-muted">${formatDate(ban.created_at)}</div></td>
                <td>${formatExpiry(ban)}</td>
                <td>${manageBtn}</td>
            </tr>`;
        }).join("");
        el("bansTable").hidden = rows.length === 0;
        el("bansEmpty").hidden = rows.length !== 0;
        if (rows.length === 0) {
            el("bansEmpty").textContent = state.staff ? "Банов нет" : "На вашем канале банов нет";
        }

        body.querySelectorAll(".ban-unban-btn").forEach((button) => {
            button.addEventListener("click", () => revokeBan(Number(button.dataset.banId), button));
        });
    }

    async function load() {
        try {
            const response = await fetch("/api/bans", { credentials: "same-origin" });
            if (!response.ok) throw new Error("Не удалось загрузить список банов");
            const data = await response.json();
            state.staff = Boolean(data.staff);
            state.bans = data.bans || [];

            el("bansIntro").textContent = state.staff
                ? "Все баны сервиса. Глобальными банами может управлять только стафф; канальные — только владелец канала."
                : "Баны на вашем канале. Забаненный зритель не сможет пользоваться выбранными функциями бота у вас на стриме. Быстрый способ — команда !бан <логин> в чате.";
            el("banFormCard").hidden = false;
            el("bansToolbar").hidden = false;
            el("banScopeRow").hidden = !state.staff;
            el("banFullNote").hidden = !state.staff;
            updateFormTitle();
            render();
        } catch (error) {
            el("bansIntro").textContent = error.message;
        }
    }

    function updateFormTitle() {
        const scope = state.staff ? el("banScope").value : "channel";
        el("banFormTitle").textContent = scope === "global" ? "Глобальный бан" : "Бан на моём канале";
        // «Сайт» имеет смысл только для глобального бана (стафф)
        const siteRow = el("banFeatureSite");
        if (siteRow) {
            siteRow.hidden = scope !== "global";
            if (scope !== "global") siteRow.querySelector("input").checked = false;
        }
    }

    async function createBan() {
        const login = el("banLogin").value.trim().replace(/^@/, "");
        if (!login) {
            if (typeof showNotification === "function") showNotification("Ошибка", "Укажите логин", true);
            return;
        }
        const features = Array.from(document.querySelectorAll('#banFeatures input[name="feature"]:checked')).map((c) => c.value);
        if (features.length === 0) {
            if (typeof showNotification === "function") {
                showNotification("Ошибка", "Выберите хотя бы одну ограничиваемую функцию", true);
            }
            return;
        }
        const hours = el("banDuration").value;
        const payload = {
            login,
            scope: state.staff ? el("banScope").value : "channel",
            features,
            reason: el("banReason").value.trim() || null,
            expires_at: hours ? new Date(Date.now() + Number(hours) * 3600 * 1000).toISOString() : null,
        };
        const submit = el("banSubmit");
        submit.disabled = true;
        try {
            const response = await fetch("/api/bans", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "same-origin",
                body: JSON.stringify(payload),
            });
            const data = await response.json().catch(() => null);
            if (!response.ok) throw new Error((data && data.detail) || "Не удалось выдать бан");
            const target = data.ban.target_name;
            if (typeof showNotification === "function") showNotification("Готово", `${target} забанен.`);
            el("banLogin").value = "";
            el("banReason").value = "";
            await load();
        } catch (error) {
            if (typeof showNotification === "function") showNotification("Ошибка", error.message, true);
        } finally {
            submit.disabled = false;
        }
    }

    async function revokeBan(banId, button) {
        button.disabled = true;
        try {
            const response = await fetch(`/api/bans/${banId}`, { method: "DELETE", credentials: "same-origin" });
            const data = await response.json().catch(() => null);
            if (!response.ok) throw new Error((data && data.detail) || "Не удалось снять бан");
            if (typeof showNotification === "function") showNotification("Готово", `${data.target_name} разбанен.`);
            await load();
        } catch (error) {
            if (typeof showNotification === "function") showNotification("Ошибка", error.message, true);
            button.disabled = false;
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        el("banSubmit").addEventListener("click", createBan);
        el("banScope").addEventListener("change", updateFormTitle);
        el("bansFilterScope").addEventListener("change", render);
        el("bansFilterSearch").addEventListener("input", render);
        load();
    });
})();
