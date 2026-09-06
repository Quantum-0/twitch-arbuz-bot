(() => {
    "use strict";

    const container = document.querySelector("[data-profile-like]");
    const button = container?.querySelector(".profile-like-button");
    if (!container || !button) return;

    button.addEventListener("click", async () => {
        if (button.disabled) return;
        const liked = button.getAttribute("aria-pressed") === "true";
        button.disabled = true;
        try {
            const response = await fetch(`/api/user/likes/${encodeURIComponent(container.dataset.userId)}`, {
                method: liked ? "DELETE" : "PUT",
                headers: {Accept: "application/json"},
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            button.setAttribute("aria-pressed", String(data.liked));
            button.classList.toggle("active", data.liked);
            button.title = data.liked ? "Убрать лайк" : "Поставить лайк";
            container.querySelector("[data-like-count]").textContent = data.likes_count;
        } catch (_error) {
            window.alert("Не удалось изменить лайк. Попробуйте ещё раз.");
        } finally {
            button.disabled = false;
        }
    });
})();
