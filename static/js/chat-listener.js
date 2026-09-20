(function () {
    "use strict";

    // Глобали, которые должны быть определены в HTML до подключения этого скрипта:
    //   CHANNEL_ID    — Twitch channel ID (number)
    //   CHANNEL_NAME  — Twitch login name (string, для IRC fallback)
    //   CHAT_FILTER   — regex-строка для фильтрации сообщений (string)
    //   OVERLAY_TYPE  — "star" | "fireworks" (для temp-commands registration)
    //   OVERLAY_SECRET — overlay_secret пользователя (string | null)

    const SSE_URL = `/sse/${CHANNEL_ID}/msg?filter=${encodeURIComponent(CHAT_FILTER)}`;

    const BASE_DELAY = 1000;
    const MAX_DELAY = 20000;
    const SSE_SILENCE_TIMEOUT = 45000; // Если SSE молчит 45с — стартуем IRC fallback
    const IRC_RECONNECT_DELAY = 5000;

    let filterRe = null;
    try {
        filterRe = new RegExp(CHAT_FILTER, "i");
    } catch (e) {
        console.warn("[ChatListener] Invalid CHAT_FILTER regex:", e);
    }

    let sseTransport = null;
    let ircClient = null;
    let ircLoaded = false;
    let ircConnecting = false;

    let sseReconnectTimer = null;
    let sseSilenceTimer = null;
    let sseAttempt = 0;
    let sseLastMessageAt = 0;
    let sseConnecting = false;

    let ircReconnectTimer = null;

    /* ---------------- utils ---------------- */

    function log(...args) {
        console.log("[ChatListener]", ...args);
    }

    function warn(...args) {
        console.warn("[ChatListener]", ...args);
    }

    function emit(detail) {
        window.dispatchEvent(new CustomEvent("chat:message", {detail}));
    }

    /* ---------------- SSE ---------------- */

    function connectSSE() {
        if (sseConnecting) return;
        sseConnecting = true;

        cleanupSSE();

        log("SSE connecting →", SSE_URL);
        sseLastMessageAt = Date.now();

        const es = new EventSource(SSE_URL);
        sseTransport = es;

        es.onopen = () => {
            if (es !== sseTransport) return;
            log("SSE connected");
            sseAttempt = 0;
            sseConnecting = false;
            sseLastMessageAt = Date.now();
            stopIRC();
            startSilenceTimer();
        };

        es.onmessage = (e) => {
            if (es !== sseTransport) return;
            sseLastMessageAt = Date.now();
            handleSSEMessage(e.data);
        };

        es.onerror = () => {
            if (es !== sseTransport) return;
            warn("SSE error, reconnecting...");
            forceSSEReconnect();
        };
    }

    function handleSSEMessage(raw) {
        try {
            const data = JSON.parse(raw);
            // SSE уже отфильтрован сервером, но на всякий случай проверим локально
            if (filterRe && !filterRe.test(data.text || "")) return;
            emit(data);
        } catch {
            warn("bad SSE json:", raw);
        }
    }

    function startSilenceTimer() {
        if (sseSilenceTimer) clearTimeout(sseSilenceTimer);
        sseSilenceTimer = setTimeout(() => {
            sseSilenceTimer = null;
            if (sseTransport && Date.now() - sseLastMessageAt > SSE_SILENCE_TIMEOUT) {
                warn(`SSE silent for ${SSE_SILENCE_TIMEOUT}ms, starting IRC fallback`);
                startIRC();
            } else if (sseTransport) {
                startSilenceTimer();
            }
        }, SSE_SILENCE_TIMEOUT);
    }

    function cleanupSSE() {
        if (sseSilenceTimer) {
            clearInterval(sseSilenceTimer);
            sseSilenceTimer = null;
        }
        if (sseReconnectTimer) {
            clearTimeout(sseReconnectTimer);
            sseReconnectTimer = null;
        }
        if (sseTransport) {
            try {
                sseTransport.onopen = null;
                sseTransport.onerror = null;
                sseTransport.onmessage = null;
                sseTransport.close();
            } catch (e) {
                warn("SSE cleanup error:", e);
            }
        }
        sseTransport = null;
        sseConnecting = false;
    }

    function forceSSEReconnect() {
        if (sseReconnectTimer) return;
        cleanupSSE();

        sseAttempt++;
        const delay = Math.min(
            BASE_DELAY * Math.pow(2, sseAttempt) + Math.random() * 500,
            MAX_DELAY
        );
        warn(`SSE reconnecting in ${Math.round(delay)}ms (attempt ${sseAttempt})`);

        sseReconnectTimer = setTimeout(() => {
            sseReconnectTimer = null;
            connectSSE();
        }, delay);
    }

    /* ---------------- IRC fallback (tmi.js) ---------------- */

    function loadTmiJS(callback) {
        if (ircLoaded || typeof tmi !== "undefined") {
            callback();
            return;
        }
        const script = document.createElement("script");
        script.src = "https://github.com/tmijs/tmi.js/releases/download/v1.8.5/tmi.min.js";
        script.onload = () => {
            ircLoaded = true;
            callback();
        };
        script.onerror = () => {
            warn("Failed to load tmi.js");
        };
        document.head.appendChild(script);
    }

    function startIRC() {
        if (ircClient || ircConnecting) return;
        if (!CHANNEL_NAME) {
            warn("No CHANNEL_NAME for IRC fallback");
            return;
        }

        ircConnecting = true;
        loadTmiJS(() => {
            if (ircConnecting === false) return; // Already stopped

            try {
                const client = new tmi.Client({
                    channels: [CHANNEL_NAME],
                    options: { skipMembership: true },
                });
                ircClient = client;
                ircConnecting = false;

                client.on("message", (channel, tags, message, self) => {
                    if (self) return;
                    handleMessageIRC(message, tags);
                });

                client.connect().catch((e) => {
                    warn("IRC connect error:", e);
                    scheduleIRCReconnect();
                });

                log("IRC fallback started for channel:", CHANNEL_NAME);
            } catch (e) {
                warn("IRC init error:", e);
                ircConnecting = false;
                scheduleIRCReconnect();
            }
        });
    }

    function handleMessageIRC(message, tags) {
        // Client-side regex-фильтрация
        if (filterRe && !filterRe.test(message)) return;

        // Определяем роль из badges (tmi.js format: tags.badges = {broadcaster:"1", moderator:"1", ...})
        const badges = tags.badges || {};
        let role = "chatter";
        if (badges.broadcaster) role = "streamer";
        else if (badges.moderator) role = "moderator";
        else if (badges.vip) role = "vip";
        else if (badges.subscriber || badges.founder) role = "subscriber";
        else if (badges.bot) role = "bot";

        emit({
            text: message,
            username: tags.username || tags["user-id"] || "",
            display_name: tags["display-name"] || tags.username || "",
            color: tags.color || "",
            role: role,
        });
    }

    function stopIRC() {
        if (ircReconnectTimer) {
            clearTimeout(ircReconnectTimer);
            ircReconnectTimer = null;
        }
        if (ircClient) {
            try {
                ircClient.removeAllListeners();
                ircClient.disconnect();
            } catch (e) {
                warn("IRC disconnect error:", e);
            }
            ircClient = null;
        }
        ircConnecting = false;
    }

    function scheduleIRCReconnect() {
        if (ircReconnectTimer) return;
        ircReconnectTimer = setTimeout(() => {
            ircReconnectTimer = null;
            ircClient = null;
            ircConnecting = false;
            startIRC();
        }, IRC_RECONNECT_DELAY);
    }

    /* ---------------- temp-commands registration ---------------- */

    function registerTempCommands() {
        if (!OVERLAY_SECRET || OVERLAY_SECRET === "None" || OVERLAY_SECRET === "null") {
            log("No overlay_secret, skipping temp-commands registration");
            return;
        }

        if (!CUSTOM_COMMANDS || !CUSTOM_COMMANDS.length) {
            log("No CUSTOM_COMMANDS defined, skipping registration");
            return;
        }

        for (const cmd of CUSTOM_COMMANDS) {
            fetch("/api/user/overlay/custom-commands", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Overlay-Secret": OVERLAY_SECRET,
                },
                body: JSON.stringify({
                    channel_id: parseInt(CHANNEL_ID, 10),
                    name: cmd.name,
                    aliases: cmd.aliases,
                    description: cmd.description,
                }),
            }).catch((e) => {
                warn("custom-commands registration failed for", cmd.name, e);
            });
        }
    }

    /* ---------------- init ---------------- */

    window.addEventListener("beforeunload", () => {
        cleanupSSE();
        stopIRC();
    });

    // Регистрируем temp-commands сразу и каждые 5 минут (TTL=15мин).
    registerTempCommands();
    setInterval(registerTempCommands, 5 * 60 * 1000);

    // Стартуем SSE — основным транспортом.
    // Если SSE молчит — IRC fallback включится автоматически.
    connectSSE();
})();
