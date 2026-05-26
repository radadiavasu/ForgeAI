# ForgeAI — Phase 10B Cursor Prompt
# Paste this entire document into Cursor as your starting instruction

---

## CONTEXT

This is Phase 10B of the ForgeAI build plan.

Phases 1-10 are complete (298 tests passing).

Do NOT modify any existing Phase 1-10 code unless a specific
instruction below requires it. Build on top of what exists.

---

## WHAT PHASE 10B BUILDS

Two things:

1. **FastAPI layer** — Lead_Agent becomes accessible via HTTP.
   5 endpoints. Runs on localhost:8000.

2. **Observability Dashboard** — Internal admin HTML page.
   Served by FastAPI. Auto-refreshes every 5 seconds.
   Shows agent states, task progress, cost, escalations.

---

## NEW PROJECT STRUCTURE

Add these files. Do not remove any existing files:

```
forgeai/
├── api/
│   ├── __init__.py
│   ├── app.py          # FastAPI application
│   ├── routes.py       # All 5 endpoints
│   ├── schemas.py      # Request/response Pydantic models
│   └── dashboard.py    # Dashboard HTML generation
└── ...existing files...

tests/
├── test_api_routes.py
└── ...existing files...

run_server.py           # Entry point: uvicorn forgeai.api.app:app
```

---

## FASTAPI APP — EXACT SPECIFICATION

### `forgeai/api/app.py`

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from forgeai.api.routes import router
from forgeai.database import AsyncSessionFactory

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
```

---

## THE 5 ENDPOINTS — EXACT SPECIFICATION

### `forgeai/api/routes.py`

```python
POST   /projects
GET    /projects/{project_id}
POST   /projects/{project_id}/approve
POST   /projects/{project_id}/changes
GET    /projects/{project_id}/report
GET    /dashboard                        # Observability Dashboard HTML
```

---

### POST /projects

Submit a new project brief. Starts bootstrap asynchronously.

**Request body:**
```python
class CreateProjectRequest(BaseModel):
    brief: str
    constraints: dict = {}
    name: str = ""
```

**Response:**
```python
class CreateProjectResponse(BaseModel):
    project_id: str
    status: str          # "bootstrapping"
    message: str         # plain language
    poll_url: str        # GET /projects/{id}
```

**Implementation:**
```python
@router.post("/projects", response_model=CreateProjectResponse)
async def create_project(
    request: CreateProjectRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    # 1. Create project record in ProjectRegistry (status=ACTIVE)
    # 2. Store brief and constraints in Project_Memory
    # 3. Launch bootstrap as background task
    # 4. Return project_id and poll_url immediately
    # Do NOT await the full run — return fast
    pass
```

---

### GET /projects/{project_id}

Get current project status in plain language.

**Response:**
```python
class ProjectStatusResponse(BaseModel):
    project_id: str
    name: str
    status: str              # ACTIVE / LIVE / ARCHIVED
    phase: str               # PLANNING / FRONTEND_PHASE / etc
    message: str             # plain language summary
    tasks_done: int
    tasks_total: int
    tasks_in_progress: int
    cost_usd: float
    pending_approvals: list[str]   # what needs human action
    escalations_needing_input: int
    created_at: str
    delivered_at: str | None
```

**Implementation:**
```python
@router.get("/projects/{project_id}")
async def get_project_status(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    # Query tasks table for counts by state
    # Query project registry for status and phase
    # Build plain-language message
    # Return ProjectStatusResponse
    pass
```

---

### POST /projects/{project_id}/approve

Approve a pending Phase Gate or other human decision.

**Request body:**
```python
class ApproveRequest(BaseModel):
    approval_type: str    # "phase_gate" / "agent_count" / "tech_stack"
    notes: str = ""
```

**Response:**
```python
class ApproveResponse(BaseModel):
    project_id: str
    approved: bool
    message: str          # plain language — what happens next
```

**Implementation:**
```python
@router.post("/projects/{project_id}/approve")
async def approve(
    project_id: str,
    request: ApproveRequest,
    db: AsyncSession = Depends(get_db),
):
    # Look up pending approval for this project
    # Mark it as approved in Project_Memory
    # Return what happens next in plain language
    pass
```

---

### POST /projects/{project_id}/changes

Submit a change request to a LIVE project.

**Request body:**
```python
class ChangeRequest(BaseModel):
    change_request: str
    decision: str = "PROCEED"   # PROCEED / QUEUE / DEFER / REJECT
```

**Response:**
```python
class ChangeResponse(BaseModel):
    project_id: str
    change_type: str        # BUGFIX / SMALL_FEATURE / etc
    risk_level: str
    affected_tasks: int
    estimated_cost_usd: float
    estimated_time_minutes: int
    decision: str
    message: str            # plain language summary
```

**Implementation:**
```python
@router.post("/projects/{project_id}/changes")
async def submit_change(
    project_id: str,
    request: ChangeRequest,
    db: AsyncSession = Depends(get_db),
):
    # Load project and master document
    # Run ChangeClassifier and ImpactAnalyser
    # Return analysis result with plain language summary
    # Do NOT execute the change — just classify and analyse
    # Human must confirm via a second call if they want PROCEED
    pass
```

---

### GET /projects/{project_id}/report

Get the final summary report for a delivered project.

**Response:**
```python
class ReportResponse(BaseModel):
    project_id: str
    name: str
    brief: str
    release_tag: str | None
    tasks_completed: int
    qa_cycles: int
    escalations: int
    lessons_accumulated: int
    cost_usd: float
    output_directory: str | None
    files_written: list[str]
    gaps_identified: list[str]
    generated_at: str
```

**Implementation:**
```python
@router.get("/projects/{project_id}/report")
async def get_report(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    # Read final summary from Project_Memory artefact
    # If not found, build from task/escalation counts
    # Return ReportResponse
    pass
```

---

## OBSERVABILITY DASHBOARD — EXACT SPECIFICATION

### GET /dashboard

Returns an HTML page. No authentication. Internal only.

### `forgeai/api/dashboard.py`

```python
async def build_dashboard_html(db: AsyncSession) -> str:
    # Query all data needed for dashboard
    # Build and return complete HTML string
    pass
```

### Dashboard data to display

Query these from the database:

```python
# 1. All projects with their status
SELECT id, name, status, created_at, delivered_at FROM projects

# 2. Task counts by project and state
SELECT project_id, current_state, COUNT(*)
FROM tasks GROUP BY project_id, current_state

# 3. Escalation events (last 50)
SELECT task_id, level, outcome, timestamp
FROM escalation_events ORDER BY timestamp DESC LIMIT 50

# 4. Agent lifecycle events (last 20)
SELECT agent_role, event_type, development_phase, created_at
FROM agent_lifecycle_events ORDER BY created_at DESC LIMIT 20

# 5. Cost estimate (sum from LLM call logs if available,
#    else estimate from task counts)
```

### Dashboard HTML structure

```html
<!DOCTYPE html>
<html>
<head>
  <title>ForgeAI — Observability Dashboard</title>
  <meta http-equiv="refresh" content="5">
  <style>
    /* Dark theme — background #0f172a, text #e2e8f0 */
    /* Cards with border #1e293b */
    /* Green for DONE, amber for IN_PROGRESS, red for escalated */
    /* Monospace font for task IDs and agent names */
  </style>
</head>
<body>
  <!-- Header: ForgeAI Observability Dashboard + last refreshed time -->

  <!-- Section 1: Projects -->
  <!-- Table: ID (short), Name, Status, Phase, Tasks Done/Total, Age -->

  <!-- Section 2: Task State Summary -->
  <!-- Per-project bar showing DONE / IN_PROGRESS / TESTING / PHASE_LOCKED -->

  <!-- Section 3: Recent Escalations -->
  <!-- Table: Task ID (short), Level, Outcome, Time ago -->

  <!-- Section 4: Agent Lifecycle Events -->
  <!-- Table: Agent role, Event, Phase, Time ago -->

  <!-- Footer: Auto-refreshes every 5 seconds -->
</body>
</html>
```

### Dashboard route

```python
from fastapi.responses import HTMLResponse

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(db: AsyncSession = Depends(get_db)):
    html = await build_dashboard_html(db)
    return HTMLResponse(content=html)
```

---

## DATABASE DEPENDENCY

```python
# forgeai/api/routes.py

from forgeai.database import AsyncSessionFactory
from sqlalchemy.ext.asyncio import AsyncSession

async def get_db():
    async with AsyncSessionFactory() as session:
        yield session
```

---

## SERVER ENTRY POINT

### `run_server.py` in project root

```python
"""Start the ForgeAI API server."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "forgeai.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
```

### To start the server:
```bash
python run_server.py
```

### To test immediately:
```bash
# Health check
curl http://localhost:8000/docs

# Dashboard
open http://localhost:8000/dashboard
```

---

## TESTS

### `tests/test_api_routes.py`

Use `httpx.AsyncClient` with `app` directly. No real DB needed
— mock the database dependency.

```python
import pytest
from httpx import AsyncClient, ASGITransport
from forgeai.api.app import app

@pytest.fixture
def client():
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    )
```

Tests to write (mock DB, no real LLM calls):

- Test POST /projects returns 200 with project_id
- Test POST /projects returns poll_url in response
- Test GET /projects/{id} returns ProjectStatusResponse shape
- Test GET /projects/{id} with unknown id returns 404
- Test POST /projects/{id}/approve returns 200
- Test POST /projects/{id}/changes returns ChangeResponse shape
- Test GET /projects/{id}/report returns ReportResponse shape
- Test GET /dashboard returns 200 with content-type text/html
- Test GET /dashboard HTML contains "ForgeAI"
- Test GET /dashboard HTML contains "Projects"

---

## REQUIREMENTS.TXT ADDITION

Add if not already present:
```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
httpx>=0.27.0
```

---

## CODE QUALITY RULES

- All endpoint responses must be plain language — no internal
  terms like "PHASE_LOCKED", "agent_id", "artefact"
- pending_approvals list must use human-readable strings:
  "Approve technology stack selection"
  "Approve frontend before backend starts"
  "Approve final delivery"
- Dashboard must work even if tables are empty (no crashes)
- All DB queries must handle missing data gracefully
- Background tasks must not crash the server if they fail —
  catch exceptions and log them

---

## WHAT SUCCESS LOOKS LIKE

```bash
# Terminal 1
python run_server.py

# Terminal 2
curl -X POST http://localhost:8000/projects \
  -H "Content-Type: application/json" \
  -d '{"brief": "Build a todo app", "name": "todo-v1"}'

# Returns immediately with project_id

curl http://localhost:8000/projects/{project_id}
# Returns plain-language status

open http://localhost:8000/dashboard
# Shows dark-themed dashboard with project and task data

pytest tests/ -v
# All existing 298 tests still pass
# New API tests pass with mocked DB
# Target: 315+ total tests passing
```

That is Phase 10B complete.
