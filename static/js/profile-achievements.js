// Панель «Достижения» на профиле: горизонтальная лента ачивок с прокруткой.
// Сервер отдаёт ачивки отсортированными «новые слева»; стрелки появляются,
// когда лента не влезает целиком, и листают её на ширину видимой части.
(() => {
    "use strict";

    const strip = document.querySelector("[data-ach-strip]");
    const row = strip?.closest(".profile-achievements-row");
    if (!strip || !row) return;

    const prev = row.querySelector('[data-ach-scroll="-1"]');
    const next = row.querySelector('[data-ach-scroll="1"]');
    if (!prev || !next) return;

    function updateArrows() {
        const canScroll = strip.scrollWidth > strip.clientWidth + 1;
        prev.hidden = !canScroll || strip.scrollLeft <= 1;
        next.hidden = !canScroll || strip.scrollLeft >= strip.scrollWidth - strip.clientWidth - 1;
    }

    prev.addEventListener("click", () => strip.scrollBy({left: -strip.clientWidth, behavior: "smooth"}));
    next.addEventListener("click", () => strip.scrollBy({left: strip.clientWidth, behavior: "smooth"}));
    strip.addEventListener("scroll", updateArrows, {passive: true});
    window.addEventListener("resize", updateArrows);
    updateArrows();
})();
