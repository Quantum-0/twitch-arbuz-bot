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

function toggleSetting(name, enabled) {
    fetch('/api/user/update_settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `${name}=${enabled ? 'true' : 'false'}`
    })
    .then(res => res.json().then(data => ({ ok: res.ok, data })))
    .then(({ok, data}) => showNotification(data.title || 'Настройки', data.message, !ok))
    .catch(err => showNotification('Ошибка', err.message, true));
}

function initToggles() {
    document.querySelectorAll('.toggle-switch').forEach(toggle => {
        toggle.addEventListener('click', () => {
            toggle.classList.toggle('active');
            toggleSetting(toggle.dataset.name, toggle.classList.contains('active'));
            if (toggle.dataset.name === 'enable_chat_bot') {
            updateDependentTogglesState();
        }
        });
    });
}

function updateDependentTogglesState() {
    const isEnabled = document.querySelector('.toggle-switch[data-name="enable_chat_bot"]').classList.contains('active');
    const container = document.getElementById('dependent-toggles');
    if (!container) return;
    container.querySelectorAll('.toggle-label').forEach(label => {
        if (isEnabled) {
            label.classList.remove('disabled');
        } else {
            label.classList.add('disabled');
        }
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

function openModal() { document.getElementById('memealert-modal').style.display = 'flex'; }
function closeModal() { document.getElementById('memealert-modal').style.display = 'none'; }
function saveMemealert() {
    const key = document.getElementById('memealert-key').value.trim();
    if (!key) return showNotification('Ошибка', 'Введите ключ', true);
    closeModal();
    toggleMemealerts(true, key, isRefresh);
}

function toggleMemealerts(enable, key='', refresh=false) {
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
    const coinInput = document.getElementById('coin-count');
    if (coinInput) {
        coinInput.addEventListener('input', triggerCoinSave);
    }
});