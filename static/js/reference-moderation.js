document.querySelectorAll('.reference-preview').forEach(preview => {
    preview.addEventListener('click', () => {
        const card = preview.closest('.reference-moderation-card');
        const description = card.querySelector('.reference-description');
        const expanded = preview.getAttribute('aria-expanded') === 'true';
        preview.setAttribute('aria-expanded', String(!expanded));
        card.classList.toggle('expanded', !expanded);
        description.hidden = expanded;
    });
});

document.querySelectorAll('.reference-decision').forEach(button => {
    button.addEventListener('click', async () => {
        const card = button.closest('.reference-moderation-card');
        const approved = button.dataset.approved === 'true';
        card.querySelectorAll('.reference-decision').forEach(item => item.disabled = true);
        try {
            const response = await fetch(`/api/moderation/references/${card.dataset.referenceId}`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({approved}),
            });
            if (!response.ok) throw new Error('Не удалось сохранить решение');
            card.querySelector('.reference-approve').classList.toggle('selected', approved);
            card.querySelector('.reference-reject').classList.toggle('selected', !approved);
            card.querySelector('.reference-status').textContent = approved ? 'Одобрено' : 'Отклонено';
            showNotification('Сохранено', approved ? 'Референс одобрен.' : 'Референс отклонён.');
        } catch (error) {
            showNotification('Ошибка', error.message, true);
        } finally {
            card.querySelectorAll('.reference-decision').forEach(item => item.disabled = false);
        }
    });
});
