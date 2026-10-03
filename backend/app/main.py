from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import get_settings
from app.routes.health import router as health_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    Path(get_settings().upload_dir).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Research Argument Graph API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(health_router, prefix="/api")
