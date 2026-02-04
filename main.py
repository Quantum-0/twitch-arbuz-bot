import logging.config
from contextlib import asynccontextmanager

import sentry_sdk
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from starlette import status
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, FileResponse
from starlette.staticfiles import StaticFiles

from config import settings
from dependencies import lifespan as lifespan_dep
from routers.routers import api_router, user_router
from utils.logging_conf import LOGGING_CONFIG

from prometheus_fastapi_instrumentator import Instrumentator

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=str(settings.sentry_dsn),
        integrations=[StarletteIntegration(), FastApiIntegration()],
    )
else:
    print("Sentry DSN is not defined!")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with lifespan_dep():
        yield


app = FastAPI(lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def sentry_request_validation_handler(
    request: Request, exc: RequestValidationError
):
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


Instrumentator().instrument(app).expose(app)
app.add_middleware(SessionMiddleware, secret_key=settings.middleware_secret_key)
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
                "message": "Создатель сервиса дурачок и не обработал ошибку:<br>"
                + str(exc),
            },
            status_code=500,
        )
    if request.url.path == "/panel" and isinstance(exc, HTTPException):
        if exc.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
            return RedirectResponse(url="/login")
    sentry_sdk.capture_exception(exc)
    return FileResponse("static/500.html")


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        log_level="debug",
        reload=False,
    )
