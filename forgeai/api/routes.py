"""ForgeAI HTTP API routes (Phase 10B)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.api import services
from forgeai.api.bootstrap_runner import run_project_bootstrap
from forgeai.api.dashboard import build_dashboard_html
from forgeai.api.schemas import (
    ApproveRequest,
    ApproveResponse,
    ChangeRequest,
    ChangeResponse,
    CreateProjectRequest,
    CreateProjectResponse,
    ProjectStatusResponse,
    ReportResponse,
)
from forgeai.database import AsyncSessionFactory

router = APIRouter()


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionFactory() as session:
        yield session


@router.post("/projects", response_model=CreateProjectResponse)
async def create_project(
    request: CreateProjectRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> CreateProjectResponse:
    response = await services.create_project_record(
        db,
        request.brief,
        request.constraints,
        request.name,
    )
    background_tasks.add_task(
        run_project_bootstrap,
        response.project_id,
        request.brief,
        request.constraints,
        request.name,
    )
    return response


@router.get("/projects/{project_id}", response_model=ProjectStatusResponse)
async def get_project_status(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> ProjectStatusResponse:
    return await services.get_project_status(db, project_id)


@router.post("/projects/{project_id}/approve", response_model=ApproveResponse)
async def approve(
    project_id: str,
    request: ApproveRequest,
    db: AsyncSession = Depends(get_db),
) -> ApproveResponse:
    return await services.approve_project(
        db,
        project_id,
        request.approval_type,
        request.notes,
    )


@router.post("/projects/{project_id}/changes", response_model=ChangeResponse)
async def submit_change(
    project_id: str,
    request: ChangeRequest,
    db: AsyncSession = Depends(get_db),
) -> ChangeResponse:
    return await services.analyse_change(
        db,
        project_id,
        request.change_request,
        request.decision,
    )


@router.get("/projects/{project_id}/report", response_model=ReportResponse)
async def get_report(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> ReportResponse:
    return await services.get_project_report(db, project_id)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    html = await build_dashboard_html(db)
    return HTMLResponse(content=html)
