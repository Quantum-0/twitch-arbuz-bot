// ── Кнопка управления наградой TTS ──────────────────────────
function updateTtsRewardButton(action) {
    const btn = document.getElementById('tts-reward-btn');
    if (!btn) return;
    btn.disabled = false;
    btn.classList.remove('btn-danger');
    btn.dataset.action = action;
    if (action === 'loading') {
        btn.disabled = true;
        btn.textContent = 'Проверка...';
    } else if (action === 'create' || action === 'fix') {
        btn.textContent = action === 'fix' ? 'Исправить награду' : 'Создать награду TTS';
    } else if (action === 'delete') {
        btn.classList.add('btn-danger');
        btn.textContent = 'Отключить награду TTS';
    }
}

async function ttsRewardAction(enable) {
    const btn = document.getElementById('tts-reward-btn');
    if (btn) btn.disabled = true;
    try {
        const response = await fetch(`/api/user/setup-tts?enable=${enable}`, {method: 'POST'});
        const data = await response.json();
        showNotification(data.title || 'TTS', data.message, !response.ok);
    } catch (e) {
        showNotification('Ошибка', e.message, true);
    }
    const card = document.querySelector('.card-status[data-type="tts-reward"]');
    if (card) await checkStatus(card);
}

function handleTtsRewardButton() {
    const btn = document.getElementById('tts-reward-btn');
    if (!btn) return;
    if (btn.dataset.action === 'delete') return ttsRewardAction(false);
    if (btn.dataset.action === 'create' || btn.dataset.action === 'fix') return ttsRewardAction(true);
}

// ── Обновление кнопки награды через MutationObserver ────────
function initTtsRewardObserver() {
    const card = document.querySelector('.card-status[data-type="tts-reward"]');
    if (!card) return;
    const indicator = card.querySelector('.status-indicator');
    if (!indicator) return;

    const sync = () => {
        if (indicator.classList.contains('active')) {
            updateTtsRewardButton('delete');
        } else if (indicator.classList.contains('error')) {
            updateTtsRewardButton('create');
        } else {
            updateTtsRewardButton('loading');
        }
    };

    sync();
    new MutationObserver(sync).observe(indicator, {attributes: true, attributeFilter: ['class']});
}

// ── Синхронизация значений ползунков ────────────────────────
function initTtsSliders() {
    const pairs = [
        ['tts-max-length', 'tts-max-length-out'],
        ['tts-cd-user', 'tts-cd-user-out'],
        ['tts-cd-channel', 'tts-cd-channel-out'],
    ];
    for (const [sliderId, outId] of pairs) {
        const slider = document.getElementById(sliderId);
        const out = document.getElementById(outId);
        if (slider && out) {
            out.textContent = slider.value;
            slider.addEventListener('input', () => { out.textContent = slider.value; });
        }
    }
}

// ── Основные настройки (форма) ──────────────────────────────
async function submitTtsSettings(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const modelInput = form.querySelector('input[name="tts_model"]:checked');
    const payload = {
        model: modelInput ? modelInput.value : null,
        max_length: parseInt(form.max_length.value, 10),
        cooldown_per_user: parseInt(form.cooldown_per_user.value, 10),
        cooldown_per_channel: parseInt(form.cooldown_per_channel.value, 10),
    };
    try {
        const res = await fetch('/api/user/tts/settings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        showNotification(data.title || 'TTS', data.message, !res.ok);
    } catch (e) {
        showNotification('Ошибка', e.message, true);
    }
}

// ── Тумблеры (enabled, read_username) ───────────────────────
async function toggleTtsSetting(name, value) {
    const payload = {};
    payload[name === 'tts_enabled' ? 'enabled' : 'read_username'] = value;
    try {
        const res = await fetch('/api/user/tts/settings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        showNotification(data.title || 'TTS', data.message, !res.ok);
    } catch (e) {
        showNotification('Ошибка', e.message, true);
    }
}

// ── Матрица разрешений ──────────────────────────────────────
function collectPermissions() {
    const roles = {};
    document.querySelectorAll('#tts-permissions input[type="checkbox"]').forEach(cb => {
        const role = cb.dataset.role;
        const trigger = cb.dataset.trigger;
        if (!roles[role]) roles[role] = {};
        roles[role][trigger] = cb.checked;
    });
    return {roles};
}

async function savePermissions() {
    const payload = collectPermissions();
    try {
        const res = await fetch('/api/user/tts/permissions', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        showNotification(data.title || 'TTS', data.message, !res.ok);
    } catch (e) {
        showNotification('Ошибка', e.message, true);
    }
}

// ── Внешний ключ ────────────────────────────────────────────
function ttsKeyState() {
    const box = document.getElementById('tts-external-key');
    if (!box) return {box: null, key: ''};
    return {box, key: box.dataset.key || ''};
}

function setTtsKeyUI(key) {
    const box = document.getElementById('tts-external-key');
    if (!box) return;
    box.dataset.key = key;
    box.textContent = key || 'не задан';
    if (key) {
        box.classList.remove('tts-key-empty');
    } else {
        box.classList.add('tts-key-empty');
    }
    const deleteBtn = document.getElementById('tts-delete-key');
    const curlBtn = document.getElementById('tts-copy-curl');
    if (deleteBtn) deleteBtn.disabled = !key;
    if (curlBtn) curlBtn.disabled = !key;
}

async function resetExternalKey() {
    try {
        const res = await fetch('/api/user/tts/reset-key', {method: 'POST'});
        const data = await res.json();
        if (res.ok && data.key) {
            setTtsKeyUI(data.key);
            showNotification('TTS', 'Новый ключ сгенерирован', false);
        } else {
            showNotification(data.title || 'TTS', data.message || 'Ошибка', !res.ok);
        }
    } catch (e) {
        showNotification('Ошибка', e.message, true);
    }
}

async function deleteExternalKey() {
    try {
        const res = await fetch('/api/user/tts/delete-key', {method: 'POST'});
        const data = await res.json();
        if (res.ok) {
            setTtsKeyUI('');
            showNotification('TTS', 'Ключ удалён', false);
        } else {
            showNotification(data.title || 'TTS', data.message || 'Ошибка', !res.ok);
        }
    } catch (e) {
        showNotification('Ошибка', e.message, true);
    }
}

function copyTtsKey() {
    const {box, key} = ttsKeyState();
    if (!box || !key) return;
    navigator.clipboard.writeText(key).then(() => {
        box.classList.add('copied');
        const orig = box.textContent;
        box.textContent = 'Скопировано!';
        setTimeout(() => {
            box.textContent = orig;
            box.classList.remove('copied');
        }, 1000);
    }).catch(() => showNotification('Ошибка', 'Не удалось скопировать', true));
}

function copyTtsCurl() {
    const {key} = ttsKeyState();
    if (!key) return;
    const base = window.location.origin;
    const url = `${base}/api/user/tts/${key}`;
    const curl = `curl -X POST '${url}' -H 'Content-Type: application/json' -d '{"input":"привет"}'`;
    navigator.clipboard.writeText(curl).then(() => {
        showNotification('TTS', 'curl-команда скопирована', false);
    }).catch(() => showNotification('Ошибка', 'Не удалось скопировать', true));
}

// ── Инициализация ───────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initTtsRewardObserver();
    initTtsSliders();

    const settingsForm = document.getElementById('tts-settings-form');
    if (settingsForm) settingsForm.addEventListener('submit', submitTtsSettings);

    const savePermsBtn = document.getElementById('tts-save-permissions');
    if (savePermsBtn) savePermsBtn.addEventListener('click', savePermissions);

    const resetKeyBtn = document.getElementById('tts-reset-key');
    if (resetKeyBtn) resetKeyBtn.addEventListener('click', resetExternalKey);

    const deleteKeyBtn = document.getElementById('tts-delete-key');
    if (deleteKeyBtn) deleteKeyBtn.addEventListener('click', deleteExternalKey);

    const copyCurlBtn = document.getElementById('tts-copy-curl');
    if (copyCurlBtn) copyCurlBtn.addEventListener('click', copyTtsCurl);

    const keyBox = document.getElementById('tts-external-key');
    if (keyBox) keyBox.addEventListener('click', copyTtsKey);

    // Тумблеры TTS (не обрабатываются panel-scripts.js, т.к. не в /update_settings)
    document.querySelectorAll('.toggle-switch[data-name^="tts_"]').forEach(toggle => {
        const applyToggle = () => {
            toggle.classList.toggle('active');
            toggle.setAttribute('aria-checked', toggle.classList.contains('active') ? 'true' : 'false');
            toggleTtsSetting(toggle.dataset.name, toggle.classList.contains('active'));
        };
        toggle.addEventListener('click', applyToggle);
        toggle.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                applyToggle();
            }
        });
    });
});
