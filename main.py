import logging

import uvicorn
from fastapi import FastAPI, HTTPException
from starlette import status
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from config import settings
from twitch.bot import ChatBot
from routers.routers import api_router, user_router
from twitch.twitch import Twitch

import sentry_sdk
sentry_sdk.init(str(settings.sentry_dsn))

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="some-secret-key")  # FIXME secret_key
app.include_router(router=api_router)
app.include_router(router=user_router)

logger = logging.getLogger()

@app.on_event("startup")
async def startup_event():
    await Twitch().startup()
    await ChatBot().startup()
    await ChatBot().update_bot_channels()

@app.exception_handler(Exception)
async def http_exception_handler(request: Request, exc: Exception):
    if "/api/user" in request.url.path:
        return JSONResponse(
            content={
                "title": "Простите, но всё сломалося",
                "message": "Создатель сервиса дурачок и не обработал ошибку:<br>" + str(exc)
            },
            status_code=500,
        )
    if request.url.path == "/panel" and isinstance(exc, HTTPException):
        if exc.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
            return RedirectResponse(url="/login")
    raise

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        log_level="debug",
        reload=True,
    )