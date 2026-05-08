# ForgeAI — Phase 3 Cursor Prompt
# Paste this entire document into Cursor as your starting instruction

---

## CONTEXT

This is Phase 3 of a 10-phase build plan for ForgeAI — an AI agent
orchestration system that simulates a real software company.

Phases 1 and 2 are complete and passing (38 tests passing).
Phase 1 delivered the Task_State_Machine and mock agents.
Phase 2 delivered the Sandbox and Test_Runner with real Docker execution.

Do NOT modify any existing Phase 1 or Phase 2 code unless a specific
instruction below requires it. Build on top of what exists.

---

## WHAT PHASE 3 BUILDS

Three things:

1. **Escalation_Ladder** — five-level failure protocol. Each level is a
   genuine attempt to resolve a failure before escalating further.
   (Req 08)

2. **Loop_Counter** — per-task counter that detects repeated identical
   failures and forces escalation when the same error occurs 3 times.
   (Req 09)

3. **Drift_Monitor** — per-agent subsystem that detects when an agent's
   output is diverging from its task specification using embedding-based
   semantic similarity. (Req 09)

When Phase 3 is complete, the system handles failure systematically
rather than crashing or looping indefinitely.

---

## TECH STACK ADDITIONS — PHASE 3 ONLY

Add to requirements.txt:
- `sentence-transformers` — local embeddings for Drift_Monitor

# IMPORTANT NOTE FOR FUTURE PHASES:
# Every call to sentence-transformers in this codebase is marked with:
# # SWAP_POINT: replace with Anthropic embeddings API from Phase 5
# When Phase 5 arrives, search for SWAP_POINT and replace all of them.
# Do not remove this comment from any embedding call.

Do NOT introduce:
- Redis (Phase 4)
- Chroma (Phase 4)
- FastAPI (Phase 4-5)
- Anthropic SDK (Phase 5)
- Any frontend framework

---

## NEW PROJECT STRUCTURE

Add these files. Do not remove any existing files:

```
forgeai/
├── escalation/
│   ├── __init__.py
│   ├── ladder.py           # Escalation_Ladder — all five levels
│   ├── loop_counter.py     # Loop_Counter — per-task error tracking
│   └── schemas.py          # Pydantic schemas for escalation events
├── monitoring/
│   ├── __init__.py
│   ├── drift_monitor.py    # Drift_Monitor — semantic similarity check
│   └── embeddings.py       # Embedding utility — SWAP_POINT marked
└── ...existing files...

tests/
├── test_escalation_ladder.py   # all five levels, happy and failure paths
├── test_loop_counter.py        # increment, reset, threshold behaviour
├── test_drift_monitor.py       # drift detection, self-correction trigger
└── ...existing files...
```

---

## LOOP_COUNTER — EXACT SPECIFICATION

### What it does

Tracks how many times the same error signature has occurred on the
same task consecutively. When it reaches 3, the agent must escalate
immediately without further self-retry.

Resets to 0 whenever the task transitions to a new state.

### Implementation — `escalation/loop_counter.py`

```python
class LoopCounter:
    def __init__(self):
        # In-memory store: Dict[task_id, Dict[error_signature, int]]
        # Phase 4 will move this to Redis. For now, in-memory is correct.
        self._counters: dict[str, dict[str, int]] = {}

    def increment(self, task_id: str, error_signature: str) -> int:
        # Increment counter for this task + error_signature combination
        # Return the new count
        pass

    def get(self, task_id: str, error_signature: str) -> int:
        # Return current count. 0 if not seen before.
        pass

    def reset(self, task_id: str) -> None:
        # Clear ALL counters for this task
        # Called whenever the task transitions to a new state
        pass

    def should_escalate(self, task_id: str,
                        error_signature: str) -> bool:
        # Return True if count >= 3
        pass
```

### Error signature

An error signature is a short normalised string identifying the type
of failure. Examples:
- `"test_failure:assertion_error"`
- `"sandbox_timeout"`
- `"output_missing"`
- `"schema_violation"`

The signature is produced by the agent reporting the failure.
It must be consistent for the same class of error — not a full
stack trace, not a random string.

---

## DRIFT_MONITOR — EXACT SPECIFICATION

### What it does

Computes a Semantic_Drift_Score (0-100) representing how far an
agent's current output has diverged from its task specification.

Score of 0 = identical meaning. Score of 100 = completely unrelated.

When the score exceeds the configurable threshold (default 40),
the agent must self-correct. If it cannot self-correct in one step,
it escalates to Lead_Agent.

### Embedding utility — `monitoring/embeddings.py`

```python
# SWAP_POINT: This entire module will be replaced with Anthropic
# embeddings API calls from Phase 5 onward.
# Search for SWAP_POINT across the codebase when Phase 5 begins.

from sentence_transformers import SentenceTransformer

_model = None

def get_model() -> SentenceTransformer:
    # Lazy load — only initialise once
    # Use model: "all-MiniLM-L6-v2" (fast, small, sufficient for drift)
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model

def compute_similarity(text_a: str, text_b: str) -> float:
    # Returns cosine similarity between two texts (0.0 to 1.0)
    # 1.0 = identical meaning, 0.0 = completely unrelated
    # SWAP_POINT: replace internals with Anthropic embeddings API
    pass
```

### DriftMonitor class — `monitoring/drift_monitor.py`

```python
class DriftMonitor:
    def __init__(self, threshold: int = 40):
        # threshold: Semantic_Drift_Score above which self-correction
        # is triggered. Configurable via .env.
        # Add to .env.example: DRIFT_THRESHOLD=40
        self.threshold = threshold

    def compute_drift_score(self,
                            task_specification: str,
                            agent_output: str) -> int:
        # 1. Compute cosine similarity between task_specification
        #    and agent_output using embeddings.compute_similarity()
        # 2. Convert to drift score: score = int((1 - similarity) * 100)
        #    similarity of 1.0 → drift score of 0 (no drift)
        #    similarity of 0.0 → drift score of 100 (maximum drift)
        # 3. Return the integer score (0-100)
        pass

    def is_drifting(self, task_specification: str,
                    agent_output: str) -> bool:
        # Return True if drift score exceeds self.threshold
        pass

    def check(self, task_specification: str,
              agent_output: str) -> DriftCheckResult:
        # Compute drift score
        # Return DriftCheckResult with score, is_drifting flag,
        # and a plain-language description of the divergence
        pass
```

### Pydantic schema — add to `escalation/schemas.py`

```python
class DriftCheckResult(BaseModel):
    score: int                  # 0-100
    is_drifting: bool
    threshold: int
    description: str            # plain language e.g.
                                # "Output has diverged significantly
                                #  from task specification (score: 67)"
```

---

## ESCALATION_LADDER — EXACT SPECIFICATION

### The five levels (Req 08)

```
Level 1 — Self-retry: agent retries with a different approach (max 2 retries)
Level 2 — Peer assist: Lead_Agent assigns a peer agent of the same domain
Level 3 — Architect review: Architect_Agent re-examines task specification
Level 4 — Task rewrite: Lead_Agent rewrites and simplifies the task
Level 5 — Human input: task marked needs_human_input, user notified
```

### Pydantic schemas — `escalation/schemas.py`

```python
from enum import IntEnum

class EscalationLevel(IntEnum):
    SELF_RETRY = 1
    PEER_ASSIST = 2
    ARCHITECT_REVIEW = 3
    TASK_REWRITE = 4
    HUMAN_INPUT = 5

class EscalationEvent(BaseModel):
    task_id: str
    agent_id: str
    level: EscalationLevel
    error_signature: str
    error_detail: str
    loop_count: int
    timestamp: datetime
    resolved: bool = False
    resolution: str = ""

class EscalationResult(BaseModel):
    level_reached: EscalationLevel
    resolved: bool
    resolution: str             # what was done to resolve
    needs_human_input: bool     # True only at Level 5
    human_message: str = ""     # plain language message for user
                                # populated only at Level 5
```

### EscalationLadder class — `escalation/ladder.py`

```python
class EscalationLadder:
    def __init__(self, loop_counter: LoopCounter,
                 max_self_retries: int = 2):
        self.loop_counter = loop_counter
        self.max_self_retries = max_self_retries
        self._events: list[EscalationEvent] = []
        # Phase 4 will persist events to PostgreSQL

    async def escalate(self, task_id: str, agent_id: str,
                       error_signature: str,
                       error_detail: str,
                       task_specification: str) -> EscalationResult:
        # This is the main entry point.
        # Determines which level to enter based on loop_counter state.
        # Executes that level.
        # Returns EscalationResult.
        pass

    async def _level_1_self_retry(self, task_id: str,
                                   agent_id: str) -> bool:
        # Simulate self-retry with a different approach
        # In Phase 3 this is a stub — returns True (resolved) or
        # False (failed) based on retry count
        # Max 2 retries total across all Level 1 calls for this task
        # Returns True if resolved, False if exhausted
        pass

    async def _level_2_peer_assist(self, task_id: str,
                                    agent_id: str) -> bool:
        # Simulate peer agent being assigned to assist
        # Stub: returns False (peer could not resolve)
        # This will be replaced with real peer agent invocation
        # in Phase 5 when real agents exist
        pass

    async def _level_3_architect_review(self,
                                         task_id: str,
                                         task_specification: str) -> bool:
        # Simulate Architect_Agent reviewing task specification
        # Stub: returns False (architect could not resolve)
        # Real implementation in Phase 5
        pass

    async def _level_4_task_rewrite(self, task_id: str,
                                     task_specification: str) -> bool:
        # Simulate Lead_Agent rewriting and simplifying the task
        # Stub: returns False (rewrite did not resolve)
        # Real implementation in Phase 5
        pass

    async def _level_5_human_input(self, task_id: str,
                                    error_detail: str) -> EscalationResult:
        # Mark task as needs_human_input
        # Produce a plain-language message for the user
        # Return EscalationResult with needs_human_input=True
        # Lead_Agent SHALL NOT retry a Level 5 task without user input
        pass

    def get_events(self, task_id: str) -> list[EscalationEvent]:
        # Return all escalation events for a task, ordered by timestamp
        pass

    def get_current_level(self, task_id: str) -> EscalationLevel | None:
        # Return the highest escalation level reached for this task
        # None if no escalation has occurred
        pass
```

### Escalation routing logic

The routing logic inside `escalate()` must follow this exact decision tree:

```
1. Check loop_counter.should_escalate(task_id, error_signature)
   - If True (count >= 3): SKIP directly to Level 2 (no more self-retry)

2. If loop_counter < 3:
   - Try Level 1 (self-retry, max 2 attempts)
   - If Level 1 resolves: return EscalationResult(resolved=True, level=1)
   - If Level 1 exhausted: continue

3. Try Level 2 (peer assist)
   - If Level 2 resolves: return EscalationResult(resolved=True, level=2)
   - If Level 2 fails: continue

4. Try Level 3 (architect review)
   - If Level 3 resolves: return EscalationResult(resolved=True, level=3)
   - If Level 3 fails: continue

5. Try Level 4 (task rewrite)
   - If Level 4 resolves: return EscalationResult(resolved=True, level=4)
   - If Level 4 fails: continue

6. Level 5 (human input) — always the final step
   - Return EscalationResult(needs_human_input=True, level=5)
```

### Add to .env.example

```
DRIFT_THRESHOLD=40
MAX_SELF_RETRIES=2
```

---

## UPDATED main.py

Replace main.py with a version that demonstrates both the happy path
AND the escalation path.

### Run 1 — Happy path (same as Phase 2)
Task succeeds, reaches DONE. Print results.

### Run 2 — Escalation path
Simulate a task that fails repeatedly:

1. LeadAgent creates task: "Build Payment API", complexity=HIGH
2. LeadAgent approves phase transition → TODO
3. LeadAgent assigns → IN_PROGRESS
4. BackendAgent produces INTENTIONALLY BROKEN code (see below)
5. QAAgent submits to Sandbox → tests fail → RunnerOutput.success=False
6. LeadAgent calls EscalationLadder.escalate() with error_signature
   "test_failure:assertion_error"
7. Level 1 attempted — fails (broken code cannot self-fix in stub)
8. Level 1 attempted again — fails
9. Level 2 attempted — fails
10. Level 3 attempted — fails
11. Level 4 attempted — fails
12. Level 5 reached — print human message

Also demonstrate Loop_Counter threshold:
- Call escalate() a third time with the same error_signature
- Show that Level 1 is skipped and escalation jumps directly to Level 2

### Run 3 — Drift detection
Simulate drift:

1. task_specification = "Build a JWT authentication API that validates
   tokens and returns user roles"
2. on_track_output = "Implemented JWT token validation with role-based
   access control. Tokens are verified using HS256 algorithm."
3. drifted_output = "Built a shopping cart with product listing and
   price calculation features."

Compute and print DriftCheckResult for both outputs.
Show that on_track_output does NOT trigger drift.
Show that drifted_output DOES trigger drift.

### Expected terminal output

```
=== RUN 1: HAPPY PATH ===
[FORGEAI] Task created: Build Auth API | State: PHASE_LOCKED
... (same as Phase 2 output)
[FORGEAI] QA approved | State: DONE

=== RUN 2: ESCALATION PATH ===
[FORGEAI] Task created: Build Payment API | State: PHASE_LOCKED
[FORGEAI] Phase transition approved | State: TODO
[FORGEAI] Task assigned to backend_agent_1 | State: IN_PROGRESS
[FORGEAI] Tests failed — initiating escalation
[ESCALATION] Level 1: Self-retry attempt 1... failed
[ESCALATION] Level 1: Self-retry attempt 2... failed (retries exhausted)
[ESCALATION] Level 2: Peer assist... failed
[ESCALATION] Level 3: Architect review... failed
[ESCALATION] Level 4: Task rewrite... failed
[ESCALATION] Level 5: Human input required
[FORGEAI] ⚠ Task needs human input: The Payment API task has failed
           after all automated recovery attempts. The core issue is
           test failures that could not be resolved automatically.
           Please review the task specification or provide guidance.

--- Loop_Counter threshold demonstration ---
[ESCALATION] Same error seen 3 times — skipping Level 1, jumping to Level 2
[ESCALATION] Level 2: Peer assist... failed
[ESCALATION] Level 3: Architect review... failed
[ESCALATION] Level 4: Task rewrite... failed
[ESCALATION] Level 5: Human input required

=== RUN 3: DRIFT DETECTION ===
Task specification: Build a JWT authentication API...

Output 1 (on-track):
  Drift score: XX/100
  Is drifting: False
  Description: Output is aligned with task specification

Output 2 (drifted):
  Drift score: XX/100
  Is drifting: True
  Description: Output has diverged significantly from task specification
```

---

## INTENTIONALLY BROKEN CODE FOR RUN 2

**broken_code:**
```python
def process_payment(amount: float) -> dict:
    # Intentionally wrong — returns nothing useful
    return {}
```

**test_code for broken_code:**
```python
from main import process_payment

def test_payment_returns_transaction_id():
    result = process_payment(99.99)
    assert "transaction_id" in result

def test_payment_returns_status():
    result = process_payment(99.99)
    assert result.get("status") == "success"
```

These tests will always fail against the broken code. That is correct.
The Sandbox will return RunnerOutput.success=False and the
EscalationLadder will activate.

---

## TESTS

### test_loop_counter.py

- Test increment returns correct count
- Test get returns 0 for unseen task/error combination
- Test should_escalate returns False below threshold
- Test should_escalate returns True at exactly 3
- Test reset clears all counters for a task
- Test reset does not affect other tasks
- Test different error signatures on same task are tracked independently

### test_escalation_ladder.py

- Test Level 1 is attempted first on first failure
- Test Level 1 is attempted maximum 2 times
- Test Level 2 is reached after Level 1 is exhausted
- Test all five levels are reached when none resolve
- Test Level 5 returns needs_human_input=True
- Test Level 5 returns a non-empty human_message
- Test loop_counter >= 3 skips Level 1 and enters at Level 2
- Test get_events returns events in chronological order
- Test get_current_level returns the highest level reached
- Test Lead_Agent does not retry after Level 5
  (escalate() called again after Level 5 raises AlreadyEscalatedError)

### test_drift_monitor.py

- Test identical texts produce drift score of 0
- Test completely unrelated texts produce drift score above threshold
- Test semantically similar texts produce drift score below threshold
- Test is_drifting returns False below threshold
- Test is_drifting returns True above threshold
- Test check() returns a DriftCheckResult with all fields populated
- Test description is a non-empty string in both drifting and
  non-drifting cases
- Test custom threshold is respected

### Add to existing test_qa_integration.py

- Test that a task with failing tests triggers escalation
- Test that escalation result is logged correctly

---

## NEW EXCEPTION — add to existing exception hierarchy

```python
class AlreadyEscalatedError(ForgeAIError):
    # Raised when escalate() is called on a task that has already
    # reached Level 5. Lead_Agent must not retry without human input.
    pass
```

---

## REQUIREMENTS BEING IMPLEMENTED

- **Req 08** — Failure Escalation Ladder
  - All five levels implemented (stubs for 1-4, real logic for 5)
  - Criterion 6: Lead_Agent SHALL NOT retry Level 5 without user input ✓
  - Criterion 8: Loop_Counter >= 2 triggers model upgrade (stubbed —
    real model routing added in Phase 5 with Model_Router)
- **Req 09** — Loop and Drift Prevention
  - Criterion 1-3: Drift_Monitor with Semantic_Drift_Score ✓
  - Criterion 4-6: Loop_Counter with reset on state transition ✓

---

## CODE QUALITY RULES

- All escalation events must be logged at WARNING level
- Level 5 events must be logged at ERROR level
- Drift score computation must be logged at INFO level with the score
- The SWAP_POINT comment must appear on every call to
  embeddings.compute_similarity() — do not omit it
- All new classes must have full type hints
- LoopCounter must be thread-safe (use asyncio.Lock for the counter dict)
- EscalationLadder must log each level attempt with:
  task_id, level number, outcome (resolved/failed), timestamp

---

## WHAT SUCCESS LOOKS LIKE

Run this and it works:

```bash
python main.py
pytest tests/ -v
```

- main.py shows all three runs completing correctly
- Drift scores print with real numbers (not placeholders)
- Escalation progresses through all five levels in Run 2
- Loop_Counter threshold skip is visible in Run 2
- All 38 existing tests still pass
- New tests pass (target: 55+ total)
- No Docker containers left running after tests:
  docker ps shows only forgeai-postgres-1

That is Phase 3 complete.
