"""FastAPI application entry (Phase 10B)."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from forgeai.api.routes import router

app = FastAPI(
    title="ForgeAI",
    description="AI agent orchestration system",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
