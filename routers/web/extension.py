from dependency_injector.wiring import inject
from fastapi import APIRouter
from starlette.responses import FileResponse

router = APIRouter(prefix="/extension", tags=["Twitch Extension"])


@router.get("/panel.html", response_class=FileResponse)
@inject
async def get_twitch_panel():
    return FileResponse(
        "static/ext_panel/panel.html",
        headers={
            "Content-Security-Policy": "frame-ancestors 'self' https://*.twitch.tv https://twitch.tv http://localhost:* https://localhost:*",
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Access-Control-Allow-Origin": "*"
        }
    )


@router.get("/control.html", response_class=FileResponse)
@inject
async def get_twitch_control():
    return FileResponse("static/ext_panel/panel.html", headers={"Cache-Control": "public, max-age=604800"})