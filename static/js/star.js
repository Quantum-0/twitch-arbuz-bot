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

const segments = 18;
const segmentLength = 12;
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
});

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
    for (let i = 0; i < points.length; i++) {
        const p = points[i];
        if (i === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
    }
    ctx.stroke();

    /* Звёздочка */
    drawStar(starPoint.x, starPoint.y, SIZE);

    requestAnimationFrame(update);
}

update();