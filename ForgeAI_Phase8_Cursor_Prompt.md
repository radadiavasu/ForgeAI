# ForgeAI — Phase 8 Cursor Prompt
# Paste this entire document into Cursor as your starting instruction

---

## CONTEXT

This is Phase 8 of a 10-phase build plan for ForgeAI — an AI agent
orchestration system that simulates a real software company.

Phases 1-7 are complete (171 tests passing).
- Phase 1: Task_State_Machine, mock agents, audit log
- Phase 2: Sandbox, Test_Runner, real Docker execution
- Phase 3: Escalation_Ladder, Loop_Counter, Drift_Monitor
- Phase 4: Redis, Chroma, MinIO, full persistence
- Phase 5: Real LLM calls, Model_Router, Research_Agent, Architect_Agent
- Phase 6: Bootstrap Protocol, Navigation_Contract, Component_Registry
- Phase 6B: Playwright frontend QA, dual-mode QA_Agent
- Phase 7: QA rejection loop, Human Gate, Phase_Completion_Report

Do NOT modify any existing Phase 1-7 code unless a specific
instruction below requires it. Build on top of what exists.

---

## BUILD NOTES FROM PREVIOUS PHASES

These decisions must be respected:

1. All new agents attempt LOW tier first, fall back to MEDIUM on
   schema validation failure.
2. All agents use defensive normalisation before Pydantic validation.
3. Large structured documents: first attempt 16384, retry 32768.
4. Sandbox containers always destroyed after execution.
5. network_mode="none" mandatory on every backend container.
6. Loop_Counter threshold is 3 — consistent across all agents.
7. Human approval simulated via auto_approve() callback in main.py.
8. Phase_Completion_Report format: plain language, no jargon,
   same structure for both frontend and backend gates.
9. Deferred items in report show generic labels — fix in Phase 10.

---

## WHAT PHASE 8 BUILDS

Four things:

1. **Backend_Agent full orchestration** — Lead_Agent runs all
   backend tasks through the complete cycle: read API_Contract →
   generate code → QA review → approve/reject loop → DONE. (Req 04)

2. **API_Contract enforcement** — Backend_Agent reads the
   API_Contract from Project_Memory before every task. QA_Agent
   validates generated endpoints against the contract. Deviations
   are defects. (Req 03)

3. **Backend Phase_Gate** — when all BACKEND_PHASE tasks reach
   DONE, Lead_Agent compiles a backend Phase_Completion_Report
   and presents it to the human. Same gate mechanism as Phase 7,
   different report content. (Req 28)

4. **Full two-cycle development model** — ForgeAI now runs
   PLANNING → FRONTEND_PHASE → HUMAN_GATE → BACKEND_PHASE →
   FINAL_REVIEW as a complete, verified sequence. (Req 28)

---

## NEW PROJECT STRUCTURE

Add these files. Do not remove any existing files:

```
forgeai/
├── orchestration/
│   ├── backend_orchestrator.py  # NEW — full backend phase runner
│   └── ...existing files...
└── ...existing files...

tests/
├── test_backend_orchestrator.py  # backend phase orchestration
├── test_api_contract_enforcement.py  # contract validation
└── ...existing files...
```

---

## BACKEND_AGENT UPDATE

Backend_Agent already produces real Python code (Phase 5).
Phase 8 adds API_Contract awareness to every task.

### Updated complete_work() — `agents/backend_agent.py`

```python
async def complete_work(self,
                         task_id: UUID,
                         task_description: str,
                         master_document_section: str,
                         api_contract: dict | None = None,
                         loop_count: int = 0) -> Task:
    # 1. Query Agent_Memory for relevant past lessons
    # 2. Build prompt:
    #    - task description
    #    - master_document_section
    #    - api_contract (if provided — required for API tasks)
    #    - top-K lessons from Agent_Memory
    #    - instruction: "Your implementation must exactly match
    #      the API_Contract endpoint, method, request schema,
    #      and response schema. Any deviation is a defect."
    # 3. Call LLMClient with task complexity and loop_count
    # 4. Parse response — extract code and test_code
    # 5. Transition IN_PROGRESS → IN_REVIEW
    pass
```

### Backend_Agent role prompt addition

Add to the existing Backend_Agent role prompt:

```
When an API_Contract is provided, your implementation is bound
by it. The endpoint path, HTTP method, request schema, and
response schema are not suggestions — they are the contract.
QA_Agent will validate your output against the contract.
Any deviation will be rejected as a defect.
```

---

## API_CONTRACT ENFORCEMENT — EXACT SPECIFICATION

### What it does

QA_Agent validates Backend_Agent output against the API_Contract
before running tests. If the generated code deviates from the
contract, QA_Agent rejects it immediately without running the
Sandbox — the deviation itself is the defect.

### ContractValidator class — add to `orchestration/backend_orchestrator.py`

```python
class ContractValidator:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def validate(self,
                        generated_code: str,
                        api_contract: dict,
                        task_description: str) -> ContractValidationResult:
        # LLM call — LOW complexity
        # Check generated_code against api_contract:
        #   - Does the endpoint path match?
        #   - Does the HTTP method match?
        #   - Does the response schema match?
        #   - Are required fields present?
        # Return ContractValidationResult
        pass
```

### ContractValidationResult schema

```python
class ContractValidationResult(BaseModel):
    valid: bool
    violations: list[str]   # plain language list of deviations
    severity: str           # "blocking" or "warning"
                            # blocking: rejects before Sandbox
                            # warning: noted in defect report only
```

### Updated QA_Agent review flow for backend tasks

```python
async def review(self,
                 task_id: UUID,
                 code: str,
                 test_code: str,
                 development_phase: str = "BACKEND_PHASE",
                 api_contract: dict | None = None
                 ) -> RunnerOutput:

    # For BACKEND_PHASE tasks with an API_Contract:
    # Step 1: Validate against contract FIRST
    if development_phase == "BACKEND_PHASE" and api_contract:
        validation = await contract_validator.validate(
            code, api_contract, task_description)
        if not validation.valid and validation.severity == "blocking":
            # Return failed RunnerOutput immediately
            # Do NOT run Sandbox — save tokens and time
            return RunnerOutput(
                success=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                test_cases=[],
                stdout="",
                stderr="\n".join(validation.violations),
                execution_time_seconds=0.0,
                sandbox_error="API contract violation: " +
                              ", ".join(validation.violations)
            )

    # Step 2: Run pytest in Sandbox (existing flow)
    return await self._run_pytest(code, test_code)
```

---

## BACKEND ORCHESTRATOR — EXACT SPECIFICATION

### BackendOrchestrator class — `orchestration/backend_orchestrator.py`

```python
class BackendOrchestrator:
    def __init__(self,
                 lead_agent: "LeadAgent",
                 backend_agent: BackendAgent,
                 qa_agent: QAAgent,
                 qa_orchestrator: QAOrchestrator,
                 contract_validator: ContractValidator,
                 db_session):
        self.lead = lead_agent
        self.backend = backend_agent
        self.qa = qa_agent
        self.qa_orch = qa_orchestrator
        self.validator = contract_validator
        self.db = db_session

    async def run_backend_phase(self,
                                 project_id: str,
                                 api_contract: dict
                                 ) -> BackendPhaseResult:
        # 1. Fetch all TODO tasks for BACKEND_PHASE
        # 2. For each task (sequential — one Backend_Agent):
        #    a. Assign task: TODO → IN_PROGRESS
        #    b. Backend_Agent generates code with API_Contract
        #    c. QAOrchestrator.process_result() handles approve/reject
        #    d. If rejected: store defect report, Backend_Agent retries
        #    e. If loop_counter >= 3: escalate
        #    f. When approved: DONE
        # 3. Return BackendPhaseResult when all tasks DONE
        pass

    async def _run_task_cycle(self,
                               task_id: str,
                               task_description: str,
                               master_doc_section: str,
                               api_contract: dict
                               ) -> QADecision:
        # Full task lifecycle for one backend task
        # Returns final QADecision (approved or escalated)
        pass
```

### BackendPhaseResult schema

```python
class BackendPhaseResult(BaseModel):
    project_id: str
    completed_tasks: list[str]      # task IDs
    total_tasks: int
    qa_cycles: int                  # total approve/reject cycles
    contract_violations_caught: int # violations caught pre-Sandbox
    escalations: int                # tasks that required escalation
    phase_duration_seconds: float
```

---

## BACKEND PHASE_GATE — EXACT SPECIFICATION

Same mechanism as Phase 7 Human Gate. Different report content.

### Backend Phase_Completion_Report content

```python
# In PhaseGate.compile_backend_report():

def format_backend_report_for_human(
        self,
        report: PhaseCompletionReport,
        backend_result: BackendPhaseResult) -> str:
    # Plain language, no jargon
    # Format:
    # "HUMAN GATE — Backend Complete
    #
    #  All X API endpoints are built and tested.
    #  The system is ready for final review.
    #
    #  APIs completed:
    #  ✓ Task creation endpoint (X tests passed)
    #  ✓ Task retrieval endpoint (X tests passed)
    #  ✓ Task completion endpoint (X tests passed)
    #  ...
    #
    #  Total tests passed: X/X
    #  Issues caught and fixed automatically: X
    #
    #  Approve to begin final review →"
    pass
```

### Phase transition after backend gate approval

```python
# In Lead_Agent:
async def execute_backend_gate(
        self,
        backend_result: BackendPhaseResult,
        project_id: str,
        human_approval_callback) -> PhaseGateResult:

    # Step 1: Compile backend Phase_Completion_Report
    report = await phase_gate.compile_backend_report(
        backend_result, project_id)

    # Step 2: Present to human — PAUSE HERE
    result = await phase_gate.present_to_human(
        report, human_approval_callback)

    # Step 3: If approved — advance to FINAL_REVIEW
    if result.approved:
        await self._set_phase("FINAL_REVIEW", project_id)

    return result
```

---

## UPDATED main.py

Add Runs 9 and 10 to main.py. Keep all existing runs.

### Run 9 — Full Backend Phase

```python
# Uses the 16 backend tasks unlocked in Run 8
# Backend_Agent processes each task with API_Contract awareness
# QA rejects any contract violations before running Sandbox
# Full rejection loop active for all tasks
```

Steps:
1. BackendOrchestrator.run_backend_phase() with API_Contract
2. For each task:
   - Backend_Agent generates Python code
   - ContractValidator checks against API_Contract
   - If violation: immediate rejection, no Sandbox
   - If valid: Sandbox runs pytest
   - QA approve/reject loop
3. Print progress for each task
4. Print BackendPhaseResult summary

### Run 10 — Backend Phase_Gate

1. Lead_Agent compiles backend Phase_Completion_Report
2. Human Gate presented (auto_approve)
3. Phase advances to FINAL_REVIEW
4. Print formatted report
5. Print phase transition confirmation

### Expected terminal output (abbreviated)

```
=== RUN 9: FULL BACKEND PHASE ===

[BACKEND] Processing task 1/16: Create task endpoint
[BACKEND] Reading API_Contract from Project_Memory...
[BACKEND] Generating implementation (claude-sonnet-4-6)...
[QA] Validating against API_Contract...
[QA] Contract validation passed ✓
[QA] Running pytest...
[QA] 6/6 passed ✓ — Task 1 DONE

[BACKEND] Processing task 2/16: Get all tasks endpoint
[BACKEND] Generating implementation...
[QA] Validating against API_Contract...
[QA] Contract violation: response missing 'created_at' field
[QA] Rejected immediately (no Sandbox) — attempt 1
[BACKEND] Fixing: add 'created_at' to response schema...
[QA] Contract validation passed ✓
[QA] Running pytest...
[QA] 5/5 passed ✓ — Task 2 DONE

...

[BACKEND] All 16 tasks complete
[BACKEND] Summary:
  Total tasks: 16
  QA cycles: 22
  Contract violations caught: 4
  Escalations: 0
  Time: ~XX minutes

=== RUN 10: BACKEND PHASE GATE ===

[LEAD] Compiling backend Phase_Completion_Report...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 HUMAN GATE — Backend Complete

 All 16 API endpoints are built and tested.
 The system is ready for final review.

 APIs completed:
 ✓ Create task endpoint (6 tests passed)
 ✓ Get all tasks endpoint (5 tests passed)
 ✓ Complete task endpoint (4 tests passed)
 ... (all 16)

 Total tests passed: XX/XX
 Issues caught and fixed automatically: 4

 Approve to begin final review →
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[GATE] Human approved — advancing to FINAL_REVIEW
[LEAD] Phase: BACKEND_PHASE → FINAL_REVIEW
```

---

## TESTS

### test_backend_orchestrator.py

Mock LLMClient and Sandbox. No real API or Docker calls.

- Test run_backend_phase() processes all TODO backend tasks
- Test tasks are processed sequentially (one at a time)
- Test API_Contract passed to Backend_Agent for each task
- Test BackendPhaseResult contains correct task count
- Test BackendPhaseResult tracks qa_cycles correctly
- Test BackendPhaseResult tracks contract_violations_caught
- Test rejection loop works for backend tasks
- Test escalation triggered when loop_counter reaches 3
- Test phase advances to FINAL_REVIEW after gate approval

### test_api_contract_enforcement.py

Mock LLMClient. No real API calls.

- Test ContractValidator returns valid=True when code matches contract
- Test ContractValidator returns valid=False on endpoint mismatch
- Test ContractValidator returns valid=False on missing response field
- Test ContractValidator returns valid=False on wrong HTTP method
- Test blocking violation skips Sandbox execution
- Test warning violation proceeds to Sandbox
- Test violations list is non-empty when invalid
- Test RunnerOutput populated correctly on contract violation
- Test sandbox_error field contains violation description

---

## REQUIREMENTS BEING IMPLEMENTED

- **Req 03** — API Contract Negotiation
  - Contract enforced at implementation time ✓
  - Violations caught before Sandbox execution ✓
- **Req 04** — Task Creation and Assignment
  - Backend tasks orchestrated through full lifecycle ✓
  - Task_Dependency_Graph respected ✓
- **Req 28** — Development Phase Gates
  - BACKEND_PHASE → FINAL_REVIEW gate implemented ✓
  - Same gate mechanism as FRONTEND → BACKEND ✓
  - Plain language report, no jargon ✓
- **Req 06** — Task Review and Quality Gate
  - Full rejection loop for backend tasks ✓
  - No self-approval enforced ✓

---

## CODE QUALITY RULES

- All LLM calls in tests must be mocked — no real API calls
- ContractValidator prompt must be a module-level constant
- BackendOrchestrator must log every task start and completion:
  INFO: "Backend task started: {task_id} {title}"
  INFO: "Backend task complete: {task_id} cycles={n}"
  WARNING: "Contract violation: {task_id} {violations}"
- Backend Phase_Completion_Report must pass the same no-jargon
  test as the frontend report — no words: "agent", "LLM",
  "Chroma", "PostgreSQL", "API_Contract"
- Apply BUILD_NOTES defensive normalisation to all new LLM
  response parsing in ContractValidator

---

## WHAT SUCCESS LOOKS LIKE

```bash
python main.py
pytest tests/ -v
```

- Run 9 shows all 16 backend tasks completing with real LLM code
- At least one contract violation is caught and fixed
- Run 10 shows the backend Human Gate with plain language report
- Phase advances to FINAL_REVIEW after approval
- All 171 existing tests still pass
- New tests pass with mocked LLM
- Target: 200+ total tests passing

That is Phase 8 complete.
