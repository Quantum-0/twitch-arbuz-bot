/**
 * Общие утилиты библиотеки q0ui.
 */

export const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

/** Количество знаков после запятой у числа — для округления и форматирования. */
export function decimalsOf(n) {
    const match = String(n).match(/(?:\.(\d+))?(?:e([+-]?\d+))?$/);
    if (!match) return 0;
    return Math.max(0, (match[1] ?? "").length - Number(match[2] ?? 0));
}

/** Округление к ближайшему кратному step без накопления ошибок float. */
export function roundToStep(value, step) {
    if (!Number.isFinite(step) || step <= 0) return value;
    return Number((Math.round(value / step) * step).toFixed(Math.min(12, decimalsOf(step))));
}

/**
 * Парсинг числа из пользовательского ввода: запятая как десятичный разделитель,
 * пробелы игнорируются. Возвращает NaN, если не число.
 */
export function parseLooseNumber(raw) {
    const v = parseFloat(String(raw).trim().replace(/\s+/g, "").replace(",", "."));
    return Number.isFinite(v) ? v : NaN;
}

/** debounce с отменой. */
export function debounce(fn, ms) {
    let timer = null;
    const wrapped = (...args) => {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
            timer = null;
            fn(...args);
        }, ms);
    };
    wrapped.cancel = () => {
        if (timer) clearTimeout(timer);
        timer = null;
    };
    return wrapped;
}
