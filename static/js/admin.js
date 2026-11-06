/* Админ-панель (/admin): бета-тестеры + пополнение баланса стримера. */
(() => {
    "use strict";

    const el = (id) => document.getElementById(id);

    async function addToBetaTest() {
        const input = el("twitchLoginInput");
        const status = el("statusMessage");
        const twitchLogin = input.value.trim();

        if (!twitchLogin) {
            status.textContent = "Пожалуйста, введите Twitch логин.";
            status.style.color = "orange";
            return;
        }

        try {
            const response = await fetch(`/api/admin/add_to_beta_test?twitch_login=${encodeURIComponent(twitchLogin)}`, {
                method: "POST",
            });

            const data = await response.json();

            if (data.success === 1) {
                status.textContent = `Пользователь "${twitchLogin}" успешно добавлен в бета-тестеры.`;
                status.style.color = "green";
                input.value = "";
            } else {
                status.textContent = `Не удалось добавить пользователя "${twitchLogin}".`;
                status.style.color = "red";
            }
        } catch (error) {
            status.textContent = "Ошибка при выполнении запроса.";
            status.style.color = "red";
            console.error(error);
        }
    }

    async function showBalance(login) {
        const status = el("depositStatus");
        try {
            const response = await fetch(`/api/admin/balance?twitch_login=${encodeURIComponent(login)}`);
            if (!response.ok) {
                status.textContent = "";
                return;
            }
            const data = await response.json();
            status.style.color = "";
            status.textContent = `Текущий баланс ${data.twitch_login}: ${data.balance}`;
        } catch (error) {
            console.error(error);
        }
    }

    async function depositBalance() {
        const login = el("depositLoginInput").value.trim().replace(/^@/, "");
        const amount = Number(el("depositAmount").value);
        const status = el("depositStatus");

        if (!login) {
            status.textContent = "Укажите логин стримера.";
            status.style.color = "orange";
            return;
        }
        if (!Number.isFinite(amount) || amount <= 0 || amount > 500) {
            status.textContent = "Укажите сумму числом от 1 до 500.";
            status.style.color = "orange";
            return;
        }

        try {
            const response = await fetch("/api/admin/balance/deposit", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({twitch_login: login, amount}),
            });
            const data = await response.json();
            if (!response.ok) {
                // 422 отдаёт detail массивом объектов ошибках валидации
                const detail = Array.isArray(data.detail)
                    ? "Некорректные данные: проверьте логин и сумму (1–500)."
                    : data.detail;
                status.textContent = detail || "Не удалось пополнить баланс.";
                status.style.color = "red";
                return;
            }
            if (Number(data.deposited) === 0) {
                status.style.color = "orange";
                status.textContent = `Ничего не начислено: баланс ${data.twitch_login} уже на максимуме (или сумма превышает лимит). Баланс: ${data.balance}`;
            } else {
                status.style.color = "green";
                status.textContent = `Пополнено ${data.deposited}, новый баланс ${data.twitch_login}: ${data.balance}`;
                el("depositAmount").value = "";
            }
        } catch (error) {
            status.textContent = "Ошибка при выполнении запроса.";
            status.style.color = "red";
            console.error(error);
        }
    }

    function init() {
        el("betaTestBtn").addEventListener("click", addToBetaTest);
        el("depositBtn").addEventListener("click", depositBalance);
        // Подсказки пользователей для пополнения (без исключений — админ может всем)
        if (window.UserSuggest) {
            window.UserSuggest.attach(el("depositLoginInput"), {
                context: "deposit",
                onSelect: (item) => showBalance(item.value),
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
