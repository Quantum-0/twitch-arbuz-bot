function showNotification(title, message, isError = false) {
    let container = document.querySelector(".notification-container");
    if (!container) {
        container = document.createElement("div");
        container.className = "notification-container";
        document.body.appendChild(container);
    }
    const div = document.createElement("div");
    div.className = "notification" + (isError ? " error" : "");
    const header = document.createElement("div");
    header.className = "notification-header";
    header.textContent = title;
    const body = document.createElement("div");
    body.className = "notification-body";
    body.textContent = message;
    div.append(header, body);
    container.appendChild(div);
    setTimeout(() => {
        div.style.animation = "fadeOutDown var(--notif-out-duration) forwards";
        setTimeout(() => div.remove(), 250);
    }, 4000);
}
