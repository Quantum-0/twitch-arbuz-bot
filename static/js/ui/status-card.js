/**
 * <q0ui-status-card> — статусная карточка с индикатором.
 *
 * Режим поллинга (карточка сама опрашивает check-эндпоинт):
 *   endpoint — GET-эндпоинт вида /api/user/check/*, ожидает
 *              {result: bool, problems: str[], warnings: str[], state?: str};
 *   interval — период опроса в секундах.
 *   result=true → ok (жёлтый warn, если есть warnings), иначе error с problems.
 *
 * Ручной режим (endpoint не задан): состояние управляется атрибутом/свойством
 * state (ok | warn | error), контент — слот. Так строятся карточки вроде
 * баланса.
 *
 * Содержимое (заголовок и т.д.) — светлое, через слот; списки проблем/варнингов
 * карточка рендерит сама. Коды проблем, которые можно показывать как HTML
 * (ссылки), — в allowlist PROBLEM_HTML ниже, остальное — чистый текст.
 *
 * Событие q0ui-status (detail: {state, problems, warnings, data}) — на него
 * подписываются, например, кнопки управления наградами (createRewardButton).
 *
 * Методы: refresh() — немедленный опрос; stop() — остановить поллинг.
 */
import { Q0Element, defineElement } from "./base.js";
import "./indicator.js";

/** Коды проблем бэкенда, которые рендерим как HTML (ссылки). Прочие — текст. */
const PROBLEM_HTML = {
    twitch_token_invalid: 'Ошибка токена Twitch — <a href="/login">Переавторизуйтесь</a>',
};

const CSS = `
:host { display: block; height: 100%; }
.card {
    background: var(--color-bg-secondary, #fff);
    border-radius: 20px;
    padding: 25px;
    box-shadow: 0 8px 32px var(--color-shadow-strong, rgba(0, 0, 0, 0.3));
    border: 1px solid rgba(128, 128, 128, 0.15);
    display: flex;
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    height: 100%;
    transition: box-shadow 0.3s ease, border-color 0.3s ease;
}
.card:hover {
    box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.15), 0 0 16px rgba(255, 255, 255, 0.08);
}
.body { min-width: 0; }
ul {
    margin: 0;
    padding-left: 20px;
    font-size: 75%;
    color: var(--color-danger, #e53935);
}
ul.warnings { color: #e6a800; }
ul:empty { display: none; }
a { color: var(--color-link); }
`;

export class Q0StatusCard extends Q0Element {
    static observedAttributes = ["endpoint", "interval", "state", "tooltip"];

    constructor() {
        super();
        const root = this.attachStyles(CSS);
        root.innerHTML = `
            <div class="card" part="card">
                <div class="body"><slot></slot><ul class="problems" part="problems"></ul><ul class="warnings" part="warnings"></ul></div>
                <q0ui-indicator></q0ui-indicator>
            </div>`;
        this._els = {
            problems: root.querySelector(".problems"),
            warnings: root.querySelector(".warnings"),
            indicator: root.querySelector("q0ui-indicator"),
        };
        this._timer = null;
        this._connected = false;
        this._problems = [];
        this._warnings = [];
        /**
         * Последний результат опроса (тот же detail, что у события q0ui-status).
         * Потребители, связывающиеся после первого опроса (например
         * createRewardButton), читают его при инициализации — иначе первое
         * событие теряется до подвешивания слушателя.
         */
        this.lastStatus = null;
    }

    connectedCallback() {
        this._connected = true;
        this._applyManualState();
        this._schedule();
    }

    disconnectedCallback() {
        // Фикс былой утечки: интервал раньше жил на DOM-узле и не чистился.
        this._connected = false;
        this._stopTimer();
    }

    attributeChangedCallback(name, oldValue, newValue) {
        if (oldValue === newValue) return;
        if (name === "state" || name === "tooltip") this._applyManualState();
        // До подключения элемента (апгрейд из разметки) расписание ведает только
        // connectedCallback — иначе aac для endpoint и interval успевает
        // стартовать поллинг по два-три раза до подключения.
        if ((name === "endpoint" || name === "interval") && this._connected) {
            this._schedule();
        }
    }

    get state() {
        return this._els ? (this._els.indicator.getAttribute("state") ?? "") : "";
    }

    /** Ручное состояние карточки: 'ok' | 'warn' | 'error' (или null — сброс). */
    setStatus(state, { problems = [], warnings = [] } = {}) {
        this._stopTimer();
        if (state === null) this._els.indicator.removeAttribute("state");
        else this._els.indicator.setAttribute("state", state);
        this._renderList(this._els.problems, problems);
        this._renderList(this._els.warnings, warnings);
    }

    /** Немедленный опрос эндпоинта (в ручном режиме — no-op). */
    async refresh() {
        if (this._endpoint()) return this._fetchStatus();
    }

    /** Остановить периодический опрос (например, при отключении интеграции). */
    stop() {
        this._stopTimer();
    }

    _endpoint() {
        const ep = this.getAttribute("endpoint");
        return ep ? ep : null;
    }

    _schedule() {
        // Идемпотентность: перед запуском всегда гасим прошлый таймер,
        // иначе повторный вызов теряет ссылку на старый интервал (утечка).
        this._stopTimer();
        const endpoint = this._endpoint();
        if (!endpoint) return;
        const interval = Math.max(1, this.numAttr("interval", 60));
        void this._fetchStatus();
        this._timer = setInterval(() => void this._fetchStatus(), interval * 1000);
    }

    _stopTimer() {
        if (this._timer) {
            clearInterval(this._timer);
            this._timer = null;
        }
    }

    _applyManualState() {
        if (!this._els) return;
        const tooltip = this.getAttribute("tooltip");
        if (tooltip !== null) this._els.indicator.setAttribute("tooltip", tooltip);
        else this._els.indicator.removeAttribute("tooltip");
        // Атрибут state осмыслен только без endpoint — иначе им управляет опрос.
        if (!this._endpoint()) {
            const state = this.getAttribute("state");
            if (state) this._els.indicator.setAttribute("state", state);
        }
    }

    async _fetchStatus() {
        const endpoint = this._endpoint();
        if (!endpoint) return;
        try {
            const res = await fetch(endpoint, { credentials: "same-origin" });
            const data = await res.json();

            if (data.result === true) {
                // Варнинги не делают результат невалидным, но подсвечивают нюансы.
                const warnings = Array.isArray(data.warnings) ? data.warnings : [];
                this._problems = [];
                this._warnings = warnings;
                this._applyState(warnings.length ? "warn" : "ok");
            } else {
                this._problems = Array.isArray(data.problems) ? data.problems : [];
                this._warnings = [];
                this._applyState("error");
            }
            this.lastStatus = {
                state: this.state,
                problems: this._problems,
                warnings: this._warnings,
                data,
            };
            this.emit("q0ui-status", this.lastStatus);
        } catch {
            // Сеть/парсинг упали — карточка красная, прежние списки не трогаем.
            this._applyState("error");
            this.lastStatus = {
                state: "error",
                problems: this._problems,
                warnings: this._warnings,
                data: null,
            };
            this.emit("q0ui-status", this.lastStatus);
        }
    }

    _applyState(state) {
        this._els.indicator.setAttribute("state", state);
        this._renderList(this._els.problems, this._problems);
        this._renderList(this._els.warnings, this._warnings);
    }

    _renderList(ul, items) {
        ul.replaceChildren();
        for (const item of items) {
            const li = document.createElement("li");
            const html = Object.hasOwn(PROBLEM_HTML, item) ? PROBLEM_HTML[item] : null;
            if (html) li.innerHTML = html;
            else li.textContent = item;
            ul.appendChild(li);
        }
    }
}

defineElement("q0ui-status-card", Q0StatusCard);
