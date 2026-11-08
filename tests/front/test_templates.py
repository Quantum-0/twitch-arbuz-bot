"""Рендер-слой: все шаблоны компилируются, страницы каталога рендерятся
в StrictUndefined (обращение к несуществующей переменной = провал)."""

from pathlib import Path

import pytest

from tests.front import pages

TEMPLATES_DIR = Path("templates")
ALL_TEMPLATES = sorted(str(p.relative_to(TEMPLATES_DIR)).replace("\\", "/") for p in TEMPLATES_DIR.rglob("*.html"))


@pytest.mark.parametrize("template", ALL_TEMPLATES, ids=ALL_TEMPLATES)
def test_template_syntax(template: str) -> None:
    """Jinja-синтаксис каждого шаблона (включая legal/overlays/docks/partials)."""
    pages.TEMPLATES_ENV.get_template(template)  # бросает TemplateSyntaxError


@pytest.mark.parametrize("spec", pages.PAGES, ids=[s.url_path for s in pages.PAGES])
def test_page_renders_strict(spec: pages.PageSpec) -> None:
    """Полный рендер страницы каталога: ловит синтаксис, отсутствующие
    контекст-переменные и падения фильтров (StrictUndefined)."""
    html = pages.render_page(spec.url_path, "http://mock.local/")
    assert len(html) > 500, "подозрительно пустой результат рендера"
    assert "<html" in html.lower()


def test_page_markers() -> None:
    """Смоук-маркеры: в отрендеренных страницах есть ключевые конструкции."""
    for url_path, markers in pages.PAGE_MARKERS.items():
        html = pages.render_page(url_path, "http://mock.local/")
        for marker in markers:
            assert marker in html, f"{url_path}: в HTML нет маркера {marker!r}"
