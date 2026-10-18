#!/usr/bin/env bash
# Бэкап БД twitch_bot перед миграцией Alembic. Запускать НА СЕРВЕРЕ (vds-msk), из корня репо.
#
# Пример:
#   ./backup-before-migrate.sh add_cookie_column && docker exec twitch-arbuz-bot-app-1 alembic upgrade head
#
# Дамп кладётся в ~/backup/postgres/twitch_bot_pre-mig_<дата>_<лейбл>.dump и НЕ ротируется
# (ежедневные бэкапы ротирует pg-backup.timer, pre-mig — вечные).
#
# Откат после неудачной миграции:
#   docker stop twitch-arbuz-bot-app-1
#   docker exec -i postgres pg_restore -U quantum0 -d twitch_bot --clean --if-exists \
#       < ~/backup/postgres/twitch_bot_pre-mig_<...>.dump
#   docker start twitch-arbuz-bot-app-1
set -euo pipefail

CONTAINER="${PG_CONTAINER:-postgres}"
PGUSER="${PG_USER:-quantum0}"
DB="${PG_DB:-twitch_bot}"
DEST_DIR="${HOME}/backup/postgres"

LABEL="$(printf '%s' "${1:-manual}" | tr -c 'a-zA-Z0-9_.-' '_' | sed 's/_+$//')"
STAMP="$(date +%F_%H%M)"
DEST="$DEST_DIR/twitch_bot_pre-mig_${STAMP}_${LABEL}.dump"

mkdir -p "$DEST_DIR"

echo "[backup-before-migrate] pg_dump $DB -> $DEST"
if ! docker exec "$CONTAINER" pg_dump -U "$PGUSER" -Fc "$DB" > "${DEST}.tmp"; then
    echo "[backup-before-migrate] ERROR: pg_dump failed" >&2
    rm -f "${DEST}.tmp"
    exit 1
fi
mv "${DEST}.tmp" "$DEST"

# проверка, что дамп читается
if ! docker exec -i "$CONTAINER" pg_restore -U "$PGUSER" --list < "$DEST" > /dev/null; then
    echo "[backup-before-migrate] ERROR: дамп не прошёл верификацию: $DEST" >&2
    exit 1
fi

echo "[backup-before-migrate] OK: $(du -h "$DEST" | cut -f1)"
echo "[backup-before-migrate] Откат при неудачной миграции:"
echo "  docker stop twitch-arbuz-bot-app-1"
echo "  docker exec -i $CONTAINER pg_restore -U $PGUSER -d $DB --clean --if-exists < $DEST"
echo "  docker start twitch-arbuz-bot-app-1"
