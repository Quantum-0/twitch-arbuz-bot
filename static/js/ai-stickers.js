let aiStickersCursor = null;
let aiStickersFinished = false;

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
        const response = await fetch(`/api/user/setup-ai-stickers?enable=${enable}`, { method: 'POST' });
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
    if (!formData.get('file').name && !String(formData.get('description') || '').trim()) {
        showNotification('Ошибка', 'Добавьте PNG-файл или описание персонажа.', true);
        return;
    }
    const response = await fetch('/api/user/reference', { method: 'POST', body: formData });
    let data = {};
    try { data = await response.json(); } catch (_) {}
    showNotification(response.ok ? 'Сохранено' : 'Ошибка', response.ok ? 'Персонаж обновлён.' : (data.detail || 'Не удалось сохранить персонажа.'), !response.ok);
}

function renderSticker(item) {
    const div = document.createElement('div');
    div.className = 'ai-sticker';
    div.innerHTML = `<a href="/files/ai-gen-stickers/${item.file_id}"><img src="/files/ai-gen-stickers/${item.file_id}" onerror="this.onerror=null; this.src='/static/images/500.png';"></a><p><i></i></p><p></p>`;
    div.querySelector('i').textContent = item.prompt;
    div.querySelector('p:last-child').textContent = `by ${item.by_chatter}`;
    return div;
}

async function loadMoreStickers() {
    if (aiStickersFinished) return;
    const url = new URL('/api/user/ai-stickers/recent', window.location.origin);
    if (aiStickersCursor) url.searchParams.set('before', aiStickersCursor);
    const response = await fetch(url);
    const data = await response.json();
    const grid = document.getElementById('ai-stickers-grid');
    data.items.forEach(item => grid.appendChild(renderSticker(item)));
    aiStickersCursor = data.next_cursor;
    if (!aiStickersCursor) {
        aiStickersFinished = true;
        document.getElementById('load-more-stickers').style.display = 'none';
        document.getElementById('stickers-end').style.display = 'block';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('input[name="ai_reference_usage_policy"], input[name="ai_sticker_model"]').forEach(input => {
        input.addEventListener('change', () => updateSetting(input.name, input.value));
    });
    document.getElementById('reference-form')?.addEventListener('submit', submitReference);
    aiStickersCursor = document.getElementById('load-more-stickers')?.dataset.cursor || null;
    document.getElementById('load-more-stickers')?.addEventListener('click', loadMoreStickers);
});
