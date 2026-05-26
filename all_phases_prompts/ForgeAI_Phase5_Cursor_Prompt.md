# ForgeAI — Phase 5 Cursor Prompt
# Paste this entire document into Cursor as your starting instruction

---

## CONTEXT

This is Phase 5 of a 10-phase build plan for ForgeAI — an AI agent
orchestration system that simulates a real software company.

Phases 1-4 are complete and passing (91 tests passing).
- Phase 1: Task_State_Machine, mock agents, audit log
- Phase 2: Sandbox, Test_Runner, real Docker execution
- Phase 3: Escalation_Ladder, Loop_Counter, Drift_Monitor
- Phase 4: Redis, Chroma, MinIO, full persistence

Phase 5 is the most significant phase so far. Every agent stub gets
replaced with a real LLM-powered agent. Real API calls begin.
The SWAP_POINTs from Phase 3 and 4 get replaced.

Do NOT modify any existing Phase 1-4 code unless a specific
instruction below requires it. Build on top of what exists.

---

## WHAT PHASE 5 BUILDS

Five things:

1. **Anthropic SDK integration** — all LLM calls go through a single
   LLMClient wrapper that enforces the Model_Router logic from Req 30.

2. **Model_Router** — routes every LLM call to the correct model based
   on task complexity tier. Hard tier ceiling enforced. (Req 30)

3. **Research_Agent** — real LLM agent that gathers domain knowledge
   and technology options. Uses web search tool. (Req 02)

4. **Architect_Agent** — real LLM agent that produces the
   Master_Document and Tech_Stack_Document. (Req 02)

5. **SWAP_POINT replacements** — replace all sentence-transformers
   embedding calls with Anthropic embeddings API calls.

When Phase 5 is complete, ForgeAI can take a project brief,
research it with a real AI agent, and produce a real
Master_Document — the first genuine end-to-end intelligent flow.

---

## TECH STACK ADDITIONS — PHASE 5 ONLY

Add to requirements.txt:
- `anthropic` — Anthropic Python SDK

Add to .env.example:
```
ANTHROPIC_API_KEY=your_key_here

# Model Pool (Req 30)
MODEL_LOW_DEFAULT=claude-haiku-4-5-20251001
MODEL_LOW_ESCALATED=claude-sonnet-4-6
MODEL_MEDIUM_DEFAULT=claude-sonnet-4-6
MODEL_MEDIUM_ESCALATED=claude-sonnet-4-6
MODEL_HIGH_DEFAULT=claude-sonnet-4-6
MODEL_HIGH_ESCALATED=claude-opus-4-6
```

Do NOT introduce:
- FastAPI (Phase 5 still runs from terminal)
- Any frontend framework

---

## NEW PROJECT STRUCTURE

Add these files. Do not remove any existing files:

```
forgeai/
├── llm/
│   ├── __init__.py
│   ├── client.py           # LLMClient — single entry point for all
│   │                       # Anthropic API calls
│   ├── model_router.py     # Model_Router — complexity → model mapping
│   └── schemas.py          # Pydantic schemas for LLMRequest, LLMResponse
├── agents/
│   ├── research_agent.py   # REPLACE stub — real LLM agent
│   ├── architect_agent.py  # REPLACE stub — real LLM agent
│   ├── lead_agent.py       # UPDATE — wire in real agents
│   ├── backend_agent.py    # UPDATE — replace hardcoded outputs with
│   │                       # real LLM calls
│   └── qa_agent.py         # UPDATE — replace hardcoded decisions with
│   │                       # real LLM-assisted defect analysis
└── ...existing files...

tests/
├── test_model_router.py        # routing logic, tier ceiling enforcement
├── test_llm_client.py          # mocked API calls, retry logic
├── test_research_agent.py      # mocked LLM — research output structure
├── test_architect_agent.py     # mocked LLM — Master_Document structure
└── ...existing files...
```

---

## MODEL_ROUTER — EXACT SPECIFICATION

### What it does

Intercepts every LLM call before it reaches the API.
Reads task complexity (LOW/MEDIUM/HIGH) and loop_count.
Returns the correct model identifier.
Has no memory. Makes no LLM calls. Holds no project context.

### Model_Router logic — `llm/model_router.py`

```python
class ModelRouter:
    def __init__(self, model_pool: ModelPool):
        self.pool = model_pool

    def route(self, complexity: str,
              loop_count: int = 0) -> str:
        # complexity: "LOW", "MEDIUM", or "HIGH"
        # loop_count: current Loop_Counter value for this task
        #
        # Routing rules (Req 30):
        # - If loop_count < 2: return pool.default for complexity tier
        # - If loop_count >= 2: return pool.escalated for complexity tier
        # - NEVER route LOW to HIGH pool (tier ceiling is hard)
        # - NEVER route MEDIUM to HIGH pool without human approval
        # - Only HIGH tier can access Opus (escalated HIGH)
        #
        # Return model identifier string e.g. "claude-sonnet-4-6"
        pass

    def get_tier_ceiling(self, complexity: str) -> str:
        # Return the maximum model available for this complexity tier
        # LOW ceiling: MODEL_LOW_ESCALATED
        # MEDIUM ceiling: MODEL_MEDIUM_ESCALATED
        # HIGH ceiling: MODEL_HIGH_ESCALATED
        pass
```

### ModelPool schema — `llm/schemas.py`

```python
class TierPool(BaseModel):
    default: str        # model used at loop_count < 2
    escalated: str      # model used at loop_count >= 2

class ModelPool(BaseModel):
    low: TierPool
    medium: TierPool
    high: TierPool

    @classmethod
    def from_env(cls) -> "ModelPool":
        # Load all six model strings from environment variables
        pass
```

---

## LLM_CLIENT — EXACT SPECIFICATION

### What it does

Single entry point for every Anthropic API call in ForgeAI.
Enforces model routing. Handles retries on rate limits.
Logs every call with model, tokens used, and cost estimate.

### LLMClient class — `llm/client.py`

```python
class LLMClient:
    def __init__(self, api_key: str, model_router: ModelRouter):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.router = model_router

    async def complete(self,
                       system_prompt: str,
                       user_message: str,
                       complexity: str,
                       loop_count: int = 0,
                       max_tokens: int = 1000,
                       tools: list | None = None) -> LLMResponse:
        # 1. Call model_router.route(complexity, loop_count)
        #    to get the model identifier
        # 2. Call Anthropic API with:
        #    - model from router
        #    - system prompt
        #    - user message
        #    - tools if provided
        #    - max_tokens
        # 3. On rate limit (429): exponential backoff, max 3 retries
        # 4. Log: model used, input tokens, output tokens, estimated cost
        # 5. Return LLMResponse
        pass

    def _estimate_cost(self, model: str,
                       input_tokens: int,
                       output_tokens: int) -> float:
        # Return estimated cost in USD based on model pricing
        # Haiku:  input $0.80/1M, output $4.00/1M  (claude-haiku-4-5)
        # Sonnet: input $3.00/1M, output $15.00/1M (claude-sonnet-4-6)
        # Opus:   input $15.00/1M, output $75.00/1M (claude-opus-4-6)
        pass
```

### LLMResponse schema

```python
class LLMResponse(BaseModel):
    content: str            # text response from model
    model_used: str         # actual model that was called
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    tool_calls: list = []   # populated if tools were used
```

---

## SWAP_POINT REPLACEMENTS

Search the codebase for every comment marked:
`# SWAP_POINT: replace with Anthropic embeddings API from Phase 5`

Replace each one with a call to the Anthropic embeddings endpoint.

Anthropic does not have a dedicated embeddings API — use the
voyage-3 model via the anthropic SDK for embeddings, or alternatively
keep sentence-transformers for embeddings (it performs well) and only
replace the completion calls.

DECISION: Keep sentence-transformers for embeddings. Anthropic's
embedding story is still maturing. Update the SWAP_POINT comments to:
`# EMBEDDING: using sentence-transformers/all-MiniLM-L6-v2`
`# Future: replace with dedicated embedding model when available`

This means the only SWAP_POINT work in Phase 5 is confirming all
completion calls go through LLMClient. Embeddings stay as-is.

---

## RESEARCH_AGENT — EXACT SPECIFICATION

### Role prompt

```
You are Research_Agent, a specialist in technology research for
software projects. Your role is to gather domain knowledge,
evaluate technology options, and produce structured research
findings that Architect_Agent will use to design the system.

You are thorough, objective, and evidence-based. You evaluate
at least two technology stack options for every major decision.
You never recommend a technology without explaining why.

You output structured JSON only. Never output prose outside
of the JSON structure.
```

### Research_Agent class — `agents/research_agent.py`

```python
class ResearchAgent(BaseAgent):
    def __init__(self, agent_id: str, db_session,
                 llm_client: LLMClient,
                 agent_memory: AgentMemory):
        super().__init__(agent_id, db_session)
        self.llm = llm_client
        self.memory = agent_memory
        self.agent_role = "research_agent"

    async def research(self, project_brief: str,
                        preflight_constraints: dict) -> ResearchOutput:
        # 1. Query agent_memory for relevant past lessons
        # 2. Build system prompt from role prompt + top lessons
        # 3. Call llm_client.complete() with:
        #    - complexity="MEDIUM"
        #    - user message = project brief + constraints
        #    - web search tool enabled
        # 4. Parse response into ResearchOutput
        # 5. Return ResearchOutput
        pass
```

### Web search tool definition

```python
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search"
}
```

Pass this in the tools list when calling LLMClient for Research_Agent.

### ResearchOutput schema

```python
class ResearchOutput(BaseModel):
    domain_summary: str         # what this project is about
    technology_options: list[TechnologyOption]
    recommended_stack: TechStack
    constraints_respected: list[str]  # pre-flight constraints honoured
    research_sources: list[str]       # URLs or references used

class TechnologyOption(BaseModel):
    name: str
    pros: list[str]
    cons: list[str]
    suitable: bool

class TechStack(BaseModel):
    language: str
    framework: str
    database: str
    testing_framework: str
    rationale: str
    rejected_alternatives: list[str]
```

---

## ARCHITECT_AGENT — EXACT SPECIFICATION

### Role prompt

```
You are Architect_Agent, a senior software architect. Your role
is to produce the Master_Document — the authoritative project
specification that all other agents will work from.

You receive research findings from Research_Agent and a project
brief. You produce a complete, unambiguous specification covering
system architecture, component boundaries, data models, and API
surface areas.

You are precise, complete, and consistent. Every component you
define must have clear boundaries. Every API you specify must
have complete request and response schemas.

You output structured JSON only. Never output prose outside
of the JSON structure.
```

### Architect_Agent class — `agents/architect_agent.py`

```python
class ArchitectAgent(BaseAgent):
    def __init__(self, agent_id: str, db_session,
                 llm_client: LLMClient,
                 agent_memory: AgentMemory):
        super().__init__(agent_id, db_session)
        self.llm = llm_client
        self.memory = agent_memory
        self.agent_role = "architect_agent"

    async def produce_master_document(
            self,
            project_brief: str,
            research_output: ResearchOutput,
            preflight_constraints: dict) -> MasterDocument:
        # 1. Query agent_memory for relevant past lessons
        # 2. Build system prompt from role prompt + top lessons
        # 3. Call llm_client.complete() with:
        #    - complexity="HIGH"
        #    - user message = brief + research_output + constraints
        # 4. Parse response into MasterDocument
        # 5. Return MasterDocument
        pass

    async def produce_tech_stack_document(
            self,
            research_output: ResearchOutput) -> TechStackDocument:
        # Produce formal Tech_Stack_Document from research findings
        # complexity="MEDIUM"
        pass
```

### MasterDocument schema

```python
class MasterDocument(BaseModel):
    version: str = "1.0"
    project_name: str
    project_summary: str
    components: list[Component]
    data_models: list[DataModel]
    api_surfaces: list[APISurface]
    tech_stack: TechStack
    constraints: list[str]
    created_at: datetime

class Component(BaseModel):
    name: str
    responsibility: str
    dependencies: list[str]
    acceptance_criteria: list[str]

class DataModel(BaseModel):
    name: str
    fields: list[DataField]

class DataField(BaseModel):
    name: str
    type: str
    required: bool
    description: str

class APISurface(BaseModel):
    endpoint: str
    method: str
    request_schema: dict
    response_schema: dict
    description: str

class TechStackDocument(BaseModel):
    language: str
    framework: str
    database: str
    testing_framework: str
    libraries: list[str]
    rationale: str
    rejected_alternatives: list[str]
    version: str = "1.0"
    created_at: datetime
```

---

## BACKEND_AGENT UPDATE

Replace the hardcoded output string with a real LLM call.

```python
class BackendAgent(BaseAgent):
    def __init__(self, agent_id: str, db_session,
                 llm_client: LLMClient,
                 agent_memory: AgentMemory):
        super().__init__(agent_id, db_session)
        self.llm = llm_client
        self.memory = agent_memory
        self.agent_role = "backend_agent"

    async def complete_work(self, task_id: UUID,
                             task_description: str,
                             master_document_section: str,
                             loop_count: int = 0) -> Task:
        # 1. Query agent_memory for relevant past lessons
        # 2. Build prompt from task + master_document_section + lessons
        # 3. Call llm_client.complete() with:
        #    - complexity from task record
        #    - loop_count for model escalation
        # 4. Extract code from response
        # 5. Transition task IN_PROGRESS → IN_REVIEW with output
        pass
```

---

## PROJECT_MEMORY INTEGRATION

Master_Document and Tech_Stack_Document must be written to
Project_Memory (PostgreSQL) after Lead_Agent approves them.

Add a new PostgreSQL table via Alembic migration:

```
project_artefacts
─────────────────
id              UUID, primary key
project_id      UUID, not null
artefact_type   String, not null
                ("master_document", "tech_stack_document",
                 "api_contract", "navigation_contract")
content         JSONB, not null
version         Integer, not null, default 1
is_current      Boolean, not null, default True
created_at      DateTime with timezone
created_by      String, not null (agent_id that produced it)
```

When a new version is written, set is_current=False on the
previous version and insert a new row with is_current=True.
This implements immutable artefact versioning from Req 11.

---

## UPDATED main.py

Replace main.py with a version that runs the first real
end-to-end intelligent flow.

### Run 1 — Research and Architecture

```
Project brief:
"Build a restaurant booking website. Users should be able to
browse the menu, make reservations, and manage their bookings.
We need an admin panel for the restaurant owner."

Pre-flight constraints:
{
  "preferred_language": "Python",
  "database": "PostgreSQL",
  "deployment": "Docker"
}
```

Steps:
1. Research_Agent researches the brief with web search
2. Print ResearchOutput summary
3. Architect_Agent produces Master_Document from research
4. Print Master_Document summary (project_name, component count,
   API surface count, data model count)
5. Lead_Agent writes both documents to Project_Memory
6. Print confirmation with artefact IDs

### Run 2 — Backend task with real LLM

1. Create a task: "Implement menu listing endpoint"
   complexity=MEDIUM
2. BackendAgent produces real code via LLM
3. Print the generated code
4. QAAgent submits to Sandbox
5. Print test results
6. Task reaches DONE or escalates if tests fail

### Expected terminal output (abbreviated)

```
=== RUN 1: RESEARCH AND ARCHITECTURE ===
[RESEARCH] Starting research for: restaurant booking website
[RESEARCH] Web search active — gathering domain knowledge
[RESEARCH] Research complete
  Domain: Restaurant management and online booking systems
  Recommended stack: Python · FastAPI · PostgreSQL · pytest
  Options evaluated: 3
  Sources: X references

[ARCHITECT] Producing Master_Document...
[ARCHITECT] Master_Document complete
  Project: Restaurant Booking System
  Components: X defined
  APIs: X endpoints specified
  Data models: X models defined

[LEAD] Writing to Project_Memory...
[LEAD] Master_Document saved — version 1.0
[LEAD] Tech_Stack_Document saved — version 1.0

=== RUN 2: BACKEND TASK WITH REAL LLM ===
[FORGEAI] Task created: Implement menu listing endpoint
...
[BACKEND] Generating implementation via LLM (claude-sonnet-4-6)...
[BACKEND] Code generated — X lines
[FORGEAI] Sandbox executing tests...
[FORGEAI] Tests complete: X/X passed
```

---

## TESTS

### test_model_router.py

- Test LOW complexity + loop_count 0 returns MODEL_LOW_DEFAULT
- Test LOW complexity + loop_count 2 returns MODEL_LOW_ESCALATED
- Test MEDIUM complexity + loop_count 0 returns MODEL_MEDIUM_DEFAULT
- Test MEDIUM complexity + loop_count 2 returns MODEL_MEDIUM_ESCALATED
- Test HIGH complexity + loop_count 0 returns MODEL_HIGH_DEFAULT
- Test HIGH complexity + loop_count 2 returns MODEL_HIGH_ESCALATED
- Test LOW task NEVER returns a HIGH tier model regardless of loop_count
- Test get_tier_ceiling returns correct ceiling per tier
- Test invalid complexity raises ValueError

### test_llm_client.py

Use unittest.mock to mock the Anthropic SDK client.
Never make real API calls in tests.

- Test complete() calls anthropic client with correct model
- Test complete() uses model from router, not hardcoded
- Test rate limit (429) triggers exponential backoff and retry
- Test max retries exceeded raises LLMRateLimitError
- Test cost estimation returns correct values for each model
- Test LLMResponse is fully populated on success
- Test loop_count=2 causes router to return escalated model

### test_research_agent.py

Mock LLMClient. Never make real API calls in tests.

- Test research() calls LLMClient with complexity="MEDIUM"
- Test research() queries agent_memory before calling LLM
- Test research() passes web search tool to LLMClient
- Test research() returns a valid ResearchOutput
- Test ResearchOutput contains at least one technology_option
- Test ResearchOutput recommended_stack is populated

### test_architect_agent.py

Mock LLMClient. Never make real API calls in tests.

- Test produce_master_document() calls LLMClient with complexity="HIGH"
- Test produce_master_document() queries agent_memory before calling LLM
- Test MasterDocument has at least one component
- Test MasterDocument has at least one api_surface
- Test MasterDocument has at least one data_model
- Test produce_tech_stack_document() returns valid TechStackDocument

---

## CRITICAL TESTING RULE

No test in this codebase may make a real Anthropic API call.
All LLM calls in tests must be mocked with unittest.mock.

Use this pattern for mocking:

```python
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_research_agent(mock_db_session, mock_agent_memory):
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=json.dumps({
            "domain_summary": "Restaurant booking system",
            "technology_options": [...],
            "recommended_stack": {...},
            "constraints_respected": [],
            "research_sources": []
        }),
        model_used="claude-sonnet-4-6",
        input_tokens=500,
        output_tokens=200,
        estimated_cost_usd=0.0045
    )
    agent = ResearchAgent("research_1", mock_db_session,
                          mock_llm, mock_agent_memory)
    result = await agent.research("Build a booking system", {})
    assert result.domain_summary != ""
```

---

## REQUIREMENTS BEING IMPLEMENTED

- **Req 02** — Research and Architecture Phase
  - Research_Agent gathers domain knowledge ✓
  - Architect_Agent produces Master_Document ✓
  - Tech_Stack_Document produced and stored ✓
- **Req 30** — Dynamic Model Routing via Model_Router
  - Complexity-based routing ✓
  - Hard tier ceiling enforced ✓
  - Loop_count escalation within tier ✓
- **Req 14** — Master_Document as Single Source of Truth
  - Written to Project_Memory as versioned artefact ✓
- **Req 21** — Technology Stack Selection
  - Tech_Stack_Document produced during Research phase ✓

---

## CODE QUALITY RULES

- Every real LLM call must go through LLMClient.complete()
  Never call anthropic.Anthropic() directly from an agent
- Every LLM call must be logged with:
  INFO: model used, token counts, estimated cost
- All agent prompts must be defined as module-level constants
  never constructed inline in the method
- LLMClient must handle the case where the Anthropic API
  returns a non-text content block gracefully
- Add LLMRateLimitError to the ForgeAI exception hierarchy
- All new Pydantic schemas must have example values defined

---

## WHAT SUCCESS LOOKS LIKE

Run this and it works:

```bash
python main.py
pytest tests/ -v
```

- Run 1 produces a real Master_Document from a real LLM
- The Master_Document is written to PostgreSQL
- Run 2 produces real code from a real LLM
- All 91 existing tests still pass
- New tests pass using mocked LLM (no real API calls in tests)
- Target: 115+ total tests passing

That is Phase 5 complete.
