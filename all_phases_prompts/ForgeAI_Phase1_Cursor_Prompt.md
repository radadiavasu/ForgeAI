# ForgeAI — Phase 1 Cursor Prompt
# Paste this entire document into Cursor as your starting instruction

---

## WHAT YOU ARE BUILDING

ForgeAI is an AI agent orchestration system that simulates a real software
company. It accepts a project brief and drives it through research,
architecture, frontend development, backend development, QA, and delivery
using a hierarchy of specialised AI agents.

This is Phase 1 of a 10-phase build plan. Phase 1 has one goal:

**Prove that the Task_State_Machine works correctly end to end.**

No UI. No real LLM calls. No Redis. No vector database. No API layer.
Just the state machine, a PostgreSQL database, and hardcoded mock agents
completing one task from TODO to DONE.

If Phase 1 ends with a task reaching DONE in the database with a full
audited state history, Phase 1 is complete.

---

## TECH STACK — PHASE 1 ONLY

- **Language:** Python 3.11.9 (Already available)
- **Database:** PostgreSQL 15+ (running in Docker)
- **ORM:** SQLAlchemy 2.0 (async) + asyncpg
- **Data validation:** Pydantic v2
- **Environment:** python-dotenv
- **Infrastructure:** Docker Compose (PostgreSQL only for now)
- **Testing:** pytest + pytest-asyncio

Do NOT introduce:
- Redis
- Chroma or any vector database
- FastAPI or any HTTP layer
- Anthropic SDK or any LLM calls
- Any frontend framework

---

## PROJECT STRUCTURE

Create this exact structure:

```
forgeai/
├── docker-compose.yml
├── .env.example
├── .env                        # gitignored
├── requirements.txt
├── README.md
├── alembic/                    # database migrations
│   ├── alembic.ini
│   └── versions/
├── forgeai/
│   ├── __init__.py
│   ├── config.py               # loads .env, exposes settings
│   ├── database.py             # SQLAlchemy async engine + session factory
│   ├── models/
│   │   ├── __init__.py
│   │   └── task.py             # Task and TaskStateHistory ORM models
│   ├── state_machine/
│   │   ├── __init__.py
│   │   ├── states.py           # TaskState enum
│   │   ├── transitions.py      # permitted transition map + validation
│   │   └── machine.py          # Task_State_Machine — core logic
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py             # BaseAgent abstract class
│   │   ├── lead_agent.py       # LeadAgent stub
│   │   ├── backend_agent.py    # BackendAgent stub
│   │   └── qa_agent.py         # QAAgent stub
│   └── schemas/
│       ├── __init__.py
│       └── task.py             # Pydantic schemas for Task and transitions
└── tests/
    ├── __init__.py
    ├── conftest.py             # pytest fixtures: db session, test task
    ├── test_state_machine.py   # all permitted and rejected transitions
    └── test_full_cycle.py      # one complete TODO → DONE cycle
```

---

## DOCKER COMPOSE

`docker-compose.yml` runs PostgreSQL only:

```yaml
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: forgeai
      POSTGRES_USER: forgeai
      POSTGRES_PASSWORD: forgeai_dev
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

`.env.example`:
```
DATABASE_URL=postgresql+asyncpg://forgeai:forgeai_dev@localhost:5432/forgeai
```

---

## TASK STATES

Define a `TaskState` enum with exactly these values, in this order:

```python
class TaskState(str, Enum):
    PHASE_LOCKED = "PHASE_LOCKED"
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    IN_REVIEW = "IN_REVIEW"
    TESTING = "TESTING"
    DONE = "DONE"
    REWORK = "REWORK"
```

---

## PERMITTED TRANSITIONS

This is the exact transition map. Enforce this with no exceptions.

```
PHASE_LOCKED → TODO         condition: requires phase_transition_approval=True
TODO         → IN_PROGRESS  condition: none
IN_PROGRESS  → IN_REVIEW    condition: none
IN_REVIEW    → TESTING      condition: none
TESTING      → DONE         condition: none
TESTING      → IN_PROGRESS  condition: requires defect_report (non-empty string)
DONE         → REWORK       condition: requires rework_reason (non-empty string)
```

Every other transition is REJECTED.

A rejected transition must:
1. Raise a `InvalidTransitionError` (custom exception)
2. Log the violation with: agent_id, from_state, to_state, task_id, timestamp
3. NOT modify the task state in the database

---

## DATABASE MODELS

### Task

```
id              UUID, primary key, generated on creation
project_id      UUID, not null (not enforced as foreign key in Phase 1 —
                just the column, as agreed for future multi-project support)
title           String, not null
description     Text, nullable
assigned_agent  String, not null (agent identifier e.g. "backend_agent_1")
complexity      Enum: LOW / MEDIUM / HIGH
current_state   TaskState, not null, default PHASE_LOCKED
created_at      DateTime with timezone, auto-set on creation
updated_at      DateTime with timezone, auto-updated on every change
output          Text, nullable (populated when task reaches DONE)
```

### TaskStateHistory

Every state transition — permitted or rejected — gets a row here.
This is the full audit log required by Req 07.

```
id              UUID, primary key
task_id         UUID, foreign key → Task.id
agent_id        String, not null (who attempted the transition)
from_state      TaskState, not null
to_state        TaskState, not null
attempted_at    DateTime with timezone, auto-set
success         Boolean, not null
rejection_reason String, nullable (populated when success=False)
defect_report   Text, nullable (populated when transition is TESTING → IN_PROGRESS)
metadata        JSONB, nullable (any extra context the agent attaches)
```

---

## TASK_STATE_MACHINE — CORE LOGIC

`machine.py` is the heart of Phase 1. It must do exactly this:

### `transition(task_id, to_state, agent_id, **kwargs) → Task`

1. Load the task from the database by task_id
2. Look up whether `(current_state → to_state)` is in the permitted
   transition map
3. If not permitted: write a FAILED row to TaskStateHistory, raise
   `InvalidTransitionError`
4. If permitted: check any conditions (phase_transition_approval,
   defect_report, rework_reason)
5. If conditions not met: write a FAILED row to TaskStateHistory, raise
   `TransitionConditionError`
6. If everything passes:
   - Update task.current_state to to_state
   - Update task.updated_at
   - Write a SUCCESS row to TaskStateHistory
   - If to_state is DONE: write task.output to the task record
   - Commit the transaction
   - Return the updated Task

### `get_history(task_id) → list[TaskStateHistory]`

Returns the full ordered history for a task. Most recent last.

### Error types to define

```python
class ForgeAIError(Exception):
    pass

class InvalidTransitionError(ForgeAIError):
    # Raised when the from→to pair is not in the permitted map
    pass

class TransitionConditionError(ForgeAIError):
    # Raised when the transition is permitted but conditions are not met
    pass
```

---

## MOCK AGENTS — PHASE 1 STUBS

These are not real agents. They are hardcoded stubs that simulate
agent behaviour by calling the Task_State_Machine directly.
No LLM calls. No async complexity beyond what SQLAlchemy requires.

### BaseAgent

```python
class BaseAgent:
    def __init__(self, agent_id: str, db_session):
        self.agent_id = agent_id
        self.db = db_session
```

### LeadAgent stub

Methods:
- `create_task(title, description, complexity, assigned_agent) → Task`
  Creates a task in PHASE_LOCKED state.
- `approve_phase_transition(task_id) → Task`
  Transitions PHASE_LOCKED → TODO by calling the state machine with
  phase_transition_approval=True.
- `assign_task(task_id) → Task`
  Transitions TODO → IN_PROGRESS.

### BackendAgent stub

Methods:
- `complete_work(task_id, output: str) → Task`
  Transitions IN_PROGRESS → IN_REVIEW. Attaches output string.

### QAAgent stub

Methods:
- `approve(task_id) → Task`
  Transitions TESTING → DONE.
- `reject(task_id, defect_report: str) → Task`
  Transitions TESTING → IN_PROGRESS with defect_report attached.
- `begin_review(task_id) → Task`
  Transitions IN_REVIEW → TESTING.

### Hard rule — no self-approval

The QAAgent must check that the agent_id that produced the work
(the agent that called IN_PROGRESS → IN_REVIEW) is NOT the same
as the QAAgent's own agent_id. If it is the same, raise a
`SelfApprovalError`. This is Req 06 criterion 8 — no exceptions, ever.

```python
class SelfApprovalError(ForgeAIError):
    pass
```

---

## THE FULL CYCLE — main.py

Create a `main.py` at the project root that runs the full cycle
as a script. This is the Phase 1 proof of life.

The script must:

1. Connect to PostgreSQL
2. Create a LeadAgent stub and a BackendAgent stub and a QAAgent stub
3. LeadAgent creates a task: title="Build Auth API", complexity=MEDIUM,
   assigned_agent="backend_agent_1"
4. Task is now PHASE_LOCKED — print state
5. LeadAgent approves phase transition → task moves to TODO — print state
6. LeadAgent assigns task → task moves to IN_PROGRESS — print state
7. BackendAgent completes work with output="JWT auth implemented" →
   task moves to IN_REVIEW — print state
8. QAAgent begins review → task moves to TESTING — print state
9. QAAgent approves → task moves to DONE — print state
10. Print the full TaskStateHistory for the task — every row,
    in order, showing agent_id, from_state, to_state, success, attempted_at

Expected terminal output (exact states, your formatting):

```
[FORGEAI] Task created: Build Auth API | State: PHASE_LOCKED
[FORGEAI] Phase transition approved | State: TODO
[FORGEAI] Task assigned to backend_agent_1 | State: IN_PROGRESS
[FORGEAI] Work completed by backend_agent_1 | State: IN_REVIEW
[FORGEAI] QA review started | State: TESTING
[FORGEAI] QA approved | State: DONE

--- FULL STATE HISTORY ---
1. PHASE_LOCKED → TODO         | agent: lead_agent_1    | success: True
2. TODO → IN_PROGRESS          | agent: lead_agent_1    | success: True
3. IN_PROGRESS → IN_REVIEW     | agent: backend_agent_1 | success: True
4. IN_REVIEW → TESTING         | agent: qa_agent_1      | success: True
5. TESTING → DONE              | agent: qa_agent_1      | success: True
```

---

## TESTS

### test_state_machine.py

Write tests for every case:

**Permitted transitions — must succeed:**
- PHASE_LOCKED → TODO with approval
- TODO → IN_PROGRESS
- IN_PROGRESS → IN_REVIEW
- IN_REVIEW → TESTING
- TESTING → DONE
- TESTING → IN_PROGRESS with defect report

**Rejected transitions — must raise InvalidTransitionError:**
- TODO → DONE (skipping states)
- IN_PROGRESS → DONE (skipping states)
- DONE → IN_PROGRESS (backwards, no rework_reason)
- TESTING → TODO (backwards, not permitted)
- IN_REVIEW → IN_PROGRESS (backwards, not permitted)

**Condition failures — must raise TransitionConditionError:**
- PHASE_LOCKED → TODO without phase_transition_approval
- TESTING → IN_PROGRESS without defect_report

**Audit log tests:**
- Every successful transition writes a SUCCESS row to TaskStateHistory
- Every failed transition writes a FAILED row to TaskStateHistory
- History rows are returned in chronological order

### test_full_cycle.py

One test: run the complete main.py cycle programmatically and assert:
- Final task state is DONE
- TaskStateHistory has exactly 5 rows (for a clean cycle)
- All 5 rows have success=True
- task.output is not null

### test_self_approval.py

One test: BackendAgent completes work on a task, then the SAME
agent_id attempts to call QAAgent.approve() on it.
Assert SelfApprovalError is raised.
Assert the task state has NOT changed from IN_REVIEW.

---

## REQUIREMENTS BEING IMPLEMENTED

This Phase 1 implements the following requirements from the
ForgeAI System Requirements Document v3.0:

- **Req 07** — Task State Machine Integrity (fully implemented)
- **Req 06, criterion 8** — No self-approval rule (SelfApprovalError)
- **Req 04, criteria 1-6** — Task creation and assignment (stub implementation)
- **Req 11, criteria 1-3** — Memory scoping (project_id column, immutable
  output on DONE)

The following are deliberately NOT implemented in Phase 1:
- Req 08 — Escalation Ladder (Phase 3)
- Req 09 — Loop and Drift Prevention (Phase 3)
- Req 10 — Self-Learning via Lessons (Phase 4)
- Req 18 — Sandbox (Phase 2)
- Req 19 — Version Control (Phase 5)
- All other requirements

---

## CODE QUALITY RULES

- All database calls must be async (SQLAlchemy async session)
- All functions that touch the database must be type-hinted
- No print statements inside library code — only in main.py and tests
- Use Python logging (not print) inside the state machine and agents,
  with log level INFO for successful transitions, WARNING for rejections
- Every function must have a docstring stating what it does,
  what it returns, and what exceptions it can raise
- No hardcoded strings outside of main.py — use the TaskState enum
  everywhere

---

## WHAT SUCCESS LOOKS LIKE

Run this and it works:

```bash
docker compose up -d
python -m alembic upgrade head
python main.py
pytest tests/ -v
```

All tests pass. The terminal output from main.py matches the expected
output exactly. The TaskStateHistory table in PostgreSQL has rows.

That is Phase 1 complete.
