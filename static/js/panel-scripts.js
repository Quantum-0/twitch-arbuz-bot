let coinSaveTimer = null;
let isRefresh = false;

function showNotification(title, message, isError=false) {
    const container = document.querySelector('.notification-container');
    const div = document.createElement('div');
    div.className = 'notification' + (isError ? ' error' : '');
    div.innerHTML = `<div class="notification-header">${title}</div><div class="notification-body">${message}</div>`;
    container.appendChild(div);
    setTimeout(() => {
        div.style.animation = 'fadeOutDown var(--notif-out-duration) forwards';
        setTimeout(() => div.remove(), 250);
    }, 4000);
}

function setupAiStickerReward(enabled) {
    fetch('/api/user/setup-ai-stickers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    })
    .then(res => res.json().then(data => ({ ok: res.ok, data })))
    .then(({ok, data}) => showNotification(data.title || 'Настройки', data.message, !ok))
    .catch(err => showNotification('Ошибка', err.message, true));
}

async function updateSetting(name, value) {
    const delays = [1000, 2000, 4000, 7000]; // Задержки в миллисекундах
    let attempt = 0;

    // Приводим к строке и кодируем спецсимволы
    const encodedValue = encodeURIComponent(String(value));

    while (true) {
        try {
            const res = await fetch('/api/user/update_settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `${name}=${encodedValue}`
            });

            // Если это ошибка 500 и попытки не исчерпаны — уходим на ретрай
            if (res.status === 500 && attempt < delays.length) {
                await new Promise(resolve => setTimeout(resolve, delays[attempt]));
                attempt++;
                continue;
            }

            // Обрабатываем результат (успех или любую другую ошибку)
            const data = await res.json();
            showNotification(data.title || 'Настройки', data.message, !res.ok);
            return;

        } catch (err) {
            // Если упала сеть, тоже пробуем повторить запрос
            if (attempt < delays.length) {
                await new Promise(resolve => setTimeout(resolve, delays[attempt]));
                attempt++;
                continue;
            }

            showNotification('Ошибка', err.message, true);
            return;
        }
    }
}

function initToggles() {
    document.querySelectorAll('.toggle-switch').forEach(toggle => {
        toggle.addEventListener('click', () => {
            toggle.classList.toggle('active');
            if (toggle.getAttribute('role') === 'switch') {
                toggle.setAttribute('aria-checked', toggle.classList.contains('active') ? 'true' : 'false');
            }
            void updateSetting(toggle.dataset.name, toggle.classList.contains('active'));
            if (toggle.dataset.name === 'enable_chat_bot') {
                if (toggle.classList.contains('active')) {
                    ym(108266334, 'reachGoal', 'enableChatBot');
                }
                updateDependentTogglesState();
            }
        });
    });
}

function initOverlays() {
    document.querySelectorAll(".overlay-card").forEach(card => {

        const linkDiv = card.querySelector(".overlay-link");
        const linkDockDiv = card.querySelector(".dock-panel-link");

        // --- Автоматическое создание подписей для range-слайдеров ---
        card.querySelectorAll("input[type='range']").forEach(slider => {
            // Создаем верхнюю строку для текста и цифры
            const header = document.createElement("div");
            header.className = "control-header";

            // Создаем метку для текста и переносим текст из родителя в неё
            const label = document.createElement("span");
            label.className = "control-label";
            // Забираем текстовый узел (название настройки) и переносим в label
            label.textContent = slider.parentNode.firstChild.textContent.trim();
            slider.parentNode.firstChild.textContent = ""; // Очищаем старый голый текст

            // Создаем элемент для вывода текущего значения
            const output = document.createElement("output");
            output.className = "control-output";
            output.textContent = slider.value;

            // Собираем структуру вместе
            header.appendChild(label);
            header.appendChild(output);
            slider.parentNode.insertBefore(header, slider);
        });
        // -----------------------------------------------------------------------

        card.querySelectorAll("[data-param]").forEach(el => {
            el.addEventListener("input", () => {
                updateOverlayLink(card);

                if (el.type === "range") {
                    const output = el.parentNode.querySelector(".control-output");
                    if (output) output.textContent = el.value;
                }
            });
        });

        card.querySelector(".overlay-toggle-settings").addEventListener("click", () => {
            card.querySelector(".overlay-settings").classList.toggle("active");
        });

        linkDiv.addEventListener("click", () => {
            navigator.clipboard.writeText(linkDiv.textContent);
            linkDiv.classList.add("copied");
            linkDiv.textContent = "Скопировано!";
            setTimeout(() => {
                updateOverlayLink(card);
                linkDiv.classList.remove("copied");
            }, 1000);
            ym(108266334, 'reachGoal', 'copiedOverlayLink');
        });

        if (linkDockDiv) {
            linkDockDiv.addEventListener("click", () => {
                navigator.clipboard.writeText(linkDockDiv.textContent);
                linkDockDiv.classList.add("copied");
                linkDockDiv.textContent = "Скопировано!";
                setTimeout(() => {
                    updateOverlayLink(card);
                    linkDockDiv.classList.remove("copied");
                }, 1000);
            });
        };

        updateOverlayLink(card);
    });
}

function updateDependentTogglesState() {
    const chatbotToggle = document.querySelector('.toggle-switch[data-name="enable_chat_bot"]');
    const isEnabled = chatbotToggle ? chatbotToggle.classList.contains('active') : false;
    const container = document.getElementById('dependent-toggles');
    const extraSettingsContainer = document.getElementById('chatbot-extra-settings');
    const extraDivider = document.getElementById('chatbot-extra-divider');
    if (container) {
        container.classList.toggle("active", isEnabled);
    }
    if (extraDivider) {
        extraDivider.classList.toggle("active", isEnabled);
    }
    if (extraSettingsContainer) {
        extraSettingsContainer.classList.toggle("active", isEnabled);
    }
}

function initTargetBehaviourRadios() {
    document.querySelectorAll('input[name="chatbot_default_target_behaviour"]').forEach(radio => {
        radio.addEventListener('change', (event) => {
            if (!event.target.checked) return;
            void updateSetting('chatbot_default_target_behaviour', event.target.value);
        });
    });
}

function openActivateModal() {
    isRefresh = false;
    openModal();
}

function openRefreshModal() {
    isRefresh = true;
    openModal();
}

function openModal() { ym(108266334, 'reachGoal', 'setupMemealertsOpenModal'); document.getElementById('memealert-modal').style.display = 'flex'; }
function closeModal() { document.getElementById('memealert-modal').style.display = 'none'; }
function saveMemealert() {
    const key = document.getElementById('memealert-key').value.trim();
    if (!key) return showNotification('Ошибка', 'Введите ключ', true);
    closeModal();
    toggleMemealerts(true, key, isRefresh);
}

function toggleMemealerts(enable, key='', refresh=false) {
    if (enable) {
        ym(108266334, 'reachGoal', 'setupMemealerts');
    }
    const btn = document.getElementById('memealerts-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Подождите...';
    }
    const url = `/api/user/memealerts?enable=${enable}${refresh ? '&refresh=true' : ''}`;
    fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: enable ? `key=${encodeURIComponent(key)}` : ''
    })
    .then(res => res.json().then(data => ({ ok: res.ok, data })))
    .then(({ok, data}) => {
        showNotification(data.title || 'Мемалёрты', data.message, !ok);
        setTimeout(() => location.reload(), 2000);
    })
    .catch(err => {
        showNotification('Ошибка', err.message, true);
        if (btn) {
            btn.disabled = false;
            btn.textContent = enable ? 'Включить мемалёрты' : 'Отключить мемалёрты';
        }
    });
}

function changeCoinCount(delta) {
    const input = document.getElementById('coin-count');
    if (!input) return;
    let val = parseInt(input.value, 10);
    if (isNaN(val)) val = 1;
    val = Math.max(1, Math.min(100, val + delta));
    input.value = val;
    triggerCoinSave();
}

function triggerCoinSave() {
    if (coinSaveTimer) clearTimeout(coinSaveTimer);
    coinSaveTimer = setTimeout(updateCoins, 3000);
}

function updateCoins() {
    const input = document.getElementById('coin-count');
    if (!input) return;
    const count = parseInt(input.value, 10);
    if (isNaN(count) || count < 1 || count > 100) {
        return showNotification('Ошибка', 'Введите число от 1 до 100', true);
    }
    fetch('/api/user/memealerts/coins', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ count: count })
    })
    .then(res => res.json().then(data => ({ ok: res.ok, data })))
    .then(({ok, data}) => showNotification(data.title || 'Мемкоины', data.message, !ok))
    .catch(err => showNotification('Ошибка', err.message, true));
}

document.addEventListener('DOMContentLoaded', () => {
    initToggles();
    updateDependentTogglesState();
    initTargetBehaviourRadios();
    initOverlays();
    initStatusCards();
    const coinInput = document.getElementById('coin-count');
    if (coinInput) {
        coinInput.addEventListener('input', triggerCoinSave);
    }
});

function setupHeat() {
    ym(108266334, 'reachGoal', 'installHeat');
    fetch('/api/user/install-heat', {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
    })
    .then(res => res.json().then(data => ({ ok: res.ok, data })))
    .then((
        {ok, data}) => {
            showNotification(ok ? 'Настройки' : 'Ошибка', ok ? 'Плагин Heat успешно установлен!' : 'Ошибка автоматической установки плагина Heat. Открываем страничку плагина.', !ok);
            if (ok) {
                document.querySelectorAll('div.plugin-required').forEach(element => {
                    element.remove();
                })
            } else {
                window.open('https://dashboard.twitch.tv/extensions/cr20njfkgll4okyrhag7xxph270sqk-2.1.1', '_blank', 'noopener,noreferrer');
            }
        })
    .catch(err => { window.open('https://dashboard.twitch.tv/extensions/cr20njfkgll4okyrhag7xxph270sqk-2.1.1', '_blank', 'noopener,noreferrer'); showNotification('Ошибка', 'Ошибка автоматической установки плагина Heat. Открываем страничку плагина.', true); });
}

function updateOverlayLink(card) {
    const base = card.dataset.base;
    const base_dock = card.dataset.base_dock;
    const params = new URLSearchParams();

    params.set("channel_id", channel_id);
    params.set("channel_name", channel_name);

    card.querySelectorAll("[data-param]").forEach(el => {
        const key = el.dataset.param;
        if (el.type === "checkbox") {
            params.set(key, el.checked);
        } else if (el.type == "radio") {
            console.log(el)
            if (el.checked)
                params.set(key, el.value);
        } else {
            params.set(key, el.value);
        }
    });

    const link = base + "?" + params.toString();
    const linkDiv = card.querySelector(".overlay-link");
    linkDiv.textContent = link;

    if (base_dock) {
        params.set("secret", slovotron_secret);
        const link = base_dock + "?" + params.toString();
        const linkDiv = card.querySelector(".dock-panel-link");
        linkDiv.textContent = link;
    }
}

async function checkStatus(card) {
    const indicator = card.querySelector(".status-indicator");
    const problems_list = card.querySelector(".card-status-problems-list");
    const endpoint = card.dataset.endpoint;
    const type = card.dataset.type;

    // состояние загрузки
    indicator.classList.remove("active", "error");

    try {
        const res = await fetch(endpoint);
        const data = await res.json();

        if (data.result === true) {
            indicator.classList.add("active");

            // Обновляем кнопку управления наградой
            if (type === "memealerts-reward") {
                updateRewardButton("delete");
            }
            if (type === "ai-stickers-reward" && typeof updateAiStickerRewardButton === "function") {
                updateAiStickerRewardButton("delete");
            }

            // Логика для Heat
            if (type === "heat") {
                document.querySelectorAll(".plugin-required").forEach(el => {
                    el.style.display = "none";
                });
            }

            if (problems_list) {
                problems_list.innerHTML = "";
            }

        } else {
            indicator.classList.add("error");
            problems_list.innerHTML = "";

            (data.problems || []).forEach(problem => {
                const li = document.createElement("li");
                li.textContent = problem;
                problems_list.appendChild(li);
            });

            // Обновляем кнопку управления наградой
            if (type === "memealerts-reward") {
                switch (data.state) {
                    case "missing":
                        updateRewardButton("create");
                        break;
                    case "broken":
                        updateRewardButton("fix");
                        break;
                    default:
                        updateRewardButton("create");
                        break;
                }
            }
            if (type === "ai-stickers-reward" && typeof updateAiStickerRewardButton === "function") {
                switch (data.state) {
                    case "missing":
                        updateAiStickerRewardButton("create");
                        break;
                    case "broken":
                        updateAiStickerRewardButton("fix");
                        break;
                    default:
                        updateAiStickerRewardButton("create");
                        break;
                }
            }
        }

    } catch (e) {
        indicator.classList.add("error");
        if (type === "memealerts-reward") {
            updateRewardButton("loading");
        }
        if (type === "ai-stickers-reward" && typeof updateAiStickerRewardButton === "function") {
            updateAiStickerRewardButton("loading");
        }
    }
}

function initStatusCards() {
    document.querySelectorAll(".card-status").forEach(card => {

        const type = card.dataset.type;
        const refresh_timer = Number(card.dataset.refreshtimer);

        // первый запуск
        checkStatus(card);

        console.log("Установлен рефреш таймер для карточки", card, "в", refresh_timer, "секунд");

        setInterval(() => checkStatus(card), refresh_timer * 1000);
    });
}

//const installBtn = document.getElementById("installBtn");
//const refreshBtn = document.getElementById("refreshBtn");
//const deleteBtn = document.getElementById("deleteBtn");
//const backdrop = document.getElementById("modalBackdrop");
//const keyInput = document.getElementById("keyInput");
//const activateBtn = document.getElementById("activateBtn");
//const cancelBtn = document.getElementById("cancelBtn");
//
//let onlyRefresh = false;
//
//function openModal(refresh=false) {
//    onlyRefresh = refresh;
//    keyInput.value = "";
//    keyInput.classList.remove("error");
//    backdrop.classList.add("active");
//}
//
//function closeModal() {
//    backdrop.classList.remove("active");
//}
//
//installBtn.onclick = () => openModal(false);
//refreshBtn.onclick = () => openModal(true);
//cancelBtn.onclick = closeModal;
//
//deleteBtn.onclick = async () => {
//    await fetch("/install-ai-overlay", {
//        method: "POST",
//        headers: {"Content-Type": "application/json"},
//        body: JSON.stringify({ delete: true })
//    });
//    installBtn.style.display = "inline-block";
//    refreshBtn.style.display = "none";
//    deleteBtn.style.display = "none";
//};
//
//activateBtn.onclick = async () => {
//    const value = keyInput.value.trim();
//    const regex = /^st-[a-z]{10}$/;
//
//    if (!regex.test(value)) {
//        keyInput.classList.add("error");
//        setTimeout(() => keyInput.classList.remove("error"), 400);
//        return;
//    }
//
//    const response = await fetch("/install-ai-overlay", {
//        method: "POST",
//        headers: {"Content-Type": "application/json"},
//        body: JSON.stringify({
//            key: value,
//            only_refresh: onlyRefresh
//        })
//    });
//
//    if (response.status === 200) {
//        closeModal();
//        installBtn.style.display = "none";
//        refreshBtn.style.display = "inline-block";
//        deleteBtn.style.display = "inline-block";
//    } else {
//        keyInput.classList.add("error");
//        setTimeout(() => keyInput.classList.remove("error"), 400);
//    }
//};


// MEMEALERTS V2 !!!!!!!!

async function memealertsAction(endpoint, method= "POST") {
    const btn = document.getElementById("memealerts-reward-btn");
    if (btn) {
        btn.disabled = true;
    }
    try {
        const response = await fetch(endpoint, {
            method: method
        });
        const data = await response.json();
        showNotification(
            data.title || "Memealerts",
            data.message,
            !response.ok
        );
    } catch (e) {
        showNotification(
            "Ошибка",
            e.message,
            true
        );
    }
    await refreshMemealertsStatuses();
}

function createMemealertsReward() {
    return memealertsAction("/api/user/memealerts/reward", "PUT");
}

function fixMemealertsReward() {
    return memealertsAction("/api/user/memealerts/reward", "PATCH");
}

function deleteMemealertsReward() {
    return memealertsAction("/api/user/memealerts/reward", "DELETE");
}

function disconnectMemealerts() {
    return memealertsAction("/api/user/memealerts", "DELETE");
}

async function refreshMemealertsStatuses() {
    const tokenCard = document.querySelector(
        '.card-status[data-type="memealerts-token"]'
    );

    const rewardCard = document.querySelector(
        '.card-status[data-type="memealerts-reward"]'
    );

    if (tokenCard) {
        await checkStatus(tokenCard);
    }

    if (rewardCard) {
        await checkStatus(rewardCard);
    }
}

function updateRewardButton(action) {
    const btn = document.getElementById("memealerts-reward-btn");
    if (!btn) return;

    btn.disabled = false;
    btn.classList.remove("btn-danger");
    btn.dataset.action = action;

    switch (action) {
        case "loading":
            btn.disabled = true;
            btn.textContent = "Проверка...";
            break;

        case "create":
            btn.textContent = "Создать награду";
            break;

        case "fix":
            btn.textContent = "Исправить награду";
            break;

        case "delete":
            btn.classList.add("btn-danger");
            btn.textContent = "Отключить награду";
            break;

        default:
            btn.disabled = true;
            btn.textContent = "Неизвестное состояние";
            break;
    }
}

async function handleRewardButton() {
    const btn = document.getElementById("memealerts-reward-btn");

    switch (btn.dataset.action) {
        case "create":
            return createMemealertsReward();

        case "fix":
            return fixMemealertsReward();

        case "delete":
            return deleteMemealertsReward();
    }
}