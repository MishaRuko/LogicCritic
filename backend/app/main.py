from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import get_settings
from app.routes.agent import router as agent_router
from app.routes.amass import router as amass_router
from app.routes.extraction import router as extraction_router
from app.routes.graph import router as graph_router
from app.routes.health import router as health_router
from app.routes.sources import router as sources_router
from app.routes.workspaces import router as workspaces_router


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
app.include_router(workspaces_router, prefix="/api")
app.include_router(sources_router, prefix="/api")
app.include_router(graph_router, prefix="/api")
app.include_router(extraction_router, prefix="/api")
app.include_router(amass_router, prefix="/api")
app.include_router(agent_router, prefix="/api")
