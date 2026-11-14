/* Живая карта потоков twitch-bot (/flowviz).
 *
 * Движок порожден PoC (poc/architecture-viz.html): canvas-частицы («капли» и
 * «искры») летят по кривым Безье между узлами сервисов. Отличие от PoC —
 * скорости потоков берутся из реальных метрик: поллинг /api/admin/flowviz
 * раз в 1 с (LiveRegistry на бэке сглаживает окном ~2 с).
 *
 * Маппинг «поток → метрика» — в таблице FLOWS (rate(d) считает скорость из
 * ответа API). Подписи у рёбер показывают настоящие числа; плотность частиц =
 * rate / upp с капом maxSpawn, чтобы при всплесках канвас не захлебнулся.
 *
 * Честные ограничения (v1): users→Twitch и users→Heat показывают наблюдаемый
 * ботом трафик (message_incoming / heat_proxy_messages), а не весь Twitch;
 * twitch→бот — все доставки EventSub (чат идёт через EventSub channel.chat.message,
 * IRC-путь бота отключён; message_incoming считался бы дважды на этом ребре);
 * twibot-tg→Telegram — скорость наших MQTT-команд (Bot API зовёт соседний
 * сервис, его собственную статистику он не экспортирует).
 */
(() => {
  'use strict';

  const API_URL = '/api/admin/flowviz';
  const POLL_MS = 1000;
  const MAX_PARTICLES = 1500;
  /* сглаживание реальной скорости (доля нового значения за поллинг) */
  const EMA = 0.45;

  const $ = s => document.querySelector(s);
  const clamp = (v, a, b) => v < a ? a : v > b ? b : v;
  const rand = (a, b) => a + Math.random() * (b - a);
  const fmt = n => {
    if (n >= 10000) return Math.round(n / 1000) + 'k';
    return n >= 9.95 ? String(Math.round(n)) : String(Math.round(n * 10) / 10);
  };

  /* ---------- модель (та же топология, что в PoC) ---------- */
  const C = {
    users: '#81c784', twitch: '#a970ff', heat: '#ffb74d', bot: '#66bb6a',
    pg: '#7fa8d9', redis: '#ff6b6b', mqtt: '#4db6ac', tgsrv: '#ffd54f', tg: '#4fc3f7',
  };

  const NODES = {
    users:  { px: .055, py: .50, r: 34, color: C.users,  icon: '👥', name: 'Пользователи', sub: 'зрители · стримеры' },
    twitch: { px: .27,  py: .16, r: 34, color: C.twitch, icon: '🎮', name: 'Twitch',       sub: 'каналы: …' },
    heat:   { px: .25,  py: .84, r: 32, color: C.heat,   icon: '🔥', name: 'Heat',         sub: 'heat-api.j38.net' },
    bot:    { px: .50,  py: .50, r: 50, color: C.bot,    icon: '🤖', name: 'twitch-bot',   sub: 'FastAPI · asyncio' },
    pg:     { px: .735, py: .17, r: 34, color: C.pg,     icon: '🐘', name: 'PostgreSQL',   sub: 'twitch_bot' },
    redis:  { px: .795, py: .50, r: 34, color: C.redis,  icon: '⚡', name: 'Redis',        sub: 'кэш · статистика' },
    mqtt:   { px: .735, py: .83, r: 34, color: C.mqtt,   icon: '📡', name: 'MQTT',         sub: 'EMQX · twibot/*' },
    tgsrv:  { px: .925, py: .645, r: 34, color: C.tgsrv, icon: '📨', name: 'twibot-tg',   sub: 'aiogram · vds-ams' },
    tg:     { px: .925, py: .17, r: 34, color: C.tg,     icon: '✈️', name: 'Telegram',    sub: 'Bot API' },
  };

  /* rate(d) — скорость потока из ответа API (единиц/с). upp — сколько единиц
   * метрики «несёт» одна частица; maxSpawn — кап частиц/с. */
  const sumSub = (d, type, subs) => {
    const t = d.rates[type];
    if (!t) return 0;
    if (!subs) return Object.values(t).reduce((a, b) => a + b, 0);
    return subs.reduce((a, s) => a + (t[s] || 0), 0);
  };
  const msgIn = d => sumSub(d, 'message_incoming');
  const eventsub = d => sumSub(d, 'eventsub_events');
  const twitchApi = d => sumSub(d, 'twitch_api_calls');
  const httpReq = d => sumSub(d, 'http_requests');
  const sseEvents = d => sumSub(d, 'sse_events');
  const heat = d => sumSub(d, 'heat_proxy_messages');
  const dbQ = d => sumSub(d, 'db_queries');
  const redisOps = d => sumSub(d, 'redis_ops');
  const mqttPub = d => sumSub(d, 'mqtt_publish');
  const mqttPubTg = d => sumSub(d, 'mqtt_publish', ['telegram']);
  const mqttIn = d => sumSub(d, 'mqtt_messages');
  const mqttInTg = d => sumSub(d, 'mqtt_messages', ['telegram']);

  const FLOWS = [
    { id: 'chat-out', a: 'users',  b: 'twitch', label: 'чат',                unit: 'сообщ', rate: msgIn,      upp: 1,  maxSpawn: 26, speed: 205, mix: .80, bend:  30, color: C.twitch },
    { id: 'chat-in',  a: 'twitch', b: 'bot',    label: 'EventSub',            unit: 'соб',   rate: eventsub,    upp: 1, maxSpawn: 28, speed: 235, mix: .70, bend:  26, color: C.twitch },
    { id: 'helix',    a: 'bot',    b: 'twitch', label: 'Helix API',          unit: 'выз',   rate: twitchApi,  upp: 1,  maxSpawn: 8,  speed: 245, mix: .20, bend:  26, color: C.twitch, faint: .75 },
    { id: 'http-in',  a: 'users',  b: 'bot',    label: 'HTTP · панель',      unit: 'запр',  rate: httpReq,    upp: 2,  maxSpawn: 20, speed: 215, mix: .35, bend:  28, color: '#1e88e5' },
    { id: 'sse-out',  a: 'bot',    b: 'users',  label: 'SSE · оверлеи',      unit: 'соб',   rate: sseEvents,  upp: 8,  maxSpawn: 20, speed: 205, mix: .30, bend:  28, color: '#26c6da' },
    { id: 'heat-in',  a: 'users',  b: 'heat',   label: 'клики',              unit: 'соб',   rate: heat,       upp: 3,  maxSpawn: 18, speed: 230, mix: .75, bend:  28, color: C.heat },
    { id: 'heat-up',  a: 'heat',   b: 'bot',    label: 'upstream WS',        unit: 'соб',   rate: heat,       upp: 3,  maxSpawn: 18, speed: 240, mix: .65, bend:  24, color: C.heat },
    { id: 'pg-q',     a: 'bot',    b: 'pg',     label: 'запросы',            unit: 'запр',  rate: dbQ,        upp: 5,  maxSpawn: 18, speed: 220, mix: .20, bend:  26, color: C.pg },
    { id: 'pg-a',     a: 'pg',     b: 'bot',    label: 'ответы',             unit: 'запр',  rate: dbQ,        upp: 5,  maxSpawn: 18, speed: 220, mix: .20, bend:  26, color: C.pg },
    { id: 'redis-q',  a: 'bot',    b: 'redis',  label: 'кэш · статистика',   unit: 'опер',  rate: redisOps,   upp: 12, maxSpawn: 22, speed: 265, mix: .70, bend:  26, color: C.redis },
    { id: 'redis-a',  a: 'redis',  b: 'bot',    label: 'результаты',         unit: 'опер',  rate: redisOps,   upp: 12, maxSpawn: 20, speed: 265, mix: .65, bend:  26, color: C.redis },
    { id: 'mqtt-pub', a: 'bot',    b: 'mqtt',   label: 'publish',            unit: 'публ',  rate: mqttPub,    upp: 1,  maxSpawn: 8,  speed: 215, mix: .25, bend:  26, color: C.mqtt },
    { id: 'mqtt-sub', a: 'mqtt',   b: 'bot',    label: 'подписки',           unit: 'соб',   rate: mqttIn,     upp: 1,  maxSpawn: 8,  speed: 215, mix: .25, bend:  26, color: C.mqtt },
    { id: 'tg-cmd',   a: 'mqtt',   b: 'tgsrv',  label: 'telegram/send_*',    unit: 'ком',   rate: mqttPubTg,  upp: 1,  maxSpawn: 8,  speed: 205, mix: .15, bend:  22, color: C.tgsrv },
    { id: 'tg-res',   a: 'tgsrv',  b: 'mqtt',   label: 'result · connected', unit: 'соб',   rate: mqttInTg,   upp: 1,  maxSpawn: 8,  speed: 205, mix: .15, bend:  22, color: C.tgsrv },
    { id: 'tg-api',   a: 'tgsrv',  b: 'tg',     label: 'Bot API',            unit: 'сообщ', rate: mqttPubTg,  upp: 1,  maxSpawn: 8,  speed: 215, mix: .15, bend: -18, color: C.tg },
  ];
  for (const f of FLOWS) {
    f.acc = 0; f.disp = 0; f.target = 0; f.dimE = 1; f.hotE = 0;
    f.geo = null; f.sprite = null;
  }

  /* ---------- состояние ---------- */
  const stage = $('#flowviz-stage'), fxCanvas = $('#fv-fx'), edgeCanvas = $('#fv-edges');
  const fxCtx = fxCanvas.getContext('2d'), egCtx = edgeCanvas.getContext('2d');
  let W = 0, H = 0, DPR = 1;
  let paused = false, speedMul = 1;
  let hovered = null;
  const particles = [];
  let lastData = null;
  for (const n of Object.values(NODES)) {
    n.x = 0; n.y = 0; n.flash = 0; n.hitAcc = 0; n.rate = 0; n.rateAcc = 0;
    n.card = null; n.rateEl = null; n.subEl = null;
  }

  /* ---------- спрайты и геометрия (как в PoC) ---------- */
  function hexA(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  function makeSprite(color) {
    const s = document.createElement('canvas'); s.width = s.height = 96;
    const g = s.getContext('2d');
    const grd = g.createRadialGradient(48, 48, 0, 48, 48, 48);
    grd.addColorStop(0, 'rgba(255,255,255,.95)');
    grd.addColorStop(.22, color);
    grd.addColorStop(1, hexA(color, 0));
    g.fillStyle = grd; g.fillRect(0, 0, 96, 96);
    return s;
  }
  const gridPat = (() => {
    const c = document.createElement('canvas'); c.width = c.height = 36;
    const g = c.getContext('2d');
    g.fillStyle = 'rgba(255,255,255,.05)';
    g.beginPath(); g.arc(18, 18, 1.1, 0, Math.PI * 2); g.fill();
    return egCtx.createPattern(c, 'repeat');
  })();

  function bez(g, t) {
    const u = 1 - t;
    return {
      x: u * u * g.ax + 2 * u * t * g.cx + t * t * g.bx,
      y: u * u * g.ay + 2 * u * t * g.cy + t * t * g.by,
    };
  }
  function bezTan(g, t) {
    const x = 2 * (1 - t) * (g.cx - g.ax) + 2 * t * (g.bx - g.cx);
    const y = 2 * (1 - t) * (g.cy - g.ay) + 2 * t * (g.by - g.cy);
    const l = Math.hypot(x, y) || 1;
    return { x: x / l, y: y / l };
  }

  function layout() {
    W = stage.clientWidth; H = stage.clientHeight;
    DPR = Math.min(2, window.devicePixelRatio || 1);
    for (const cv of [fxCanvas, edgeCanvas]) {
      cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR);
      cv.style.width = W + 'px'; cv.style.height = H + 'px';
    }
    fxCtx.setTransform(DPR, 0, 0, DPR, 0, 0);
    egCtx.setTransform(DPR, 0, 0, DPR, 0, 0);
    fxCtx.fillStyle = '#0d0d0d'; fxCtx.fillRect(0, 0, W, H);

    for (const n of Object.values(NODES)) {
      n.x = n.px * W; n.y = n.py * H;
      if (n.card) {
        n.card.style.left = n.x + 'px';
        n.card.style.top = n.y + 'px';
      }
    }
    for (const f of FLOWS) {
      const A = NODES[f.a], B = NODES[f.b];
      const dx = B.x - A.x, dy = B.y - A.y, len = Math.hypot(dx, dy) || 1;
      const ux = dx / len, uy = dy / len, nx = -uy, ny = ux;
      const g = {
        ax: A.x + ux * (A.r + 8), ay: A.y + uy * (A.r + 8),
        bx: B.x - ux * (B.r + 10), by: B.y - uy * (B.r + 10),
        nx, ny, len,
      };
      g.cx = (g.ax + g.bx) / 2 + nx * f.bend;
      g.cy = (g.ay + g.by) / 2 + ny * f.bend;
      let pl = 0, prev = bez(g, 0);
      for (let i = 1; i <= 16; i++) {
        const p = bez(g, i / 16); pl += Math.hypot(p.x - prev.x, p.y - prev.y); prev = p;
      }
      g.len = pl;
      const mid = bez(g, .52);
      const off = f.bend / 2 + 24;
      g.lx = mid.x + nx * off; g.ly = mid.y + ny * off;
      f.geo = g;
      if (!f.sprite) f.sprite = makeSprite(f.color);
    }
    drawEdges();
  }

  /* ---------- частицы ---------- */
  function spawn(f) {
    if (particles.length >= MAX_PARTICLES) return;
    const spark = Math.random() < f.mix;
    particles.push({
      f, t: 0,
      dur: f.geo.len / f.speed * rand(.9, 1.15),
      size: spark ? rand(7, 13) : rand(17, 27),
      spark,
      wobA: rand(1.5, 5), wobF: rand(1, 2.2), wobP: rand(0, Math.PI * 2),
    });
  }

  function drawParticles(sdt) {
    fxCtx.globalCompositeOperation = 'source-over';
    fxCtx.fillStyle = 'rgba(13,13,13,.26)';
    fxCtx.fillRect(0, 0, W, H);
    fxCtx.globalCompositeOperation = 'lighter';
    for (let i = particles.length - 1; i >= 0; i--) {
      const p = particles[i];
      p.t += sdt / p.dur;
      if (p.t >= 1) {
        const n = NODES[p.f.b];
        n.hitAcc++; n.flash = Math.min(2.5, n.flash + (p.spark ? .16 : .3));
        particles.splice(i, 1); continue;
      }
      const g = p.f.geo;
      const wob = Math.sin(p.t * p.wobF * Math.PI * 2 + p.wobP) * p.wobA * Math.sin(Math.PI * p.t);
      const pos = bez(g, p.t);
      const x = pos.x + g.nx * wob, y = pos.y + g.ny * wob;
      const tan = bezTan(g, p.t);
      const dim = p.f.dimE;
      if (dim < .35) continue;
      const spr = p.f.sprite, s = p.size;
      for (let k = 1; k <= 2; k++) {
        const tt = p.t - .038 * k;
        if (tt <= 0) break;
        const tp = bez(g, tt), ts = s * (1 - .27 * k);
        fxCtx.globalAlpha = (k === 1 ? .24 : .10) * dim;
        fxCtx.drawImage(spr, tp.x - ts / 2, tp.y - ts / 2, ts, ts);
      }
      fxCtx.globalAlpha = .95 * dim;
      if (p.spark) {
        fxCtx.drawImage(spr, x - s / 2, y - s / 2, s, s);
      } else {
        fxCtx.save();
        fxCtx.translate(x, y);
        fxCtx.rotate(Math.atan2(tan.y, tan.x));
        fxCtx.drawImage(spr, -s * .95, -s * .48, s * 1.9, s * .96);
        fxCtx.restore();
      }
    }
    fxCtx.globalAlpha = 1;
    fxCtx.globalCompositeOperation = 'source-over';
  }

  /* ---------- рёбра/подписи/гало ---------- */
  function drawEdges() {
    egCtx.clearRect(0, 0, W, H);
    egCtx.fillStyle = gridPat; egCtx.fillRect(0, 0, W, H);

    const vg = egCtx.createRadialGradient(W / 2, H * .45, Math.min(W, H) * .3, W / 2, H * .45, Math.max(W, H) * .75);
    vg.addColorStop(0, 'rgba(13,13,13,0)');
    vg.addColorStop(1, 'rgba(5,5,5,.55)');
    egCtx.fillStyle = vg; egCtx.fillRect(0, 0, W, H);

    for (const f of FLOWS) {
      const g = f.geo, faint = f.faint || 1;
      const a = f.dimE * (1 + .9 * f.hotE);
      egCtx.beginPath();
      egCtx.moveTo(g.ax, g.ay);
      egCtx.quadraticCurveTo(g.cx, g.cy, g.bx, g.by);
      egCtx.strokeStyle = hexA(f.color, .055 * faint * a);
      egCtx.lineWidth = 7; egCtx.stroke();
      egCtx.strokeStyle = hexA(f.color, (.20 + .25 * f.hotE) * faint * Math.min(a, 1.6));
      egCtx.lineWidth = 1.25; egCtx.stroke();

      const t = .93, p = bez(g, t), d = bezTan(g, t), s2 = 5;
      egCtx.strokeStyle = hexA(f.color, .5 * faint * a);
      egCtx.lineWidth = 1.5;
      egCtx.beginPath();
      egCtx.moveTo(p.x - d.x * s2 + d.y * s2 * .8, p.y - d.y * s2 - d.x * s2 * .8);
      egCtx.lineTo(p.x, p.y);
      egCtx.lineTo(p.x - d.x * s2 - d.y * s2 * .8, p.y - d.y * s2 + d.x * s2 * .8);
      egCtx.stroke();
    }

    egCtx.globalCompositeOperation = 'lighter';
    for (const n of Object.values(NODES)) {
      const R = n.r * 1.8 + n.flash * 12;
      const grd = egCtx.createRadialGradient(n.x, n.y, n.r * .4, n.x, n.y, R);
      const al = .10 + Math.min(.22, n.flash * .10);
      grd.addColorStop(0, hexA(n.color, al));
      grd.addColorStop(1, hexA(n.color, 0));
      egCtx.fillStyle = grd;
      egCtx.beginPath(); egCtx.arc(n.x, n.y, R, 0, Math.PI * 2); egCtx.fill();
      egCtx.strokeStyle = hexA(n.color, .25);
      egCtx.lineWidth = 1;
      egCtx.beginPath(); egCtx.arc(n.x, n.y, n.r + 6, 0, Math.PI * 2); egCtx.stroke();
    }
    egCtx.globalCompositeOperation = 'source-over';

    egCtx.font = '10.5px ui-monospace, Menlo, Consolas, monospace';
    egCtx.textBaseline = 'middle';
    for (const f of FLOWS) {
      if (f.dimE < .35) continue;
      const txt = `${f.label} · ${fmt(f.disp)} ${f.unit}/с`;
      const tw = egCtx.measureText(txt).width;
      const g = f.geo;
      const x = g.lx, y = g.ly;
      egCtx.globalAlpha = clamp(f.dimE * (0.75 + .4 * f.hotE), 0, 1);
      egCtx.shadowColor = 'rgba(0,0,0,.7)';
      egCtx.shadowBlur = 6;
      egCtx.fillStyle = 'rgba(10,10,10,.88)';
      egCtx.strokeStyle = hexA(f.color, .30);
      egCtx.lineWidth = 1;
      egCtx.beginPath(); egCtx.roundRect(x - tw / 2 - 7, y - 9, tw + 14, 18, 9); egCtx.fill(); egCtx.stroke();
      egCtx.shadowBlur = 0;
      egCtx.fillStyle = hexA(f.color, .95);
      egCtx.fillText(txt, x - tw / 2, y + .5);
    }
    egCtx.globalAlpha = 1;
  }

  /* ---------- поллинг реальных метрик ---------- */
  function setConnState(cls, text) {
    const el = $('#connState');
    el.className = 'conn' + (cls ? ' ' + cls : '');
    el.querySelector('span').textContent = text;
  }

  let polling = false;
  async function poll() {
    if (polling) return; // не накладываем запросы: зависший ответ не копится
    polling = true;
    try {
      const resp = await fetch(API_URL, {
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout ? AbortSignal.timeout(3000) : undefined,
      });
      if (resp.status === 401 || resp.status === 403) {
        setConnState('deny', 'нет доступа (staff only)');
        decayTargets();
        return;
      }
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const d = await resp.json();
      lastData = d;
      setConnState('ok', 'живые данные');
      for (const f of FLOWS) f.target = f.rate(d);
      updateHeader(d);
    } catch {
      setConnState('err', 'нет связи с API');
      decayTargets();
    } finally {
      polling = false;
    }
  }

  function decayTargets() {
    for (const f of FLOWS) f.target = 0;
  }

  function updateHeader(d) {
    $('#m-msg').textContent = fmt(msgIn(d));
    $('#m-ops').textContent = fmt(httpReq(d) + dbQ(d) + redisOps(d));
    $('#m-sse').textContent = d.sse && d.sse.total != null ? d.sse.total : '—';
    $('#m-tg').textContent = fmt(mqttPubTg(d));
    const tw = NODES.twitch;
    if (tw.subEl) tw.subEl.textContent = 'каналы: ' + (d.active_channels != null ? d.active_channels : '…');
  }

  setInterval(poll, POLL_MS);
  poll();

  /* ---------- главный цикл ---------- */
  function updateFlows(dt) {
    for (const f of FLOWS) {
      f.disp += (f.target - f.disp) * EMA;
      /* спавн в реальном времени (не зависит от слайдера скорости анимации) */
      const spawnRate = clamp(f.disp / f.upp, 0, f.maxSpawn);
      f.acc += spawnRate * dt;
      let n = Math.floor(f.acc);
      if (n > 40) n = 40;
      f.acc -= n;
      while (n-- > 0) spawn(f);
      const related = hovered && (f.a === hovered || f.b === hovered);
      const dimTarget = hovered && !related ? .12 : 1;
      f.dimE += (dimTarget - f.dimE) * Math.min(1, dt * 8);
      const hotTarget = related ? 1 : 0;
      f.hotE += (hotTarget - f.hotE) * Math.min(1, dt * 8);
    }
  }

  let domAcc = 0;
  function updateDom(dt) {
    for (const n of Object.values(NODES)) {
      n.flash *= Math.exp(-3 * dt);
      n.rateAcc += dt;
      if (n.rateAcc >= .5) {
        n.rate = n.rate + ((n.hitAcc / n.rateAcc) - n.rate) * .4;
        n.hitAcc = 0; n.rateAcc = 0;
      }
    }
    domAcc += dt;
    if (domAcc < .15) return;
    domAcc = 0;
    for (const n of Object.values(NODES)) {
      n.rateEl.textContent = n.rate >= .05 ? `~${fmt(n.rate)}/с` : 'тихо';
    }
    $('#m-part').textContent = particles.length;
  }

  let last = performance.now();
  function frame(now) {
    requestAnimationFrame(frame);
    const dt = Math.min(.05, (now - last) / 1000);
    last = now;
    if (paused) return;
    updateFlows(dt);
    drawParticles(dt * speedMul);
    drawEdges();
    updateDom(dt);
  }

  /* ---------- DOM: карточки узлов, контролы ---------- */
  function buildCards() {
    const wrap = $('#flowviz-cards');
    for (const [id, n] of Object.entries(NODES)) {
      const el = document.createElement('div');
      el.className = 'card'; el.dataset.id = id;
      el.style.setProperty('--c', n.color);
      el.style.setProperty('--r', n.r + 'px');
      el.innerHTML = `
        <div class="ball" style="width:${n.r * 2}px;height:${n.r * 2}px"><span>${n.icon}</span></div>
        <div class="name">${n.name}</div>
        <div class="sub">${n.sub}</div>
        <div class="rate">—</div>`;
      wrap.appendChild(el);
      n.card = el;
      n.rateEl = el.querySelector('.rate');
      n.subEl = el.querySelector('.sub');
      el.addEventListener('mouseenter', () => setHover(id));
      el.addEventListener('mouseleave', () => setHover(null));
    }
  }

  function setHover(id) {
    hovered = id;
    const wrap = $('#flowviz-cards');
    wrap.classList.toggle('dim', !!id);
    for (const [nid, n] of Object.entries(NODES)) n.card.classList.toggle('hot', nid === id);
  }

  $('#btnPause').addEventListener('click', togglePause);
  function togglePause() {
    paused = !paused;
    $('#btnPause').textContent = paused ? '▶ Пуск' : '⏸ Пауза';
  }
  $('#speed').addEventListener('input', e => {
    speedMul = +e.target.value;
    $('#speedV').textContent = speedMul.toFixed(1) + '×';
  });
  document.addEventListener('keydown', e => {
    if (e.code === 'Space' && e.target === document.body) { e.preventDefault(); togglePause(); }
  });

  buildCards();
  layout();
  new ResizeObserver(() => layout()).observe(stage);
  requestAnimationFrame(frame);
})();
