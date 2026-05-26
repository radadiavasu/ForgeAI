# ForgeAI — Phase 2 Cursor Prompt
# Paste this entire document into Cursor as your starting instruction

---

## CONTEXT

This is Phase 2 of a 10-phase build plan for ForgeAI — an AI agent
orchestration system that simulates a real software company.

Phase 1 is complete and passing. It delivered:
- Task_State_Machine with all permitted transitions enforced
- TaskStateHistory audit log
- Mock agent stubs (LeadAgent, BackendAgent, QAAgent)
- 23 passing tests

Do NOT modify any existing Phase 1 code unless a specific instruction
below requires it. Build on top of what exists.

---

## WHAT PHASE 2 BUILDS

Two things only:

1. **Sandbox** — an isolated Docker container that executes code safely.
   No network egress. Restricted filesystem. Destroyed after each run.

2. **Test_Runner** — runs a test suite inside the Sandbox and returns
   structured results that QAAgent uses to make its pass/fail decision.

When Phase 2 is complete, QAAgent is no longer a stub that blindly
approves work. It submits code to the Sandbox, receives real test
results, and makes its decision based on those results.

---

## TECH STACK ADDITIONS — PHASE 2 ONLY

Add to requirements.txt:
- `docker` (docker-py — Python SDK for Docker)

Do NOT introduce:
- Redis
- Chroma or any vector database
- FastAPI or any HTTP layer
- Anthropic SDK or any LLM calls
- Any frontend framework

---

## NEW PROJECT STRUCTURE

Add these files to the existing structure. Do not remove any existing files:

```
forgeai/
├── sandbox/
│   ├── __init__.py
│   ├── sandbox.py          # Sandbox — provisions and destroys containers
│   ├── runner.py           # Test_Runner — executes tests, returns results
│   └── schemas.py          # Pydantic schemas for TestResult and RunnerOutput
├── agents/
│   └── qa_agent.py         # UPDATE ONLY — replace stub with real Sandbox integration
└── ...existing files...

tests/
├── test_sandbox.py         # Sandbox provisioning and isolation tests
├── test_runner.py          # Test_Runner execution and result parsing tests
├── test_qa_integration.py  # Full QA cycle: code submitted → Sandbox → result → decision
└── ...existing files...
```

---

## SANDBOX — EXACT SPECIFICATION

### What the Sandbox does

The Sandbox provisions an ephemeral Docker container, copies submitted
code and test files into it, executes the tests, captures the output,
then destroys the container. The host filesystem is never touched.
Network egress is blocked at the container level.

### Sandbox configuration

These values must be configurable via .env. Add to .env.example:

```
SANDBOX_IMAGE=python:3.11-slim
SANDBOX_CPU_LIMIT=1.0
SANDBOX_MEMORY_LIMIT=256m
SANDBOX_TIMEOUT_LOW=60
SANDBOX_TIMEOUT_MEDIUM=180
SANDBOX_TIMEOUT_HIGH=600
SANDBOX_WORKING_DIR=/sandbox
```

### Sandbox class — `sandbox/sandbox.py`

```python
class Sandbox:
    def __init__(self, complexity: str, config: SandboxConfig):
        # complexity: LOW / MEDIUM / HIGH
        # config: loaded from .env
        pass

    async def run(self, code: str, test_code: str) -> RunnerOutput:
        # 1. Pull image if not cached (python:3.11-slim, pinned — never :latest)
        # 2. Provision container with:
        #    - network_mode="none"  ← no network egress, non-negotiable
        #    - mem_limit from config
        #    - nano_cpus from config (1.0 CPU = 1_000_000_000 nano_cpus)
        #    - working_dir = SANDBOX_WORKING_DIR
        #    - auto_remove=False (we remove manually after capturing output)
        # 3. Write code to /sandbox/main.py inside the container
        # 4. Write test_code to /sandbox/test_main.py inside the container
        # 5. Execute: python -m pytest test_main.py -v --tb=short --no-header
        #    with timeout from config based on complexity
        # 6. Capture stdout and stderr
        # 7. Destroy container unconditionally (even if execution fails)
        # 8. Parse output and return RunnerOutput
        pass

    async def _write_file(self, container, path: str, content: str) -> None:
        # Write a string as a file into the container filesystem
        # Use container.put_archive() with a tar stream
        pass

    async def _destroy(self, container) -> None:
        # Stop and remove container unconditionally
        # Must not raise if container is already gone
        pass
```

### Critical rules for the Sandbox

1. `network_mode="none"` is mandatory on every container. No exceptions.
2. The container is ALWAYS destroyed after execution — success or failure.
   Never leave a container running.
3. Never use `:latest` image tags. Pin to `python:3.11-slim` exactly.
4. If the Sandbox fails to provision, raise `SandboxProvisionError` and
   log the failure. Do not silently swallow it.
5. If execution exceeds the timeout for the task's complexity tier,
   raise `SandboxTimeoutError`, destroy the container, and return a
   failed RunnerOutput.
6. Sandbox must work on Windows with Docker Desktop (the dev environment
   is Windows 11 with Docker Desktop running Linux containers).

---

## TEST_RUNNER — EXACT SPECIFICATION

### What the Test_Runner does

The Test_Runner sits between QAAgent and the Sandbox. It takes code
and test code, submits them to the Sandbox, and parses the raw pytest
output into a structured RunnerOutput.

### Pydantic schemas — `sandbox/schemas.py`

```python
class TestCaseResult(BaseModel):
    name: str           # test function name e.g. "test_auth_returns_token"
    passed: bool
    stdout: str = ""    # captured stdout for this test
    error: str = ""     # error message if failed, empty if passed

class RunnerOutput(BaseModel):
    success: bool               # True only if ALL tests passed
    total_tests: int
    passed_tests: int
    failed_tests: int
    test_cases: list[TestCaseResult]
    stdout: str                 # full raw stdout from pytest
    stderr: str                 # full raw stderr
    execution_time_seconds: float
    timed_out: bool = False
    sandbox_error: str = ""     # populated if Sandbox itself failed
```

### Test_Runner class — `sandbox/runner.py`

```python
class TestRunner:
    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox

    async def run(self, code: str, test_code: str) -> RunnerOutput:
        # 1. Submit code and test_code to self.sandbox.run()
        # 2. Parse the raw pytest output into RunnerOutput
        # 3. Return RunnerOutput
        pass

    def _parse_pytest_output(self, stdout: str, stderr: str,
                              execution_time: float) -> RunnerOutput:
        # Parse pytest -v output format:
        # "PASSED" and "FAILED" lines identify individual test results
        # Final summary line: "X passed, Y failed in Z.ZZs"
        # Return fully populated RunnerOutput
        pass
```

### Parsing rules for pytest output

Parse the pytest `-v` output format:

- Each test result line ends with `PASSED` or `FAILED`
- Extract test name from the line (everything before `PASSED`/`FAILED`)
- Final summary line format: `X passed` or `X passed, Y failed in Z.ZZs`
- If stdout is empty or unparseable, return RunnerOutput with
  success=False, sandbox_error="No output captured"
- Execution time comes from the summary line if present, otherwise
  use the wall clock time from the Sandbox

---

## QA_AGENT UPDATE

Replace the existing QAAgent stub with a real implementation that uses
the Sandbox. The state machine calls remain identical — only the
internal decision logic changes.

### Updated QAAgent — `agents/qa_agent.py`

```python
class QAAgent(BaseAgent):
    def __init__(self, agent_id: str, db_session, test_runner: TestRunner):
        super().__init__(agent_id, db_session)
        self.test_runner = test_runner

    async def begin_review(self, task_id: UUID) -> Task:
        # Transition IN_REVIEW → TESTING (unchanged from Phase 1)
        pass

    async def review(self, task_id: UUID,
                     code: str, test_code: str) -> RunnerOutput:
        # 1. Check self-approval: load task, verify task.assigned_agent
        #    is NOT self.agent_id. Raise SelfApprovalError if it is.
        # 2. Submit code and test_code to self.test_runner.run()
        # 3. Return RunnerOutput — do NOT transition state here.
        #    The caller (LeadAgent) reads RunnerOutput and decides.
        pass

    async def approve(self, task_id: UUID, output: str) -> Task:
        # Transition TESTING → DONE (unchanged from Phase 1)
        # Only called by LeadAgent after RunnerOutput.success is True
        pass

    async def reject(self, task_id: UUID,
                     defect_report: str) -> Task:
        # Transition TESTING → IN_PROGRESS with defect_report
        # Only called by LeadAgent after RunnerOutput.success is False
        pass
```

### Self-approval rule — reinforced

The self-approval check in `review()` is the primary enforcement point.
It must compare the agent_id that last transitioned the task to
IN_REVIEW (stored in TaskStateHistory) against self.agent_id.
Raise SelfApprovalError if they match. This is Req 06 criterion 8.

---

## UPDATED main.py

Replace the existing main.py with a version that runs a real Sandbox
cycle. The cycle:

1. LeadAgent creates task: "Build Auth API", complexity=MEDIUM
2. LeadAgent approves phase transition → TODO
3. LeadAgent assigns task → IN_PROGRESS
4. BackendAgent produces real Python code (hardcoded string below)
5. BackendAgent submits code + tests to QAAgent → IN_REVIEW
6. QAAgent begins review → TESTING
7. QAAgent.review() submits to Sandbox via Test_Runner
8. Sandbox executes tests in an isolated Docker container
9. RunnerOutput returned — if success: LeadAgent calls approve() → DONE
10. Print RunnerOutput details and full TaskStateHistory

### Hardcoded code for the demo cycle

Use these exact strings so the Sandbox has something real to execute:

**code (main.py inside Sandbox):**
```python
def generate_token(user_id: str) -> str:
    import hashlib
    return hashlib.sha256(f"token:{user_id}".encode()).hexdigest()

def validate_token(token: str, user_id: str) -> bool:
    expected = generate_token(user_id)
    return token == expected
```

**test_code (test_main.py inside Sandbox):**
```python
from main import generate_token, validate_token

def test_generate_token_returns_string():
    token = generate_token("user_123")
    assert isinstance(token, str)
    assert len(token) == 64

def test_validate_token_correct():
    token = generate_token("user_123")
    assert validate_token(token, "user_123") is True

def test_validate_token_wrong_user():
    token = generate_token("user_123")
    assert validate_token(token, "user_456") is False

def test_validate_token_empty():
    token = generate_token("")
    assert validate_token(token, "") is True
```

### Expected terminal output

```
[FORGEAI] Task created: Build Auth API | State: PHASE_LOCKED
[FORGEAI] Phase transition approved | State: TODO
[FORGEAI] Task assigned to backend_agent_1 | State: IN_PROGRESS
[FORGEAI] Work completed by backend_agent_1 | State: IN_REVIEW
[FORGEAI] QA review started | State: TESTING
[FORGEAI] Sandbox executing tests...
[FORGEAI] Tests complete: 4/4 passed in X.XXs
[FORGEAI] QA approved | State: DONE

--- RUNNER OUTPUT ---
Success: True
Total: 4 | Passed: 4 | Failed: 0
  ✓ test_generate_token_returns_string
  ✓ test_validate_token_correct
  ✓ test_validate_token_wrong_user
  ✓ test_validate_token_empty

--- FULL STATE HISTORY ---
1. PHASE_LOCKED → TODO         | agent: lead_agent_1    | success: True
2. TODO → IN_PROGRESS          | agent: lead_agent_1    | success: True
3. IN_PROGRESS → IN_REVIEW     | agent: backend_agent_1 | success: True
4. IN_REVIEW → TESTING         | agent: qa_agent_1      | success: True
5. TESTING → DONE              | agent: qa_agent_1      | success: True
```

---

## TESTS

### test_sandbox.py

- Test that a container is provisioned and destroyed in one run
- Test that `network_mode="none"` is enforced (attempt a network call
  inside the container, assert it fails)
- Test that a container exceeding timeout raises SandboxTimeoutError
- Test that the container is destroyed even when execution fails
- Test that code written to the container executes correctly

### test_runner.py

- Test that passing tests return RunnerOutput with success=True
- Test that failing tests return RunnerOutput with success=False
- Test that partial failures (some pass, some fail) are correctly counted
- Test that RunnerOutput.test_cases contains one entry per test
- Test that a syntax error in submitted code returns success=False with
  a populated stderr
- Test _parse_pytest_output with a known pytest output string

### test_qa_integration.py

- Test full cycle: code + tests → Sandbox → RunnerOutput → DONE
- Test rejection cycle: code with failing tests → RunnerOutput →
  task returns to IN_PROGRESS with defect_report populated
- Test self-approval: same agent_id as task producer attempts review,
  assert SelfApprovalError raised, task state unchanged
- Test that task.output is populated when task reaches DONE

---

## REQUIREMENTS BEING IMPLEMENTED

- **Req 18** — Code Execution Sandbox (fully implemented)
  - Criterion 1: ephemeral container per task ✓
  - Criterion 2: no network egress, restricted filesystem, CPU/memory limits ✓
  - Criterion 3: QA executes tests via Test_Runner inside Sandbox ✓
  - Criterion 4: container destroyed and recreated per execution ✓
  - Criterion 5: SandboxProvisionError raised and treated as escalation ✓
  - Criterion 6: RunnerOutput schema matches required fields ✓
  - Criterion 7: timeout defaults by complexity tier ✓
- **Req 06, criterion 8** — No self-approval (reinforced at review() level)

---

## CODE QUALITY RULES

- All Sandbox and Test_Runner operations must be async
- Use Python logging for all Sandbox lifecycle events:
  INFO for provision, execute, destroy
  WARNING for timeouts
  ERROR for provision failures
- All containers must be named with a unique ID per run for
  debuggability: e.g. `forgeai-sandbox-{uuid4()}`
- Never use shell=True in any subprocess or exec call
- All Pydantic schemas must have full type hints
- Add SandboxProvisionError and SandboxTimeoutError to the existing
  ForgeAI exception hierarchy in state_machine/machine.py

---

## WHAT SUCCESS LOOKS LIKE

Run this and it works:

```bash
python main.py
pytest tests/ -v
```

- main.py shows real test results from a real Docker container
- All existing 23 tests still pass
- New Sandbox and QA integration tests pass
- No Docker containers left running after tests complete
  (verify with: docker ps — should show only forgeai-postgres-1)

That is Phase 2 complete.
