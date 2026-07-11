from dependency_injector.wiring import inject
from fastapi import APIRouter
from starlette.responses import FileResponse

router = APIRouter(prefix="/extension", tags=["Twitch Extension"])


@router.get("/panel.html", response_class=FileResponse)
async def get_twitch_panel():
    return FileResponse(
        "static/ext_panel/panel.html",
    )


@router.get("/control.html", response_class=FileResponse)
async def get_twitch_control():
    return FileResponse("static/ext_panel/panel.html", headers={"Cache-Control": "public, max-age=604800"})