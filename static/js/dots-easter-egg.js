// Пасхалка «сборщик точек»: если пользователь больше минуты держит возле курсора
// целую кучу точек фона и не прокручивает страницу — приглушаем интерфейс,
// чтобы фон было видно лучше, и подкидываем ещё точек.
// Счёт точек возле курсора берётся из API js/stars-bg.js.
(function () {
    var COLLECT_RADIUS = 90;      // Радиус от курсора, внутри которого точка считается «в куче»
    var COLLECT_THRESHOLD = 15;   // Сколько точек должно собраться возле курсора
    var SUSTAIN_MS = 60 * 1000;   // Как долго пользователь должен удерживать кучу
    var GRACE_MS = 2 * 1000;      // Допустимый провал счёта (резкий рывок курсора) без сброса таймера
    var CHECK_INTERVAL_MS = 500;  // Как часто пересчитывать точки возле курсора
    var EXTRA_DOTS = 100;         // Сколько точек накинуть после второго уведомления

    var triggered = false;
    var sustainStart = null;
    var belowSince = null;

    function notify(title, message) {
        if (typeof showNotification === "function") showNotification(title, message);
    }

    // Любой скролл (в том числе внутренних блоков) означает, что страницу листают, а не собирают точки.
    window.addEventListener("scroll", function () {
        sustainStart = null;
        belowSince = null;
    }, {passive: true, capture: true});

    function check() {
        if (triggered) return;
        var count = window.starsBg ? window.starsBg.nearMouseCount(COLLECT_RADIUS) : 0;
        var now = Date.now();
        if (count >= COLLECT_THRESHOLD) {
            belowSince = null;
            if (sustainStart === null) sustainStart = now;
            if (now - sustainStart >= SUSTAIN_MS) trigger();
        } else if (sustainStart !== null) {
            // Курсор вне кучи (или его нет): короткий провал прощаем, длинный — сбрасываем выдержку.
            if (belowSince === null) belowSince = now;
            if (now - belowSince > GRACE_MS) {
                sustainStart = null;
                belowSince = null;
            }
        }
    }

    function trigger() {
        triggered = true;
        document.body.classList.add("dots-easter-egg");
        notify(
            "Тоже залипаешь на эти точки? :з",
            "Дааа, мне тоже нравится их ловить! Сделал интерфейс прозрачным, чтоб тебе было удобнее. Развлекайся! :D"
        );
        setTimeout(function () {
            notify("На, держи ещё точек :D", "");
            if (window.starsBg) window.starsBg.addPoints(EXTRA_DOTS);
        }, 20 * 1000);
        setTimeout(function () {
            notify("Как надоест — просто обнови страницу, интерфейс снова станет непрозрачным", "");
        }, 35 * 1000);
    }

    setInterval(check, CHECK_INTERVAL_MS);
})();
