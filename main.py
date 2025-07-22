import logging.config
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette import status
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

from config import settings
from database.database import async_engine
from dependencies import init_and_startup
from routers.routers import api_router, user_router

import sentry_sdk
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration

from utils.logging_conf import LOGGING_CONFIG

sentry_sdk.init(
    dsn=str(settings.sentry_dsn),
    integrations=[StarletteIntegration(), FastApiIntegration()],
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_and_startup()
    yield
    await async_engine.dispose()

app = FastAPI(lifespan=lifespan)

@app.exception_handler(RequestValidationError)
async def sentry_request_validation_handler(request: Request, exc: RequestValidationError):
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("type", "request_validation_error")
        scope.set_extra("path", str(request.url))
        scope.set_extra("errors", exc.errors())
        try:
            body = await request.body()
            scope.set_extra("raw_body", body.decode("utf-8", errors="replace"))
        except Exception:
            pass
        sentry_sdk.capture_exception(exc)
    return await request_validation_exception_handler(request, exc)

app.add_middleware(SessionMiddleware, secret_key="some-secret-key")  # FIXME secret_key
app.include_router(router=api_router)
app.include_router(router=user_router)
app.mount("/static", StaticFiles(directory="static"), name="static")

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


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