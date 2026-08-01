function profileNotify(title, message, isError = false) {
    if (typeof showNotification === 'function') {
        showNotification(title, message, isError);
    } else {
        console.error(`${title}: ${message}`);
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
    if (mode !== 'from_me' && item.by_chatter) {
        parts.push(`by <a href="https://twitch.tv/${encodeURIComponent(item.by_chatter)}" target="_blank" rel="noopener">${escapeHtml(item.by_chatter)}</a>`);
    }
    if (mode !== 'mine' && item.channel_login) {
        parts.push(`на канале <a href="/profile/${encodeURIComponent(item.channel_login)}">${escapeHtml(item.channel_login)}</a>`);
    }
    meta.innerHTML = parts.join(' ') || '\u00A0';
    return div;
}

const tabsContainer = document.querySelector('.stickers-tabs[data-tabs]');
const profileLogin = tabsContainer?.dataset.profile || '';

let cursor = null;
let finished = false;
let loading = false;
let mode = 'mine';

async function loadMore() {
    if (finished || loading) return;
    loading = true;
    const btn = document.getElementById('load-more-stickers');
    if (btn) btn.disabled = true;
    try {
        const url = new URL(`/api/profile/${encodeURIComponent(profileLogin)}/ai-stickers`, window.location.origin);
        url.searchParams.set('mode', mode);
        if (cursor) url.searchParams.set('before', cursor);
        const response = await fetch(url);
        if (!response.ok) {
            profileNotify('Ошибка', 'Не удалось загрузить стикеры', true);
            return;
        }
        const data = await response.json();
        if (!data.items) return;
        const grid = document.getElementById('ai-stickers-grid');
        data.items.forEach(item => grid.appendChild(renderSticker(item)));
        cursor = data.next_cursor;
        if (!cursor) {
            finished = true;
            if (btn) btn.style.display = 'none';
            document.getElementById('stickers-end').style.display = 'block';
        }
    } catch (e) {
        profileNotify('Ошибка', e.message || 'Сетевая ошибка при загрузке стикеров.', true);
    } finally {
        loading = false;
        if (btn) btn.disabled = false;
    }
}

function resetView() {
    const grid = document.getElementById('ai-stickers-grid');
    if (grid) grid.innerHTML = '';
    cursor = null;
    finished = false;
    const btn = document.getElementById('load-more-stickers');
    const end = document.getElementById('stickers-end');
    if (end) end.style.display = 'none';
    if (btn) {
        btn.style.display = '';
        btn.disabled = false;
    }
}

async function switchTab(newMode) {
    if (newMode === mode) return;
    mode = newMode;
    document.querySelectorAll('.stickers-tabs .tab').forEach(t => {
        t.classList.toggle('active', t.dataset.mode === newMode);
    });
    resetView();
    await loadMore();
}

document.addEventListener('DOMContentLoaded', () => {
    if (!tabsContainer) return;
    document.querySelectorAll('.stickers-tabs .tab').forEach(t => {
        t.addEventListener('click', () => switchTab(t.dataset.mode));
    });
    document.getElementById('load-more-stickers')?.addEventListener('click', loadMore);
    loadMore();
});
