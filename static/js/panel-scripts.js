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

        card.querySelectorAll("[data-param]").forEach(el => {
            el.addEventListener("input", () => updateOverlayLink(card));
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

    /*fetch('/api/user/check-heat-installed', {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
    })
    .then(res => res.json().then(data => ({ ok: res.ok, data })))
    .then((
        {ok, data}) => {
            if (ok) {
                document.querySelectorAll('div.plugin-required').forEach(element => {
                    element.remove();
                })
            };
        }
    );*/
}

function updateDependentTogglesState() {
    const isEnabled = document.querySelector('.toggle-switch[data-name="enable_chat_bot"]').classList.contains('active');
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
    const endpoint = card.dataset.endpoint;
    const type = card.dataset.type;

    // состояние загрузки
    indicator.classList.remove("active", "error");

    try {
        const res = await fetch(endpoint);
        const data = await res.json();

        if (data.result === true) {
            indicator.classList.add("active");

            // 🔥 важная логика для heat
            if (type === "heat") {
                document.querySelectorAll(".plugin-required").forEach(el => {
                    el.style.display = "none";
                });
            }

        } else {
            indicator.classList.add("error");
        }

    } catch (e) {
        indicator.classList.add("error");
    }
}

function initStatusCards() {
    document.querySelectorAll(".overlay-card-status").forEach(card => {

        const type = card.dataset.type;

        // первый запуск
        checkStatus(card);

        // интервалы
        if (type === "heat") {
            setInterval(() => checkStatus(card), 180000); // 3 минуты
        }

        if (type === "sse") {
            setInterval(() => checkStatus(card), 5000); // 5 секунд
        }
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
