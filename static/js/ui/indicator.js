/**
 * <q0ui-indicator> — светящийся индикатор-точка состояния.
 *
 * Атрибуты:
 *  state   — ok | warn | error (без атрибута — нейтральный серый);
 *  tooltip — текст всплывающей подсказки при наведении.
 *
 * Используется отдельно и внутри <q0ui-status-card>.
 */
import { Q0Element, defineElement } from "./base.js";

const CSS = `
:host { display: inline-block; position: relative; cursor: help; flex: none; }
.dot {
    display: block;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: radial-gradient(circle, #888, #666);
    box-shadow: 0 0 8px rgba(255, 255, 255, 0.2);
    transition: 0.3s;
}
:host([state="ok"]) .dot {
    background: radial-gradient(circle, #00ff88, #00cc66);
    box-shadow: 0 0 12px #00ff88, 0 0 25px rgba(0, 255, 136, 0.6);
}
:host([state="warn"]) .dot {
    background: radial-gradient(circle, #ffcc00, #e6a800);
    box-shadow: 0 0 12px #ffcc00, 0 0 25px rgba(255, 204, 0, 0.6);
}
:host([state="error"]) .dot {
    background: radial-gradient(circle, #ff3366, #cc0033);
    box-shadow: 0 0 12px #ff3366, 0 0 25px rgba(255, 51, 102, 0.6);
}
.tip {
    visibility: hidden;
    position: absolute;
    bottom: calc(100% + 6px);
    left: 50%;
    transform: translateX(-50%);
    width: max-content;
    max-width: 220px;
    background: #000;
    color: #fff;
    font-size: 12px;
    line-height: 1.35;
    text-align: center;
    border-radius: 6px;
    padding: 5px 8px;
    z-index: 10;
    opacity: 0;
    transition: opacity 0.15s;
    pointer-events: none;
}
.tip:empty { display: none; }
:host(:hover) .tip, :host(:focus-within) .tip { visibility: visible; opacity: 1; }
`;

export class Q0Indicator extends Q0Element {
    static observedAttributes = ["state", "tooltip"];

    constructor() {
        super();
        const root = this.attachStyles(CSS);
        root.innerHTML = `<div class="dot" part="dot"></div><span class="tip" part="tip"></span>`;
        this._els = { tip: root.querySelector(".tip") };
    }

    connectedCallback() {
        this._render();
    }

    attributeChangedCallback() {
        this._render();
    }

    _render() {
        if (!this._els) return;
        this._els.tip.textContent = this.getAttribute("tooltip") ?? "";
    }
}

defineElement("q0ui-indicator", Q0Indicator);
