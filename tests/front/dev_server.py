"""Дев-сервер фронтенда без бэка: БД/Redis/MQTT не нужны.

    poetry run python -m tests.front.dev_server [--port 8099]

Открываете напечатанные URL в браузере и кликаете: страницы рендерятся из
мок-каталога tests/front/pages.py (на каждый запрос — правите шаблон, жмёте F5),
/api/* отвечает моками (тумблеры «сохраняются», тосты работают), /static/*
отдаётся из репозитория. Внешние CDN (Font Awesome, Chart.js) грузятся по сети
как обычно — это ручной режим, не тестовый.
"""

import argparse

from tests.front import pages
from tests.front.server import FrontServer


def main() -> None:
    parser = argparse.ArgumentParser(description="Фронт без бэка: страницы с мок-данными")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()

    server = FrontServer(port=args.port)
    print("Фронт без бэка (мок-данные, /api отвечает заглушками):")
    for spec in pages.PAGES:
        print(f"  {server.base_url.rstrip('/')}{spec.url_path}")
    print("Витрина компонентов q0ui:")
    print(f"  {server.base_url}ui-demo")
    print("\nCtrl+C — остановить.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")


if __name__ == "__main__":
    main()
