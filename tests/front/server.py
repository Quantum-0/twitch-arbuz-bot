"""Мини-HTTP-сервер фронтенда без бэка.

Отдаёт:
  /static/*   — статика из репозитория;
  /__front/*  — ассет-страницы тестов (tests/front/static/);
  /api/*      — мок-ответы (api_mocks);
  /blank      — пустая страница (полигон для evaluate);
  прочие пути — страницы из мок-каталога tests/front/pages.py.

Ничего не пишет на диск и не делает сетевых вызовов наружу: рендер в память,
статика и моки локальны. Используется браузерными тестами (порт 0) и
дев-сервером dev_server.py.
"""

import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from tests.front import api_mocks, pages

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "static"
FRONT_STATIC_DIR = Path(__file__).resolve().parent / "static"

# Расширение → Content-Type для статики.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".json": "application/json",
    ".txt": "text/plain",
}


class FrontServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), FrontHandler)
        self.base_url = f"http://127.0.0.1:{self.server_address[1]}/"


class FrontHandler(BaseHTTPRequestHandler):
    server: FrontServer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    # ── HTTP-методы ───────────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path.startswith("/static/"):
            self._serve_file(STATIC_DIR, path[len("/static/") :])
        elif path.startswith("/__front/"):
            self._serve_file(FRONT_STATIC_DIR, path[len("/__front/") :])
        elif path == "/favicon.ico":
            self._send(204, "text/plain", b"")
        elif path == "/blank":
            self._send(200, "text/html", b"<!DOCTYPE html><html><body></body></html>")
        elif path.startswith("/api/"):
            status, body = api_mocks.default_for("GET", path)
            self._send_json(status, body)
        elif path in pages.PAGES_BY_PATH:
            self._serve_page(path)
        else:
            self._send(404, "text/plain", f"Нет такой страницы в мок-каталоге: {path}".encode())

    def do_POST(self) -> None:  # noqa: N802
        self._handle_api()

    def do_PUT(self) -> None:  # noqa: N802
        self._handle_api()

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle_api()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle_api()

    # ── внутреннее ────────────────────────────────────────────────────────────

    def _handle_api(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)  # тело не важно — отвечаем моком
        status, body = api_mocks.default_for(self.command, urlsplit(self.path).path)
        self._send_json(status, body)

    def _serve_page(self, path: str) -> None:
        try:
            html = pages.render_page(path, self.server.base_url)
        except Exception:
            # Разработчику сразу видна причина (dev-сервер без бэка).
            self._send(500, "text/html; charset=utf-8", f"<pre>{traceback.format_exc()}</pre>".encode())
            return
        self._send(200, "text/html; charset=utf-8", html.encode())

    def _serve_file(self, base: Path, rel_path: str) -> None:
        full = (base / unquote(rel_path)).resolve()
        # Защита от path traversal: файл обязан лежать внутри базовой папки.
        if not str(full).startswith(f"{base.resolve()}/") or not full.is_file():
            self._send(404, "text/plain", b"not found")
            return
        content_type = CONTENT_TYPES.get(full.suffix.lower(), "application/octet-stream")
        self._send(200, content_type, full.read_bytes())

    def _send_json(self, status: int, body: dict) -> None:
        self._send(status, "application/json", json.dumps(body, ensure_ascii=False).encode())

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
