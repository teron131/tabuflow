"""FastAPI application factory for the Tabuflow workbench."""

from collections.abc import Awaitable, Callable
import logging
from time import perf_counter

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from ..config import DEV_FRONTEND_ORIGINS, FRONTEND_DIST
from ..logger import configure_logging
from .routes import router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the workbench API application."""
    configure_logging()
    api = FastAPI(title="Tabuflow Workbench", version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_FRONTEND_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api.middleware("http")
    async def log_api_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        start = perf_counter()
        path = request.url.path
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Request failed method=%s path=%s", request.method, path)
            raise
        duration_ms = (perf_counter() - start) * 1000
        if path.startswith("/api"):
            logger.info("Request completed method=%s path=%s status=%s duration_ms=%.1f", request.method, path, response.status_code, duration_ms)
        return response

    api.include_router(router)

    if FRONTEND_DIST.exists():
        api.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return api


app = create_app()
