/* ============================================ */

const grid = document.getElementById("game");

let first = null;
let second = null;
let locked = false;

function uuid() {
    return 'xxxxxxxx'.replace(/x/g, () =>
        Math.floor(Math.random() * 16).toString(16)
    );
}


const deck = [...PAIRS, ...PAIRS]
    .map(p => ({
        ...p,
        uid: uuid()
    }))
    .sort(() => Math.random() - 0.5);

deck.forEach(data => {
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.id = data.id;

    card.innerHTML = `
  <div class="face front"></div>
  <div class="face back">
    <img src="${data.img}" draggable="false">
    <div class="caption">${data.caption}</div>
  </div>
`;

    card.addEventListener("click", () => flip(card));
    grid.appendChild(card);
});

function flip(card) {
    if (locked || card.classList.contains("flipped")) return;

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
        // showToast(lastClickUser, first.dataset.id);
        reset();
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

/* ================= HEAT ================= */

window.addEventListener("heat:message", (e) => {
    const data = e.detail;

    const x = data.x * window.innerWidth;
    const y = data.y * window.innerHeight;

    const el = document.elementFromPoint(x, y);
    const card = el?.closest(".card");
    if (card) card.click();
});