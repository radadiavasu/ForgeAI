# ForgeAI — Phase 6 Cursor Prompt
# Paste this entire document into Cursor as your starting instruction

---

## CONTEXT

This is Phase 6 of a 10-phase build plan for ForgeAI — an AI agent
orchestration system that simulates a real software company.

Phases 1-5 are complete and passing (117 tests passing).
- Phase 1: Task_State_Machine, mock agents, audit log
- Phase 2: Sandbox, Test_Runner, real Docker execution
- Phase 3: Escalation_Ladder, Loop_Counter, Drift_Monitor
- Phase 4: Redis, Chroma, MinIO, full persistence
- Phase 5: Real LLM calls, Model_Router, Research_Agent, Architect_Agent

Do NOT modify any existing Phase 1-5 code unless a specific
instruction below requires it. Build on top of what exists.

---

## BUILD NOTES FROM PREVIOUS PHASES

These decisions were made during Phase 5 and must be respected:

1. All new agents attempt LOW tier first, fall back to MEDIUM on
   schema validation failure.
2. All agents must use defensive normalisation helpers before
   Pydantic validation. Never assume clean LLM output shape.
3. Large structured documents: first attempt max_tokens=16384,
   retry at 32768 if truncation detected.
4. Settings fields use pool_* internally with MODEL_* env aliases.
5. All agent constructors must support optional llm_client for
   backward compatibility with existing tests.

---

## WHAT PHASE 6 BUILDS

Six things:

1. **Agent_Bootstrap_Protocol** — Lead_Agent creates agents in the
   correct mandatory order. Human creates Lead_Agent. Lead_Agent
   creates Research_Agent and Architect_Agent automatically. After
   Master_Document is produced, Lead_Agent recommends execution
   agent count. Human approves. Lead_Agent creates them. (Req 29)

2. **Navigation_Contract** — before any Frontend_Agent writes code,
   all Frontend_Agents negotiate and agree on routes, component
   ownership, shared layout, and linking convention. Mediated by
   Lead_Agent. Written to Project_Memory. (Req 27)

3. **Component_Registry** — shared record in Project_Memory of all
   reusable UI components. Agents register components on completion.
   Agents query before building to avoid duplicates. (Req 27)

4. **Root layout dependency** — Frontend_Agent #1 builds the shared
   layout first. Frontend_Agent #2 and #3 are Phase_Locked until
   #1's root layout task is QA-verified and committed. (Req 27)

5. **Layout specification flow** — both paths:
   Path A: user provides mockup file
   Path B: Architect_Agent produces layout spec from Master_Document
   Lead_Agent reviews before passing to Frontend_Agents. (Req 22)

6. **Frontend_Agent with real React code output** — real LLM calls,
   real React component output, basic pytest-compatible tests
   alongside each component. Playwright QA comes in Phase 6B.

---

## PROJECT FOR THIS PHASE

**Brief:** "Build a personal task manager. Users can create tasks,
mark them complete, and view their task history."

**Pre-flight constraints:**
```python
{
    "frontend_framework": "React",
    "styling": "Tailwind CSS",
    "deployment": "Docker"
}
```

**Expected pages (3):**
- Dashboard — task list, add task form, completion toggle
- History — completed tasks with timestamps
- Settings — user preferences (theme, notifications)

This is intentionally simple. 3 pages, 3 Frontend_Agents,
one Navigation_Contract negotiation, one Component_Registry.

---

## TECH STACK ADDITIONS — PHASE 6 ONLY

Add to requirements.txt:
- No new Python packages required for Phase 6

Add to .env.example:
```
# Frontend Agent
FRONTEND_FRAMEWORK=react
```

---

## NEW PROJECT STRUCTURE

Add these files. Do not remove any existing files:

```
forgeai/
├── bootstrap/
│   ├── __init__.py
│   ├── protocol.py         # Agent_Bootstrap_Protocol
│   └── schemas.py          # AgentRecommendation, BootstrapConfig
├── contracts/
│   ├── __init__.py
│   ├── navigation.py       # Navigation_Contract negotiation
│   ├── registry.py         # Component_Registry
│   └── schemas.py          # NavigationContract, ComponentEntry,
│                           # LayoutSpecification
├── agents/
│   ├── frontend_agent.py   # NEW — real LLM frontend agent
│   └── lead_agent.py       # UPDATE — full bootstrap orchestration
└── ...existing files...

tests/
├── test_bootstrap_protocol.py  # agent creation order, human approval
├── test_navigation_contract.py # route negotiation, ownership
├── test_component_registry.py  # register, query, duplicate detection
├── test_frontend_agent.py      # mocked LLM — React output structure
└── ...existing files...
```

---

## AGENT_BOOTSTRAP_PROTOCOL — EXACT SPECIFICATION

### The mandatory creation sequence (Req 29)

```
Step 1: Human creates Lead_Agent with project brief
Step 2: Lead_Agent automatically creates Research_Agent + Architect_Agent
Step 3: Research + Architect produce Master_Document + Tech_Stack_Document
Step 4: Lead_Agent analyses task decomposition
Step 5: Lead_Agent presents plain-language agent recommendation to human
Step 6: Human approves agent count
Step 7: Lead_Agent creates approved execution agents
```

### AgentBootstrapProtocol class — `bootstrap/protocol.py`

```python
class AgentBootstrapProtocol:
    def __init__(self, lead_agent: "LeadAgent"):
        self.lead = lead_agent

    async def run(self, project_brief: str,
                  preflight_constraints: dict,
                  human_approval_callback) -> BootstrapResult:
        # Step 1: already done — Lead_Agent exists
        # Step 2: create Research_Agent + Architect_Agent automatically
        research_agent = await self._create_research_agent()
        architect_agent = await self._create_architect_agent()

        # Step 3: produce Master_Document
        research_output = await research_agent.research(
            project_brief, preflight_constraints)
        master_doc = await architect_agent.produce_master_document(
            project_brief, research_output, preflight_constraints)
        tech_stack = await architect_agent.produce_tech_stack_document(
            research_output)

        # Write to Project_Memory
        await self.lead.write_to_project_memory(
            "master_document", master_doc)
        await self.lead.write_to_project_memory(
            "tech_stack_document", tech_stack)

        # Step 4: analyse and decompose tasks
        task_plan = await self.lead.decompose_tasks(master_doc)

        # Step 5: produce plain-language recommendation
        recommendation = self._build_recommendation(task_plan)

        # Step 6: present to human and wait for approval
        approved_config = await human_approval_callback(recommendation)

        # Step 7: create execution agents
        execution_agents = await self._create_execution_agents(
            approved_config, master_doc)

        return BootstrapResult(
            master_document=master_doc,
            tech_stack_document=tech_stack,
            task_plan=task_plan,
            agents_created=execution_agents,
            recommendation=recommendation
        )

    async def _create_research_agent(self) -> ResearchAgent:
        # Create and return a ResearchAgent instance
        # Log creation event to Observability (agent_id, role, timestamp)
        pass

    async def _create_architect_agent(self) -> ArchitectAgent:
        # Create and return an ArchitectAgent instance
        # Log creation event
        pass

    async def _create_execution_agents(
            self, config: ApprovedConfig,
            master_doc: MasterDocument) -> list[BaseAgent]:
        # Create Frontend_Agents, Backend_Agents, QA_Agents
        # based on approved counts
        # Log each creation event
        pass

    def _build_recommendation(
            self, task_plan: TaskPlan) -> AgentRecommendation:
        # Analyse task_plan and produce a plain-language recommendation
        # Example output:
        # "Based on the project, I recommend:
        #  - 2 Frontend Agents (3 pages, parallelisable after root layout)
        #  - 1 Backend Agent (5 API endpoints, sequential)
        #  - 1 QA Agent (shared across both phases)
        #  Estimated time with this config: 45 minutes
        #  Cost estimate: ~$0.45"
        pass
```

### Pydantic schemas — `bootstrap/schemas.py`

```python
class AgentRecommendation(BaseModel):
    frontend_agent_count: int
    backend_agent_count: int
    qa_agent_count: int
    reasoning: str              # plain language explanation
    time_estimate_minutes: int
    cost_estimate_usd: float

class ApprovedConfig(BaseModel):
    frontend_agent_count: int
    backend_agent_count: int
    qa_agent_count: int
    approved_by: str = "human"
    approved_at: datetime

class TaskPlan(BaseModel):
    frontend_tasks: list[TaskSpec]
    backend_tasks: list[TaskSpec]
    total_tasks: int
    estimated_complexity_distribution: dict  # LOW/MEDIUM/HIGH counts

class TaskSpec(BaseModel):
    title: str
    description: str
    complexity: str             # LOW/MEDIUM/HIGH
    phase: str                  # FRONTEND_PHASE/BACKEND_PHASE
    dependencies: list[str]     # titles of tasks that must complete first

class BootstrapResult(BaseModel):
    master_document: MasterDocument
    tech_stack_document: TechStackDocument
    task_plan: TaskPlan
    agents_created: list[str]   # list of agent_ids created
    recommendation: AgentRecommendation
```

---

## NAVIGATION_CONTRACT — EXACT SPECIFICATION

### What it does

Before any Frontend_Agent writes code, Lead_Agent initiates a
negotiation session. Each Frontend_Agent proposes which routes
it should own. Lead_Agent mediates and produces the final
Navigation_Contract. Written to Project_Memory.

### NavigationContract schema — `contracts/schemas.py`

```python
class RouteDefinition(BaseModel):
    path: str                   # e.g. "/", "/history", "/settings"
    owner_agent_id: str         # which Frontend_Agent owns this route
    component_name: str         # e.g. "DashboardPage"
    is_root_layout: bool        # True for only one route

class NavigationContract(BaseModel):
    version: str = "1.0"
    project_id: str
    routes: list[RouteDefinition]
    shared_layout_component: str    # e.g. "AppLayout"
    shared_layout_owner: str        # agent_id of root layout owner
    linking_convention: str         # e.g. "react-router-dom Link component"
    created_at: datetime
    approved_by: str = "lead_agent"

class LayoutSpecification(BaseModel):
    project_id: str
    source: str                 # "mockup" or "architect_generated"
    pages: list[PageSpec]
    shared_components: list[SharedComponentSpec]
    design_tokens: dict         # colours, fonts, spacing

class PageSpec(BaseModel):
    name: str
    route: str
    sections: list[str]         # e.g. ["header", "task-list", "add-form"]
    interactions: list[str]     # e.g. ["click add button", "toggle complete"]
    acceptance_criteria: list[str]

class SharedComponentSpec(BaseModel):
    name: str
    used_by_pages: list[str]
    props: list[str]
    description: str
```

### Navigation negotiation flow

```python
class NavigationNegotiator:
    def __init__(self, lead_agent: "LeadAgent",
                 llm_client: LLMClient):
        self.lead = lead_agent
        self.llm = llm_client

    async def negotiate(self,
                        frontend_agents: list["FrontendAgent"],
                        layout_spec: LayoutSpecification,
                        project_id: str) -> NavigationContract:
        # 1. Lead_Agent presents layout_spec to all Frontend_Agents
        # 2. Each agent proposes which routes it should own
        #    (LLM call per agent, LOW complexity)
        # 3. Lead_Agent mediates any conflicts
        #    (two agents proposing the same route)
        # 4. Lead_Agent assigns root layout to the first agent
        # 5. Lead_Agent produces final NavigationContract
        # 6. Write to Project_Memory
        # 7. Return NavigationContract
        pass

    async def _get_agent_proposal(
            self,
            agent: "FrontendAgent",
            layout_spec: LayoutSpecification) -> list[RouteDefinition]:
        # Ask the agent which routes it proposes to own
        # LOW complexity LLM call
        pass

    def _resolve_conflicts(
            self,
            proposals: dict[str, list[RouteDefinition]]
            ) -> dict[str, list[RouteDefinition]]:
        # If two agents propose the same route,
        # assign it to the agent with fewer routes
        pass
```

---

## COMPONENT_REGISTRY — EXACT SPECIFICATION

### What it does

Shared record in Project_Memory. Frontend_Agents register
completed reusable components. Agents query before building
to avoid duplicate implementations.

### ComponentRegistry class — `contracts/registry.py`

```python
class ComponentRegistry:
    def __init__(self, db_session):
        self.db = db_session

    async def register(self, project_id: str,
                       component_name: str,
                       owner_agent_id: str,
                       interface_definition: str,
                       file_path: str) -> ComponentEntry:
        # Write component to project_artefacts table
        # artefact_type = "component_registry_entry"
        # Raise DuplicateComponentError if name already registered
        #   for this project_id
        pass

    async def query(self, project_id: str,
                    component_name: str) -> ComponentEntry | None:
        # Return component entry if it exists, None if not
        pass

    async def list_all(self,
                       project_id: str) -> list[ComponentEntry]:
        # Return all registered components for this project
        pass

    async def mark_used_by(self, project_id: str,
                            component_name: str,
                            consumer_agent_id: str) -> None:
        # Record that consumer_agent_id is using this component
        pass
```

### ComponentEntry schema

```python
class ComponentEntry(BaseModel):
    component_name: str
    owner_agent_id: str
    interface_definition: str   # props, types, usage example
    file_path: str              # e.g. "src/components/NavBar.jsx"
    project_id: str
    registered_at: datetime
    used_by: list[str] = []     # agent_ids consuming this component
```

### DuplicateComponentError

```python
class DuplicateComponentError(ForgeAIError):
    # Raised when an agent attempts to register a component
    # that already exists in the registry for this project.
    # QA_Agent uses this as a defect signal (Req 27 criterion 8)
    pass
```

---

## ROOT LAYOUT DEPENDENCY

Frontend_Agent #1 always builds the shared layout first.
Frontend_Agent #2 and #3 are Phase_Locked on a dependency.

### Implementation

When Lead_Agent creates the Task_Dependency_Graph (Req 04):

```python
# Root layout task — assigned to Frontend_Agent #1
root_layout_task = TaskSpec(
    title="Build AppLayout — shared shell, NavBar, Footer",
    complexity="MEDIUM",
    phase="FRONTEND_PHASE",
    dependencies=[]  # no dependencies — goes first
)

# All other frontend tasks depend on root layout
dashboard_task = TaskSpec(
    title="Build Dashboard page",
    complexity="LOW",
    phase="FRONTEND_PHASE",
    dependencies=["Build AppLayout — shared shell, NavBar, Footer"]
)

history_task = TaskSpec(
    title="Build History page",
    complexity="LOW",
    phase="FRONTEND_PHASE",
    dependencies=["Build AppLayout — shared shell, NavBar, Footer"]
)

settings_task = TaskSpec(
    title="Build Settings page",
    complexity="LOW",
    phase="FRONTEND_PHASE",
    dependencies=["Build AppLayout — shared shell, NavBar, Footer"]
)
```

When the root layout task reaches DONE and is QA-verified:
- Lead_Agent updates the Task_Dependency_Graph
- Phase_Locked dependent tasks transition to TODO
- Lead_Agent notifies all other Frontend_Agents
- They query Component_Registry and find NavBar, Footer, AppLayout
- They import rather than rebuild

---

## LAYOUT SPECIFICATION FLOW

### Path A — Mockup provided

```python
async def process_mockup(self, mockup_file_path: str,
                          project_id: str) -> LayoutSpecification:
    # 1. Read file (PNG/JPEG/PDF/Figma JSON)
    # 2. Pass to Architect_Agent with vision prompt
    # 3. Extract: pages, sections, interactions, shared components
    # 4. Return LayoutSpecification
    # 5. Lead_Agent reviews — if approved, write to Project_Memory
    # 6. If not approved, return to Architect_Agent for revision
    pass
```

### Path B — No mockup (architect generates)

```python
async def generate_layout_spec(self,
                                master_doc: MasterDocument,
                                project_id: str) -> LayoutSpecification:
    # 1. Architect_Agent analyses Master_Document components
    # 2. Produces LayoutSpecification covering all pages
    # 3. Lead_Agent reviews against project brief
    # 4. If approved: write to Project_Memory
    # 5. If not approved: return to Architect_Agent with feedback
    # complexity = "MEDIUM"
    pass
```

### Lead_Agent layout review

Lead_Agent reviews the layout spec with a real LLM call.
It checks:
- Does every page in the Master_Document have a corresponding PageSpec?
- Does every shared component make sense for the project type?
- Are the acceptance criteria specific enough for Frontend_Agent to build against?

If any check fails, Lead_Agent sends it back with specific feedback.
Maximum 2 revision cycles before escalating to human.

---

## FRONTEND_AGENT — EXACT SPECIFICATION

### Role prompt

```
You are Frontend_Agent, a specialist in building React user
interface components. You receive a task specification, a
layout specification, a Navigation_Contract, and a
Component_Registry query result.

You produce complete, working React components using
Tailwind CSS for styling. Your code must be importable,
correctly typed with PropTypes, and follow the
Navigation_Contract's linking convention exactly.

Before building any component, you check the Component_Registry.
If a suitable component already exists, you import it.
You never rebuild what already exists.

After completing a component, you register it in the
Component_Registry if it is reusable.

You output structured JSON with two fields:
- "code": the complete React component code as a string
- "test_code": basic test code as a string
- "components_registered": list of component names you built
- "components_imported": list of component names you imported
```

### Frontend_Agent class — `agents/frontend_agent.py`

```python
class FrontendAgent(BaseAgent):
    def __init__(self, agent_id: str, db_session,
                 llm_client: LLMClient | None = None,
                 agent_memory: AgentMemory | None = None,
                 component_registry: ComponentRegistry | None = None,
                 navigation_contract: NavigationContract | None = None):
        super().__init__(agent_id, db_session)
        self.llm = llm_client
        self.memory = agent_memory
        self.registry = component_registry
        self.nav_contract = navigation_contract
        self.agent_role = "frontend_agent"

    async def complete_work(self,
                             task_id: UUID,
                             task_description: str,
                             page_spec: PageSpec,
                             loop_count: int = 0) -> Task:
        # 1. Query Component_Registry for existing components
        # 2. Query Agent_Memory for relevant lessons
        # 3. Build prompt with:
        #    - task description
        #    - page_spec (sections, interactions, criteria)
        #    - Navigation_Contract (routes, linking convention)
        #    - existing components from registry
        #    - relevant lessons
        # 4. Call LLMClient with complexity="LOW", loop_count
        # 5. Parse response — extract code and test_code
        # 6. Register new reusable components in Component_Registry
        # 7. Transition task IN_PROGRESS → IN_REVIEW
        pass

    async def propose_routes(
            self,
            layout_spec: LayoutSpecification) -> list[RouteDefinition]:
        # Used during Navigation_Contract negotiation
        # LOW complexity LLM call
        # Returns list of routes this agent proposes to own
        pass
```

### Frontend output schema

```python
class FrontendOutput(BaseModel):
    code: str                       # complete React component
    test_code: str                  # basic test assertions
    components_registered: list[str]
    components_imported: list[str]
    file_path: str                  # e.g. "src/pages/Dashboard.jsx"
```

---

## UPDATED LEAD_AGENT

Lead_Agent gets full bootstrap orchestration in Phase 6.

Add these methods to LeadAgent:

```python
async def run_bootstrap(self,
                        project_brief: str,
                        preflight_constraints: dict,
                        human_approval_callback) -> BootstrapResult:
    # Delegates to AgentBootstrapProtocol.run()
    pass

async def decompose_tasks(self,
                          master_doc: MasterDocument) -> TaskPlan:
    # LLM call — HIGH complexity
    # Analyse Master_Document and produce TaskPlan
    # Must respect frontend/backend phase separation
    # Must identify root layout task
    # Must set dependencies correctly
    pass

async def initiate_navigation_contract(
        self,
        frontend_agents: list[FrontendAgent],
        layout_spec: LayoutSpecification,
        project_id: str) -> NavigationContract:
    # Delegates to NavigationNegotiator.negotiate()
    pass

async def review_layout_spec(
        self,
        layout_spec: LayoutSpecification,
        project_brief: str) -> tuple[bool, str]:
    # LLM call — MEDIUM complexity
    # Returns (approved: bool, feedback: str)
    pass

async def unlock_dependent_tasks(self,
                                  completed_task_title: str,
                                  project_id: str) -> list[str]:
    # When root layout task reaches DONE:
    # Find all tasks with this as dependency
    # Transition them Phase_Locked → TODO
    # Notify assigned Frontend_Agents
    # Return list of unlocked task titles
    pass
```

---

## UPDATED main.py

Replace main.py with the full bootstrap + frontend build flow.

### Run 1 — Full Bootstrap Protocol

```python
brief = """Build a personal task manager. Users can create tasks,
mark them complete, and view their task history."""

constraints = {
    "frontend_framework": "React",
    "styling": "Tailwind CSS",
    "deployment": "Docker"
}
```

Steps:
1. Human creates Lead_Agent (instantiate in code)
2. Lead_Agent runs bootstrap protocol
3. Print: "Research_Agent created"
4. Print: "Architect_Agent created"
5. Print: Master_Document summary
6. Print: AgentRecommendation (plain language)
7. Simulate human approval: `approved_config = auto_approve(recommendation)`
8. Print: agents created (Frontend x2, Backend x1, QA x1)

### Run 2 — Layout Specification (Path B — no mockup)

1. Architect_Agent generates LayoutSpecification from Master_Document
2. Lead_Agent reviews it
3. Print: LayoutSpecification summary (pages, shared components)
4. Print: Lead_Agent review result (approved/feedback)

### Run 3 — Navigation Contract Negotiation

1. Frontend_Agent #1 and #2 propose routes
2. Lead_Agent mediates
3. Print: final NavigationContract
   - Route table: path → owner → component name
   - Shared layout: AppLayout owned by Frontend_Agent #1

### Run 4 — Root Layout Build

1. Frontend_Agent #1 builds AppLayout + NavBar + Footer
2. Components registered in Component_Registry
3. Print: registered components
4. QA_Agent reviews (using existing pytest Sandbox for now)
5. Root layout task reaches DONE
6. Lead_Agent unlocks dependent tasks
7. Print: "Dashboard task unlocked"
8. Print: "History task unlocked"

### Run 5 — Parallel Frontend Build

1. Frontend_Agent #2 queries Component_Registry
2. Finds NavBar, Footer, AppLayout — imports them
3. Builds Dashboard page using imported components
4. Print: components_imported list
5. Print: components_registered list (new ones only)
6. Task reaches DONE

### Expected terminal output (abbreviated)

```
=== RUN 1: BOOTSTRAP PROTOCOL ===
[BOOTSTRAP] Step 2: Creating Research_Agent + Architect_Agent...
[BOOTSTRAP] research_agent_1 created
[BOOTSTRAP] architect_agent_1 created
[BOOTSTRAP] Step 3: Running Research & Architecture phase...
[RESEARCH] Research complete
[ARCHITECT] Master_Document complete — 4 components, 6 APIs
[BOOTSTRAP] Step 4: Analysing task decomposition...
[BOOTSTRAP] Step 5: Agent recommendation:
  Based on this project I recommend:
  - 2 Frontend Agents (3 pages, root layout dependency)
  - 1 Backend Agent (6 API endpoints)
  - 1 QA Agent (shared)
  Estimated time: ~35 minutes | Cost estimate: ~$0.30
[BOOTSTRAP] Step 6: Human approved configuration
[BOOTSTRAP] Step 7: Creating execution agents...
[BOOTSTRAP] frontend_agent_1 created
[BOOTSTRAP] frontend_agent_2 created
[BOOTSTRAP] backend_agent_1 created
[BOOTSTRAP] qa_agent_1 created

=== RUN 2: LAYOUT SPECIFICATION ===
[ARCHITECT] Generating layout specification...
[LAYOUT] LayoutSpecification produced:
  Pages: Dashboard, History, Settings
  Shared components: AppLayout, NavBar, Footer, TaskCard
[LEAD] Reviewing layout specification...
[LEAD] Layout specification approved ✓

=== RUN 3: NAVIGATION CONTRACT ===
[NAV] frontend_agent_1 proposes: /, /history
[NAV] frontend_agent_2 proposes: /settings
[LEAD] No conflicts — NavigationContract finalised
[NAV] Routes agreed:
  /           → frontend_agent_1 → DashboardPage (root layout owner)
  /history    → frontend_agent_1 → HistoryPage
  /settings   → frontend_agent_2 → SettingsPage
[NAV] Shared layout: AppLayout owned by frontend_agent_1

=== RUN 4: ROOT LAYOUT BUILD ===
[FRONTEND #1] Building AppLayout, NavBar, Footer...
[REGISTRY] Registered: AppLayout (frontend_agent_1)
[REGISTRY] Registered: NavBar (frontend_agent_1)
[REGISTRY] Registered: Footer (frontend_agent_1)
[QA] Root layout verified ✓
[LEAD] Unlocking dependent tasks...
[LEAD] Dashboard task: Phase_Locked → TODO
[LEAD] History task: Phase_Locked → TODO

=== RUN 5: PARALLEL FRONTEND BUILD ===
[FRONTEND #2] Querying Component_Registry...
[FRONTEND #2] Found: AppLayout, NavBar, Footer — importing
[FRONTEND #2] Building Dashboard page...
[REGISTRY] components_imported: [AppLayout, NavBar, Footer]
[REGISTRY] components_registered: [TaskCard, AddTaskForm]
[QA] Dashboard verified ✓
```

---

## TESTS

### test_bootstrap_protocol.py

Mock all LLM calls. Never make real API calls in tests.

- Test Research_Agent and Architect_Agent created automatically
  in Step 2 without human approval
- Test execution agents NOT created before human approval
- Test execution agents created after human approval
- Test BootstrapResult contains all required fields
- Test agent creation events are logged
- Test bootstrap fails gracefully if Master_Document production fails

### test_navigation_contract.py

- Test each Frontend_Agent proposes at least one route
- Test conflicts are resolved (two agents proposing same route)
- Test exactly one route has is_root_layout=True
- Test NavigationContract written to Project_Memory
- Test NavigationContract has shared_layout_component populated
- Test lead_agent is set as approved_by

### test_component_registry.py

- Test register() stores a component successfully
- Test query() returns component if exists
- Test query() returns None if not exists
- Test list_all() returns all components for project
- Test DuplicateComponentError raised on duplicate name
- Test mark_used_by() updates used_by list
- Test components scoped by project_id

### test_frontend_agent.py

Mock LLMClient. Never make real API calls in tests.

- Test complete_work() queries Component_Registry before LLM call
- Test complete_work() queries Agent_Memory before LLM call
- Test complete_work() calls LLM with complexity="LOW"
- Test FrontendOutput has non-empty code field
- Test FrontendOutput has non-empty test_code field
- Test components_registered list is populated
- Test components_imported list populated when registry has entries
- Test propose_routes() returns at least one RouteDefinition

---

## AGENT CREATION LOGGING

Every agent creation and destruction event must be logged to a
new PostgreSQL table via Alembic migration:

```
agent_lifecycle_events
──────────────────────
id              UUID, primary key
agent_id        String, not null
agent_role      String, not null
event_type      String, not null  ("created" / "destroyed")
created_by      String, not null  ("human" / "lead_agent")
project_id      UUID, nullable
development_phase String, nullable
timestamp       DateTime with timezone
```

This implements Req 29 criterion 12.

---

## REQUIREMENTS BEING IMPLEMENTED

- **Req 27** — Intra-Frontend Coordination Protocol
  - Navigation_Contract before any coding ✓
  - Component_Registry — register and query ✓
  - Root layout dependency enforced ✓
  - Duplicate component detection ✓
- **Req 29** — Agent Lifecycle and Bootstrap Protocol
  - Mandatory creation sequence enforced ✓
  - Human approves execution agent count ✓
  - Agent creation events logged ✓
- **Req 22** — UI/UX Mockup Ingestion
  - Both paths implemented ✓
  - Lead_Agent review before Frontend_Agents receive spec ✓
- **Req 04** — Task_Dependency_Graph
  - Root layout dependency tracked ✓
  - Dependent tasks unlocked on completion ✓

---

## CODE QUALITY RULES

- All LLM calls in tests must be mocked — no real API calls
- Frontend_Agent role prompt must be a module-level constant
- NavigationContract must be validated by Lead_Agent before
  being written to Project_Memory
- ComponentRegistry operations must be atomic — no partial writes
- All agent creation events must be logged before the agent
  is returned from the factory method
- Apply BUILD_NOTES defensive normalisation to all new LLM
  response parsing in Frontend_Agent and NavigationNegotiator

---

## WHAT SUCCESS LOOKS LIKE

Run this and it works:

```bash
python main.py
pytest tests/ -v
```

- All 5 runs in main.py complete without errors
- Bootstrap protocol creates agents in correct order
- Navigation_Contract shows correct route ownership
- Component_Registry shows registered and imported components
- Root layout dependency unlocks correctly
- All 117 existing tests still pass
- New tests pass with mocked LLM (no real API calls in tests)
- Target: 150+ total tests passing

That is Phase 6 complete.
