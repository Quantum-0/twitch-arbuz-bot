let aiStickersCursor = null;
let aiStickersFinished = false;
let aiStickersLoading = false;
let aiStickersMode = 'mine';

function updateAiStickerRewardButton(action) {
    const btn = document.getElementById('ai-stickers-reward-btn');
    if (!btn) return;
    btn.disabled = false;
    btn.classList.remove('btn-danger');
    btn.dataset.action = action;
    if (action === 'loading') {
        btn.disabled = true;
        btn.textContent = 'Проверка...';
    } else if (action === 'create' || action === 'fix') {
        btn.textContent = action === 'fix' ? 'Исправить награду' : 'Создать награду';
    } else if (action === 'delete') {
        btn.classList.add('btn-danger');
        btn.textContent = 'Отключить награду';
    }
}

async function aiStickerRewardAction(enable) {
    const btn = document.getElementById('ai-stickers-reward-btn');
    if (btn) btn.disabled = true;
    try {
        const response = await fetch(`/api/user/setup-ai-stickers?enable=${enable}`, {method: 'POST'});
        const data = await response.json();
        showNotification(data.title || 'ИИ-стикеры', data.message, !response.ok);
    } catch (e) {
        showNotification('Ошибка', e.message, true);
    }
    const card = document.querySelector('.card-status[data-type="ai-stickers-reward"]');
    if (card) await checkStatus(card);
}

function handleAiStickerRewardButton() {
    const btn = document.getElementById('ai-stickers-reward-btn');
    if (!btn) return;
    if (btn.dataset.action === 'delete') return aiStickerRewardAction(false);
    if (btn.dataset.action === 'create' || btn.dataset.action === 'fix') return aiStickerRewardAction(true);
}

async function submitReference(event) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const file = formData.get('file');
    const hasFile = file && file.name;
    const hasDescription = !!String(formData.get('description') || '').trim();

    if (!hasFile && !hasDescription) {
        showNotification('Ошибка', 'Добавьте PNG-файл или описание персонажа.', true);
        return;
    }
    if (hasFile && file.size > 10_000_000) {
        showNotification('Ошибка', 'Файл слишком большой. Максимальный размер — 10 МБ.', true);
        return;
    }

    try {
        const response = await fetch('/api/user/reference', {method: 'POST', body: formData});
        let data = null;
        try {
            data = await response.json();
        } catch (_) {
        }
        let detail = data && (data.detail || data.message);
        if (!detail) {
            if (response.status === 413) detail = 'Файл слишком большой. Максимальный размер — 10 МБ.';
            else if (response.status === 415) detail = 'Только PNG-изображения.';
            else if (response.status === 400) detail = 'Добавьте PNG-файл или описание персонажа.';
            else if (response.status >= 500) detail = 'Серверная ошибка. Попробуйте позже.';
            else detail = 'Не удалось сохранить персонажа.';
        }
        showNotification(response.ok ? 'Сохранено' : 'Ошибка', response.ok ? 'Персонаж обновлён.' : detail, !response.ok);
    } catch (e) {
        showNotification('Ошибка', e.message || 'Сетевая ошибка. Попробуйте ещё раз.', true);
    }
}

function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function renderSticker(item) {
    const div = document.createElement('div');
    div.className = 'ai-sticker';
    div.innerHTML = `<a href="/files/ai-gen-stickers/${item.file_id}" class="ai-sticker-img-wrap"><img src="/files/ai-gen-stickers/${item.file_id}" loading="lazy" onerror="this.onerror=null; this.src='/static/images/500.png';"></a><p class="ai-sticker-prompt"><i></i></p><p class="ai-sticker-meta"></p>`;
    div.querySelector('i').textContent = item.prompt;

    const meta = div.querySelector('.ai-sticker-meta');
    const parts = [];
    if (aiStickersMode !== 'from_me' && item.by_chatter) {
        parts.push(`by <a href="https://twitch.tv/${encodeURIComponent(item.by_chatter)}" target="_blank" rel="noopener">${escapeHtml(item.by_chatter)}</a>`);
    }
    if (aiStickersMode !== 'mine' && item.channel_login) {
        parts.push(`на канале <a href="/profile/${encodeURIComponent(item.channel_login)}">${escapeHtml(item.channel_login)}</a>`);
    }
    meta.innerHTML = parts.join(' ') || '\u00A0';
    return div;
}

async function loadMoreStickers() {
    if (aiStickersFinished || aiStickersLoading) return;
    aiStickersLoading = true;
    const btn = document.getElementById('load-more-stickers');
    if (btn) btn.disabled = true;
    try {
        const url = new URL('/api/user/ai-stickers/recent', window.location.origin);
        url.searchParams.set('mode', aiStickersMode);
        if (aiStickersCursor) url.searchParams.set('before', aiStickersCursor);
        const response = await fetch(url);
        if (!response.ok) {
            showNotification('Ошибка', 'Не удалось загрузить стикеры', true);
            return;
        }
        const data = await response.json();
        if (!data.items) return;
        const grid = document.getElementById('ai-stickers-grid');
        data.items.forEach(item => grid.appendChild(renderSticker(item)));
        aiStickersCursor = data.next_cursor;
        if (!aiStickersCursor) {
            aiStickersFinished = true;
            if (btn) btn.style.display = 'none';
            document.getElementById('stickers-end').style.display = 'block';
        }
    } catch (e) {
        showNotification('Ошибка', e.message || 'Сетевая ошибка при загрузке стикеров.', true);
    } finally {
        aiStickersLoading = false;
        if (btn) btn.disabled = false;
    }
}

function resetStickersView() {
    const grid = document.getElementById('ai-stickers-grid');
    if (grid) grid.innerHTML = '';
    aiStickersCursor = document.getElementById('load-more-stickers')?.dataset.cursor || null;
    aiStickersFinished = false;
    const btn = document.getElementById('load-more-stickers');
    const end = document.getElementById('stickers-end');
    if (end) end.style.display = 'none';
    if (btn) {
        btn.style.display = aiStickersCursor ? '' : 'none';
        btn.disabled = false;
    }
    if (!aiStickersCursor) {
        aiStickersFinished = true;
        if (end) end.style.display = 'block';
    }
}

async function switchStickersTab(mode) {
    if (mode === aiStickersMode) return;
    aiStickersMode = mode;
    document.querySelectorAll('.stickers-tabs .tab').forEach(t => {
        t.classList.toggle('active', t.dataset.mode === mode);
    });
    resetStickersView();
    if (!aiStickersFinished) await loadMoreStickers();
}

function updateBalanceIndicator() {
    const card = document.getElementById('balance-card');
    const indicator = document.getElementById('balance-indicator');
    if (!card || !indicator) return;
    const balance = parseFloat(card.dataset.balance);
    if (isNaN(balance)) return;
    indicator.classList.remove('active', 'error', 'warn');
    if (balance < 10) indicator.classList.add('error');
    else if (balance < 75) indicator.classList.add('warn');
    else indicator.classList.add('active');
}

function initAiStickerToggles() {
    document.querySelectorAll('.toggle-switch[role="switch"]').forEach(toggle => {
        toggle.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggle.click();
            }
        });
    });
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('input[name="ai_reference_usage_policy"], input[name="ai_sticker_model"]').forEach(input => {
        input.addEventListener('change', () => updateSetting(input.name, input.value));
    });
    document.getElementById('reference-form')?.addEventListener('submit', submitReference);
    aiStickersCursor = document.getElementById('load-more-stickers')?.dataset.cursor || null;
    if (!aiStickersCursor) {
        aiStickersFinished = true;
        document.getElementById('stickers-end').style.display = 'block';
    }
    document.getElementById('load-more-stickers')?.addEventListener('click', loadMoreStickers);
    document.querySelectorAll('.stickers-tabs .tab').forEach(t => {
        t.addEventListener('click', () => switchStickersTab(t.dataset.mode));
    });
    initAiStickerToggles();
    updateBalanceIndicator();
});
