"""FastAPI application factory for the data-agentics workbench."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..config import DEV_FRONTEND_ORIGINS, FRONTEND_DIST
from .routes import router


def create_app() -> FastAPI:
    """Create and configure the workbench API application."""
    api = FastAPI(title="data-agentics workbench", version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_FRONTEND_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api.include_router(router)

    if FRONTEND_DIST.exists():
        api.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return api


app = create_app()
