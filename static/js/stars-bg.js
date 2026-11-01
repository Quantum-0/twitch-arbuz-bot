// Звёздный фон: плавающие точки с линиями, притягивающиеся к курсору.
// Канвас #stars-bg добавляется в templates/base.html и рендерится позади контента.
(function () {
    var canvas = document.getElementById("stars-bg");
    if (!canvas || !canvas.getContext) return;
    var ctx = canvas.getContext("2d");

    var settings = {
        count: 85,        // Количество звёзд
        speed: 0.35,      // Скорость движения
        link: 140,        // Расстояние соединения точек
        mouseRadius: 190  // Радиус притяжения к мыши
    };

    var width, height, points = [];
    var frame = null;
    var previousTime = 0;
    var mouse = { x: null, y: null };

    function resize() {
        width = window.innerWidth;
        height = window.innerHeight;
        var ratio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(height * ratio);
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        points = Array.from({ length: settings.count }, function () {
            return {
                x: Math.random() * width,
                y: Math.random() * height,
                vx: (Math.random() - 0.5) * settings.speed,
                vy: (Math.random() - 0.5) * settings.speed,
                radius: 1 + Math.random() * 1.4
            };
        });
    }

    function draw(time) {
        var step = previousTime ? Math.min((time - previousTime) / 16.67, 2) : 1;
        previousTime = time;
        ctx.clearRect(0, 0, width, height);
        for (var i = 0; i < points.length; i++) {
            var point = points[i];
            point.x += point.vx * step;
            point.y += point.vy * step;
            // Мягкое притяжение к курсору.
            if (mouse.x !== null) {
                var dx = mouse.x - point.x;
                var dy = mouse.y - point.y;
                var distance = Math.hypot(dx, dy);
                if (distance < settings.mouseRadius) {
                    var force = (1 - distance / settings.mouseRadius) * 0.022;
                    point.x += dx * force * step;
                    point.y += dy * force * step;
                }
            }
            // Выходящие за экран точки появляются с другой стороны.
            point.x = (point.x + width) % width;
            point.y = (point.y + height) % height;
        }
        // Чем ближе точки, тем заметнее линия между ними.
        ctx.lineWidth = 0.7;
        for (var a = 0; a < points.length; a++) {
            for (var b = a + 1; b < points.length; b++) {
                var p1 = points[a];
                var p2 = points[b];
                var d = Math.hypot(p1.x - p2.x, p1.y - p2.y);
                if (d < settings.link) {
                    var opacity = (1 - d / settings.link) * 0.35;
                    ctx.strokeStyle = "rgba(96, 205, 125, " + opacity + ")";
                    ctx.beginPath();
                    ctx.moveTo(p1.x, p1.y);
                    ctx.lineTo(p2.x, p2.y);
                    ctx.stroke();
                }
            }
        }
        ctx.fillStyle = "rgba(155, 240, 175, 0.85)";
        for (var j = 0; j < points.length; j++) {
            ctx.beginPath();
            ctx.arc(points[j].x, points[j].y, points[j].radius, 0, Math.PI * 2);
            ctx.fill();
        }
        frame = requestAnimationFrame(draw);
    }

    function schedule() {
        cancelAnimationFrame(frame);
        previousTime = 0;
        canvas.hidden = document.hidden;
        if (!document.hidden) frame = requestAnimationFrame(draw);
    }

    window.addEventListener("resize", resize);
    window.addEventListener("pointermove", function (event) {
        mouse.x = event.clientX;
        mouse.y = event.clientY;
    });
    function resetMouse() {
        mouse.x = null;
        mouse.y = null;
    }
    document.documentElement.addEventListener("pointerleave", resetMouse);
    window.addEventListener("blur", resetMouse);
    document.addEventListener("visibilitychange", schedule);

    resize();
    schedule();
})();
