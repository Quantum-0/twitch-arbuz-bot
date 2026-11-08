/**
 * Тонкие контроллеры связи q0ui-компонентов с API страницы.
 * Сами компоненты ничего не сохраняют — только кричат событиями; отсюда
 * события превращаются в запросы и обновление зависимых блоков.
 */

/**
 * Автосохранение тумблеров настроек: слушает change всех <q0ui-switch name=...>
 * внутри root и зовёт onSave(name, checked, switchEl) — колбэк делает фактический
 * POST и показывает уведомление.
 */
export function bindSettingSwitches(root, onSave) {
    root.addEventListener("change", (event) => {
        const control = event.target;
        if (!(control instanceof HTMLElement) || control.tagName !== "Q0UI-SWITCH") return;
        const name = control.getAttribute("name");
        if (!name || control.disabled) return;
        onSave(name, control.checked, control);
    });
}

/**
 * Кнопка управления наградой («Создать/Исправить/Отключить награду») —
 * state-machine поверх событий q0ui-status карточки. Заменяет три копии
 * updateRewardButton/handleRewardButton из легаси.
 *
 * btn     — кнопка;
 * card    — <q0ui-status-card> с эндпоинтом проверки награды;
 * actions — {create: fn, fix: fn, delete: fn}; по клику вызывается действие
 *           текущего состояния. Действие само обновляет карточку (card.refresh()).
 */
export function createRewardButton(btn, card, actions) {
    const LABELS = {
        loading: "Проверка...",
        create: "Создать награду",
        fix: "Исправить награду",
        delete: "Отключить награду",
        unknown: "Неизвестное состояние",
    };
    let action = "loading";

    const apply = (next) => {
        action = next;
        btn.disabled = next === "loading" || next === "unknown";
        btn.classList.toggle("btn-danger", next === "delete");
        btn.dataset.action = next;
        btn.textContent = LABELS[next] ?? LABELS.unknown;
    };

    const applyFromStatus = ({ state, problems, data }) => {
        if (state === "ok" || state === "warn") {
            apply("delete"); // награда работает — можно отключить
            return;
        }
        if (!data) {
            apply("loading"); // сеть/сервер недоступны
            return;
        }
        // При проблеме токена кнопки бесполезны: управление наградой вернёт
        // ошибку до переавторизации — оставляем прежнее состояние.
        if (problems.includes("twitch_token_invalid")) return;
        apply(data.state === "broken" ? "fix" : "create");
    };

    card.addEventListener("q0ui-status", (event) => applyFromStatus(event.detail));
    btn.addEventListener("click", () => {
        actions[action]?.();
    });

    apply("loading");
    // Первый опрос карточки мог пройти до подвешивания слушателя.
    if (card.lastStatus) applyFromStatus(card.lastStatus);
}
