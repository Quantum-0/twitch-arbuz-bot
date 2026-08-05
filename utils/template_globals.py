from functools import lru_cache
from pathlib import Path

_STATIC_DIR = Path("static")


@lru_cache(maxsize=128)
def _mtime(path: str) -> int:
    full = _STATIC_DIR / path.lstrip("/")
    try:
        return int(full.stat().st_mtime)
    except OSError:
        return 0


def static_url(path: str) -> str:
    """Возвращает URL статического файла с cache-busting-параметром ?v=<mtime>."""
    v = _mtime(path)
    sep = "&" if "?" in path else "?"
    return f"/static/{path.lstrip('/')}{sep}v={v}" if v else f"/static/{path.lstrip('/')}"


def register_template_globals(templates) -> None:
    """Регистрирует общие Jinja2-глобалы для экземпляра Jinja2Templates."""
    templates.env.globals["static_url"] = static_url
