# ForgeAI — Phase 9B Cursor Prompt
# Paste this entire document into Cursor as your starting instruction

---

## CONTEXT

This is Phase 9B of the ForgeAI build plan — a focused extension
that adds post-delivery project lifecycle management and intelligent
change handling.

Phases 1-9 are complete (224 tests passing).

Do NOT modify any existing Phase 1-9 code unless a specific
instruction below requires it. Build on top of what exists.

---

## BUILD NOTES — MUST RESPECT

1. All agents attempt LOW tier first, fall back to MEDIUM on
   schema validation failure.
2. All agents use defensive normalisation before Pydantic validation.
3. Large structured documents: first attempt 16384, retry 32768.
4. Loop_Counter threshold is 3 — consistent across all agents.
5. Human approval simulated via auto_approve() callback in main.py.
6. Every table already has project_id column — use it.
7. ContextWindowManager is optional on LLMClient.
8. FinalReviewer runs after ALL phases complete in production.

---

## WHAT PHASE 9B BUILDS

Nine things:

1. **Project registry** — tracks project lifecycle states:
   ACTIVE, LIVE, ARCHIVED. Added to PostgreSQL via migration.

2. **LIVE mode** — post-delivery dormancy. Project stays open
   after release-v1. Lead_Agent available but not running.
   No cost while idle.

3. **Change classifier** — automatically classifies every
   incoming change request into one of four types:
   BUGFIX, SMALL_FEATURE, LARGE_FEATURE, ARCHITECTURAL.

4. **Impact analyser** — before any agent touches a change,
   produces: affected tasks, cost estimate, risk level.

5. **Human confirmation gate** — for MEDIUM risk and above,
   human sees impact analysis and chooses:
   PROCEED / QUEUE / DEFER / REJECT.

6. **PATCH mode** — for BUGFIX and SMALL_FEATURE.
   No bootstrap. No research phase. Targeted REWORK only.
   Agents created only for affected tasks. Destroyed when done.

7. **CHANGE mode** — for LARGE_FEATURE.
   Research_Agent and Architect_Agent created.
   Change specification document produced.
   Human approves scope. Execution agents created for new tasks.
   Full QA on new and affected areas.

8. **ARCHITECTURAL handling** — always escalates to human.
   Never auto-executes. Lead_Agent presents full impact analysis.

9. **Change history artefact** — every change request, impact
   analysis, human decision, and execution result stored
   permanently in Project_Memory.

---

## NEW PROJECT STRUCTURE

Add these files. Do not remove any existing files:

```
forgeai/
├── lifecycle/
│   ├── __init__.py
│   ├── project_registry.py   # Project state management
│   ├── change_classifier.py  # Change type detection
│   ├── impact_analyser.py    # Affected tasks, cost, risk
│   ├── change_executor.py    # PATCH and CHANGE mode execution
│   └── schemas.py            # All lifecycle schemas
└── ...existing files...

tests/
├── test_project_registry.py
├── test_change_classifier.py
├── test_impact_analyser.py
├── test_patch_mode.py
├── test_change_mode.py
└── ...existing files...
```

---

## PROJECT REGISTRY — EXACT SPECIFICATION

### New Alembic migration — projects table

```sql
CREATE TABLE projects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR NOT NULL,
    brief           TEXT NOT NULL,
    status          VARCHAR NOT NULL DEFAULT 'ACTIVE',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at    TIMESTAMPTZ,
    archived_at     TIMESTAMPTZ,
    release_tag     VARCHAR,
    project_memory_snapshot JSONB
);
```

### ProjectRegistry class — `lifecycle/project_registry.py`

```python
class ProjectRegistry:
    def __init__(self, db_session):
        self.db = db_session

    async def create_project(self,
                              name: str,
                              brief: str) -> Project:
        # Create project with status=ACTIVE
        pass

    async def set_live(self, project_id: str,
                        release_tag: str) -> Project:
        # Transition ACTIVE → LIVE
        # Set delivered_at and release_tag
        pass

    async def set_archived(self, project_id: str) -> Project:
        # Transition LIVE → ARCHIVED
        # Set archived_at
        # Only human can trigger — never automatic
        pass

    async def get_project(self, project_id: str) -> Project:
        pass

    async def list_live_projects(self) -> list[Project]:
        pass

    async def list_active_projects(self) -> list[Project]:
        pass
```

### Schemas — `lifecycle/schemas.py`

```python
class ProjectStatus(str, Enum):
    ACTIVE = "ACTIVE"
    LIVE = "LIVE"
    ARCHIVED = "ARCHIVED"

class Project(BaseModel):
    id: str
    name: str
    brief: str
    status: ProjectStatus
    created_at: datetime
    delivered_at: datetime | None = None
    archived_at: datetime | None = None
    release_tag: str | None = None

class ChangeType(str, Enum):
    BUGFIX = "BUGFIX"
    SMALL_FEATURE = "SMALL_FEATURE"
    LARGE_FEATURE = "LARGE_FEATURE"
    ARCHITECTURAL = "ARCHITECTURAL"

class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    ARCHITECTURAL = "ARCHITECTURAL"

class ChangeClassification(BaseModel):
    change_type: ChangeType
    risk_level: RiskLevel
    reasoning: str
    requires_human_confirmation: bool
    estimated_new_tasks: int
    classified_at: datetime

class ImpactAnalysis(BaseModel):
    project_id: str
    change_request: str
    classification: ChangeClassification
    affected_task_ids: list[str]
    affected_task_titles: list[str]
    conflicting_task_ids: list[str]
    new_tasks_required: list[str]
    estimated_cost_usd: float
    estimated_time_minutes: int
    human_message: str
    analysed_at: datetime

class ChangeDecision(str, Enum):
    PROCEED = "PROCEED"
    QUEUE = "QUEUE"
    DEFER = "DEFER"
    REJECT = "REJECT"

class HumanChangeApproval(BaseModel):
    project_id: str
    change_request: str
    impact_analysis: ImpactAnalysis
    decision: ChangeDecision
    decided_at: datetime
    decided_by: str = "human"

class ChangeSpecDocument(BaseModel):
    project_id: str
    change_request: str
    summary: str
    new_components: list[str]
    modified_components: list[str]
    new_api_surfaces: list[str]
    modified_api_surfaces: list[str]
    new_tasks: list[TaskSpec]
    rework_tasks: list[str]
    estimated_cost_usd: float
    estimated_time_minutes: int
    version: str = "1.0"
    created_at: datetime

class PatchResult(BaseModel):
    project_id: str
    change_request: str
    rework_tasks_completed: list[str]
    new_tasks_completed: list[str]
    regression_tests_passed: bool
    regression_failures: list[str]
    total_cost_usd: float
    duration_seconds: float
    completed_at: datetime

class RegressionResult(BaseModel):
    tasks_checked: list[str]
    all_passed: bool
    failures: list[str]

class ChangeHistoryEntry(BaseModel):
    entry_id: str
    project_id: str
    change_request: str
    classification: ChangeClassification
    impact_analysis: ImpactAnalysis
    human_decision: HumanChangeApproval
    execution_result: PatchResult | None = None
    outcome: str
    created_at: datetime
```

---

## CHANGE CLASSIFIER — EXACT SPECIFICATION

```python
class ChangeClassifier:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def classify(self,
                        change_request: str,
                        master_document: MasterDocument,
                        project_status: ProjectStatus
                        ) -> ChangeClassification:
        # LLM call — LOW complexity
        # Analyse change_request against master_document
        # Determine: change_type, risk_level, reasoning
        # Apply defensive normalisation
        # requires_human_confirmation = risk_level != LOW
        pass
```

### Classification rules

```
BUGFIX:
  - Change describes something broken
  - Fix applies to existing code only
  - Risk: LOW
  - Auto-proceeds

SMALL_FEATURE:
  - New functionality
  - Fits within existing architecture
  - 1-3 new tasks estimated
  - Risk: LOW-MEDIUM
  - AUTO if LOW, gate if MEDIUM

LARGE_FEATURE:
  - New functionality
  - Requires new components or API surfaces
  - 4+ new tasks estimated
  - Risk: MEDIUM-HIGH
  - Always gates

ARCHITECTURAL:
  - Changes data model, tech stack, or core structure
  - Risk: ARCHITECTURAL
  - Always escalates, never auto-executes
```

---

## IMPACT ANALYSER — EXACT SPECIFICATION

```python
class ImpactAnalyser:
    def __init__(self, llm_client: LLMClient,
                 db_session):
        self.llm = llm_client
        self.db = db_session

    async def analyse(self,
                       change_request: str,
                       classification: ChangeClassification,
                       project_id: str,
                       master_document: MasterDocument
                       ) -> ImpactAnalysis:
        # 1. Query all DONE tasks for this project
        # 2. LLM call — MEDIUM complexity
        #    Determine which tasks are affected
        # 3. Estimate cost
        # 4. Build plain-language human_message
        # 5. Return ImpactAnalysis
        pass
```

### Human message format — no jargon

```
This change request affects your project as follows:

Change type: {plain_type}
Risk level: {plain_risk}

Work affected:
  {n} completed tasks will need to be revisited
  {n} tasks currently in progress will be interrupted
  {n} new tasks will be created

Estimated additional cost: ~${cost}
Estimated additional time: ~{minutes} minutes

What would you like to do?
  PROCEED — start immediately
  QUEUE   — complete current tasks first, then start
  DEFER   — implement when current phase completes
  REJECT  — do not implement this change
```

---

## PATCH MODE — EXACT SPECIFICATION

```python
class PatchExecutor:
    def __init__(self,
                 lead_agent: "LeadAgent",
                 qa_orchestrator: QAOrchestrator,
                 db_session):
        self.lead = lead_agent
        self.qa_orch = qa_orchestrator
        self.db = db_session

    async def execute(self,
                       impact_analysis: ImpactAnalysis,
                       approval: HumanChangeApproval,
                       project_id: str) -> PatchResult:
        # 1. Handle conflicting IN_PROGRESS tasks:
        #    Save Task_Checkpoint, pause task
        # 2. Transition affected DONE tasks → REWORK
        # 3. Create execution agents for affected domains only
        # 4. Run REWORK tasks through full QA cycle
        # 5. Run regression tests on adjacent tasks
        # 6. Resume paused tasks from checkpoint
        # 7. Transition project back to LIVE
        # 8. Write change history artefact to Project_Memory
        # 9. Return PatchResult
        pass

    async def _run_regression_tests(
            self,
            affected_task_ids: list[str],
            project_id: str) -> RegressionResult:
        # Run QA tests on tasks adjacent to changed ones
        # Adjacent = tasks sharing data models or API endpoints
        # with the changed tasks
        # Return RegressionResult
        pass
```

---

## CHANGE MODE — EXACT SPECIFICATION

```python
class ChangeExecutor:
    def __init__(self,
                 lead_agent: "LeadAgent",
                 llm_client: LLMClient,
                 qa_orchestrator: QAOrchestrator,
                 db_session):
        self.lead = lead_agent
        self.llm = llm_client
        self.qa_orch = qa_orchestrator
        self.db = db_session

    async def execute_change(self,
                              change_request: str,
                              approval: HumanChangeApproval,
                              project_id: str,
                              master_document: MasterDocument,
                              human_scope_callback
                              ) -> ChangeResult:
        # 1. Create Research_Agent + Architect_Agent
        # 2. Produce ChangeSpecDocument
        # 3. Present ChangeSpecDocument to human for scope approval
        # 4. If approved: create execution agents for new tasks
        # 5. Transition rework tasks → REWORK
        # 6. Run all tasks through full QA cycle
        # 7. Update Master_Document with new components/APIs
        # 8. Transition project back to LIVE
        # 9. Write change history artefact
        # 10. Return ChangeResult
        pass

    async def _produce_change_spec(
            self,
            change_request: str,
            master_document: MasterDocument,
            impact_analysis: ImpactAnalysis
            ) -> ChangeSpecDocument:
        # Research_Agent gathers context
        # Architect_Agent produces ChangeSpecDocument
        # HIGH complexity — this is architectural work
        pass
```

---

## ARCHITECTURAL HANDLING

```python
async def handle_architectural(self,
                                 impact_analysis: ImpactAnalysis,
                                 project_id: str,
                                 human_callback) -> None:
    # Format detailed impact report
    # Present to human — plain language
    # Include recommendation:
    #   "This change affects the core structure of the project.
    #    We recommend treating this as a new project rather than
    #    a change to the existing one."
    # Wait for human decision
    # If PROCEED: human manually configures approach
    # If REJECT: return to LIVE unchanged
    # Write change history regardless of outcome
    pass
```

---

## LIVE MODE — LEAD_AGENT

### Add to LeadAgent

```python
async def enter_live_mode(self,
                           project_id: str,
                           release_tag: str) -> None:
    # 1. Destroy all execution agents
    # 2. Update project status to LIVE via ProjectRegistry
    # 3. Log lifecycle event
    # 4. Enter dormancy
    pass

async def accept_change_request(self,
                                 change_request: str,
                                 project_id: str,
                                 human_approval_callback
                                 ) -> ChangeHistoryEntry:
    # Only callable when project is LIVE
    # 1. Classify change
    # 2. Analyse impact
    # 3. If LOW risk: auto-proceed to PATCH
    # 4. If MEDIUM+: present to human, wait for decision
    # 5. Execute based on decision and change type:
    #    BUGFIX/SMALL_FEATURE + PROCEED → PatchExecutor
    #    LARGE_FEATURE + PROCEED → ChangeExecutor
    #    ARCHITECTURAL → handle_architectural
    #    QUEUE/DEFER → store and return
    #    REJECT → write history, return
    # 6. Return ChangeHistoryEntry
    pass

async def archive_project(self,
                           project_id: str) -> None:
    # Only callable by human explicitly
    # Transition LIVE → ARCHIVED
    # Final Project_Memory snapshot
    # Log archival event
    pass
```

---

## UPDATED main.py

Add Runs 14, 15, and 16. Keep all existing runs.

### Run 14 — Project enters LIVE mode

1. Create project record in registry
2. Simulate delivery: project transitions ACTIVE → LIVE
3. Lead_Agent enters dormancy
4. Print project status

### Run 15 — PATCH mode (BUGFIX)

1. Submit: "Fix the task completion endpoint — it returns 200
   even when the task ID doesn't exist. Should return 404."
2. Classifier: BUGFIX → LOW risk → auto-proceed
3. Impact analysis: 1 task affected
4. PatchExecutor: REWORK → QA → DONE
5. Regression tests on adjacent tasks
6. Project returns to LIVE
7. Change history written — print summary

### Run 16 — CHANGE mode (LARGE_FEATURE)

1. Submit: "Add a team collaboration feature — users should be
   able to share task lists with team members and assign tasks
   to each other."
2. Classifier: LARGE_FEATURE → HIGH risk
3. Impact analysis: multiple tasks affected, new tasks required
4. Human confirmation gate — auto-approve (PROCEED)
5. Research_Agent + Architect_Agent produce ChangeSpecDocument
6. Scope presented — auto-approve
7. Execution agents created, tasks run through QA
8. Project returns to LIVE
9. Change history written — print summary

### Expected terminal output (abbreviated)

```
=== RUN 14: LIVE MODE ===
[LEAD] Destroying execution agents...
[REGISTRY] Project status: ACTIVE → LIVE
[LEAD] Dormancy entered — project is live
  Status: LIVE | Release: release-v1

=== RUN 15: PATCH MODE (BUGFIX) ===
[CHANGE] Received: Fix the task completion endpoint...
[CLASSIFIER] BUGFIX | Risk: LOW | Auto-proceeding
[IMPACT] 1 task affected | ~$0.02 | ~3 min
[PATCH] DONE → REWORK: task completion endpoint
[BACKEND] Fixing endpoint...
[QA] Fix verified ✓
[PATCH] Regression: 2 adjacent tasks checked — pass ✓
[REGISTRY] Status: LIVE restored
[HISTORY] Change written to Project_Memory

=== RUN 16: CHANGE MODE (LARGE FEATURE) ===
[CHANGE] Received: Add team collaboration feature...
[CLASSIFIER] LARGE_FEATURE | Risk: HIGH
[IMPACT] 4 tasks affected | 8 new tasks | ~$0.85
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CHANGE REQUEST — Your Approval Needed

 You requested: Add team collaboration feature

 4 completed tasks will need updating
 8 new tasks will be created
 Estimated cost: ~$0.85 | Time: ~52 minutes

 PROCEED / QUEUE / DEFER / REJECT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[GATE] Human approved — PROCEED
[CHANGE] Creating Research + Architect agents...
[CHANGE] ChangeSpecDocument produced
  New components: TeamList, MemberCard, AssigneeSelector
  New APIs: /api/teams, /api/members, /api/assign
[GATE] Scope approved — executing
[CHANGE] 8 new tasks + 4 REWORK tasks running...
[QA] All tasks verified ✓
[REGISTRY] Status: LIVE restored
[HISTORY] Change written to Project_Memory
```

---

## TESTS

### test_project_registry.py

- Test create_project() returns Project with ACTIVE status
- Test set_live() transitions ACTIVE → LIVE
- Test set_live() sets delivered_at and release_tag
- Test set_archived() transitions LIVE → ARCHIVED
- Test set_archived() sets archived_at
- Test list_live_projects() returns only LIVE projects
- Test list_active_projects() returns only ACTIVE projects

### test_change_classifier.py

Mock LLMClient. No real API calls.

- Test BUGFIX classified correctly
- Test SMALL_FEATURE classified correctly
- Test LARGE_FEATURE classified correctly
- Test ARCHITECTURAL classified correctly
- Test LOW risk: requires_human_confirmation=False
- Test MEDIUM risk: requires_human_confirmation=True
- Test HIGH risk: requires_human_confirmation=True
- Test ARCHITECTURAL: requires_human_confirmation=True
- Test ChangeClassification has non-empty reasoning

### test_impact_analyser.py

Mock LLMClient. No real API calls.

- Test analyse() returns ImpactAnalysis
- Test affected_task_ids populated
- Test human_message is non-empty
- Test human_message contains no jargon
  (no: "agent", "LLM", "Chroma", "PostgreSQL", "artefact")
- Test estimated_cost_usd is positive
- Test estimated_time_minutes is positive

### test_patch_mode.py

Mock LLMClient, Sandbox. No real API or Docker calls.

- Test REWORK transition applied to affected tasks
- Test new tasks created for SMALL_FEATURE
- Test paused IN_PROGRESS tasks resume from checkpoint
- Test regression tests run on adjacent tasks
- Test project returns to LIVE after PATCH completes
- Test change history written to Project_Memory
- Test PatchResult contains correct task counts

### test_change_mode.py

Mock LLMClient. No real API calls.

- Test Research_Agent and Architect_Agent created
- Test ChangeSpecDocument produced with new_tasks populated
- Test new tasks created from change spec
- Test REWORK applied to affected existing tasks
- Test project returns to LIVE after CHANGE completes
- Test change history written to Project_Memory

---

## REQUIREMENTS BEING IMPLEMENTED

- **Req 16** — Change Bucket and Change Planning
  - Change classifier replaces manual Change_Type assignment ✓
  - Impact analysis before any agent touches the change ✓
  - Human confirmation for MEDIUM+ risk ✓
- **Req 17** — Mid-Project Change Execution
  - Task_Checkpoint save/resume for interrupted tasks ✓
  - REWORK state for affected DONE tasks ✓
  - Regression testing on adjacent tasks ✓
  - Change history as permanent artefact ✓
- **New — Post-Delivery Lifecycle**
  - LIVE mode after delivery ✓
  - PATCH mode for targeted fixes ✓
  - CHANGE mode for new features ✓
  - ARCHITECTURAL escalation ✓
  - Human-controlled archival ✓

---

## CODE QUALITY RULES

- All LLM calls in tests must be mocked — no real API calls
- Change classifier prompt must be a module-level constant
- Impact analyser human_message must pass no-jargon test
- All lifecycle transitions logged to agent_lifecycle_events
  with project_id and timestamp
- PatchExecutor must log every REWORK transition:
  INFO: "PATCH: task={id} DONE→REWORK reason={request[:50]}"
- Apply BUILD_NOTES defensive normalisation to all new
  LLM response parsing

---

## WHAT SUCCESS LOOKS LIKE

```bash
python main.py
pytest tests/ -v
```

- Run 14 shows project entering LIVE mode cleanly
- Run 15 shows a real BUGFIX PATCH cycle completing
- Run 16 shows LARGE_FEATURE CHANGE with human gate,
  ChangeSpecDocument, and QA cycle
- All 224 existing tests still pass
- New tests pass with mocked LLM
- Target: 265+ total tests passing

That is Phase 9B complete.
