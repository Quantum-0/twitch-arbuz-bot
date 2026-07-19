(() => {
    "use strict";

    const SORT_LABELS = {
        recommended: "Рекомендуемое",
        followers: "По фолловерам",
        created: "По дате регистрации",
        name: "По имени",
    };

    const VALID_SORTS = ["recommended", "followers", "created", "name"];
    const VALID_ORDERS = ["asc", "desc"];
    const FILTER_KEYS = ["bot", "meme", "ai", "overlay", "online", "pants"];

    const TRISTATE_CYCLE = ["null", "true", "false"];

    const ROLE_BADGES = {
        beta: {
            title: "Бета-тестер",
            svg: `<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
                <defs>
                    <linearGradient id="betaGradient" x1="0" y1="0" x2="48" y2="48" gradientUnits="userSpaceOnUse">
                        <stop stop-color="#7B61FF"/><stop offset="1" stop-color="#4AE0FF"/>
                    </linearGradient>
                </defs>
                <circle cx="24" cy="24" r="14" fill="url(#betaGradient)" stroke="white" stroke-width="2"/>
                <text x="24" y="25.5" text-anchor="middle" dominant-baseline="middle"
                      fill="white" font-size="14" font-weight="bold" font-family="sans-serif">β</text>
            </svg>`,
        },
        dev: {
            title: "Разработчик",
            svg: `<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
                <defs>
                    <linearGradient id="devGradientSunset" x1="0" y1="0" x2="48" y2="48" gradientUnits="userSpaceOnUse">
                        <stop stop-color="#FF8A00"/><stop offset="1" stop-color="#E52E71"/>
                    </linearGradient>
                </defs>
                <circle cx="24" cy="24" r="14" fill="url(#devGradientSunset)" stroke="white" stroke-width="2"/>
                <text x="24" y="25.5" text-anchor="middle" dominant-baseline="middle"
                      fill="white" font-size="11.5" font-weight="bold" font-family="sans-serif">DEV</text>
            </svg>`,
        },
        donater: {
            title: "Донатер",
            svg: `<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
                <defs>
                    <linearGradient id="donaterGradient" x1="0" y1="0" x2="48" y2="48" gradientUnits="userSpaceOnUse">
                        <stop stop-color="#faba2f"/><stop offset="1" stop-color="#e39312"/>
                    </linearGradient>
                </defs>
                <circle cx="24" cy="24" r="14" fill="url(#donaterGradient)" stroke="white" stroke-width="2"/>
                <text x="24" y="25.5" text-anchor="middle" dominant-baseline="middle"
                      fill="#fcf39f" font-size="11.5" font-weight="bold" font-family="sans-serif">$</text>
            </svg>`,
        },
    };

    const state = {
        sort: "recommended",
        order: "desc",
        filters: Object.fromEntries(FILTER_KEYS.map((k) => [k, null])),
    };

    const els = {
        grid: document.getElementById("streamersGrid"),
        loading: document.getElementById("streamersLoading"),
        sortBtn: document.getElementById("streamersSortBtn"),
        sortLabel: document.getElementById("streamersSortLabel"),
        orderBtn: document.getElementById("streamersOrderBtn"),
        orderIcon: document.getElementById("streamersOrderIcon"),
        filtersBtn: document.getElementById("streamersFiltersBtn"),
        sortDropdown: document.getElementById("streamersSortDropdown"),
        filtersPanel: document.getElementById("streamersFilters"),
        template: document.getElementById("streamerCardTemplate"),
    };

    function readUrlState() {
        const params = new URLSearchParams(window.location.search);
        const sort = params.get("sort");
        if (sort && VALID_SORTS.includes(sort)) state.sort = sort;
        const order = params.get("order");
        if (order && VALID_ORDERS.includes(order)) state.order = order;
        for (const key of FILTER_KEYS) {
            const raw = params.get(`f_${key}`);
            if (raw === "true") state.filters[key] = true;
            else if (raw === "false") state.filters[key] = false;
            else state.filters[key] = null;
        }
    }

    function writeUrlState() {
        const params = new URLSearchParams();
        if (state.sort !== "recommended") params.set("sort", state.sort);
        if (state.sort !== "recommended" && state.order !== "desc") params.set("order", state.order);
        for (const key of FILTER_KEYS) {
            const v = state.filters[key];
            if (v !== null) params.set(`f_${key}`, v ? "true" : "false");
        }
        const qs = params.toString();
        const url = qs ? `?${qs}` : window.location.pathname;
        window.history.replaceState(null, "", url);
    }

    function renderControls() {
        els.sortLabel.textContent = SORT_LABELS[state.sort] || SORT_LABELS.recommended;
        const isRecommended = state.sort === "recommended";
        els.orderBtn.disabled = isRecommended;
        els.orderBtn.classList.toggle("disabled", isRecommended);
        els.orderIcon.textContent = state.order === "asc" ? "↑" : "↓";
        els.sortBtn.classList.toggle("active", !isRecommended);

        els.sortDropdown.querySelectorAll("[data-sort]").forEach((btn) => {
            btn.classList.toggle("selected", btn.dataset.sort === state.sort);
        });

        els.filtersPanel.querySelectorAll(".streamer-filter-chip").forEach((chip) => {
            const key = chip.dataset.filter;
            const v = state.filters[key];
            const stateStr = v === null ? "null" : v ? "true" : "false";
            const indicator = chip.querySelector(".chip-indicator");
            indicator.dataset.state = stateStr;
            chip.classList.toggle("active", v !== null);
        });

        const anyFilterActive = Object.values(state.filters).some((v) => v !== null);
        els.filtersBtn.classList.toggle("active", anyFilterActive);
    }

    function renderStreamers(rows) {
        els.grid.innerHTML = "";
        if (!rows || rows.length === 0) {
            const empty = document.createElement("div");
            empty.className = "streamers-empty";
            empty.textContent = "Никого не найдено по выбранным фильтрам.";
            els.grid.appendChild(empty);
            return;
        }
        const fragment = document.createDocumentFragment();
        for (const row of rows) {
            const node = els.template.content.firstElementChild.cloneNode(true);
            const link = node.querySelector("a");
            link.href = `/profile/${row.username}`;
            const img = node.querySelector("img");
            img.src = row.avatar_url;
            img.alt = row.username;
            if (row.role && ROLE_BADGES[row.role]) {
                const badge = node.querySelector(".streamer-badge");
                badge.title = ROLE_BADGES[row.role].title;
                badge.innerHTML = ROLE_BADGES[row.role].svg;
                badge.hidden = false;
            }
            if (row.is_live) {
                node.querySelector(".live-indicator").hidden = false;
            }
            fragment.appendChild(node);
        }
        els.grid.appendChild(fragment);
    }

    function showLoading() {
        els.grid.innerHTML = "";
        const loading = document.createElement("div");
        loading.id = "streamersLoading";
        loading.className = "streamers-loading";
        loading.textContent = "Загрузка списка...";
        els.grid.appendChild(loading);
    }

    function showError(message) {
        els.grid.innerHTML = "";
        const err = document.createElement("div");
        err.className = "streamers-empty";
        err.textContent = message || "Не удалось загрузить список стримеров.";
        els.grid.appendChild(err);
    }

    let currentRequest = null;

    async function fetchStreamers() {
        showLoading();
        const params = new URLSearchParams();
        params.set("sort", state.sort);
        params.set("order", state.order);
        for (const key of FILTER_KEYS) {
            const v = state.filters[key];
            if (v !== null) params.set(`f_${key}`, v ? "true" : "false");
        }
        if (currentRequest && currentRequest.abort) currentRequest.abort();
        const controller = new AbortController();
        currentRequest = controller;
        try {
            const resp = await fetch(`/api/user/streamers?${params.toString()}`, {
                signal: controller.signal,
                headers: { Accept: "application/json" },
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            renderStreamers(data);
        } catch (e) {
            if (e.name === "AbortError") return;
            showError();
        } finally {
            if (currentRequest === controller) currentRequest = null;
        }
    }

    function cycleFilter(key) {
        const current = state.filters[key];
        const currentStr = current === null ? "null" : current ? "true" : "false";
        const idx = TRISTATE_CYCLE.indexOf(currentStr);
        const nextStr = TRISTATE_CYCLE[(idx + 1) % TRISTATE_CYCLE.length];
        state.filters[key] = nextStr === "null" ? null : nextStr === "true";
    }

    function initEvents() {
        els.sortBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const expanded = !els.sortDropdown.hidden;
            els.sortDropdown.hidden = expanded;
            els.sortBtn.setAttribute("aria-expanded", String(!expanded));
        });
        els.sortDropdown.addEventListener("click", (e) => {
            const btn = e.target.closest("[data-sort]");
            if (!btn) return;
            state.sort = btn.dataset.sort;
            els.sortDropdown.hidden = true;
            els.sortBtn.setAttribute("aria-expanded", "false");
            renderControls();
            writeUrlState();
            fetchStreamers();
        });
        document.addEventListener("click", (e) => {
            if (!els.sortBtn.contains(e.target) && !els.sortDropdown.contains(e.target)) {
                els.sortDropdown.hidden = true;
                els.sortBtn.setAttribute("aria-expanded", "false");
            }
        });
        els.orderBtn.addEventListener("click", () => {
            if (els.orderBtn.disabled) return;
            state.order = state.order === "asc" ? "desc" : "asc";
            renderControls();
            writeUrlState();
            fetchStreamers();
        });
        els.filtersBtn.addEventListener("click", () => {
            const expanded = !els.filtersPanel.hidden;
            els.filtersPanel.hidden = expanded;
            els.filtersBtn.setAttribute("aria-expanded", String(!expanded));
        });
        els.filtersPanel.addEventListener("click", (e) => {
            const chip = e.target.closest(".streamer-filter-chip");
            if (!chip) return;
            cycleFilter(chip.dataset.filter);
            renderControls();
            writeUrlState();
            fetchStreamers();
        });
    }

    function init() {
        readUrlState();
        renderControls();
        initEvents();
        fetchStreamers();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
