(function () {
    const PRIMARY = `wss://heat-api.j38.net/channel/${CHANNEL_ID}`;
    const BACKUP  = `wss://bot.quantum0.ru/ws/heat/${CHANNEL_ID}`;

    if (!PRIMARY) {
        console.error("[HeatWS] PRIMARY url not set");
        return;
    }

    const URLS = BACKUP ? [PRIMARY, BACKUP] : [PRIMARY];

    let ws = null;
    let urlIndex = 0;
    let reconnectTimer = null;
    let attempt = 0;

    const BASE_DELAY = 1000;
    const MAX_DELAY = 20000;

    function connect() {
        const url = URLS[urlIndex];
        console.log(`[HeatWS] connecting → ${url}`);

        ws = new WebSocket(url);

        ws.onopen = () => {
            console.log("[HeatWS] connected");
            attempt = 0;
            emit("heat:open");
        };

        ws.onmessage = (e) => {
            try {
                let data = JSON.parse(e.data);
                if (data.type !== "click") return;
                emit("heat:message", data);
            } catch {
                console.warn("[HeatWS] bad json", e.data);
            }
        };

        ws.onclose = (e) => {
            console.warn("[HeatWS] closed", e.code);
            emit("heat:close", e);
            reconnect();
        };

        ws.onerror = () => ws.close();
    }

    function reconnect() {
        if (reconnectTimer) return;

        urlIndex = (urlIndex + 1) % URLS.length;

        const delay = Math.min(
            BASE_DELAY * 2 ** attempt,
            MAX_DELAY
        );

        attempt++;

        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connect();
        }, delay);
    }

    function emit(name, detail) {
        window.dispatchEvent(new CustomEvent(name, { detail }));
    }

    connect();
})();

/*
setInterval(() => {
    if (ws?.readyState === 1) ws.send("ping");
}, 15000);


document.addEventListener("visibilitychange", () => {
    if (!document.hidden && ws?.readyState !== 1) {
        ws.close();
    }
});
*/