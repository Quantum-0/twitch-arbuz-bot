/* Переиспользуемый виджет подсказок поиска пользователей (/api/user/search).
 *
 * Использование:
 *   UserSuggest.attach(inputEl, {context: "bans" | "deposit" | "generic", onSelect});
 *
 * Подсказки не строгие: поле остаётся свободным вводом, выпадающий список —
 * только помощь. Стили — .user-suggest-* в styles.css.
 */
window.UserSuggest = (() => {
    "use strict";

    const MIN_LENGTH = 2;
    const DEBOUNCE_MS = 250;

    function attach(input, options = {}) {
        const context = options.context || "generic";
        const dropdown = document.createElement("div");
        dropdown.className = "user-suggest-dropdown";
        dropdown.hidden = true;

        const host = input.parentElement;
        if (!host) return null;
        if (getComputedStyle(host).position === "static") {
            host.classList.add("user-suggest-host");
        }
        host.appendChild(dropdown);

        let items = []; // плоский список для навигации: {value, label, source}
        let activeIndex = -1;
        let debounceTimer = null;
        let currentFetch = null;
        let outsideClickListener = null;

        function close() {
            dropdown.hidden = true;
            activeIndex = -1;
            if (outsideClickListener) {
                document.removeEventListener("mousedown", outsideClickListener);
                outsideClickListener = null;
            }
        }

        function renderItem(item) {
            const row = document.createElement("div");
            row.className = "user-suggest-item";
            row.dataset.index = String(item.index);
            const icon = item.avatar_url
                ? Object.assign(document.createElement("img"), {src: item.avatar_url, alt: ""})
                : Object.assign(document.createElement("span"), {className: "user-suggest-icon", textContent: "💬"});
            const label = Object.assign(document.createElement("span"), {textContent: item.label});
            row.append(icon, label);
            return row;
        }

        function renderGroup(title, groupItems, startIndex) {
            const fragment = document.createDocumentFragment();
            const header = document.createElement("div");
            header.className = "user-suggest-group";
            header.textContent = title;
            fragment.appendChild(header);
            groupItems.forEach((item, i) => {
                const flat = {value: item.value, label: item.label, avatar_url: item.avatar_url, source: item.source, index: startIndex + i};
                items.push(flat);
                fragment.appendChild(renderItem(flat));
            });
            return fragment;
        }

        function render(data) {
            items = [];
            // Прошлая подсветка больше не соответствует новому списку — сбрасываем,
            // иначе Enter молча выберет невидимый элемент
            activeIndex = -1;
            dropdown.innerHTML = "";
            if (outsideClickListener) {
                document.removeEventListener("mousedown", outsideClickListener);
                outsideClickListener = null;
            }
            const users = (data.users || []).map((u) => ({value: u.login, label: u.login, avatar_url: u.avatar_url, source: "user"}));
            // Чаттер хранится display name'ом — в поле подставляем lower (это логин)
            const chatters = (data.chatters || []).map((c) => ({
                value: String(c.name).toLowerCase(), label: c.name, avatar_url: null, source: "chatter",
            }));
            if (users.length) dropdown.appendChild(renderGroup("Пользователи сайта", users, items.length));
            if (chatters.length) dropdown.appendChild(renderGroup("Зрители канала", chatters, items.length));
            if (!items.length) {
                const empty = document.createElement("div");
                empty.className = "user-suggest-empty";
                empty.textContent = "Нет подсказок — можно ввести логин вручную";
                dropdown.appendChild(empty);
            }
            dropdown.hidden = false;
            outsideClickListener = (e) => {
                if (!dropdown.contains(e.target) && e.target !== input) close();
            };
            document.addEventListener("mousedown", outsideClickListener);
        }

        async function loadSuggestions() {
            const q = input.value.trim().replace(/^@/, "");
            if (q.length < MIN_LENGTH) {
                close();
                return;
            }
            if (currentFetch) currentFetch.abort();
            const controller = new AbortController();
            currentFetch = controller;
            try {
                const resp = await fetch(
                    `/api/user/search?q=${encodeURIComponent(q)}&context=${encodeURIComponent(context)}&limit=10`,
                    {signal: controller.signal, headers: {Accept: "application/json"}, credentials: "same-origin"},
                );
                if (!resp.ok) return;
                render(await resp.json());
            } catch (e) {
                if (e.name !== "AbortError") close();
            } finally {
                if (currentFetch === controller) currentFetch = null;
            }
        }

        function select(item) {
            input.value = item.value;
            close();
            if (typeof options.onSelect === "function") options.onSelect(item);
        }

        function setActive(index) {
            const rows = dropdown.querySelectorAll(".user-suggest-item");
            rows.forEach((row) => row.classList.toggle("active", Number(row.dataset.index) === index));
            rows[index]?.scrollIntoView({block: "nearest"});
        }

        input.addEventListener("input", () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(loadSuggestions, DEBOUNCE_MS);
        });

        input.addEventListener("keydown", (e) => {
            if (dropdown.hidden || !items.length) return;
            if (e.key === "ArrowDown") {
                e.preventDefault();
                activeIndex = (activeIndex + 1) % items.length;
                setActive(activeIndex);
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                activeIndex = (activeIndex <= 0 ? items.length : activeIndex) - 1;
                setActive(activeIndex);
            } else if (e.key === "Enter" && activeIndex >= 0) {
                e.preventDefault();
                select(items[activeIndex]);
            } else if (e.key === "Escape") {
                close();
            }
        });

        dropdown.addEventListener("mousedown", (e) => {
            const row = e.target.closest(".user-suggest-item");
            if (!row) return;
            e.preventDefault(); // не уводим фокус из поля до обработки выбора
            select(items[Number(row.dataset.index)]);
        });

        return {close};
    }

    return {attach};
})();
