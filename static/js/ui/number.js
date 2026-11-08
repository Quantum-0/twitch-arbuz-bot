/**
 * <q0ui-number> — выбор числа кнопками ±/вводом (эволюция фидлового
 * numeric-updown).
 *
 * Атрибуты:
 *  value   — текущее значение;
 *  min, max — границы (по умолчанию 0 и 100);
 *  step    — шаг кнопок и клавиатуры (по умолчанию 1);
 *  editable — разрешить ручной ввод (иначе поле только для чтения);
 *  units   — суффикс единиц рядом с числом (например «шт»);
 *  disabled — блокировка.
 *
 * События: input (detail {value}) — каждое изменение, change (detail {value}) —
 * фиксация (кнопка, Enter/blur, стрелки).
 *
 * Доступность: кнопки честные <button>, стрелки ↑/↓ в поле, фокус-обводка.
 * Ввод понимает запятую как десятичный разделитель.
 */
import { Q0Element, defineElement } from "./base.js";
import { clamp, parseLooseNumber, roundToStep } from "./utils.js";

const CSS = `
:host { display: inline-block; }
.wrap {
    display: inline-flex;
    align-items: stretch;
    border: 1px solid var(--color-border, #ccc);
    border-radius: 5px;
    overflow: hidden;
    background: var(--color-bg-secondary, #fff);
    user-select: none;
}
.btn {
    width: 32px;
    border: none;
    background: var(--color-primary, #4CAF50);
    color: #fff;
    font-size: 18px;
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    transition: background-color 0.2s;
}
.btn:hover:enabled { background: var(--color-primary-hover, #45a049); }
.btn:disabled { background: var(--color-border, #ccc); cursor: not-allowed; }
.btn:focus-visible { outline: 2px solid var(--color-primary, #4CAF50); outline-offset: -2px; }
.fieldbox { display: inline-flex; align-items: center; flex: 1; min-width: 0; }
.field {
    flex: 1;
    width: 64px;
    min-width: 0;
    border: none;
    outline: none;
    background: transparent;
    color: var(--color-text, inherit);
    text-align: center;
    font: inherit;
    font-size: 15px;
    padding: 5px 4px;
}
.field[readonly] { cursor: default; }
.units { padding: 0 8px 0 2px; font-size: 13px; opacity: 0.7; white-space: nowrap; align-self: center; }
.units:empty { display: none; }
:host([disabled]) { opacity: 0.55; pointer-events: none; }
`;

export class Q0Number extends Q0Element {
    static observedAttributes = ["value", "min", "max", "step", "units", "disabled"];

    constructor() {
        super();
        const root = this.attachStyles(CSS);
        root.innerHTML = `
            <div class="wrap" part="wrap">
                <button class="btn btn-minus" part="button" type="button" aria-label="Уменьшить">−</button>
                <span class="fieldbox">
                    <input class="field" part="field" type="text" inputmode="decimal" autocomplete="off">
                    <span class="units"></span>
                </span>
                <button class="btn btn-plus" part="button" type="button" aria-label="Увеличить">+</button>
            </div>`;
        this._els = {
            minus: root.querySelector(".btn-minus"),
            plus: root.querySelector(".btn-plus"),
            field: root.querySelector(".field"),
            units: root.querySelector(".units"),
        };
        this._value = 0;
        this._min = 0;
        this._max = 100;
        this._step = 1;

        this._els.minus.addEventListener("click", () => this._changeBy(-this._step));
        this._els.plus.addEventListener("click", () => this._changeBy(this._step));

        this._els.field.addEventListener("input", () => {
            // Живой ввод: кричим input, но текст в поле не переписываем — не мешаем печатать.
            const v = parseLooseNumber(this._els.field.value);
            if (!Number.isNaN(v)) this.emit("input", { value: clamp(v, this._min, this._max) });
        });
        this._els.field.addEventListener("change", () => this._commitField());
        this._els.field.addEventListener("keydown", (event) => {
            if (event.key === "ArrowUp") {
                event.preventDefault();
                this._changeBy(this._step);
            } else if (event.key === "ArrowDown") {
                event.preventDefault();
                this._changeBy(-this._step);
            }
        });
    }

    connectedCallback() {
        this._readConfig();
        if (!this.hasAttribute("editable")) this._els.field.setAttribute("readonly", "");
        else this._els.field.removeAttribute("readonly");
        this.setAttribute("tabindex", "-1"); // фокус — на внутреннем поле и кнопках
        this._render();
    }

    attributeChangedCallback(name, oldValue, newValue) {
        if (oldValue === newValue) return;
        this._readConfig();
        if (name === "value" && Number(this.getAttribute("value")) !== this._value) {
            this._value = this._snap(this.numAttr("value", this._min));
        }
        if (this._els) this._render();
    }

    get value() {
        return this._value;
    }
    set value(v) {
        const next = this._snap(Number(v));
        this._value = next;
        this.setAttribute("value", String(next));
        this._render();
    }

    get disabled() {
        return this.hasAttribute("disabled");
    }
    set disabled(v) {
        this.toggleAttribute("disabled", Boolean(v));
    }

    _readConfig() {
        this._min = this.numAttr("min", 0);
        this._max = Math.max(this._min, this.numAttr("max", 100));
        this._step = Math.abs(this.numAttr("step", 1)) || 1;
        this._value = this._snap(clamp(this.numAttr("value", this._min), this._min, this._max));
    }

    /** Значение прибивается к сетке step, кратной от min. */
    _snap(v) {
        return clamp(roundToStep(v - this._min, this._step) + this._min, this._min, this._max);
    }

    _changeBy(delta) {
        const next = this._snap(this._value + delta);
        if (next === this._value) {
            this._render(); // на границе — обновить disabled-кнопки
            return;
        }
        this._value = next;
        this.setAttribute("value", String(next));
        this._render();
        this.emit("input", { value: this.value });
        this.emit("change", { value: this.value });
    }

    /** Фиксация ручного ввода: парсим, прибиваем к шагу, пишем обратно. */
    _commitField() {
        const parsed = parseLooseNumber(this._els.field.value);
        if (Number.isNaN(parsed)) {
            this._render();
            return;
        }
        this._value = this._snap(parsed);
        this.setAttribute("value", String(this._value));
        this._render();
        this.emit("input", { value: this.value });
        this.emit("change", { value: this.value });
    }

    _render() {
        if (!this._els) return;
        this._els.field.value = this._format(this._value);
        this._els.units.textContent = this.getAttribute("units") ?? "";
        this._els.minus.disabled = this._value <= this._min;
        this._els.plus.disabled = this._value >= this._max;
    }

    _format(v) {
        return String(Number(v.toFixed(6)));
    }
}

defineElement("q0ui-number", Q0Number);
