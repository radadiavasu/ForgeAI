# ForgeAI — Phase 4 Cursor Prompt
# Paste this entire document into Cursor as your starting instruction

---

## CONTEXT

This is Phase 4 of a 10-phase build plan for ForgeAI — an AI agent
orchestration system that simulates a real software company.

Phases 1, 2, and 3 are complete and passing (65 tests passing).
- Phase 1: Task_State_Machine, mock agents, audit log
- Phase 2: Sandbox, Test_Runner, real Docker execution
- Phase 3: Escalation_Ladder, Loop_Counter, Drift_Monitor

Do NOT modify any existing Phase 1, 2, or 3 code unless a specific
instruction below requires it. Build on top of what exists.

---

## WHAT PHASE 4 BUILDS

Four things:

1. **Redis** — moves LoopCounter from in-memory dict to Redis.
   Task_Memory backed by Redis with TTL. (Req 11)

2. **Chroma** — Agent_Memory. Stores and retrieves Lessons using
   semantic similarity. Keyed by agent role, not agent instance. (Req 10)

3. **MinIO** — Object storage for Task_Checkpoints. Agents can save
   and resume in-progress work without loss. (Req 17)

4. **EscalationEvent persistence** — moves EscalationEvents from
   in-memory list to PostgreSQL. (Req 08)

When Phase 4 is complete, no critical state lives in memory.
Everything survives a process restart.

---

## TECH STACK ADDITIONS — PHASE 4 ONLY

Add to requirements.txt:
- `redis[asyncio]` — async Redis client
- `chromadb` — vector database client
- `minio` — MinIO object storage client
- `alembic` — if not already present (database migrations)

Do NOT introduce:
- Anthropic SDK (Phase 5)
- FastAPI (Phase 5)
- Any frontend framework

---

## DOCKER COMPOSE UPDATE

Replace the existing docker-compose.yml with this complete version.
Do not lose the existing PostgreSQL service:

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

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes

  chroma:
    image: chromadb/chroma:latest
    ports:
      - "8000:8000"
    volumes:
      - chroma_data:/chroma/chroma
    environment:
      - IS_PERSISTENT=TRUE
      - ANONYMIZED_TELEMETRY=FALSE

  minio:
    image: minio/minio:latest
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - minio_data:/data
    environment:
      MINIO_ROOT_USER: forgeai
      MINIO_ROOT_PASSWORD: forgeai_dev
    command: server /data --console-address ":9001"

volumes:
  postgres_data:
  redis_data:
  chroma_data:
  minio_data:
```

Add to .env.example:
```
# Redis
REDIS_URL=redis://localhost:6379

# Chroma
CHROMA_HOST=localhost
CHROMA_PORT=8000

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=forgeai
MINIO_SECRET_KEY=forgeai_dev
MINIO_BUCKET=forgeai-checkpoints
MINIO_SECURE=false

# Task Memory TTL (seconds)
TASK_MEMORY_TTL=86400
```

---

## NEW PROJECT STRUCTURE

Add these files. Do not remove any existing files:

```
forgeai/
├── memory/
│   ├── __init__.py
│   ├── agent_memory.py       # Chroma — Lesson storage and retrieval
│   ├── task_memory.py        # Redis — per-task ephemeral context
│   ├── task_checkpoint.py    # MinIO — in-progress work snapshots
│   └── schemas.py            # Pydantic schemas for Lesson, TaskMemory,
│                             # TaskCheckpoint
├── escalation/
│   └── persistence.py        # NEW — PostgreSQL persistence for
│                             # EscalationEvents
└── ...existing files...

tests/
├── test_agent_memory.py      # Lesson write, retrieve, semantic ranking
├── test_task_memory.py       # Redis set, get, TTL expiry, scoping
├── test_task_checkpoint.py   # MinIO save, load, delete
├── test_escalation_persistence.py  # EscalationEvent DB persistence
└── ...existing files...
```

---

## LESSON SCHEMA — `memory/schemas.py`

```python
class Lesson(BaseModel):
    id: str                     # UUID, generated on creation
    agent_role: str             # e.g. "backend_agent", "qa_agent"
                                # NOT agent instance ID
    failure_description: str    # what failed
    root_cause: str             # why it failed
    resolution: str             # what fixed it
    rule: str                   # rule to avoid recurrence
    created_at: datetime
    project_id: str             # which project produced this lesson
    task_id: str                # which task produced this lesson

class LessonQueryResult(BaseModel):
    lesson: Lesson
    relevance_score: float      # cosine similarity score (0.0 to 1.0)
```

---

## AGENT_MEMORY — `memory/agent_memory.py`

### What it does

Stores Lessons in Chroma. Retrieves the top-K most semantically
relevant Lessons for a given task description.

Keyed by agent_role — all instances of the same role share one
Chroma collection. A newly created agent inherits all Lessons
from every previous instance of the same role.

### AgentMemory class

```python
class AgentMemory:
    def __init__(self, chroma_host: str, chroma_port: int):
        # Connect to Chroma
        # Each agent_role gets its own Chroma collection
        # Collection name format: "agent_memory_{agent_role}"
        pass

    async def write_lesson(self, lesson: Lesson) -> None:
        # Store the lesson in the agent_role's Chroma collection
        # The text embedded is a concatenation of:
        #   failure_description + " " + root_cause + " " + rule
        # Store the full lesson as metadata
        # SWAP_POINT: embedding is handled by Chroma's default
        # embedding function (all-MiniLM-L6-v2) for now.
        # Replace with Anthropic embeddings from Phase 5.
        pass

    async def retrieve_lessons(self, agent_role: str,
                                task_description: str,
                                top_k: int = 3) -> list[LessonQueryResult]:
        # Query the agent_role's Chroma collection using
        # task_description as the query text
        # Return top_k most relevant Lessons with relevance scores
        # Return empty list if collection has no documents
        # SWAP_POINT: replace query embeddings with Anthropic API
        # from Phase 5
        pass

    async def get_lesson_count(self, agent_role: str) -> int:
        # Return total number of lessons stored for this agent_role
        pass
```

### Critical rules

1. Collection name must be deterministic from agent_role:
   `"agent_memory_backend_agent"` not `"agent_memory_backend_agent_1"`
2. If a collection does not exist, create it automatically on first write
3. If a collection is empty and retrieve_lessons is called,
   return an empty list — never raise an exception
4. The embedded text must combine failure_description + root_cause + rule
   so semantic search finds lessons by problem type, not just keywords

---

## TASK_MEMORY — `memory/task_memory.py`

### What it does

Ephemeral per-task context store. Backed by Redis with TTL.
Accessible only to the assigned agent and Lead_Agent.
Discarded when the task reaches DONE.

### TaskMemory class

```python
class TaskMemory:
    def __init__(self, redis_url: str, ttl_seconds: int = 86400):
        # Connect to Redis
        # ttl_seconds: how long task memory lives before auto-expiry
        pass

    async def set(self, task_id: str, key: str,
                  value: str) -> None:
        # Store value at task_id:key in Redis
        # Key format: "task_memory:{task_id}:{key}"
        # Always set TTL on write
        pass

    async def get(self, task_id: str, key: str) -> str | None:
        # Retrieve value. Return None if not found.
        pass

    async def delete_all(self, task_id: str) -> None:
        # Delete ALL keys for this task_id
        # Called when task transitions to DONE (Req 11, criterion 5)
        # Use Redis SCAN to find all "task_memory:{task_id}:*" keys
        pass

    async def exists(self, task_id: str, key: str) -> bool:
        # Return True if key exists and has not expired
        pass
```

### Integration with Task_State_Machine

When a task transitions to DONE, TaskMemory.delete_all() must be
called automatically. Add this call to the DONE transition handler
in `state_machine/machine.py`.

This implements Req 11, criterion 5:
"WHEN a Task reaches DONE, THE Task_Memory for that Task SHALL
be discarded."

---

## TASK_CHECKPOINT — `memory/task_checkpoint.py`

### What it does

Saves and restores in-progress work snapshots to MinIO.
Used when a CRITICAL change interrupts an active task (Req 17).
The agent saves its current state, pauses, and resumes later
without loss of progress.

### TaskCheckpoint class

```python
class TaskCheckpoint:
    def __init__(self, minio_endpoint: str, access_key: str,
                 secret_key: str, bucket: str, secure: bool = False):
        # Connect to MinIO
        # Create bucket if it does not exist
        pass

    async def save(self, task_id: str,
                   agent_id: str,
                   checkpoint_data: dict) -> str:
        # Serialise checkpoint_data to JSON
        # Store in MinIO at object path:
        #   "checkpoints/{task_id}/{agent_id}/{timestamp}.json"
        # Return the object path (used to retrieve later)
        pass

    async def load(self, object_path: str) -> dict:
        # Retrieve and deserialise checkpoint from MinIO
        # Raise CheckpointNotFoundError if object does not exist
        pass

    async def delete(self, task_id: str) -> None:
        # Delete ALL checkpoints for this task_id
        # Called when task reaches DONE or REWORK is complete
        pass

    async def get_latest(self, task_id: str,
                          agent_id: str) -> dict | None:
        # Return the most recent checkpoint for task_id + agent_id
        # Return None if no checkpoint exists
        pass
```

### Add to exception hierarchy

```python
class CheckpointNotFoundError(ForgeAIError):
    pass
```

---

## ESCALATION EVENT PERSISTENCE — `escalation/persistence.py`

### New PostgreSQL table

Add a new Alembic migration for the escalation_events table:

```
escalation_events
─────────────────
id                  UUID, primary key
task_id             UUID, not null
agent_id            String, not null
level               Integer, not null (1-5)
error_signature     String, not null
error_detail        Text, not null
loop_count          Integer, not null
attempted_at        DateTime with timezone
resolved            Boolean, not null, default False
resolution          Text, nullable
needs_human_input   Boolean, not null, default False
human_message       Text, nullable
```

### EscalationPersistence class

```python
class EscalationPersistence:
    def __init__(self, db_session):
        self.db = db_session

    async def save_event(self,
                         event: EscalationEvent) -> None:
        # Write EscalationEvent to escalation_events table
        pass

    async def get_events(self,
                         task_id: str) -> list[EscalationEvent]:
        # Return all events for task_id ordered by attempted_at
        pass

    async def mark_resolved(self, event_id: str,
                             resolution: str) -> None:
        # Update resolved=True and resolution text
        pass
```

### Update EscalationLadder

Inject EscalationPersistence into EscalationLadder.
Replace the in-memory `self._events` list with calls to
EscalationPersistence.save_event() and get_events().
The public API of EscalationLadder does not change.

---

## UPDATED main.py

Replace main.py with a version demonstrating all four persistence
systems working together.

### Run 1 — Happy path with Task_Memory

Same happy path as Phase 3, plus:
- Store context in Task_Memory during IN_PROGRESS
  (e.g. `task_memory.set(task_id, "approach", "JWT with HS256")`)
- Verify it exists during TESTING
- Verify it is deleted after DONE

### Run 2 — Lesson write and retrieval

1. Write 3 hardcoded Lessons to Agent_Memory for "backend_agent":

   Lesson 1:
   - failure: "Booking API threw errors on date inputs"
   - root_cause: "Timezone handling inconsistent, mixing local and UTC"
   - resolution: "Rewrote date logic to enforce UTC throughout"
   - rule: "Always convert all dates to UTC at the API boundary"

   Lesson 2:
   - failure: "Auth API returned 500 on empty password field"
   - root_cause: "No input validation before database query"
   - resolution: "Added input validation layer before all DB calls"
   - rule: "Validate all inputs before touching the database"

   Lesson 3:
   - failure: "Payment API double-charged on network timeout"
   - root_cause: "No idempotency key on payment requests"
   - resolution: "Added idempotency keys to all payment endpoints"
   - rule: "All payment endpoints must use idempotency keys"

2. Query Agent_Memory with task description:
   "Build a reservation API that handles booking dates and timezones"

3. Print top-3 results with relevance scores.
   Lesson 1 (date/timezone) must rank highest.

### Run 3 — Task_Checkpoint save and restore

1. Create a task, move it to IN_PROGRESS
2. Save a checkpoint: {"progress": "50%", "last_step": "schema defined"}
3. Print checkpoint saved confirmation
4. Load the checkpoint back
5. Print loaded checkpoint data
6. Move task to DONE, verify checkpoint is deleted

### Run 4 — Escalation persistence

Same escalation path as Phase 3 Run 2, but now:
- EscalationEvents are written to PostgreSQL
- After escalation, query DB and print all events for the task

### Expected terminal output (abbreviated)

```
=== RUN 1: TASK MEMORY ===
[FORGEAI] Task created: Build Auth API | State: PHASE_LOCKED
...
[MEMORY] Task memory set: approach = JWT with HS256
[MEMORY] Task memory verified during TESTING: JWT with HS256
[FORGEAI] QA approved | State: DONE
[MEMORY] Task memory deleted on DONE — 1 key removed

=== RUN 2: LESSON WRITE AND RETRIEVAL ===
[MEMORY] Lesson written for backend_agent: date/timezone issue
[MEMORY] Lesson written for backend_agent: input validation issue
[MEMORY] Lesson written for backend_agent: payment idempotency issue
[MEMORY] Querying: Build a reservation API that handles booking dates...
[MEMORY] Top 3 lessons retrieved:
  1. [0.89] Always convert all dates to UTC at the API boundary
  2. [0.71] Validate all inputs before touching the database
  3. [0.54] All payment endpoints must use idempotency keys

=== RUN 3: TASK CHECKPOINT ===
[CHECKPOINT] Saved: checkpoints/{task_id}/backend_agent_1/{timestamp}.json
[CHECKPOINT] Loaded: {"progress": "50%", "last_step": "schema defined"}
[CHECKPOINT] Deleted on DONE

=== RUN 4: ESCALATION PERSISTENCE ===
[FORGEAI] Task created: Build Payment API | State: PHASE_LOCKED
...
[ESCALATION] Level 5: Human input required
[DB] Escalation events for task:
  1. level=1 | error=test_failure:assertion_error | resolved=False
  2. level=2 | error=test_failure:assertion_error | resolved=False
  3. level=3 | error=test_failure:assertion_error | resolved=False
  4. level=4 | error=test_failure:assertion_error | resolved=False
  5. level=5 | needs_human_input=True
```

---

## TESTS

### test_agent_memory.py

- Test write_lesson stores a lesson successfully
- Test retrieve_lessons returns empty list when collection is empty
- Test retrieve_lessons returns results after writing lessons
- Test the most semantically relevant lesson ranks highest
- Test lessons are scoped by agent_role (backend_agent lessons
  do not appear in qa_agent queries)
- Test get_lesson_count returns correct count after writes
- Test multiple instances of same role share the same collection
  (write with "backend_agent_1", retrieve with "backend_agent_2",
  same agent_role — results must appear)

### test_task_memory.py

- Test set and get round-trip
- Test get returns None for missing key
- Test delete_all removes all keys for a task
- Test delete_all does not affect other tasks
- Test exists returns True for present key
- Test exists returns False for missing key
- Test TTL expiry: set with TTL of 1 second, wait 2 seconds,
  verify key is gone (use a very short TTL for test only)
- Test task memory is deleted when task transitions to DONE
  (integration with Task_State_Machine)

### test_task_checkpoint.py

- Test save returns a non-empty object path
- Test load retrieves the correct data
- Test load raises CheckpointNotFoundError for missing path
- Test get_latest returns most recent checkpoint
- Test get_latest returns None when no checkpoint exists
- Test delete removes all checkpoints for a task
- Test delete does not raise if no checkpoints exist

### test_escalation_persistence.py

- Test save_event writes to database
- Test get_events returns events in chronological order
- Test get_events returns empty list for unknown task_id
- Test mark_resolved updates resolved and resolution fields
- Test events are scoped by task_id

---

## REQUIREMENTS BEING IMPLEMENTED

- **Req 10** — Self-Learning via Lessons
  - Criteria 1-4: Lesson write and semantic retrieval via Chroma ✓
  - Criterion 4: keyed by agent role, not instance ✓
- **Req 11** — Memory Scoping and Immutability
  - Criterion 4: Task_Memory scoped to single task ✓
  - Criterion 5: Task_Memory deleted on DONE ✓
  - Criterion 6: Agent_Memory scoped by role ✓
- **Req 17** — Mid-Project Change Execution
  - Task_Checkpoint save/load/delete infrastructure ✓
- **Req 08** — Escalation persistence to PostgreSQL ✓

---

## CODE QUALITY RULES

- All Redis, Chroma, and MinIO operations must be async where
  the client supports it (MinIO Python client is sync —
  wrap blocking calls with asyncio.to_thread())
- All connections must be initialised lazily and reused
  (do not open a new connection per operation)
- All memory operations must be logged at INFO level:
  "Task memory set: {task_id}:{key}"
  "Lesson written: {agent_role} — {rule[:50]}"
  "Checkpoint saved: {object_path}"
- Add CHROMA_HOST, CHROMA_PORT, REDIS_URL, MINIO_ENDPOINT to
  the Settings class in config.py
- All new exceptions (CheckpointNotFoundError) go in the
  existing ForgeAI exception hierarchy

---

## WHAT SUCCESS LOOKS LIKE

Run this and it works:

```bash
docker compose up -d
python -m alembic upgrade head
python main.py
pytest tests/ -v
```

- All 4 runs in main.py complete without errors
- Lesson retrieval in Run 2 shows real relevance scores
- The most relevant lesson ranks first
- All 65 existing tests still pass
- New tests pass (target: 90+ total)
- docker ps shows all 4 services running:
  postgres, redis, chroma, minio

That is Phase 4 complete.
