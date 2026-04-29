"""FastAPI application factory for the data-agentics workbench."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .constants import FRONTEND_DIST
from .routes import router

DEV_FRONTEND_ORIGINS = [
    "http://localhost:5174",
]


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
