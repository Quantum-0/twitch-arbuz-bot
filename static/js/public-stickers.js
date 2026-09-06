(() => {
    "use strict";

    let cursor = null;
    let finished = false;
    let loading = false;

    function renderSticker(item) {
        const card = document.createElement("div");
        card.className = "ai-sticker";

        const imageLink = document.createElement("a");
        imageLink.href = `/files/ai-gen-stickers/${encodeURIComponent(item.file_id)}`;
        imageLink.className = "ai-sticker-img-wrap";
        const image = document.createElement("img");
        image.src = imageLink.href;
        image.loading = "lazy";
        image.alt = item.prompt || "ИИ-стикер";
        image.onerror = () => {
            image.onerror = null;
            image.src = "/static/images/500.png";
        };
        imageLink.appendChild(image);

        const prompt = document.createElement("p");
        prompt.className = "ai-sticker-prompt";
        const promptText = document.createElement("i");
        promptText.textContent = item.prompt;
        prompt.appendChild(promptText);

        const meta = document.createElement("p");
        meta.className = "ai-sticker-meta";
        if (item.by_chatter) {
            meta.append("by ");
            const chatterLink = document.createElement("a");
            chatterLink.href = `https://twitch.tv/${encodeURIComponent(item.by_chatter)}`;
            chatterLink.target = "_blank";
            chatterLink.rel = "noopener";
            chatterLink.textContent = item.by_chatter;
            meta.appendChild(chatterLink);
        }
        if (item.channel_login) {
            meta.append(item.by_chatter ? " на канале " : "На канале ");
            const channelLink = document.createElement("a");
            channelLink.href = `/profile/${encodeURIComponent(item.channel_login)}`;
            channelLink.textContent = item.channel_login;
            meta.appendChild(channelLink);
        }

        card.append(imageLink, prompt, meta);
        return card;
    }

    async function loadMore() {
        if (finished || loading) return;
        loading = true;
        const button = document.getElementById("load-more-stickers");
        button.disabled = true;
        try {
            const url = new URL("/api/galleries/stickers", window.location.origin);
            if (cursor) url.searchParams.set("before", cursor);
            const response = await fetch(url, {headers: {Accept: "application/json"}});
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            const grid = document.getElementById("ai-stickers-grid");
            data.items.forEach((item) => grid.appendChild(renderSticker(item)));
            cursor = data.next_cursor;
            if (!cursor) {
                finished = true;
                button.style.display = "none";
                document.getElementById("stickers-end").style.display = "block";
            }
        } catch (_error) {
            if (typeof showNotification === "function") {
                showNotification("Ошибка", "Не удалось загрузить стикеры", true);
            }
        } finally {
            loading = false;
            button.disabled = false;
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.getElementById("load-more-stickers").addEventListener("click", loadMore);
        loadMore();
    });
})();
