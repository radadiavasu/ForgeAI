# ForgeAI — Phase 7 Cursor Prompt
# Paste this entire document into Cursor as your starting instruction

---

## CONTEXT

This is Phase 7 of a 10-phase build plan for ForgeAI — an AI agent
orchestration system that simulates a real software company.

Phases 1-6B are complete (147 tests passing).
- Phase 1: Task_State_Machine, mock agents, audit log
- Phase 2: Sandbox, Test_Runner, real Docker execution
- Phase 3: Escalation_Ladder, Loop_Counter, Drift_Monitor
- Phase 4: Redis, Chroma, MinIO, full persistence
- Phase 5: Real LLM calls, Model_Router, Research_Agent, Architect_Agent
- Phase 6: Bootstrap Protocol, Navigation_Contract, Component_Registry
- Phase 6B: Playwright frontend QA, dual-mode QA_Agent

Do NOT modify any existing Phase 1-6B code unless a specific
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
6. Frontend sandbox uses forgeai-frontend-sandbox:latest image.
7. Layout specification has deterministic fallback on parse failure.
8. Loop_Counter threshold is 3 — consistent across all agents.
9. Human approval is simulated via auto_approve() callback in main.py.

---

## WHAT PHASE 7 BUILDS

Five things:

1. **Full QA rejection loop** — RunnerOutput drives a real decision.
   Pass → DONE. Fail → structured defect report, task back to
   IN_PROGRESS, reassigned to original agent. Applies to both
   pytest and Playwright modes. (Req 06)

2. **Lead_Agent full orchestration** — Lead_Agent reads every
   RunnerOutput, makes approve/reject decision, coordinates
   reassignment to the correct agent, tracks retry count via
   Loop_Counter. (Req 04, Req 08)

3. **Human Gate** — when all FRONTEND_PHASE tasks reach DONE,
   Lead_Agent compiles Phase_Completion_Report, presents to human,
   pauses all backend tasks until Phase_Transition_Approval
   received. (Req 28)

4. **Phase_Completion_Report** — structured summary of all
   completed frontend work. (Req 28)

5. **API_Contract review at gate** — Lead_Agent reviews
   API_Contract against verified frontend before backend starts.
   Updates if necessary. (Req 03, Req 28)

---

## NEW PROJECT STRUCTURE

Add these files. Do not remove any existing files:

```
forgeai/
├── orchestration/
│   ├── __init__.py
│   ├── qa_loop.py          # Full QA approval/rejection orchestration
│   ├── phase_gate.py       # Human Gate — Phase_Completion_Report
│   └── schemas.py          # PhaseCompletionReport, DefectReport,
│                           # PhaseGateResult, APIContractReview
└── ...existing files...

tests/
├── test_qa_loop.py         # approval, rejection, reassignment, retry
├── test_phase_gate.py      # report compilation, gate pause, approval
├── test_api_contract_review.py  # contract review at gate
└── ...existing files...
```

---

## FULL QA REJECTION LOOP — EXACT SPECIFICATION

### DefectReport schema — `orchestration/schemas.py`

```python
class DefectReport(BaseModel):
    task_id: str
    agent_id: str               # QA_Agent that produced this report
    original_agent_id: str      # agent that produced the failing work
    failure_summary: str        # plain language — what failed
    failed_tests: list[str]     # test names that failed
    passed_tests: list[str]     # test names that passed
    execution_mode: str         # "pytest" or "playwright"
    suggestions: str            # what the original agent should fix
    retry_count: int            # how many times this task has failed QA
    created_at: datetime
```

### QAOrchestrator class — `orchestration/qa_loop.py`

```python
class QAOrchestrator:
    def __init__(self,
                 state_machine: TaskStateMachine,
                 loop_counter: LoopCounter,
                 escalation_ladder: EscalationLadder,
                 llm_client: LLMClient,
                 db_session):
        self.sm = state_machine
        self.loop_counter = loop_counter
        self.escalation = escalation_ladder
        self.llm = llm_client
        self.db = db_session

    async def process_result(self,
                              task_id: str,
                              runner_output: RunnerOutput,
                              qa_agent_id: str,
                              original_agent_id: str,
                              development_phase: str
                              ) -> QADecision:
        # Decision logic:
        # IF runner_output.success is True:
        #   → call _approve(task_id, qa_agent_id)
        #   → return QADecision(approved=True)
        #
        # IF runner_output.success is False:
        #   → generate DefectReport via LLM
        #   → check loop_counter for this task + "qa_failure"
        #   → IF loop_counter >= 3: escalate immediately
        #   → ELSE: reject and reassign
        #   → return QADecision(approved=False, defect_report=...)
        pass

    async def _approve(self, task_id: str,
                        qa_agent_id: str) -> None:
        # Transition TESTING → DONE
        # Reset loop_counter for this task
        # Write task output to Project_Memory
        pass

    async def _reject(self, task_id: str,
                       qa_agent_id: str,
                       defect_report: DefectReport) -> None:
        # Transition TESTING → IN_PROGRESS
        # Attach defect_report to task
        # Increment loop_counter for "qa_failure"
        # Log rejection event
        pass

    async def _generate_defect_report(
            self,
            task_id: str,
            runner_output: RunnerOutput,
            qa_agent_id: str,
            original_agent_id: str) -> DefectReport:
        # LLM call — LOW complexity
        # Input: runner_output (failed tests, stdout, stderr)
        # Output: DefectReport with:
        #   - plain language failure summary
        #   - specific suggestions for the original agent
        #   - list of failed test names
        # LOW tier first, MEDIUM fallback on parse failure
        pass

    async def _reassign_to_original_agent(
            self,
            task_id: str,
            original_agent_id: str,
            defect_report: DefectReport) -> None:
        # Store defect_report in Task_Memory for the task
        # Key: "defect_report"
        # Original agent reads this when it picks up the task
        # Log reassignment event
        pass
```

### QADecision schema

```python
class QADecision(BaseModel):
    task_id: str
    approved: bool
    defect_report: DefectReport | None = None
    escalated: bool = False
    escalation_result: EscalationResult | None = None
```

### DefectReport generation prompt

```
You are QA_Agent analyzing test failures.

Failed tests: {failed_test_names}
Test output: {stdout}
Error details: {stderr}

Produce a structured defect report with:
1. failure_summary: one plain-language sentence describing the core problem
2. suggestions: specific actionable steps the developer should take to fix it
3. failed_tests: list of test names that failed
4. passed_tests: list of test names that passed

Output JSON only.
```

---

## LEAD_AGENT FULL ORCHESTRATION

### Add these methods to LeadAgent

```python
async def orchestrate_qa(self,
                          task_id: str,
                          code: str,
                          test_code: str,
                          qa_agent: QAAgent,
                          original_agent_id: str,
                          development_phase: str,
                          page_spec: PageSpec | None = None
                          ) -> QADecision:
    # 1. QA_Agent begins review: IN_REVIEW → TESTING
    # 2. QA_Agent runs tests (pytest or playwright based on phase)
    # 3. QAOrchestrator.process_result() makes approve/reject decision
    # 4. If rejected: store defect report, reassign to original agent
    # 5. Return QADecision
    pass

async def run_frontend_phase(self,
                              frontend_agents: list[FrontendAgent],
                              qa_agent: QAAgent,
                              layout_spec: LayoutSpecification,
                              navigation_contract: NavigationContract,
                              project_id: str) -> FrontendPhaseResult:
    # Run all FRONTEND_PHASE tasks through the full cycle:
    # 1. Root layout task first (Frontend_Agent #1)
    # 2. Wait for root layout DONE → unlock dependent tasks
    # 3. Remaining frontend tasks (can run in parallel)
    # 4. Each task: build → QA → approve/reject loop
    # 5. If rejected: agent fixes → QA again
    # 6. When all tasks DONE: compile Phase_Completion_Report
    # 7. Return FrontendPhaseResult
    pass

async def handle_qa_rejection(self,
                               task_id: str,
                               defect_report: DefectReport,
                               original_agent: FrontendAgent | BackendAgent
                               ) -> None:
    # Store defect report in Task_Memory
    # Notify original agent of the defect
    # Log the rejection event
    # The agent reads defect_report from Task_Memory
    # when it picks up the IN_PROGRESS task again
    pass
```

### FrontendPhaseResult schema

```python
class FrontendPhaseResult(BaseModel):
    project_id: str
    completed_tasks: list[str]      # task IDs
    total_tasks: int
    qa_cycles: int                  # total approve/reject cycles
    components_registered: list[str]
    agents_used: list[str]
    phase_duration_seconds: float
```

---

## HUMAN GATE — EXACT SPECIFICATION

### PhaseCompletionReport schema — `orchestration/schemas.py`

```python
class TaskSummary(BaseModel):
    task_id: str
    title: str
    agent_id: str
    qa_cycles: int          # how many times QA ran before DONE
    final_status: str       # always "DONE" in the report

class PhaseCompletionReport(BaseModel):
    project_id: str
    phase: str              # "FRONTEND_PHASE"
    completed_tasks: list[TaskSummary]
    total_tasks: int
    total_qa_cycles: int
    components_registry: list[ComponentEntry]
    navigation_contract_summary: str  # plain language route summary
    deferred_items: list[str]         # anything not completed
    compiled_at: datetime
    compiled_by: str        # "lead_agent"

class PhaseGateResult(BaseModel):
    approved: bool
    approved_at: datetime | None = None
    feedback: str = ""      # if not approved, what needs fixing
    api_contract_updated: bool = False
```
```

### PhaseGate class — `orchestration/phase_gate.py`

```python
class PhaseGate:
    def __init__(self,
                 lead_agent: "LeadAgent",
                 llm_client: LLMClient,
                 db_session):
        self.lead = lead_agent
        self.llm = llm_client
        self.db = db_session

    async def compile_report(
            self,
            frontend_phase_result: FrontendPhaseResult,
            component_registry: ComponentRegistry,
            navigation_contract: NavigationContract,
            project_id: str) -> PhaseCompletionReport:
        # Compile all frontend work into structured report
        # Query completed tasks from PostgreSQL
        # Get Component_Registry contents
        # Summarise Navigation_Contract in plain language
        # Return PhaseCompletionReport
        pass

    async def present_to_human(
            self,
            report: PhaseCompletionReport,
            human_approval_callback) -> PhaseGateResult:
        # Format report as plain-language summary
        # Call human_approval_callback with formatted summary
        # Return PhaseGateResult
        pass

    def format_report_for_human(
            self,
            report: PhaseCompletionReport) -> str:
        # Convert PhaseCompletionReport to plain language
        # No technical jargon — human should understand
        # without knowing what agents are
        # Format:
        # "HUMAN GATE — Frontend Complete
        #
        #  All X pages are built and verified.
        #  No backend code has been written yet.
        #
        #  Pages completed:
        #  ✓ Dashboard (X tests passed)
        #  ✓ History (X tests passed)
        #  ✓ Settings (X tests passed)
        #
        #  Shared components built: AppLayout, NavBar, Footer
        #
        #  Approve to start Backend Phase →"
        pass

    async def review_api_contract(
            self,
            api_contract: dict,
            frontend_phase_result: FrontendPhaseResult,
            project_id: str) -> APIContractReview:
        # LLM call — MEDIUM complexity
        # Lead_Agent reviews API_Contract against what
        # the verified frontend actually requires
        # Identifies any gaps or changes needed
        # Returns APIContractReview with updated contract if needed
        pass
```

### APIContractReview schema

```python
class APIContractReview(BaseModel):
    project_id: str
    original_contract: dict
    updated_contract: dict
    changes_made: list[str]     # plain language list of changes
    requires_update: bool
    reviewed_at: datetime
```

### Phase transition sequence

```python
# In Lead_Agent — add this method
async def execute_human_gate(
        self,
        frontend_phase_result: FrontendPhaseResult,
        component_registry: ComponentRegistry,
        navigation_contract: NavigationContract,
        api_contract: dict,
        project_id: str,
        human_approval_callback) -> PhaseGateResult:

    # Step 1: Compile Phase_Completion_Report
    report = await phase_gate.compile_report(...)

    # Step 2: Review API_Contract
    contract_review = await phase_gate.review_api_contract(...)
    if contract_review.requires_update:
        await self.write_to_project_memory(
            "api_contract", contract_review.updated_contract)

    # Step 3: Present to human — PAUSE HERE
    result = await phase_gate.present_to_human(
        report, human_approval_callback)

    # Step 4: If approved — unlock all backend tasks
    if result.approved:
        await self._unlock_backend_tasks(project_id)

    # Step 5: If not approved — create new frontend tasks
    # to address feedback, re-run frontend cycle
    else:
        await self._create_feedback_tasks(result.feedback)

    return result

async def _unlock_backend_tasks(self, project_id: str) -> None:
    # Find all tasks with state PHASE_LOCKED
    # Transition each to TODO
    # This is Phase_Transition_Approval received
    # Log the phase transition event
    pass
```

---

## UPDATED main.py

Replace main.py with the full Phase 7 flow.
Keep Runs 1-6 from previous phases. Add Runs 7 and 8.

### Run 7 — Full Frontend Phase with QA Loop

Build all 3 pages with real QA rejection cycles:

```python
# Intentionally produce one failing page first
# to demonstrate the rejection loop
```

Steps:
1. Bootstrap (same as Run 1)
2. Layout spec + Navigation_Contract (same as Runs 2-3)
3. Root layout — Frontend_Agent #1 builds AppLayout
4. QA runs Playwright — simulate a failure on first attempt
   (use intentionally incomplete React code)
5. QA rejects — defect report generated
6. Frontend_Agent #1 fixes the code
7. QA runs again — passes
8. Root layout DONE — dependent tasks unlock
9. Dashboard and Settings pages built and verified
10. All frontend tasks DONE

### Run 8 — Human Gate

1. Lead_Agent compiles Phase_Completion_Report
2. API_Contract reviewed and updated if needed
3. Human Gate presented (auto_approve in main.py)
4. Backend tasks unlock (Phase_Locked → TODO)
5. Print Phase_Completion_Report summary
6. Print confirmation that backend tasks are unlocked

### Expected terminal output (abbreviated)

```
=== RUN 7: FULL FRONTEND PHASE WITH QA LOOP ===

[FRONTEND #1] Building AppLayout (attempt 1)...
[QA] Running Playwright tests... 3/9 passed
[QA] Rejected — generating defect report...
[QA] Defect: Missing sections: header, task-list, nav links
[LEAD] Reassigning to frontend_agent_1 — attempt 2
[FRONTEND #1] Fixing: header section, task-list, nav links...
[QA] Running Playwright tests... 9/9 passed ✓
[QA] Approved — AppLayout DONE

[REGISTRY] Registered: AppLayout, NavBar, Footer
[LEAD] Root layout verified — unlocking dependent tasks
[LEAD] Dashboard task: Phase_Locked → TODO
[LEAD] History task: Phase_Locked → TODO

[FRONTEND #2] Building Dashboard (attempt 1)...
[QA] Running Playwright tests... 8/8 passed ✓
[QA] Approved — Dashboard DONE

[FRONTEND #2] Building Settings (attempt 1)...
[QA] Running Playwright tests... 6/6 passed ✓
[QA] Approved — Settings DONE

[LEAD] All frontend tasks DONE — compiling Phase_Completion_Report

=== RUN 8: HUMAN GATE ===

[LEAD] Compiling Phase_Completion_Report...
[LEAD] Reviewing API_Contract...
[LEAD] API_Contract — no changes required

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 HUMAN GATE — Frontend Complete

 All 3 pages are built and verified.
 No backend code has been written yet.

 Pages completed:
 ✓ AppLayout + shared components (2 QA cycles)
 ✓ Dashboard (1 QA cycle)
 ✓ Settings (1 QA cycle)

 Shared components: AppLayout, NavBar, Footer
 Total tests passed: 23/23

 Approve to start Backend Phase →
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[GATE] Human approved — starting Backend Phase
[LEAD] Unlocking backend tasks...
[LEAD] 15 backend tasks: Phase_Locked → TODO
[LEAD] BACKEND_PHASE starting
```

---

## TESTS

### test_qa_loop.py

Mock LLMClient, Sandbox, FrontendSandbox. No real API or Docker calls.

- Test process_result() approves when RunnerOutput.success=True
- Test process_result() rejects when RunnerOutput.success=False
- Test rejection transitions task TESTING → IN_PROGRESS
- Test approval transitions task TESTING → DONE
- Test defect report is generated on rejection
- Test defect report stored in Task_Memory on rejection
- Test loop_counter incremented on rejection
- Test loop_counter reset on approval
- Test escalation triggered when loop_counter reaches 3
- Test reassignment targets original_agent_id not qa_agent_id
- Test QADecision.approved is True only when all tests pass
- Test QADecision.defect_report populated on rejection

### test_phase_gate.py

Mock LLMClient. No real API calls.

- Test compile_report() returns PhaseCompletionReport
- Test report contains all completed task summaries
- Test report contains Component_Registry contents
- Test format_report_for_human() returns non-empty string
- Test format_report_for_human() contains no technical jargon
  (no words: "agent", "LLM", "Chroma", "PostgreSQL")
- Test present_to_human() calls human_approval_callback
- Test PhaseGateResult.approved=True when callback approves
- Test backend tasks transition Phase_Locked → TODO on approval
- Test backend tasks remain Phase_Locked when gate not approved
- Test re-presentation after feedback creates new tasks

### test_api_contract_review.py

Mock LLMClient. No real API calls.

- Test review_api_contract() calls LLM with MEDIUM complexity
- Test APIContractReview populated with changes list
- Test requires_update=False when no changes needed
- Test requires_update=True when gaps identified
- Test updated contract written to Project_Memory when changed

---

## REQUIREMENTS BEING IMPLEMENTED

- **Req 06** — Task Review and Quality Gate
  - Full rejection loop with defect reports ✓
  - No self-approval enforced throughout ✓
- **Req 28** — Development Phase Gates and Human Verification
  - FRONTEND_PHASE → HUMAN_GATE → BACKEND_PHASE ✓
  - Phase_Completion_Report compiled ✓
  - Backend tasks Phase_Locked until approval ✓
  - Human verification gate enforced ✓
- **Req 03** — API Contract Negotiation
  - API_Contract reviewed at gate ✓
  - Updated before backend starts ✓
- **Req 04** — Task Creation and Dependency Graph
  - Lead_Agent orchestrates full task lifecycle ✓
  - Reassignment tracked correctly ✓

---

## CODE QUALITY RULES

- All LLM calls in tests must be mocked — no real API calls
- DefectReport generation prompt must be module-level constant
- Phase_Completion_Report format_for_human() output must contain
  zero technical terms — tested explicitly
- QAOrchestrator must log every decision:
  INFO: "QA approved: task={task_id} agent={qa_agent_id}"
  WARNING: "QA rejected: task={task_id} attempt={retry_count}"
  ERROR: "QA escalating: task={task_id} loop_count=3"
- Phase transition events must be logged to agent_lifecycle_events
  table with development_phase field populated
- Apply BUILD_NOTES defensive normalisation to DefectReport
  LLM response parsing

---

## WHAT SUCCESS LOOKS LIKE

```bash
python main.py
pytest tests/ -v
```

- Run 7 shows a real rejection cycle — task fails QA, gets fixed,
  passes QA on second attempt
- Run 8 shows the Human Gate with formatted plain-language report
- Backend tasks unlock after gate approval
- All 147 existing tests still pass
- New tests pass with mocked LLM
- Target: 185+ total tests passing

That is Phase 7 complete.
