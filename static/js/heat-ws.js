(function () {
    const PRIMARY_WS = `wss://heat-api.j38.net/channel/${CHANNEL_ID}`;
    const BACKUP_WS  = `wss://bot.quantum0.ru/ws/heat/${CHANNEL_ID}`;
    const BACKUP_SSE = `https://bot.quantum0.ru/sse/heat/${CHANNEL_ID}`;

    const BASE_DELAY = 1000;
    const MAX_DELAY  = 20000;
    const CONNECT_TIMEOUT = 4000;

    const HEARTBEAT_CHECK_INTERVAL = 5000;
    const HEARTBEAT_TIMEOUT        = 30000;

    let transport = null; // WebSocket | EventSource
    let transportType = null; // "ws" | "sse"

    let urlIndex = 0;
    let attempt = 0;
    let forcedUrl = null;
    let forced = false;

    let reconnectTimer = null;
    let connectTimer   = null;
    let heartbeatTimer = null;

    let lastMessageAt = 0;
    let connecting = false;

    /* ---------------- utils ---------------- */

    function log(...args) {
        console.log("[Heat]", ...args);
    }

    function warn(...args) {
        console.warn("[Heat]", ...args);
    }

    function emit(name, detail) {
        window.dispatchEvent(new CustomEvent(name, { detail }));
    }

    function parseForcedUrl() {
        const params = new URLSearchParams(window.location.search);
        const raw = params.get("url") || params.get("channel");

        if (!raw) return null;

        forced = true;

        if (raw.startsWith("sse://")) {
            return {
                type: "sse",
                url: "https://" + raw.slice(6)
            };
        }

        if (raw.startsWith("ws://") || raw.startsWith("wss://")) {
            return { type: "ws", url: raw };
        }

        if (raw.startsWith("http://") || raw.startsWith("https://")) {
            return { type: "sse", url: raw };
        }

        warn("unknown url scheme:", raw);
        return null;
    }

    const AUTO_URLS = [
        { type: "ws",  url: PRIMARY_WS },
        { type: "ws",  url: BACKUP_WS },
        { type: "sse", url: BACKUP_SSE }
    ];

    /* ---------------- cleanup ---------------- */

    function cleanup() {
        if (connectTimer) {
            clearTimeout(connectTimer);
            connectTimer = null;
        }

        if (heartbeatTimer) {
            clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }

        if (transport) {
            try {
                if (transportType === "ws") {
                    transport.onopen =
                    transport.onclose =
                    transport.onerror =
                    transport.onmessage = null;
                    transport.close();
                } else if (transportType === "sse") {
                    transport.onopen =
                    transport.onerror =
                    transport.onmessage = null;
                    transport.close();
                }
            } catch {}
        }

        transport = null;
        transportType = null;
        connecting = false;
    }

    /* ---------------- connect ---------------- */

    function connect() {
        if (connecting) return;
        connecting = true;

        cleanup();

        const target = forced
            ? forcedUrl
            : AUTO_URLS[urlIndex];

        log("connecting →", target.type, target.url);

        lastMessageAt = Date.now();

        if (target.type === "ws") {
            connectWS(target.url);
        } else {
            connectSSE(target.url);
        }
    }

    function connectWS(url) {
        transportType = "ws";
        const ws = new WebSocket(url);
        transport = ws;

        connectTimer = setTimeout(() => {
            warn("ws connect timeout");
            forceReconnect();
        }, CONNECT_TIMEOUT);

        ws.onopen = () => {
            clearTimeout(connectTimer);
            connectTimer = null;

            log("ws connected");
            attempt = 0;
            connecting = false;

            emit("heat:open");

            heartbeatTimer = setInterval(heartbeatCheck, HEARTBEAT_CHECK_INTERVAL);
        };

        ws.onmessage = (e) => {
            lastMessageAt = Date.now();
            handleMessage(e.data);
        };

        ws.onerror = (e) => {
            warn("ws error", e);
            forceReconnect();
        };

        ws.onclose = (e) => {
            warn("ws closed", e.code);
            emit("heat:close", e);
            forceReconnect();
        };
    }

    function connectSSE(url) {
        transportType = "sse";
        const es = new EventSource(url);
        transport = es;

        connectTimer = setTimeout(() => {
            warn("sse connect timeout");
            forceReconnect();
        }, CONNECT_TIMEOUT);

        es.onopen = () => {
            clearTimeout(connectTimer);
            connectTimer = null;

            log("sse connected");
            attempt = 0;
            connecting = false;

            emit("heat:open");

            heartbeatTimer = setInterval(heartbeatCheck, HEARTBEAT_CHECK_INTERVAL);
        };

        es.onmessage = (e) => {
            lastMessageAt = Date.now();
            handleMessage(e.data);
        };

        es.onerror = (e) => {
            warn("sse error");
            forceReconnect();
        };
    }

    function handleMessage(raw) {
        try {
            const data = JSON.parse(raw);
            if (data.type !== "click") return;
            emit("heat:message", data);
        } catch {
            warn("bad json", raw);
        }
    }

    /* ---------------- heartbeat ---------------- */

    function heartbeatCheck() {
        const delta = Date.now() - lastMessageAt;
        if (delta > HEARTBEAT_TIMEOUT) {
            log("idle");
            lastMessageAt = Date.now();
        }
    }

    /* ---------------- reconnect ---------------- */

    function forceReconnect() {
        if (reconnectTimer) return;

        cleanup();

        if (!forced) {
            urlIndex = (urlIndex + 1) % AUTO_URLS.length;
        }

        attempt++;

        const delay = Math.min(
            BASE_DELAY * 2 ** attempt + Math.random() * 500,
            MAX_DELAY
        );

        warn(`reconnecting in ${delay}ms`);

        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connect();
        }, delay);
    }

    /* ---------------- init ---------------- */

    forcedUrl = parseForcedUrl();

    window.addEventListener("beforeunload", cleanup);

    connect();
})();
