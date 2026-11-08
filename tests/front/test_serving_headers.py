"""Заголовки кэширования ES-модулей: внутренние импорты ui/* не покрыты
cache-busting «?v=» из static_url, поэтому отдаются с no-cache
(см. middleware es_modules_no_cache в main.py)."""

from fastapi.testclient import TestClient

from main import app


def test_es_modules_served_no_cache() -> None:
    client = TestClient(app)

    no_cache_paths = [
        "/static/js/ui/index.js",
        "/static/js/ui/switch.js",
        "/static/js/panel.js",
        "/static/js/panel-telegram.js",
    ]
    for path in no_cache_paths:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers.get("cache-control") == "no-cache", path

    # Остальная статика версионируется через ?v= — остаётся кэшируемой.
    response = client.get("/static/styles.css")
    assert response.status_code == 200
    assert response.headers.get("cache-control") != "no-cache"
