// Звёздный фон: плавающие точки с линиями, притягивающиеся к курсору.
// Канвас #stars-bg добавляется в templates/base.html и рендерится позади контента.
(function () {
    var canvas = document.getElementById("stars-bg");
    if (!canvas || !canvas.getContext) return;
    var ctx = canvas.getContext("2d");

    var settings = {
        count: 85,        // Количество звёзд
        speed: 0.35,      // Скорость базового дрейфа
        link: 140,        // Расстояние соединения точек
        mouseRadius: 190, // Радиус, в котором на точки действует притяжение к курсору
        accel: 0.05,      // Максимальное ускорение к курсору (px/кадр²), ближе к нему — сильнее
        maxSpeed: 1.8,    // Предел скорости точки (px/кадр)
        relax: 0.02,      // Торможение к базовому дрейфу внутри зоны (куча у курсора остаётся плотной)
        glideRelax: 0.005 // Торможение вне зоны: точки, разгоненные курсором, летят по инерции секунды
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
            return spawnPoint(Math.random() * width, Math.random() * height);
        });
    }

    function draw(time) {
        var step = previousTime ? Math.min((time - previousTime) / 16.67, 2) : 1;
        previousTime = time;
        ctx.clearRect(0, 0, width, height);
        for (var i = 0; i < points.length; i++) {
            var point = points[i];
            var insideZone = false;
            // Притяжение: в радиусе от курсора точка плавно набирает ускорение
            // в его сторону, чем ближе — тем сильнее (скорость, а не позиция:
            // полученный импульс точка сохраняет и летит по инерции).
            if (mouse.x !== null) {
                var dx = mouse.x - point.x;
                var dy = mouse.y - point.y;
                var distance = Math.hypot(dx, dy);
                if (distance < settings.mouseRadius && distance > 0) {
                    insideZone = true;
                    var accel = settings.accel * (1 - distance / settings.mouseRadius) * step;
                    point.vx += dx / distance * accel;
                    point.vy += dy / distance * accel;
                }
            }
            // Торможение к базовому дрейфу: внутри зоны сильное (рой не разгоняется
            // вечно и держится кучей у курсора), снаружи слабое — точки ещё долго
            // летят туда, куда их разогнал курсор.
            var relax = (insideZone ? settings.relax : settings.glideRelax) * step;
            point.vx += (point.bvx - point.vx) * relax;
            point.vy += (point.bvy - point.vy) * relax;
            // Предел скорости, чтобы рой у курсора не превращался в хаос.
            var speed = Math.hypot(point.vx, point.vy);
            if (speed > settings.maxSpeed) {
                var limit = settings.maxSpeed / speed;
                point.vx *= limit;
                point.vy *= limit;
            }
            point.x += point.vx * step;
            point.y += point.vy * step;
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

    function spawnPoint(x, y) {
        var bvx = (Math.random() - 0.5) * settings.speed;
        var bvy = (Math.random() - 0.5) * settings.speed;
        return {
            x: x,
            y: y,
            vx: bvx,
            vy: bvy,
            bvx: bvx,
            bvy: bvy,
            radius: 1 + Math.random() * 1.4
        };
    }

    // Публичный API для пасхалки со сбором точек (js/dots-easter-egg.js).
    window.starsBg = {
        // Сколько точек сейчас в радиусе radius от курсора (0, если курсора нет).
        nearMouseCount: function (radius) {
            if (mouse.x === null) return 0;
            var count = 0;
            for (var i = 0; i < points.length; i++) {
                if (Math.hypot(points[i].x - mouse.x, points[i].y - mouse.y) <= radius) count++;
            }
            return count;
        },
        // Добавить точки: кольцом вокруг курсора в радиусе притяжения,
        // чтобы они видимым ручьём стеклись к нему.
        addPoints: function (n) {
            for (var i = 0; i < n; i++) {
                var x, y;
                if (mouse.x !== null) {
                    var angle = Math.random() * Math.PI * 2;
                    var distance = 100 + Math.random() * 150;
                    x = mouse.x + Math.cos(angle) * distance;
                    y = mouse.y + Math.sin(angle) * distance;
                } else {
                    x = Math.random() * width;
                    y = Math.random() * height;
                }
                points.push(spawnPoint(x, y));
            }
            // resize() пересоздаёт точки по settings.count — учитываем добавленные.
            settings.count = points.length;
        }
    };

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
