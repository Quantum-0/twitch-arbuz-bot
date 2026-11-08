/**
 * <q0ui-slider> — слайдер с тиками (эволюция фидлового custom-slider).
 *
 * Атрибуты:
 *  value     — текущее значение (реальное, не нормализованное);
 *  min, max  — границы (по умолчанию 0 и 1 — как в исходном фидле);
 *  step      — шаг изменения значения (к нему прибивается драг и клавиатура);
 *  tick-step — шаг штрихов на дорожке (в реальных единицах);
 *  snap      — прибивать значение к ближайшему штриху вместо step;
 *  label     — подпись; если задана, рисуется строка «подпись — значение»;
 *  units     — суффикс единиц в выводе значения (например «мин» или «px»);
 *  disabled  — блокировка.
 *
 * События: input (detail {value}) — при каждом изменении (драг/клавиатура),
 * change (detail {value}) — при фиксации значения (отпускание указателя,
 * нажатие клавиши).
 *
 * Доступность: role="slider", стрелки/Home/End, aria-valuemin/max/now.
 * Указатель: Pointer Events + setPointerCapture — работает мышью и тачем.
 */
import { Q0Element, defineElement } from "./base.js";
import { clamp, decimalsOf, roundToStep } from "./utils.js";

const CSS = `
:host { display: block; }
.wrap { display: flex; flex-direction: column; gap: 6px; }
.header { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 13px; }
.header:empty { display: none; }
.label { opacity: 0.85; }
.value {
    font-weight: 600;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    background: rgba(128, 128, 128, 0.15);
    padding: 1px 6px;
    border-radius: 4px;
    white-space: nowrap;
}
.track {
    position: relative;
    height: 6px;
    margin: 5px 0;
    border-radius: 3px;
    background: var(--q0ui-slider-track, rgba(128, 128, 128, 0.35));
    cursor: pointer;
    touch-action: none;
}
.tick {
    position: absolute;
    top: 50%;
    width: 2px;
    height: 10px;
    background: var(--color-text-muted, #888);
    opacity: 0.6;
    transform: translate(-50%, -50%);
    pointer-events: none;
}
.thumb {
    position: absolute;
    top: 50%;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--color-primary, #35b639);
    transform: translate(-50%, -50%);
    cursor: grab;
    transition: background-color 0.2s;
}
.thumb:hover { background: var(--color-primary-hover, #45a049); }
:host([disabled]) { opacity: 0.5; }
:host([disabled]) .track { cursor: not-allowed; }
:host(:focus-visible) .thumb { outline: 2px solid var(--color-primary, #4CAF50); outline-offset: 2px; }
`;

export class Q0Slider extends Q0Element {
    static observedAttributes = [
        "value", "min", "max", "step", "tick-step", "snap", "units", "label", "disabled",
    ];

    constructor() {
        super();
        const root = this.attachStyles(CSS);
        root.innerHTML = `
            <div class="wrap">
                <div class="header"><span class="label"></span><span class="value" part="value"></span></div>
                <div class="track" part="track"><div class="thumb" part="thumb"></div></div>
            </div>`;
        this._els = {
            header: root.querySelector(".header"),
            label: root.querySelector(".label"),
            value: root.querySelector(".value"),
            track: root.querySelector(".track"),
            thumb: root.querySelector(".thumb"),
        };
        this._value = 0;
        this._min = 0;
        this._max = 1;
        this._step = null;
        this._tickStep = null;
        this._dragging = false;

        this._els.track.addEventListener("pointerdown", (event) => {
            if (this.disabled) return;
            event.preventDefault();
            this._dragging = true;
            this._els.track.setPointerCapture(event.pointerId);
            this._applyPointer(event);
        });
        this._els.track.addEventListener("pointermove", (event) => {
            if (this._dragging) this._applyPointer(event);
        });
        const release = () => {
            if (!this._dragging) return;
            this._dragging = false;
            this.emit("change", { value: this.value });
        };
        this._els.track.addEventListener("pointerup", release);
        this._els.track.addEventListener("pointercancel", release);

        this.addEventListener("keydown", (event) => {
            if (this.disabled) return;
            const step = this._step > 0 ? this._step : (this._max - this._min) / 100;
            let next = null;
            switch (event.key) {
                case "ArrowLeft":
                case "ArrowDown":
                    next = this._value - step;
                    break;
                case "ArrowRight":
                case "ArrowUp":
                    next = this._value + step;
                    break;
                case "Home":
                    next = this._min;
                    break;
                case "End":
                    next = this._max;
                    break;
                default:
                    return;
            }
            event.preventDefault();
            this._setValue(this._snapValue(next), true);
            this.emit("change", { value: this.value });
        });
    }

    connectedCallback() {
        this._readConfig();
        this.setAttribute("role", "slider");
        this.setAttribute("tabindex", this.disabled ? "-1" : "0");
        this.setAttribute("aria-orientation", "horizontal");
        this._render();
    }

    attributeChangedCallback(name, oldValue, newValue) {
        if (oldValue === newValue) return;
        this._readConfig();
        if (name === "value" && Number(this.getAttribute("value")) !== this._value) {
            // Внешнее изменение значения (из JS или разметки).
            this._value = this._snapValue(clamp(this.numAttr("value", this._min), this._min, this._max));
        }
        if (name === "disabled") this.setAttribute("tabindex", this.disabled ? "-1" : "0");
        if (this._els) this._render();
    }

    get value() {
        return this._value;
    }
    set value(v) {
        this._setValue(Number(v), false);
    }

    get min() {
        return this._min;
    }
    get max() {
        return this._max;
    }

    get disabled() {
        return this.hasAttribute("disabled");
    }
    set disabled(v) {
        this.toggleAttribute("disabled", Boolean(v));
    }

    refresh() {
        this._render();
    }

    _readConfig() {
        this._min = this.numAttr("min", 0);
        this._max = Math.max(this._min, this.numAttr("max", 1));
        this._step = this.hasAttribute("step") ? Math.abs(this.numAttr("step", 1)) : null;
        this._tickStep = this.hasAttribute("tick-step") ? Math.abs(this.numAttr("tick-step", 0.1)) : null;
        if (!Number.isFinite(this._value)) this._value = this._min;
        this._value = this._snapValue(clamp(this.numAttr("value", this._value), this._min, this._max));
    }

    /** Шаг прибивания: к тикам (snap) либо к step; значения кратны от min. */
    _snapValue(v) {
        const grid = this.hasAttribute("snap") ? this._tickStep : this._step;
        if (grid > 0) return clamp(roundToStep(v - this._min, grid) + this._min, this._min, this._max);
        return clamp(v, this._min, this._max);
    }

    _applyPointer(event) {
        const rect = this._els.track.getBoundingClientRect();
        const ratio = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1);
        this._setValue(this._snapValue(this._min + ratio * (this._max - this._min)), true);
    }

    _setValue(v, emitInput) {
        this._value = clamp(v, this._min, this._max);
        this.setAttribute("value", this._format(this._value));
        this._render();
        if (emitInput) this.emit("input", { value: this.value });
    }

    _render() {
        if (!this._els) return;
        const span = this._max - this._min || 1;
        const ratio = clamp((this._value - this._min) / span, 0, 1);

        this._els.thumb.style.left = `${ratio * 100}%`;
        this._els.value.textContent = this._format(this._value) + (this.getAttribute("units") ?? "");

        const label = this.getAttribute("label");
        this._els.label.textContent = label ?? "";
        this._els.header.style.display = label ? "" : "none";

        this.setAttribute("aria-valuemin", String(this._min));
        this.setAttribute("aria-valuemax", String(this._max));
        this.setAttribute("aria-valuenow", String(this._value));
        this.setAttribute("aria-valuetext", this._format(this._value) + (this.getAttribute("units") ?? ""));
        if (label) this.setAttribute("aria-label", label);

        this._renderTicks(span);
    }

    _renderTicks(span) {
        this._els.track.querySelectorAll(".tick").forEach((tick) => tick.remove());
        if (!(this._tickStep > 0)) return;
        const count = Math.round(span / this._tickStep);
        const maxTicks = 200; // страховка от tick-step вроде 0.0001
        for (let k = 1; k < count && k <= maxTicks; k++) {
            const tick = document.createElement("div");
            tick.className = "tick";
            tick.style.left = `${((k * this._tickStep) / span) * 100}%`;
            this._els.track.insertBefore(tick, this._els.thumb);
        }
    }

    /** Форматирование значения: шаг задаёт точность, хвостовые нули убираются. */
    _format(v) {
        const grid = this.hasAttribute("snap") ? this._tickStep : this._step;
        const decimals = grid > 0 ? decimalsOf(grid) : 3;
        return String(Number(v.toFixed(Math.max(decimals, decimalsOf(this._min), decimalsOf(this._max)))));
    }
}

defineElement("q0ui-slider", Q0Slider);
