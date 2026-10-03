import asyncio

import asyncpg
import redis.asyncio as redis
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter(tags=["health"])


class ReadinessResponse(BaseModel):
    database: str
    redis: str
    status: str


@router.get("/health")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness() -> ReadinessResponse:
    settings = get_settings()

    async def check_database() -> str:
        connection = await asyncpg.connect(settings.postgres_url)
        try:
            await connection.execute("SELECT 1")
        finally:
            await connection.close()
        return "ok"

    async def check_redis() -> str:
        client = redis.from_url(settings.redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()
        return "ok"

    database, redis_status = await asyncio.gather(check_database(), check_redis(), return_exceptions=True)
    response = ReadinessResponse(
        database="ok" if database == "ok" else "unavailable",
        redis="ok" if redis_status == "ok" else "unavailable",
        status="ok" if database == "ok" and redis_status == "ok" else "unavailable",
    )

    if response.status != "ok":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=response.model_dump())

    return response
