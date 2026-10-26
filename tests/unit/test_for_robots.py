# ruff: noqa: S101, S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Тесты routers/robots/for_robots.py: sitemap.xml и robots.txt."""

import re
from xml.etree import ElementTree as ET

import pytest
from httpx import ASGITransport, AsyncClient

from main import app
from routers.robots.for_robots import generate_sitemap, get_disallowed_patterns_from_robots

NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


@pytest.fixture
async def client(session_override):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def parse_locs(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)  # noqa: S314
    return [el.text for el in root.iter(f"{NS}loc") if el.text]


class TestDisallowedPatterns:
    def test_reads_real_robots_txt(self):
        patterns = get_disallowed_patterns_from_robots()
        assert "/api/*" in patterns  # путь с / получает * для fnmatch
        assert "/metrics" in patterns
        assert "/login-callback" in patterns
        assert all(not p.endswith("/") for p in patterns)

    def test_missing_file_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        assert get_disallowed_patterns_from_robots() == []


class TestSitemap:
    async def test_returns_xml(self, client):
        resp = await client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/xml")

    async def test_public_pages_present(self, client):
        resp = await client.get("/sitemap.xml")
        locs = parse_locs(resp.text)
        assert "http://test/" in locs
        assert "http://test/streamers" in locs
        assert "http://test/faq" in locs

    async def test_disallowed_paths_excluded(self, client):
        resp = await client.get("/sitemap.xml")
        locs = parse_locs(resp.text)
        assert not any("/api/" in loc for loc in locs)
        assert not any(loc.endswith("/metrics") for loc in locs)
        assert "http://test/login" not in locs
        assert "http://test/login-callback" not in locs

    async def test_no_brace_params_in_locs(self, client):
        resp = await client.get("/sitemap.xml")
        locs = parse_locs(resp.text)
        assert all("{" not in loc and "}" not in loc for loc in locs)

    async def test_priorities(self, client):
        resp = await client.get("/sitemap.xml")
        root = ET.fromstring(resp.text)  # noqa: S314
        priorities = {url.find(f"{NS}loc").text: url.find(f"{NS}priority").text for url in root.findall(f"{NS}url")}
        assert priorities["http://test/"] == "1"
        assert priorities["http://test/streamers"] == "1"
        assert "0.5" in priorities.values()

    def test_port_included_in_base_url(self):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "scheme": "http",
            "server": ("testserver", 8080),
            "http_version": "1.1",
            "method": "GET",
            "path": "/sitemap.xml",
            "query_string": b"",
            "headers": [],
            "root_path": "",
            "app": app,
            "client": ("127.0.0.1", 12345),
        }
        response = generate_sitemap(Request(scope))
        locs = parse_locs(response.body.decode("utf-8"))
        assert all(loc.startswith("http://testserver:8080") for loc in locs)

    def test_standard_ports_omitted(self):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "scheme": "https",
            "server": ("testserver", 443),
            "http_version": "1.1",
            "method": "GET",
            "path": "/sitemap.xml",
            "query_string": b"",
            "headers": [],
            "root_path": "",
            "app": app,
            "client": ("127.0.0.1", 12345),
        }
        response = generate_sitemap(Request(scope))
        assert re.search(rb"<loc>https://testserver/</loc>", response.body)


class TestRobotsTxt:
    async def test_serves_static_file(self, client):
        resp = await client.get("/robots.txt")
        assert resp.status_code == 200
        assert "Disallow: /api/" in resp.text
        assert "Sitemap:" in resp.text
