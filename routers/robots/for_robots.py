import fnmatch
import re
from fastapi import Response, Request, APIRouter
from fastapi.openapi.utils import get_openapi
from xml.etree import ElementTree as ET

from starlette.responses import FileResponse

router = APIRouter(prefix="", tags=["Robots"])

def get_disallowed_patterns_from_robots():
    """Reads robots.txt and extracts all Disallow paths."""
    disallowed = []
    try:
        # Читаем локальный файл robots.txt
        with open("static/robots.txt", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.lower().startswith("disallow:"):
                    # Получаем сам путь (например, /api/ или /metrics)
                    path = line.split(":", 1)[1].strip()
                    if path:
                        # Если путь заканчивается на /, добавляем звездочку для fnmatch
                        if path.endswith("/"):
                            path += "*"
                        disallowed.append(path)
    except FileNotFoundError:
        # Если файла нет, возвращаем пустой список
        pass
    return disallowed


@router.get("/sitemap.xml", response_class=Response)
def generate_sitemap(request: Request):
    # 1. Получаем базовый URL (автоматически определит схему и домен, например https://quantum0.ru)
    base_url = f"{request.url.scheme}://{request.url.hostname}"
    if request.url.port and request.url.port not in (80, 443):
        base_url += f":{request.url.port}"

    # 2. Парсим правила из robots.txt
    disallowed_patterns = get_disallowed_patterns_from_robots()

    # 3. Достаем все пути из встроенной схемы OpenAPI (Swagger)
    openapi_schema = get_openapi(
        title=request.app.title,
        version=request.app.version,
        routes=request.app.routes,
    )
    all_paths = openapi_schema.get("paths", {}).keys()

    # 4. Инициализируем XML-структуру sitemap
    xmlns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    root = ET.Element("urlset", xmlns=xmlns)

    for path in all_paths:
        # Игнорируем сам sitemap и роуты документации
        if path in ["/sitemap.xml", "/openapi.json", "/docs", "/redoc"]:
            continue

        # Превращаем пути со swagger-параметрами вроде /items/{item_id} в чистый путь /items/
        # (Поисковикам нельзя сканировать фигурные скобки)
        clean_path = re.sub(r"\{.*?\}", "", path)

        # Проверяем, заблокирован ли путь правилами из robots.txt
        is_disallowed = False
        for pattern in disallowed_patterns:
            if fnmatch.fnmatch(clean_path, pattern) or fnmatch.fnmatch(path, pattern):
                is_disallowed = True
                break

        # Если путь разрешен — добавляем его в sitemap.xml
        if not is_disallowed:
            url_el = ET.SubElement(root, "url")
            ET.SubElement(url_el, "loc").text = f"{base_url}{clean_path}"
            ET.SubElement(url_el, "changefreq").text = "weekly"
            if path in {"/", "/faq", "/about", "/streamers", "/memealerts-tutorial", "/kinda_roadmap"}:
                ET.SubElement(url_el, "priority").text = "1"
            else:
                ET.SubElement(url_el, "priority").text = "0.5"

    # 5. Превращаем дерево элементов в финальную XML-строку
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    xml_string = xml_bytes.decode("utf-8")

    return Response(content=xml_string, media_type="application/xml")


@router.get("/robots.txt", response_class=FileResponse)
async def robots_txt():
    return FileResponse("static/robots.txt")