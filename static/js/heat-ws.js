(function () {
    const PRIMARY = `wss://heat-api.j38.net/channel/${CHANNEL_ID}`;
    const BACKUP  = `wss://bot.quantum0.ru/ws/heat/${CHANNEL_ID}`;

    const URLS = BACKUP ? [PRIMARY, BACKUP] : [PRIMARY];

    const BASE_DELAY = 1000;
    const MAX_DELAY  = 20000;
    const CONNECT_TIMEOUT = 4000;

    const HEARTBEAT_CHECK_INTERVAL = 5000;
    const HEARTBEAT_TIMEOUT        = 30000;

    let ws = null;
    let urlIndex = 0;
    let attempt = 0;

    let reconnectTimer = null;
    let connectTimer   = null;
    let heartbeatTimer = null;

    let lastMessageAt = 0;
    let connecting = false;

    function log(...args) {
        console.log("[HeatWS]", ...args);
    }

    function warn(...args) {
        console.warn("[HeatWS]", ...args);
    }

    function emit(name, detail) {
        window.dispatchEvent(new CustomEvent(name, { detail }));
    }

    function cleanup() {
        if (connectTimer) {
            clearTimeout(connectTimer);
            connectTimer = null;
        }

        if (heartbeatTimer) {
            clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }

        if (ws) {
            try {
                ws.onopen = ws.onclose = ws.onerror = ws.onmessage = null;
                ws.close();
            } catch {}
            ws = null;
        }

        connecting = false;
    }

    function connect() {
        if (connecting) return;
        connecting = true;

        const url = URLS[urlIndex];
        log("connecting →", url);

        cleanup();

        ws = new WebSocket(url);
        lastMessageAt = Date.now();

        connectTimer = setTimeout(() => {
            warn("connect timeout");
            forceReconnect();
        }, CONNECT_TIMEOUT);

        ws.onopen = () => {
            clearTimeout(connectTimer);
            connectTimer = null;

            log("connected");
            attempt = 0;
            connecting = false;

            emit("heat:open");

            heartbeatTimer = setInterval(heartbeatCheck, HEARTBEAT_CHECK_INTERVAL);
        };

        ws.onmessage = (e) => {
            lastMessageAt = Date.now();

            try {
                const data = JSON.parse(e.data);
                if (data.type !== "click") return;
                emit("heat:message", data);
            } catch {
                warn("bad json", e.data);
            }
        };

        ws.onerror = (e) => {
            warn("socket error", e);
            forceReconnect();
        };

        ws.onclose = (e) => {
            warn("closed", e.code);
            emit("heat:close", e);
            forceReconnect();
        };
    }

    function heartbeatCheck() {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;

        const delta = Date.now() - lastMessageAt;
        if (delta > HEARTBEAT_TIMEOUT) {
            warn("heartbeat timeout", delta);
            forceReconnect();
        }
    }

    function forceReconnect() {
        if (reconnectTimer) return;

        cleanup();

        urlIndex = (urlIndex + 1) % URLS.length;
        attempt++;

        const delay = Math.min(
            BASE_DELAY * 2 ** attempt + Math.random() * 500,
            MAX_DELAY
        );

        warn(`reconnecting in ${delay}ms (attempt ${attempt})`);

        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connect();
        }, delay);
    }

    // OBS иногда выгружает страницу без onclose
    window.addEventListener("beforeunload", cleanup);

    connect();
})();
