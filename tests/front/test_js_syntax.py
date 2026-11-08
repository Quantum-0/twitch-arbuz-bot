"""Синтаксис всего static/js проверяется в настоящем Chromium:

модули (import/export) — через динамический import(), классические скрипты —
через new Function (чистый парс, код не исполняется)."""

from pathlib import Path

from playwright.async_api import Page

JS_DIR = Path("static/js")
JS_FILES = sorted(str(p.relative_to(JS_DIR)).replace("\\", "/") for p in JS_DIR.rglob("*.js"))

PARSE_JS = """
    async (files) => {
        const out = [];
        for (const rel of files) {
            const url = '/static/js/' + rel;
            try {
                const src = await (await fetch(url)).text();
                if (/^\\s*(import|export)\\b/m.test(src)) {
                    await import(url);
                } else {
                    new Function(src);
                }
            } catch (e) {
                out.push(rel + ': ' + (e && e.message ? e.message : String(e)));
            }
        }
        return out;
    }
"""


async def test_all_static_js_parse(front_page: Page, front_server_url: str) -> None:
    assert JS_FILES, "static/js пуст — что-то не так с репозиторием"
    await front_page.goto(front_server_url + "blank")
    failures = await front_page.evaluate(PARSE_JS, JS_FILES)
    assert not failures, "Синтаксические/импортные ошибки в JS:\n" + "\n".join(failures)
