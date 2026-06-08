"""
Database connection — asyncpg pool with lifespan management.
Set DATABASE_URL in your .env / Render environment variables.
"""
import os
from contextlib import asynccontextmanager
import asyncpg
from fastapi import FastAPI

_pool: asyncpg.Pool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=1,
        max_size=5,
        statement_cache_size=0,
        ssl="require",
    )
    yield
    await _pool.close()


@asynccontextmanager
async def get_db():
    async with _pool.acquire() as conn:
        yield conn
