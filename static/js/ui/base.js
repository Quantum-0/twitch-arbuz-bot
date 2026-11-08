/**
 * Базовый класс веб-компонентов библиотеки q0ui.
 *
 * Конвенции библиотеки:
 *  - состояние задаётся атрибутами (плюс отражённые свойства для JS);
 *  - наружу компоненты кричат стандартными событиями input/change
 *    (bubbles + composed, пробиваются из shadow DOM) и кастомными событиями
 *    с префиксом q0ui-;
 *  - компоненты не знают про API — сохранением занимается JS страницы
 *    (см. bindings.js);
 *  - темизация — CSS-переменные сайта (--color-*), они наследуются внутрь
 *    shadow DOM; точечная кастомизация — атрибуты и ::part().
 */

const BASE_CSS = `
:host { box-sizing: border-box; font-family: inherit; }
*, *::before, *::after { box-sizing: border-box; }
`;

export class Q0Element extends HTMLElement {
    /**
     * Создаёт открытый shadow-корень и подключает в него общие + компонентные
     * стили. Возвращает корень (разметку компонент добавляет сам).
     */
    attachStyles(css) {
        const root = this.attachShadow({ mode: "open" });
        const text = BASE_CSS + css;
        if (typeof CSSStyleSheet !== "undefined" && root.adoptedStyleSheets !== undefined) {
            try {
                const sheet = new CSSStyleSheet();
                sheet.replaceSync(text);
                root.adoptedStyleSheets = [sheet];
                return root;
            } catch {
                /* старые браузеры без constructable stylesheets — <style> ниже */
            }
        }
        const style = document.createElement("style");
        style.textContent = text;
        root.appendChild(style);
        return root;
    }

    /** Событие всплывает и пробивается из shadow DOM наружу. */
    emit(type, detail = null) {
        this.dispatchEvent(new CustomEvent(type, { bubbles: true, composed: true, detail }));
    }

    /** Числовой атрибут с фолбэком. */
    numAttr(name, fallback) {
        const v = parseFloat(this.getAttribute(name));
        return Number.isFinite(v) ? v : fallback;
    }
}

/** Регистрация с защитой от повторного define (модуль импортирован дважды). */
export function defineElement(name, ctor) {
    if (!customElements.get(name)) customElements.define(name, ctor);
}
