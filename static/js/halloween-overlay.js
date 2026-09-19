'use strict';

/* ==========================================================================
   Halloween overlay для twitch-bot
   Порт из ~/Downloads/halloween-overlay/overlay/overlay.js
   Изменения:
     - SSE twitch-events канал вместо прямого /events
     - chat-listener.js для чат-сообщений (chat:message events)
     - windX импульс для листиков при !шуш/!фуф/!дуть/!дунуть
     - POST /api/user/eventsub при загрузке + heartbeat
     - пути к ассетам → /static/images/overlays/halloween/
   ========================================================================== */

const ASSETS = '/static/images/overlays/halloween/';

const CONFIG = {
  debug: new URLSearchParams(location.search).get('debug') === '1',

  chat: {
    ignoreUsers: ['nightbot', 'streamelements', 'streamlabs', 'moobot', 'fossabot', 'wizebot'],
    showNick: true,
    showEmotes: true,
    maxChars: 140,
  },

  pumpkin: {
    glowMs: 3500,
    bubbleMinMs: 4000,
    bubbleMsPerChar: 45,
    bubbleMaxMs: 9000,
  },

  spider: {
    chance: parseFloat(new URLSearchParams(location.search).get('spider') || '0.03'),
    eyeColor: 'nick',
  },

  leaves: {
    max: 22,
    spawnEveryMs: [700, 1500],
    size: [34, 80],
    speed: [35, 85],
    swayAmp: [20, 60],
    swayFreq: [0.5, 1.0],
  },

  ghost: {
    holdMs: [7000, 10000],
    slideMs: 1300,
    gapMs: 800,
  },

  raid: {
    maxGhosts: 250,
    width: [120, 230],
    speed: [260, 400],
    amp: [40, 230],
    wavelength: [380, 1000],
    startSpread: 500,
    direction: 'ltr',
  },

  sounds: { follow: ASSETS + 'sound.mp3', sub: ASSETS + 'sound.mp3', resub: ASSETS + 'sound.mp3', raid: null },
  volume: parseFloat(new URLSearchParams(location.search).get('volume') || '0.8'),

  wind: {
    impulse: -120,
    decay: 1.5,
  },
};

const STAGE_W = 1920, STAGE_H = 1080;
const TAU = Math.PI * 2;

const DEFAULT_COLORS = ['#FF0000', '#0000FF', '#008000', '#B22222', '#FF7F50', '#9ACD32', '#FF4500',
                        '#2E8B57', '#DAA520', '#D2691E', '#5F9EA0', '#1E90FF', '#FF69B4', '#8A2BE2', '#00FF7F'];

/* ============================== 1. УТИЛИТЫ ============================= */

const $ = (sel) => document.querySelector(sel);
const rand = (a, b) => a + Math.random() * (b - a);
const randIn = ([a, b]) => rand(a, b);
const lerp = (a, b, t) => a + (b - a) * t;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function hashString(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function fallbackColor(login) { return DEFAULT_COLORS[hashString(login) % DEFAULT_COLORS.length]; }

function loadImage(src) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve({ src, w: img.naturalWidth, h: img.naturalHeight });
    img.onerror = () => { console.warn('Не удалось загрузить', src); resolve({ src, w: 100, h: 100 }); };
    img.src = src;
  });
}

function playSound(kind) {
  const src = CONFIG.sounds[kind];
  if (!src) return;
  const a = new Audio(src);
  a.volume = clamp(CONFIG.volume, 0, 1);
  a.play().catch((e) => console.warn('Звук не воспроизведён:', e.message));
}

function fitStage() {
  const s = Math.min(innerWidth / STAGE_W, innerHeight / STAGE_H);
  const st = $('#stage');
  st.style.transform = `scale(${s})`;
  st.style.left = `${(innerWidth - STAGE_W * s) / 2}px`;
  st.style.top = `${(innerHeight - STAGE_H * s) / 2}px`;
}


/* ================== 2. ИГРОВОЙ ЦИКЛ (листья + призраки рейда) ============ */

const actors = new Set();
let lastFrame = performance.now();
let leafCount = 0;
let nextLeafIn = 0;

let windX = 0;

function frame(now) {
  const dt = Math.min(0.05, (now - lastFrame) / 1000);
  lastFrame = now;

  nextLeafIn -= dt * 1000;
  if (nextLeafIn <= 0 && leafCount < CONFIG.leaves.max) {
    spawnLeaf(false);
    nextLeafIn = randIn(CONFIG.leaves.spawnEveryMs);
  }

  for (const a of actors) {
    if (a.update(dt) === false) {
      a.el.remove();
      if (a.onRemove) a.onRemove();
      actors.delete(a);
    }
  }
  requestAnimationFrame(frame);
}


/* ================================ 3. ЛИСТЬЯ ============================= */

let leafSprites = [];

function spawnLeaf(prefill) {
  const L = CONFIG.leaves;
  const spr = pick(leafSprites);
  const depth = Math.random();
  const size = lerp(L.size[0], L.size[1], depth);
  const k = size / Math.max(spr.w, spr.h);
  const w = spr.w * k, h = spr.h * k;

  const el = document.createElement('img');
  el.className = 'leaf';
  el.src = spr.src;
  el.style.width = `${w}px`;
  el.style.height = `${h}px`;
  el.style.opacity = (0.6 + 0.4 * depth).toFixed(2);
  $('#leaves').appendChild(el);

  const baseX = rand(-20, STAGE_W - 20);
  let y = prefill ? rand(-h, STAGE_H) : -h - rand(0, 60);
  const speed = lerp(L.speed[0], L.speed[1], depth) * rand(0.9, 1.1);
  const amp = randIn(L.swayAmp);
  const freq = randIn(L.swayFreq);
  const phase = rand(0, TAU);
  const tilt = rand(15, 40);
  const rot0 = rand(0, 360);
  const spin = rand(-12, 12);
  let t = 0;

  leafCount++;
  actors.add({
    el,
    onRemove: () => { leafCount--; },
    update(dt) {
      t += dt;
      y += speed * dt;
      baseX += windX * dt;
      const x = baseX + amp * Math.sin(t * freq + phase);
      const rot = rot0 + spin * t + tilt * Math.sin(t * freq + phase + 1.2);
      el.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0) rotate(${rot.toFixed(1)}deg)`;
      return y < STAGE_H + 10;
    },
  });
}


/* ================ 4. ОБЛАЧКА, ТЫКВЫ, ПАУК (реакция на чат) ============== */

const pumpkinEls = [0, 1, 2].map((i) => $(`#pumpkin-${i}`));
const spiderEl = $('#spider');
const spiderBubble = $('#spider-bubble');
let lastPumpkin = -1;

function showBubble(bubbleEl, html, ms) {
  clearTimeout(bubbleEl._timer);
  bubbleEl.innerHTML = html;
  bubbleEl.classList.remove('show', 'out');
  void bubbleEl.offsetWidth;
  bubbleEl.classList.add('show');
  bubbleEl._timer = setTimeout(() => {
    bubbleEl.classList.remove('show');
    bubbleEl.classList.add('out');
  }, ms);
}

function lightUp(el, color, ms) {
  clearTimeout(el._glowTimer);
  el.style.setProperty('--c', color);
  el.style.setProperty('--eye', color);
  el.classList.add('on');
  el._glowTimer = setTimeout(() => el.classList.remove('on'), ms);
}

function renderMessage(text, emotes) {
  if (!CONFIG.chat.showEmotes || !emotes || !emotes.length) {
    const chars = Array.from(text);
    if (chars.length > CONFIG.chat.maxChars) {
      return escapeHtml(chars.slice(0, CONFIG.chat.maxChars).join('')) + '…';
    }
    return escapeHtml(text);
  }

  let result = '';
  let remaining = CONFIG.chat.maxChars;
  let pos = 0;
  const chars = Array.from(text);

  for (const emote of emotes) {
    const emoteText = emote.text || '';
    const idx = chars.indexOf(Array.from(emoteText)[0], pos);
    if (idx === -1 || idx > pos) {
      const segment = chars.slice(pos, idx === -1 ? chars.length : idx).join('');
      const shown = segment.slice(0, remaining);
      if (shown.length < segment.length) {
        result += escapeHtml(shown) + '…';
        return result;
      }
      result += escapeHtml(shown);
      remaining -= shown.length;
      if (remaining <= 0) return result;
    }
    if (idx === -1) break;

    if (remaining < 2) { result += '…'; return result; }
    result += `<img class="emote" alt="" src="https://static-cdn.jtvnw.net/emoticons/v2/${encodeURIComponent(emote.id)}/default/light/2.0">`;
    remaining -= 2;
    pos = idx + Array.from(emoteText).length;
  }

  if (pos < chars.length) {
    const segment = chars.slice(pos).join('');
    const shown = segment.slice(0, remaining);
    if (shown.length < segment.length) {
      result += escapeHtml(shown) + '…';
    } else {
      result += escapeHtml(shown);
    }
  }
  return result;
}

function bubbleHtml(msg) {
  const nick = CONFIG.chat.showNick
    ? `<span class="bubble-nick" style="color:${msg.color}">${escapeHtml(msg.display_name || msg.username || '')}</span><br>` : '';
  return `${nick}<span class="bubble-text">${renderMessage(msg.text, msg.emotes)}</span>`;
}

function bubbleDuration(text) {
  const P = CONFIG.pumpkin;
  return clamp(P.bubbleMinMs + Array.from(text).length * P.bubbleMsPerChar, P.bubbleMinMs, P.bubbleMaxMs);
}

function onChatMessage(msg, forceSpider = false) {
  const ms = bubbleDuration(msg.text);

  if (forceSpider || Math.random() < CONFIG.spider.chance) {
    const eye = CONFIG.spider.eyeColor === 'nick' ? msg.color : `hsl(${Math.floor(rand(0, 360))} 100% 55%)`;
    lightUp(spiderEl, eye, CONFIG.pumpkin.glowMs);
    showBubble(spiderBubble, bubbleHtml(msg), ms);
    return;
  }

  let idx;
  do { idx = Math.floor(Math.random() * pumpkinEls.length); } while (idx === lastPumpkin && pumpkinEls.length > 1);
  lastPumpkin = idx;

  const el = pumpkinEls[idx];
  lightUp(el, msg.color, CONFIG.pumpkin.glowMs);
  showBubble(el.querySelector('.bubble'), bubbleHtml(msg), ms);
}


/* ================= 6. ПРИЗРАК-«ВЫГЛЯДЫВАЛКА» (follow / sub) ============= */

const ghostPeek = $('#ghost-peek');
const ghostQueue = [];
let ghostBusy = false;

function ghostKindText(ev) {
  switch (ev.type) {
    case 'follow': return 'новый фолловер';
    case 'sub':    return ev.gift ? 'получил(а) подарочную подписку' : 'новый подписчик';
    case 'resub':  return ev.months ? `продлил(а) подписку · ${ev.months} мес.` : 'продлил(а) подписку';
    default:       return '';
  }
}

async function showGhost(ev) {
  const nameEl = $('#ghost-name');
  nameEl.textContent = ev.user;
  nameEl.style.fontSize = ev.user.length > 18 ? '46px' : ev.user.length > 11 ? '58px' : '72px';
  $('#ghost-kind').textContent = ghostKindText(ev);

  playSound(ev.type);
  ghostPeek.classList.add('in');
  await sleep(randIn(CONFIG.ghost.holdMs));
  ghostPeek.classList.remove('in');
  await sleep(CONFIG.ghost.slideMs + CONFIG.ghost.gapMs);
}

async function runGhostQueue() {
  ghostBusy = true;
  while (ghostQueue.length) await showGhost(ghostQueue.shift());
  ghostBusy = false;
}

function queueGhost(ev) {
  ghostQueue.push(ev);
  if (!ghostBusy) runGhostQueue();
}


/* ========================== 7. ПРИЗРАКИ РЕЙДА =========================== */

let ghostFlySprite = null;

function spawnRaidGhost() {
  const R = CONFIG.raid;
  const dir = R.direction === 'rtl' ? -1 : 1;

  const w = randIn(R.width);
  const h = w * ghostFlySprite.h / ghostFlySprite.w;
  const speed = randIn(R.speed);
  const amp = randIn(R.amp);
  const waveLen = randIn(R.wavelength);
  const k = TAU / waveLen;
  const phase = rand(0, TAU);
  const baseY = rand(amp + h / 2 + 30, STAGE_H - amp - h / 2 - 30);
  const startOffset = rand(0, R.startSpread);

  let x = dir > 0 ? -w / 2 - startOffset : STAGE_W + w / 2 + startOffset;

  const el = document.createElement('img');
  el.className = 'raid-ghost';
  el.src = ghostFlySprite.src;
  el.style.width = `${w}px`;
  el.style.height = `${h}px`;
  el.style.opacity = rand(0.78, 0.96).toFixed(2);
  $('#raid-layer').appendChild(el);

  actors.add({
    el,
    update(dt) {
      x += dir * speed * dt;
      const arg = k * x + phase;
      const y = baseY + amp * Math.sin(arg);
      const slope = amp * k * Math.cos(arg);
      const rot = clamp(Math.atan(slope) * 180 / Math.PI * 0.45, -30, 30);
      el.style.transform =
        `translate3d(${(x - w / 2).toFixed(1)}px, ${(y - h / 2).toFixed(1)}px, 0) rotate(${rot.toFixed(1)}deg)` +
        (dir < 0 ? ' scaleX(-1)' : '');
      return dir > 0 ? x < STAGE_W + w : x > -w;
    },
  });
}

function launchRaid(count) {
  const n = clamp(Math.floor(count) || 1, 1, CONFIG.raid.maxGhosts);
  if (n < count) console.warn(`Рейд ${count}: показано ${n} (raid.maxGhosts)`);
  playSound('raid');
  for (let i = 0; i < n; i++) spawnRaidGhost();
}


/* ================== 8. СОБЫТИЯ ОТ BACKEND (SSE twitch-events) ========== */

function handleServerEvent(ev) {
  switch (ev.type) {
    case 'follow':
    case 'sub':
    case 'resub':
      queueGhost(ev);
      break;
    case 'raid':
      launchRaid(Number(ev.count) || 1);
      break;
    default:
      console.warn('Неизвестное событие', ev);
  }
}

function connectBackend() {
  if (typeof CHANNEL_ID === 'undefined') {
    console.warn('[halloween] CHANNEL_ID not defined, skipping SSE');
    return;
  }
  const es = new EventSource(`/sse/${CHANNEL_ID}/twitch-events`);
  es.onopen = () => console.log('[halloween] SSE twitch-events подключено');
  es.onmessage = (e) => {
    try { handleServerEvent(JSON.parse(e.data)); } catch (err) { console.error(err); }
  };
  es.onerror = () => console.warn('[halloween] SSE нет соединения, браузер повторит');
}


/* ================== 9. EventSub heartbeat =========================== */

const EVENTSUB_TYPES = ["channel.follow", "channel.subscribe", "channel.subscription.message"];

function subscribeEventSub() {
  if (typeof OVERLAY_SECRET === 'undefined' || !OVERLAY_SECRET || OVERLAY_SECRET === 'None' || OVERLAY_SECRET === 'null') {
    console.log('[halloween] No overlay_secret, skipping EventSub subscribe');
    return;
  }
  if (typeof CHANNEL_ID === 'undefined') return;

  fetch("/api/user/eventsub", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Overlay-Secret": OVERLAY_SECRET,
    },
    body: JSON.stringify({
      channel_id: parseInt(CHANNEL_ID, 10),
      types: EVENTSUB_TYPES,
    }),
  }).catch((e) => console.warn('[halloween] EventSub subscribe failed', e));
}


/* ================== 10. ЧАТ через chat-listener.js ================== */

function onChatMessageEvent(e) {
  const msg = e.detail;
  if (!msg || !msg.text) return;

  if (CONFIG.chat.ignoreUsers.includes(msg.username)) return;

  const color = /^#[0-9a-f]{6}$/i.test(msg.color || '') ? msg.color : fallbackColor(msg.username || '');

  onChatMessage({
    name: msg.display_name || msg.username || '',
    color,
    text: msg.text,
    emotes: msg.emotes || [],
  });
}

const WIND_COMMANDS = ['!шуш', '!фуф', '!дуть', '!дунуть', '!подуть', '!star'];

function checkWindCommand(e) {
  const msg = e.detail;
  if (!msg || !msg.text) return;
  const text = msg.text.trim().toLowerCase();
  if (WIND_COMMANDS.includes(text)) {
    windX = CONFIG.wind.impulse;
  }
}


/* ================== 11. ОТЛАДОЧНАЯ ПАНЕЛЬ И ЗАПУСК ======================== */

const TEST_NAMES = ['PumpkinKing', 'ghost_hunter', 'NightOwl_42', 'Ведьмочка', 'xX_BatLord_Xx', 'CandyCorn'];
const TEST_TEXTS = ['Тыквы светятся, красота!', 'Всем привет из чата 🎃', 'Kappa это лучшая трансляция', 'ну и страшно тут у вас', 'gg wp', 'Кто-нибудь видел паука?'];

function testChat(forceSpider) {
  onChatMessage({ name: pick(TEST_NAMES), color: pick(DEFAULT_COLORS), text: pick(TEST_TEXTS), emotes: [] }, forceSpider);
}

function buildDebugPanel() {
  document.body.classList.add('debug');
  const panel = document.createElement('div');
  panel.id = 'debug-panel';
  const add = (label, fn) => {
    const b = document.createElement('button');
    b.textContent = label;
    b.onclick = fn;
    panel.appendChild(b);
  };
  add('Сообщение', () => testChat(false));
  add('Паук', () => testChat(true));
  add('Ветер!', () => { windX = CONFIG.wind.impulse; });
  add('Фолловер', () => handleServerEvent({ type: 'follow', user: pick(TEST_NAMES) }));
  add('Подписка', () => handleServerEvent({ type: 'sub', user: pick(TEST_NAMES) }));
  add('Ресаб', () => handleServerEvent({ type: 'resub', user: pick(TEST_NAMES), months: 7 }));
  add('Рейд 5', () => handleServerEvent({ type: 'raid', user: 'RaiderX', count: 5 }));
  add('Рейд 30', () => handleServerEvent({ type: 'raid', user: 'RaiderX', count: 30 }));
  document.body.appendChild(panel);
}

async function init() {
  fitStage();
  addEventListener('resize', fitStage);

  [leafSprites, ghostFlySprite] = await Promise.all([
    Promise.all([1, 2, 3, 4, 5, 6].map((i) => loadImage(ASSETS + `leaf${i}.png`))),
    loadImage(ASSETS + 'ghost_fly.png'),
  ]);

  for (let i = 0; i < 14; i++) spawnLeaf(true);
  lastFrame = performance.now();
  requestAnimationFrame(frame);

  if (CONFIG.debug) buildDebugPanel();

  window.addEventListener('chat:message', onChatMessageEvent);
  window.addEventListener('chat:message', checkWindCommand);

  connectBackend();

  subscribeEventSub();
  setInterval(subscribeEventSub, 5 * 60 * 1000);
}

init();
