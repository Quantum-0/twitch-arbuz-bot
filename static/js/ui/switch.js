/**
 * <q0ui-switch> — тумблер вкл/выкл.
 *
 * Атрибуты: checked, disabled, name (имя настройки — читается контроллерами
 * страницы, см. bindSettingSwitches в bindings.js).
 * События: change (detail: {checked}).
 *
 * Доступность: role="switch", aria-checked, клавиатура (Space/Enter).
 */
import { Q0Element, defineElement } from "./base.js";

const CSS = `
:host { display: inline-block; flex: none; }
.track {
    position: relative;
    width: 50px;
    height: 26px;
    border-radius: 13px;
    background: var(--color-border, #ccc);
    cursor: pointer;
    transition: background-color 0.3s;
}
.knob {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background: #fff;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
    transition: transform 0.3s;
}
:host([checked]) .track { background: var(--color-primary, #4CAF50); }
:host([checked]) .knob { transform: translateX(24px); }
:host([disabled]) { opacity: 0.5; }
:host([disabled]) .track { cursor: not-allowed; }
:host(:focus-visible) .track { outline: 2px solid var(--color-primary, #4CAF50); outline-offset: 2px; }
`;

export class Q0Switch extends Q0Element {
    static observedAttributes = ["checked", "disabled"];

    constructor() {
        super();
        const root = this.attachStyles(CSS);
        root.innerHTML = `<div class="track" part="track"><div class="knob" part="knob"></div></div>`;

        this.addEventListener("click", () => {
            if (this.disabled) return;
            this.checked = !this.checked;
            this.emit("change", { checked: this.checked });
        });
        this.addEventListener("keydown", (event) => {
            if (this.disabled) return;
            if (event.key === " " || event.key === "Enter") {
                event.preventDefault();
                this.click();
            }
        });
    }

    connectedCallback() {
        this.setAttribute("role", "switch");
        this.setAttribute("tabindex", this.disabled ? "-1" : "0");
        this.setAttribute("aria-checked", String(this.checked));
    }

    attributeChangedCallback(name) {
        if (name === "checked") this.setAttribute("aria-checked", String(this.checked));
        if (name === "disabled") this.setAttribute("tabindex", this.disabled ? "-1" : "0");
    }

    get checked() {
        return this.hasAttribute("checked");
    }
    set checked(v) {
        this.toggleAttribute("checked", Boolean(v));
    }

    get disabled() {
        return this.hasAttribute("disabled");
    }
    set disabled(v) {
        this.toggleAttribute("disabled", Boolean(v));
    }

    get name() {
        return this.getAttribute("name") ?? "";
    }
}

defineElement("q0ui-switch", Q0Switch);
