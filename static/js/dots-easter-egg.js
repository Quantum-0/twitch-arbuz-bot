// Пасхалка «сборщик точек»: если пользователь продолжительное время активно
// собирает возле курсора кучу точек фона, не прокручивая страницу и не кликая, —
// приглушаем интерфейс, чтобы фон было видно лучше, и подкидываем ещё точек.
// Куча считается через API js/stars-bg.js, активность — по пути, пройденному курсором.
(function () {
    var COLLECT_RADIUS = 90;            // Радиус от курсора, внутри которого точка считается «в куче»
    var COLLECT_THRESHOLD = 15;         // Сколько точек должно собраться возле курсора
    var SUSTAIN_MS = 60 * 1000;         // Как долго пользователь должен удерживать кучу
    var GRACE_MS = 2 * 1000;            // Допустимый провал условий (рывок курсора) без сброса таймера
    var CHECK_INTERVAL_MS = 500;        // Как часто пересчитывать условия
    var EXTRA_DOTS = 100;               // Сколько точек накинуть после второго уведомления
    var ACTIVITY_WINDOW_MS = 10 * 1000; // Окно оценки активности курсора
    var ACTIVITY_DISTANCE_PX = 150;     // Минимальный путь курсора за окно, чтобы считать сбор активным
    var RECENT_MOVE_MS = 5 * 1000;      // Последнее движение курсора не старше этого — иначе мышь «замерла»

    var triggered = false;
    var sustainStart = null;
    var belowSince = null;

    // Путь курсора за последнее окно: список отрезков [{t, d}] с инкрементальной суммой.
    var moves = [];
    var moveSum = 0;
    var lastMouse = null;
    var lastMoveT = 0;

    function pruneMoves(now) {
        while (moves.length > 0 && now - moves[0].t > ACTIVITY_WINDOW_MS) {
            moveSum -= moves[0].d;
            moves.shift();
        }
    }

    window.addEventListener("pointermove", function (event) {
        var now = Date.now();
        if (lastMouse) {
            var d = Math.hypot(event.clientX - lastMouse.x, event.clientY - lastMouse.y);
            moves.push({t: now, d: d});
            moveSum += d;
        }
        lastMouse = {x: event.clientX, y: event.clientY};
        lastMoveT = now;
        pruneMoves(now);
    });

    function isCollecting(now) {
        var count = window.starsBg ? window.starsBg.nearMouseCount(COLLECT_RADIUS) : 0;
        if (count < COLLECT_THRESHOLD) return false;
        // Лежащая мышь не собирает: точки сами натекают к стоящему курсору,
        // поэтому требуем, чтобы курсор двигался прямо сейчас, а путь набирал
        // за окно непрерывно, а не был намотан когда-то до паузы.
        if (now - lastMoveT > RECENT_MOVE_MS) return false;
        pruneMoves(now);
        return moveSum >= ACTIVITY_DISTANCE_PX;
    }

    function reset() {
        sustainStart = null;
        belowSince = null;
    }

    // Скролл, клики и клавиатура — обычное пользование сайтом, а не сбор точек.
    window.addEventListener("scroll", reset, {passive: true, capture: true});
    window.addEventListener("pointerdown", reset, {passive: true});
    window.addEventListener("keydown", reset);
    // Свёрнутая вкладка и переключение окна — пользователя нет за игрой.
    document.addEventListener("visibilitychange", function () {
        if (document.hidden) reset();
    });
    window.addEventListener("blur", reset);

    function check() {
        if (triggered) return;
        var now = Date.now();
        if (isCollecting(now)) {
            belowSince = null;
            if (sustainStart === null) sustainStart = now;
            if (now - sustainStart >= SUSTAIN_MS) trigger();
        } else if (sustainStart !== null) {
            // Условие нарушено (рассыпалась куча, курсор замер или ушёл):
            // короткий провал прощаем, длинный — сбрасываем выдержку.
            if (belowSince === null) belowSince = now;
            if (now - belowSince > GRACE_MS) reset();
        }
    }

    function notify(title, message) {
        if (typeof showNotification === "function") showNotification(title, message, false, 10000);
    }

    function trigger() {
        triggered = true;
        document.body.classList.add("dots-easter-egg");
        notify(
            "Тоже залипаешь на эти точки? :з",
            "Дааа, мне тоже нравится их ловить!\nСделал интерфейс прозрачным, чтоб тебе было удобнее.\nРазвлекайся! :D"
        );
        setTimeout(function () {
            notify("Подарок", "На, держи ещё точек :D");
            if (window.starsBg) window.starsBg.addPoints(EXTRA_DOTS);
        }, 20 * 1000);
        setTimeout(function () {
            notify("Надоело баловаться с точками?", "тогда просто обнови страницу, всё вернётся как было");
        }, 35 * 1000);
    }

    setInterval(check, CHECK_INTERVAL_MS);
})();
