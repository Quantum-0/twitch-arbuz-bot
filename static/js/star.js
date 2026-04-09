const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");

function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
}
window.addEventListener("resize", resize);
resize();

/* ================== ФИЗИКА ================== */

const GRAVITY = 0.25;
const FRICTION = 0.999;
const AIR_FORCE = 20;
const ITERATIONS = 6;
const MIN_INTERACTIONS_BEFORE_BREAK = 100;

/* Точка (Verlet) */
class Point {
    constructor(x, y, pinned = false) {
        this.x = x;
        this.y = y;
        this.oldx = x;
        this.oldy = y;
        this.pinned = pinned;
    }

    update() {
        if (this.pinned) return;

        const vx = (this.x - this.oldx) * FRICTION;
        const vy = (this.y - this.oldy) * FRICTION;

        this.oldx = this.x;
        this.oldy = this.y;

        this.x += vx;
        this.y += vy + GRAVITY;
    }

    applyForce(fx, fy) {
        this.x += fx;
        this.y += fy;
    }
}

/* Сегмент (constraint) */
class Stick {
    constructor(p0, p1, length) {
        this.p0 = p0;
        this.p1 = p1;
        this.length = length;
    }

    update() {
        const dx = this.p1.x - this.p0.x;
        const dy = this.p1.y - this.p0.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        const diff = (this.length - dist) / dist * 0.5;

        const offsetX = dx * diff;
        const offsetY = dy * diff;

        if (!this.p0.pinned) {
            this.p0.x -= offsetX;
            this.p0.y -= offsetY;
        }
        if (!this.p1.pinned) {
            this.p1.x += offsetX;
            this.p1.y += offsetY;
        }
    }
}

/* ================== НИТКА ================== */

const points = [];
const sticks = [];

const segments = 5 + Math.round(LENGTH * 15);
const segmentLength = 10 + Math.round(LENGTH * 25);
const startX = canvas.width * POSITION;
const startY = 0;

for (let i = 0; i <= segments; i++) {
    points.push(new Point(
        startX,
        startY + i * segmentLength,
        i === 0
    ));
    if (i > 0) {
        sticks.push(new Stick(
            points[i - 1],
            points[i],
            segmentLength
        ));
    }
}

const starPoint = points[points.length - 1];

/* ====== РАЗРЫВ НИТКИ ====== */

let isBroken = false;
let interactionsBeforeBreak = 0;

function breakString() {
    if (isBroken) return;
    isBroken = true;

    const breakIndex = 1 + Math.floor(Math.random() * (sticks.length - 2));

    // удаляем сегмент
    sticks.splice(breakIndex, 1);

    // нижняя часть отрывается
    for (let i = breakIndex + 1; i < points.length; i++) {
        points[i].pinned = false;
    }

    // импульс вниз
    for (let i = breakIndex + 1; i < points.length; i++) {
        points[i].oldy -= Math.random() * 5 + 2;
    }

    // дополнительный импульс звезде
    starPoint.oldx -= (Math.random() - 0.5) * 10;
    starPoint.oldy -= Math.random() * 10;
}

/* ================== ВЕТЕР ================== */

canvas.addEventListener("pointerdown", e => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const dx = starPoint.x - mx;
    const dy = starPoint.y - my;
    const dist = Math.sqrt(dx * dx + dy * dy) || 1;

    const fx = (dx / dist) * AIR_FORCE;
    const fy = (dy / dist) * AIR_FORCE * 0.4;

    starPoint.applyForce(fx, fy);

    if (!isBroken && interactionsBeforeBreak > MIN_INTERACTIONS_BEFORE_BREAK && Math.random() < (BREAK_CHANCE / 100)) {
        breakString();
    } else {
        interactionsBeforeBreak += 1;
    }

    spawnParticles(2 + Math.random() * 5);
});

/* ================= HEAT ================= */

window.addEventListener("heat:message", (e) => {
    const data = e.detail;

    const x = data.x * window.innerWidth;
    const y = data.y * window.innerHeight;

    const rect = canvas.getBoundingClientRect();
    const mx = x - rect.left;
    const my = y - rect.top;

    const dx = starPoint.x - mx;
    const dy = starPoint.y - my;
    const dist = Math.sqrt(dx * dx + dy * dy) || 1;

    const fx = (dx / dist) * AIR_FORCE;
    const fy = (dy / dist) * AIR_FORCE * 0.4;

    starPoint.applyForce(fx, fy);

    if (!isBroken && interactionsBeforeBreak < MIN_INTERACTIONS_BEFORE_BREAK && Math.random() < (BREAK_CHANCE / 100)) {
        breakString();
    } else {
        interactionsBeforeBreak += 1;
    }

    spawnParticles(2 + Math.random() * 5);
});

/* ================== Патиклы =================== */

const particles = [];

class Particle {
  constructor(x, y) {
    const angle = Math.random() * Math.PI * 2;
    const speed = Math.random() * 2 + 1;

    this.x = x;
    this.y = y;
    this.vx = Math.cos(angle) * speed;
    this.vy = Math.sin(angle) * speed;
    this.life = 1;
    this.size = Math.random() * 2 + 1;
  }

  update() {
    this.vx *= 0.98;
    this.vy *= 0.98;
    this.vy += 0.05;
    this.x += this.vx;
    this.y += this.vy;
    this.life -= 0.02;
  }

  draw() {
    ctx.fillStyle = hexToRgba(COLOR, this.life);
    ctx.beginPath();
    ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
    ctx.fill();
  }
}

function hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function spawnParticles(count) {
  for (let i = 0; i < count; i++) {
    particles.push(new Particle(starPoint.x, starPoint.y));
  }
}

/* ================== РЕНДЕР ================== */

function drawStar(x, y, r) {
    ctx.save();
    ctx.translate(x, y);
    ctx.beginPath();
    for (let i = 0; i < 5; i++) {
        ctx.lineTo(
            Math.cos((18 + i * 72) * Math.PI / 180) * r,
            -Math.sin((18 + i * 72) * Math.PI / 180) * r
        );
        ctx.lineTo(
            Math.cos((54 + i * 72) * Math.PI / 180) * r * 0.5,
            -Math.sin((54 + i * 72) * Math.PI / 180) * r * 0.5
        );
    }
    ctx.closePath();
    ctx.fillStyle = COLOR;
    ctx.shadowColor = COLOR;
    ctx.shadowBlur = 15;
    ctx.fill();
    ctx.restore();
}

function update() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    for (const p of points) p.update();

    for (let i = 0; i < ITERATIONS; i++) {
        for (const s of sticks) s.update();
    }

    /* Ниточка */
    ctx.beginPath();
    ctx.strokeStyle = "#aaa";
    ctx.lineWidth = 2;

    for (let i = 0; i < sticks.length; i++) {
        const s = sticks[i];
        ctx.moveTo(s.p0.x, s.p0.y);
        ctx.lineTo(s.p1.x, s.p1.y);
    }
    ctx.stroke();

    /* Звёздочка */
    drawStar(starPoint.x, starPoint.y, SIZE);

    for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        p.update();
        p.draw();
        if (p.life <= 0) particles.splice(i, 1);
    }
    requestAnimationFrame(update);
}

update();