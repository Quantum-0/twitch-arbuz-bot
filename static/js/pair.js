/* ============================================ */

const grid = document.getElementById("game");

let first = null;
let second = null;
let locked = false;
let roundActive = false;
let matchedPairs = 0;
let totalPairs = 0;
let roundCards = [];        // колода текущего раунда (для решафла при ошибке сети)
let roundHasSpiral = false;
let spiralCard = null;
let spiralDone = false;

function uuid() {
    return 'xxxxxxxx'.replace(/x/g, () =>
        Math.floor(Math.random() * 16).toString(16)
    );
}

function newRound(cards, hasSpiral) {
    grid.innerHTML = "";

    first = null;
    second = null;
    locked = false;
    matchedPairs = 0;
    totalPairs = cards.length;
    roundCards = cards;
    roundHasSpiral = hasSpiral;
    spiralCard = null;
    spiralDone = false;
    roundActive = true;

    const deck = [...cards, ...cards]
        .map(p => ({
            ...p,
            uid: uuid()
        }))
        .sort(() => Math.random() - 0.5);

    // лишняя клетка нечётной сетки — спиральная карточка в случайном месте
    if (hasSpiral) {
        deck.splice(Math.floor(Math.random() * (deck.length + 1)), 0, { spiral: true });
    }

    deck.forEach(data => {
        const card = document.createElement("div");
        card.className = "card";

        if (data.spiral) {
            card.dataset.spiral = "1";
            card.innerHTML = `
              <div class="face front"></div>
              <div class="face back"><div class="spiral-disc"></div></div>
            `;
            spiralCard = card;
        } else {
            card.dataset.id = data.id;
            card.innerHTML = `
              <div class="face front"></div>
              <div class="face back">
                <img src="${data.img}" draggable="false">
                <div class="caption">${data.caption}</div>
              </div>
            `;
        }

        card.addEventListener("click", () => onCardClick(card));
        grid.appendChild(card);
    });

    fitCaptions();
}

function onCardClick(card) {
    if (card.dataset.spiral) {
        openSpiral(card);
        return;
    }
    flip(card);
}

function flip(card) {
    if (!roundActive || locked || card.classList.contains("flipped")) return;

    card.classList.add("flipped");

    if (!first) {
        first = card;
        return;
    }

    second = card;
    locked = true;

    if (first.dataset.id === second.dataset.id) {
        first.classList.add("matched");
        second.classList.add("matched");
        reset();
        matchedPairs++;
        if (matchedPairs === totalPairs) onWin();
    } else {
        setTimeout(() => {
            first.classList.remove("flipped");
            second.classList.remove("flipped");
            reset();
        }, 800);
    }
}

function reset() {
    first = null;
    second = null;
    locked = false;
}

/* ================= SPIRAL ================= */

function openSpiral(card) {
    if (!roundActive || card.classList.contains("flipped")) return;

    card.classList.add("flipped");
    spiralDone = true;

    // 3 секунды крутится, потом рассыпается на части и исчезает
    setTimeout(() => {
        const rect = card.getBoundingClientRect();
        const count = Math.min(220, Math.max(60, Math.round(rect.width * rect.height / 300)));
        scatter(rect, ["#1a1a1a", "#f2f2f2", "#8a8a8a"], count);
        card.classList.add("gone");
    }, 3000);
}

/* ================= WIN / RESTART ================= */

function onWin() {
    // если спираль так и не открыли — открываем на прощание (до блокировки кликов)
    if (spiralCard && !spiralDone) openSpiral(spiralCard);

    roundActive = false;
    celebrate();
    setTimeout(startNextRound, RESTART_DELAY * 1000);
}

function celebrate() {
    const rect = grid.getBoundingClientRect();
    for (let i = 0; i < 5; i++) {
        setTimeout(() => {
            for (let j = 0; j < 3; j++) {
                burst(
                    rect.left + Math.random() * rect.width,
                    rect.top + Math.random() * rect.height,
                    70
                );
            }
        }, i * 350);
    }
}

async function startNextRound() {
    const params = new URLSearchParams({
        channel_id: String(CHANNEL_ID),
        grid: `${GRID_COLS}x${GRID_ROWS}`,
        use_twitch_emoji: String(USE_TWITCH_EMOJI),
        arbuz: String(ARBUZ),
    });
    // новый раунд — новые лица: просим сервер не брать стримеров прошлого раунда
    const ids = [...new Set(roundCards.map(c => c.id))];
    if (ids.length) params.set("exclude", ids.join(","));

    try {
        const res = await fetch(`/overlay/pair/deck?${params.toString()}`);
        if (!res.ok) throw new Error(`deck request failed: ${res.status}`);
        const data = await res.json();
        newRound(data.cards, data.spiral);
    } catch (e) {
        // сеть отвалилась — игра не встаёт: решафл текущей колоды
        newRound(roundCards, roundHasSpiral);
    }
}

/* ================= PARTICLES ================= */

const fxCanvas = document.getElementById("fx-canvas");
const fxCtx = fxCanvas.getContext("2d");
const fxParticles = [];

function fxResize() {
    fxCanvas.width = window.innerWidth;
    fxCanvas.height = window.innerHeight;
}

window.addEventListener("resize", fxResize);
fxResize();

class FxParticle {
    constructor(x, y, color, opts = {}) {
        this.x = x;
        this.y = y;
        this.color = color;

        this.radius = Math.random() * 3 + 1.5;

        const angle = opts.angle ?? Math.random() * Math.PI * 2;
        const speed = opts.speed ?? (Math.random() * 6 + 2);

        this.vx = Math.cos(angle) * speed;
        this.vy = Math.sin(angle) * speed;

        this.alpha = 1;
        this.decay = Math.random() * 0.012 + 0.012;
        this.gravity = opts.gravity ?? 0.12;
        this.friction = 0.98;
    }

    update() {
        this.vx *= this.friction;
        this.vy *= this.friction;
        this.vy += this.gravity;
        this.x += this.vx;
        this.y += this.vy;
        this.alpha -= this.decay;
    }

    draw() {
        fxCtx.save();
        fxCtx.globalAlpha = Math.max(this.alpha, 0);
        fxCtx.beginPath();
        fxCtx.arc(this.x, this.y, this.radius, 0, Math.PI * 2);
        fxCtx.fillStyle = this.color;
        fxCtx.shadowBlur = 10;
        fxCtx.shadowColor = this.color;
        fxCtx.fill();
        fxCtx.restore();
    }
}

function confettiColors() {
    return Array.from({ length: 4 }, () => `hsl(${Math.floor(Math.random() * 360)},100%,60%)`);
}

function burst(x, y, count, colors = confettiColors()) {
    for (let i = 0; i < count; i++) {
        fxParticles.push(new FxParticle(x, y, colors[Math.floor(Math.random() * colors.length)]));
    }
}

// частицы рождаются по всей площади прямоугольника (эффект «рассыпания»)
function scatter(rect, colors, count) {
    for (let i = 0; i < count; i++) {
        fxParticles.push(
            new FxParticle(
                rect.left + Math.random() * rect.width,
                rect.top + Math.random() * rect.height,
                colors[Math.floor(Math.random() * colors.length)],
                { speed: Math.random() * 4 + 1 }
            )
        );
    }
}

function fxAnimate() {
    fxCtx.clearRect(0, 0, fxCanvas.width, fxCanvas.height);

    for (let i = fxParticles.length - 1; i >= 0; i--) {
        fxParticles[i].update();
        fxParticles[i].draw();

        if (fxParticles[i].alpha <= 0) {
            fxParticles.splice(i, 1);
        }
    }

    requestAnimationFrame(fxAnimate);
}

fxAnimate();

/* ================= CAPTIONS ================= */

const MIN_CAPTION_PX = 9;

// Уменьшаем кегль ника, пока он не влезет в карточку целиком;
// многоточие остаётся только для совсем длинных ников на мелких карточках.
function fitCaptions() {
    document.querySelectorAll("#game .caption").forEach(el => {
        el.style.fontSize = "";
        let size = parseFloat(getComputedStyle(el).fontSize);
        while (size > MIN_CAPTION_PX && el.scrollWidth > el.clientWidth) {
            size -= 0.5;
            el.style.fontSize = size + "px";
        }
    });
}

window.__fitPairCaptions = fitCaptions;

/* ================= HEAT ================= */

window.addEventListener("heat:message", (e) => {
    const data = e.detail;

    const x = data.x * window.innerWidth;
    const y = data.y * window.innerHeight;

    const el = document.elementFromPoint(x, y);
    const card = el?.closest(".card");
    if (card) card.click();
});

/* ================= START ================= */

newRound(PAIRS, HAS_SPIRAL);
