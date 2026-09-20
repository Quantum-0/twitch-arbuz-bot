function showNotification(title, message, isError = false) {
    let container = document.querySelector(".notification-container");
    if (!container) {
        container = document.createElement("div");
        container.className = "notification-container";
        document.body.appendChild(container);
    }
    const div = document.createElement("div");
    div.className = "notification" + (isError ? " error" : "");
    div.innerHTML = `<div class="notification-header">${title}</div><div class="notification-body">${message}</div>`;
    container.appendChild(div);
    setTimeout(() => {
        div.style.animation = "fadeOutDown var(--notif-out-duration) forwards";
        setTimeout(() => div.remove(), 250);
    }, 4000);
}
