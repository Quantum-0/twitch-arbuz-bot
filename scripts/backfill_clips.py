"""Разовый бэкфилл клипов и ярких моментов в Telegram за период недоступности TG-сервиса.

Контекст (ноябрь 2026): TG-сервис был недоступен (тикет FirstVDS 15045113,
с 2026-11-09), доставка клипов fire-and-forget без подтверждения — клипы за окно
простоя терялись. Поллер идёт по курсору ``last_clip_date`` и назад не
возвращается, поэтому скрипт перезапрашивает клипы за период у Twitch Helix и
отправляет их в clips-чат теми же payload'ами, что и поллер
(``ClipsPollerService._send_clip`` / ``_send_highlight``).

Особенности:
- Клипы, которые за окно всё же успели дойти, уйдут повторно (истории доставок
  нет) — дубликаты согласованы.
- Курсоры ``last_clip_date`` / ``last_highlight_date`` не трогаются: обычный
  поллинг не сбивается.
- Повторный запуск после сбоя в середине батча повторит уже отправленное.

Запуск (на проде или локально с туннелями к прод-БД и MQTT):

    poetry run python -m scripts.backfill_clips --all \\
        --from 2026-11-09T00:00:00Z --to 2026-11-10T00:00:00Z \\
        [--include-highlights] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import joinedload

from database.database import AsyncSessionLocal
from database.models import TelegramSettings, User
from services.clips_poller import ClipsPollerService
from services.mqtt import MQTTClient
from services.twitch_token_service import TwitchTokenExpiredError, TwitchTokenService

logger = logging.getLogger(__name__)


def parse_dt(value: str) -> datetime:
    """ISO дата/дата-время → naive UTC ('2026-11-09', '2026-11-09T00:00:00Z', '+03:00'-зоны)."""
    normalized = value.strip().replace(" ", "T")
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def window_items(items: list[dict[str, Any]], from_dt: datetime, to_dt: datetime) -> list[dict[str, Any]]:
    """Оставить элементы с created_at в [from_dt, to_dt] (naive UTC)."""
    parse = ClipsPollerService._parse_created_at
    return [item for item in items if from_dt <= parse(item.get("created_at", "")) <= to_dt]


async def select_users(logins: list[str] | None) -> list[User]:
    """Пользователи с включённой рассылкой клипов (как у поллера); при указании — только данные логины."""
    q = (
        sa.select(User)
        .options(joinedload(User.telegram))
        .join(TelegramSettings, TelegramSettings.user_id == User.id)
        .where(
            TelegramSettings.clips_enabled.is_(True),
            TelegramSettings.clips_chat_id.is_not(None),
            User._access_token.is_not(None),
        )
    )
    if logins:
        q = q.where(User.login_name.in_(logins))
    async with AsyncSessionLocal() as db:
        return list((await db.execute(q)).scalars().all())


async def backfill_user(
    service: ClipsPollerService,
    token_service: TwitchTokenService,
    user: User,
    from_dt: datetime,
    to_dt: datetime,
    *,
    include_highlights: bool,
    dry_run: bool,
) -> tuple[int, int]:
    """Бэкфилл одного пользователя. Возвращает (отправлено клипов, отправлено моментов)."""
    tg = user.telegram
    if tg is None or not tg.clips_chat_id:
        print(f"  {user.login_name}: clips-чат не подключён, пропускаем")
        return (0, 0)

    try:
        access_token = await token_service.get_valid_access_token(user)
    except TwitchTokenExpiredError as e:
        print(f"  {user.login_name}: Twitch-токен истёк ({e}), пропускаем — нужно перелогиниться")
        return (0, 0)

    clips = await service._fetch_clips(access_token=access_token, broadcaster_id=user.twitch_id, started_at=from_dt)
    clips = window_items(service._filter_clips(clips, None, tg.clips_mode), from_dt, to_dt)
    sent_clips = 0
    for clip in clips:
        if dry_run:
            print(f"  [dry-run] клип {clip.get('id')} ({clip.get('created_at', '')}): {clip.get('title', '')}")
            continue
        try:
            await service._send_clip(access_token, user.twitch_id, tg.clips_chat_id, clip, tg.clips_delivery or "link")
            sent_clips += 1
        except Exception:
            logger.error("Бэкфилл: не удалось отправить клип %s", clip.get("id"), exc_info=True)

    sent_highlights = 0
    if include_highlights:
        videos = await service._fetch_highlights(access_token=access_token, user_id=user.twitch_id, threshold=from_dt)
        videos = window_items(videos, from_dt, to_dt)
        for video in videos:
            if dry_run:
                print(f"  [dry-run] момент {video.get('id')} ({video.get('created_at', '')}): {video.get('title', '')}")
                continue
            try:
                await service._send_highlight(tg.clips_chat_id, video)
                sent_highlights += 1
            except Exception:
                logger.error("Бэкфилл: не удалось отправить момент %s", video.get("id"), exc_info=True)

    return (sent_clips, sent_highlights)


async def run(args: argparse.Namespace) -> int:
    from_dt: datetime = args.from_dt
    to_dt: datetime = args.to_dt

    mqtt = MQTTClient()
    # Отдельный client_id: одинаковый id с живым ботом = взаимные дисконнекты на брокере.
    mqtt._client_id = f"{mqtt._client_id}-backfill"
    token_service = TwitchTokenService(db_session_factory=AsyncSessionLocal)
    service = ClipsPollerService(
        db_session_factory=AsyncSessionLocal,
        twitch_token_service=token_service,
        mqtt=mqtt,
    )

    users = await select_users(args.logins)
    if args.logins:
        found = {u.login_name for u in users}
        missing = [login for login in args.logins if login not in found]
        if missing:
            print(f"Не найдены (нет включённой рассылки клипов или токена): {', '.join(missing)}")
    if not users:
        print("Подходящих пользователей нет — нечего отправлять.")
        return 0

    mode = " [dry-run]" if args.dry_run else ""
    print(f"Бэкфилл клипов за {from_dt.isoformat()}Z .. {to_dt.isoformat()}Z{mode}, юзеров: {len(users)}")

    total_clips = 0
    total_highlights = 0
    # dry-run ничего не публикует — MQTT-подключение не поднимаем.
    mqtt_ctx = contextlib.nullcontext() if args.dry_run else mqtt.lifespan()
    async with mqtt_ctx:
        for user in users:
            sent_clips, sent_highlights = await backfill_user(
                service,
                token_service,
                user,
                from_dt,
                to_dt,
                include_highlights=args.include_highlights,
                dry_run=args.dry_run,
            )
            total_clips += sent_clips
            total_highlights += sent_highlights
            print(f"  {user.login_name}: отправлено клипов {sent_clips}, моментов {sent_highlights}")

    suffix = " (dry-run, ничего не отправлено)" if args.dry_run else ""
    print(f"Готово: клипов {total_clips}, моментов {total_highlights}{suffix}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Разовый бэкфилл клипов/моментов в Telegram за период недоступности TG-сервиса."
    )
    parser.add_argument(
        "--from",
        dest="from_dt",
        required=True,
        type=parse_dt,
        help="начало окна, ISO (напр. 2026-11-09T00:00:00Z)",
    )
    parser.add_argument("--to", dest="to_dt", type=parse_dt, default=None, help="конец окна (по умолчанию — сейчас)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--login", nargs="+", metavar="LOGIN", help="логины Twitch через пробел")
    group.add_argument("--all", dest="all_users", action="store_true", help="все юзеры с включённой рассылкой клипов")
    parser.add_argument("--include-highlights", action="store_true", help="также яркие моменты за окно")
    parser.add_argument("--dry-run", action="store_true", help="показать, что будет отправлено, без публикации")
    args = parser.parse_args()

    if args.to_dt is None:
        args.to_dt = datetime.now(UTC).replace(tzinfo=None)
    if args.from_dt >= args.to_dt:
        parser.error("--from должен быть раньше --to")
    args.logins = None if args.all_users else list(args.login or [])
    return args


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
