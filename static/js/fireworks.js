const canvas = document.getElementById("fireworksCanvas");
const ctx = canvas.getContext("2d");

function resizeCanvas() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
}

window.addEventListener("resize", resizeCanvas);
resizeCanvas();

let particles = [];

class Particle {
    constructor(x, y, color) {
        this.x = x;
        this.y = y;
        this.color = color;

        this.radius = Math.random() * 3 + 1;

        const angle = Math.random() * Math.PI * 2;
        const speed = Math.random() * 6 + 2;

        this.vx = Math.cos(angle) * speed;
        this.vy = Math.sin(angle) * speed;

        this.alpha = 1;
        this.decay = Math.random() * DECAY + DECAY;

        this.gravity = GRAVITY;
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
        ctx.save();
        ctx.globalAlpha = Math.max(this.alpha, 0);
        ctx.beginPath();
        ctx.arc(this.x, this.y, this.radius, 0, Math.PI * 2);
        ctx.fillStyle = this.color;
        ctx.shadowBlur = 10;
        ctx.shadowColor = this.color;
        ctx.fill();
        ctx.restore();
    }
}

function createFirework(x, y) {
    const hue = Math.floor(Math.random() * 360);

    for (let i = 0; i < PARTICLE_COUNT; i++) {
        const color = `hsl(${hue + Math.floor(Math.random() * 30 - 15)}, 100%, 60%)`;
        particles.push(new Particle(x, y, color));
    }
}

/* ================= HEAT ================= */

window.addEventListener("heat:message", (e) => {
    const x = e.detail.x * window.innerWidth;
    const y = e.detail.y * window.innerHeight;
    createFirework(x, y);
});

/* ============== ЛОКАЛЬНЫЙ КЛИК (тест) ============== */

canvas.addEventListener("pointerdown", (e) => {
    createFirework(e.clientX, e.clientY);
});

/* ================== АНИМАЦИЯ ================== */

function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    for (let i = particles.length - 1; i >= 0; i--) {
        particles[i].update();
        particles[i].draw();

        if (particles[i].alpha <= 0) {
            particles.splice(i, 1);
        }
    }

    requestAnimationFrame(animate);
}

animate();
