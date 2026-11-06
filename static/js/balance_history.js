/* История баланса: таблица операций с курсорной пагинацией (/balance-history). */
(() => {
    "use strict";

    const PAGE_SIZE = 30;

    const TYPE_LABELS = {deposit: "Начисление", spend: "Списание"};
    const ACTION_LABELS = {
        admin_deposit: "Пополнение администратором",
        ai_sticker: "Генерация ИИ-стикера",
        initial_balance: "Инициализация истории операций с текущим балансом",
    };

    const el = (id) => document.getElementById(id);

    function esc(text) {
        const div = document.createElement("div");
        div.textContent = text == null ? "" : String(text);
        // innerHTML сериализует только & < > — кавычки экранируем отдельно:
        // результат подставляется в HTML-атрибуты (title="...")
        return div.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    // API отдаёт naive-UTC ISO без суффикса Z; new Date() парсит такое как
    // локальное время — дописываем Z (как в bans.js).
    function formatDate(value) {
        if (!value) return "—";
        const s = String(value);
        const d = new Date(/Z|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + "Z");
        if (isNaN(d.getTime())) return "—";
        return d.toLocaleString("ru-RU", {
            day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit",
        });
    }

    function formatAmount(value) {
        return Number(value).toFixed(2);
    }

    function detailsText(op) {
        const d = op.details || {};
        if (op.action === "ai_sticker") {
            const parts = [];
            if (d.chatter) parts.push(`чаттер: ${d.chatter}`);
            if (d.prompt) parts.push(`промпт: «${d.prompt}»`);
            return parts.join(", ");
        }
        if (op.action === "admin_deposit" && d.admin) return `админ: ${d.admin}`;
        if (d.note) return d.note;
        return "";
    }

    function renderRow(op) {
        const typeLabel = TYPE_LABELS[op.type] || op.type;
        const actionLabel = ACTION_LABELS[op.action] || op.action;
        const sign = op.type === "deposit" ? "+" : "−";
        const details = detailsText(op);
        return `<tr>
            <td>${esc(formatDate(op.created_at))}</td>
            <td><span class="balance-type-badge ${esc(op.type)}">${esc(typeLabel)}</span></td>
            <td class="balance-amount ${esc(op.type)}">${sign}${esc(formatAmount(op.amount))}</td>
            <td>${esc(actionLabel)}${details ? `<div class="balance-action-details" title="${esc(details)}">${esc(details)}</div>` : ""}</td>
            <td class="balance-balance-after">${esc(formatAmount(op.balance_after))}</td>
        </tr>`;
    }

    let nextCursor = null;
    let loading = false;

    async function loadPage() {
        if (loading) return; // двойной клик не должен дублировать страницу
        loading = true;
        el("balanceMore").disabled = true;
        const params = new URLSearchParams({limit: String(PAGE_SIZE)});
        if (nextCursor) params.set("before_id", String(nextCursor));
        try {
            const resp = await fetch(`/api/user/balance/operations?${params}`, {
                headers: {Accept: "application/json"},
                credentials: "same-origin",
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();

            el("balanceLoading").hidden = true;
            el("balanceTable").hidden = false;
            el("balanceTableBody").insertAdjacentHTML("beforeend", data.items.map(renderRow).join(""));
            el("balanceEmpty").hidden = data.items.length > 0 || el("balanceTableBody").children.length > 0;
            nextCursor = data.next_cursor;
            el("balanceMore").hidden = nextCursor == null;
        } catch (e) {
            el("balanceLoading").hidden = true;
            el("balanceEmpty").hidden = false;
            el("balanceEmpty").textContent = "Не удалось загрузить историю.";
        } finally {
            loading = false;
            el("balanceMore").disabled = false;
        }
    }

    function init() {
        el("balanceMore").addEventListener("click", loadPage);
        loadPage();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
