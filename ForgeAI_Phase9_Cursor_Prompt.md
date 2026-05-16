# ForgeAI — Phase 9 Cursor Prompt
# Paste this entire document into Cursor as your starting instruction

---

## CONTEXT

This is Phase 9 of a 10-phase build plan for ForgeAI — an AI agent
orchestration system that simulates a real software company.

Phases 1-8 are complete (189 tests passing).
- Phase 1: Task_State_Machine, mock agents, audit log
- Phase 2: Sandbox, Test_Runner, real Docker execution
- Phase 3: Escalation_Ladder, Loop_Counter, Drift_Monitor
- Phase 4: Redis, Chroma, MinIO, full persistence
- Phase 5: Real LLM calls, Model_Router, Research_Agent, Architect_Agent
- Phase 6: Bootstrap Protocol, Navigation_Contract, Component_Registry
- Phase 6B: Playwright frontend QA, dual-mode QA_Agent
- Phase 7: QA rejection loop, Human Gate, Phase_Completion_Report
- Phase 8: Backend phase, API contract enforcement, second Human Gate

Do NOT modify any existing Phase 1-8 code unless a specific
instruction below requires it. Build on top of what exists.

---

## BUILD NOTES — MUST RESPECT

### From Phase 5:
1. All agents attempt LOW tier first, fall back to MEDIUM on
   schema validation failure.
2. All agents use defensive normalisation before Pydantic validation.
3. Large structured documents: first attempt 16384, retry 32768.
4. Settings fields use pool_* internally with MODEL_* env aliases.
5. BackendAgent complete_work() supports both legacy and LLM paths.
6. QAAgent llm_client is optional.

### From Phase 6:
7. LayoutSpecification has deterministic fallback on parse failure.

### From Phase 7:
8. Phase_Completion_Report deferred items show generic labels — fix in Phase 10.

### From Phase 8:
9. Pre-pull python:3.11-slim before backend phase to avoid TLS timeouts.

### Phase 9 Targets (from BUILD_NOTES — implement all four):
10. Lesson confidence levels — tag by resolution phase.
11. Lesson flagging and health score — track usage success rate.
12. Context guards — lesson metadata checked before injection.
13. Agent-driven compatibility check — APPLY / ADAPT / IGNORE.

---

## WHAT PHASE 9 BUILDS

Four things:

1. **Agent_Memory upgrades** — all four Phase 9 targets from
   BUILD_NOTES implemented on top of the existing Chroma-based
   AgentMemory system. (Req 10)

2. **Confidence scoring** — agents self-report certainty on every
   output. Low-confidence outputs trigger automatic peer review
   before entering the QA gate. (Req 12)

3. **Context_Window_Manager** — tracks token counts before every
   LLM call. Applies reduction strategies when context approaches
   the model's limit. (Req 20)

4. **Final review stub** — Lead_Agent performs a holistic review
   of all DONE task outputs against the Master_Document before
   declaring the project ready for delivery. (Req 13)

---

## NEW PROJECT STRUCTURE

Add these files. Do not remove any existing files:

```
forgeai/
├── memory/
│   ├── lesson_health.py      # NEW — health score and flagging
│   └── ...existing files...
├── intelligence/
│   ├── __init__.py
│   ├── confidence.py         # Confidence scoring
│   ├── context_manager.py    # Context_Window_Manager
│   ├── peer_review.py        # Automatic peer review trigger
│   └── schemas.py            # ConfidenceScore, ContextReduction,
│                             # PeerReviewResult, FinalReviewResult
└── ...existing files...

tests/
├── test_lesson_upgrades.py       # confidence, health, guards, APPLY/ADAPT/IGNORE
├── test_confidence_scoring.py    # scoring, threshold routing, peer review
├── test_context_manager.py       # token tracking, reduction strategies
├── test_final_review.py          # holistic review against Master_Document
└── ...existing files...
```

---

## AGENT_MEMORY UPGRADES — EXACT SPECIFICATION

### Upgrade 1 — Lesson confidence levels

Update the Lesson schema in `memory/schemas.py`:

```python
class Lesson(BaseModel):
    id: str
    agent_role: str
    failure_description: str
    root_cause: str
    resolution: str
    rule: str
    created_at: datetime
    project_id: str
    task_id: str
    # NEW FIELDS:
    confidence: str = "high"        # "low" / "medium" / "high"
    human_verified: bool = False     # True when resolved at Level 5
    resolved_at_escalation_level: int = 4  # 1-5
    health_score: float = 1.0       # 0.0 to 1.0
    total_uses: int = 0
    success_count: int = 0
    fail_count: int = 0
    flagged: bool = False
    flag_reason: str = ""
    context_guards: dict = {}        # tech_stack, framework, env
    supersedes: str | None = None    # ID of lesson this replaces
```

Confidence mapping — apply when writing a lesson:
```python
def confidence_from_escalation_level(level: int,
                                      human_verified: bool) -> str:
    if human_verified:
        return "high"
    if level <= 2:
        return "low"
    if level == 3:
        return "medium"
    return "high"   # level 4 or 5
```

### Upgrade 2 — Lesson health score and flagging

Add `LessonHealth` class — `memory/lesson_health.py`:

```python
class LessonHealth:
    def __init__(self, agent_memory: AgentMemory):
        self.memory = agent_memory

    async def record_success(self, lesson_id: str,
                              agent_role: str) -> None:
        # Increment success_count and total_uses
        # Recalculate health_score = success_count / total_uses
        # If flagged and health_score recovers above 0.7:
        #   unflag the lesson automatically
        pass

    async def record_failure(self, lesson_id: str,
                              agent_role: str,
                              failure_detail: str) -> None:
        # Increment fail_count and total_uses
        # Recalculate health_score
        # If health_score drops below 0.5: flag the lesson
        # Set flag_reason = failure_detail
        pass

    async def flag_lesson(self, lesson_id: str,
                           agent_role: str,
                           reason: str) -> None:
        # Explicitly flag a lesson
        # Flagged lessons excluded from search results
        pass

    async def get_health_report(self,
                                 agent_role: str) -> list[dict]:
        # Return all lessons for role with health metrics
        # Sorted by health_score ascending (worst first)
        pass
```

### Upgrade 3 — Context guards

When writing a lesson, automatically capture context from the
current project's Tech_Stack_Document:

```python
def build_context_guards(tech_stack: TechStackDocument) -> dict:
    return {
        "language": tech_stack.language,
        "framework": tech_stack.framework,
        "database": tech_stack.database,
        "environment": "any"        # default — can be overridden
    }
```

When retrieving lessons, filter by context guards AFTER
vector search:

```python
def context_matches(guards: dict,
                     current_context: dict) -> bool:
    for key, value in guards.items():
        if value == "any":
            continue
        if current_context.get(key) != value:
            return False
    return True
```

Update `AgentMemory.retrieve_lessons()` to:
1. Run vector search as before
2. Filter flagged lessons out
3. Apply context_matches() filter
4. Return only matching, unflagged lessons

### Upgrade 4 — Agent-driven compatibility check

This is implemented at the prompt level, not the code level.
Every agent's system prompt gets a structured section added
when lessons are retrieved:

```python
LESSON_COMPATIBILITY_PROMPT = """
Before starting your task, you have been given {count} relevant
lesson(s) from past failures on similar tasks:

{lessons_formatted}

For each lesson, do the following:
1. Compare the lesson against your project context:
   - Master Document: {master_doc_summary}
   - Tech Stack: {tech_stack_summary}
   - Your task: {task_description}

2. Decide:
   APPLY   — lesson is fully compatible. Follow it as your
             first approach.
   ADAPT   — lesson intent is right but specifics differ.
             Use its approach, adjust to your tech stack.
   IGNORE  — lesson contradicts your project context.
             Proceed independently.

3. State your decision and reasoning before starting.

Project docs always take priority over lessons.
A lesson is a shortcut only when it aligns with your context.
"""
```

Add `format_lessons_for_prompt()` to AgentMemory:

```python
def format_lessons_for_prompt(
        self,
        lessons: list[LessonQueryResult],
        task_description: str,
        master_doc_summary: str,
        tech_stack_summary: str) -> str:
    # Format lessons with confidence labels:
    # [HIGH CONFIDENCE] lesson.rule
    # [MEDIUM CONFIDENCE — verify applies] lesson.rule
    # [LOW CONFIDENCE — hint only] lesson.rule
    # Return filled LESSON_COMPATIBILITY_PROMPT
    pass
```

---

## CONFIDENCE SCORING — EXACT SPECIFICATION

### What it does

Every agent attaches a Confidence_Score (0-100) to every output.
Scores below the per-agent threshold trigger automatic peer review
before the task enters the QA gate.

### ConfidenceScore schema — `intelligence/schemas.py`

```python
class ConfidenceScore(BaseModel):
    score: int              # 0-100
    agent_id: str
    task_id: str
    rationale: str          # why the agent gave this score
    scored_at: datetime

class PeerReviewResult(BaseModel):
    task_id: str
    reviewer_agent_id: str
    approved: bool
    feedback: str
    confidence_in_review: int   # reviewer's own confidence
    reviewed_at: datetime
```

### Per-agent thresholds (from requirements document Q6.6)

```python
CONFIDENCE_THRESHOLDS = {
    "qa_agent": 80,             # strictest — last line of defence
    "research_agent": 75,       # high-stakes — feeds Master_Document
    "architect_agent": 75,      # high-stakes — feeds Master_Document
    "frontend_agent": 70,       # default
    "backend_agent": 70,        # default
    "lead_agent": 70,           # default
}
```

### ConfidenceScorer class — `intelligence/confidence.py`

```python
class ConfidenceScorer:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def score(self, task_id: str,
                     agent_id: str,
                     agent_role: str,
                     task_description: str,
                     output: str) -> ConfidenceScore:
        # LLM call — LOW complexity
        # Ask the agent to rate its own output 0-100
        # Include rationale in structured response
        # Apply defensive normalisation
        pass

    def get_threshold(self, agent_role: str) -> int:
        return CONFIDENCE_THRESHOLDS.get(agent_role, 70)

    def needs_peer_review(self, score: ConfidenceScore,
                           agent_role: str) -> bool:
        return score.score < self.get_threshold(agent_role)
```

### Automatic peer review — `intelligence/peer_review.py`

```python
class PeerReviewer:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def review(self, task_id: str,
                      task_description: str,
                      output: str,
                      original_agent_id: str,
                      reviewer_agent_id: str) -> PeerReviewResult:
        # LLM call — MEDIUM complexity
        # Reviewer reads task_description and output
        # Produces: approved (bool), feedback (str),
        #           confidence_in_review (int)
        # If approved: task proceeds to QA gate
        # If not approved: structured feedback returned to
        #   original agent before QA gate
        pass
```

### Integration with existing flow

Add confidence scoring step to QAOrchestrator.process_result():

```python
# BEFORE transitioning to IN_REVIEW, agent scores its own output:
confidence = await confidence_scorer.score(
    task_id, agent_id, agent_role, task_description, output)

if confidence_scorer.needs_peer_review(confidence, agent_role):
    peer_result = await peer_reviewer.review(
        task_id, task_description, output,
        agent_id, f"peer_{agent_role}_1")

    if not peer_result.approved:
        # Return output to agent with peer feedback
        # before it enters QA gate
        # This is NOT a QA rejection — it's pre-QA correction
        pass
```

---

## CONTEXT_WINDOW_MANAGER — EXACT SPECIFICATION

### What it does

Tracks estimated token count of each agent's input context before
every LLM call. When context exceeds 80% of the model's limit,
applies reduction strategies in priority order.

### Context reduction strategies (Req 20, criterion 3)

Priority order:
1. Retrieve only semantically relevant Lessons (already done — top-K)
2. Summarise completed Task_Memory into a compact digest
3. Retrieve only the relevant Master_Document section

### ContextWindowManager class — `intelligence/context_manager.py`

```python
class ContextWindowManager:
    # Token limits per model
    MODEL_TOKEN_LIMITS = {
        "claude-haiku-4-5-20251001": 200_000,
        "claude-sonnet-4-6": 200_000,
        "claude-opus-4-6": 200_000,
    }

    # Warn at 80% of limit
    WARNING_THRESHOLD = 0.80

    def __init__(self, llm_client: LLMClient,
                 task_memory: TaskMemory):
        self.llm = llm_client
        self.task_memory = task_memory
        self._reduction_log: list[dict] = []

    def estimate_tokens(self, text: str) -> int:
        # Rough estimate: 1 token ≈ 4 characters
        # Good enough for threshold detection
        return len(text) // 4

    def get_limit(self, model: str) -> int:
        return self.MODEL_TOKEN_LIMITS.get(model, 200_000)

    async def check_and_reduce(self,
                                context: str,
                                model: str,
                                task_id: str,
                                agent_id: str,
                                master_doc_section: str | None = None
                                ) -> ContextReductionResult:
        # 1. Estimate current token count
        # 2. If below 80% threshold: return context unchanged
        # 3. If above 80%: apply reduction strategies in order
        #    Strategy 1: already done (top-K lessons)
        #    Strategy 2: summarise Task_Memory into digest
        #    Strategy 3: truncate to relevant Master_Doc section
        # 4. Log every reduction event
        # 5. Return ContextReductionResult
        pass

    async def _summarise_task_memory(self,
                                      task_id: str) -> str:
        # Retrieve all Task_Memory keys for this task
        # LLM call — LOW complexity — summarise into compact digest
        # Returns digest string
        pass

    def _log_reduction(self, agent_id: str,
                        task_id: str,
                        strategy: str,
                        tokens_before: int,
                        tokens_after: int) -> None:
        # Log reduction event with agent, strategy, tokens saved
        pass

    def get_reduction_log(self) -> list[dict]:
        return self._reduction_log
```

### ContextReductionResult schema

```python
class ContextReductionResult(BaseModel):
    original_tokens: int
    final_tokens: int
    reduction_applied: bool
    strategies_used: list[str]
    under_limit: bool       # True if final tokens < limit
    reduced_context: str    # the reduced context string
```

### Integration with LLMClient

Add context check to LLMClient.complete():

```python
async def complete(self, system_prompt: str,
                   user_message: str,
                   complexity: str,
                   loop_count: int = 0,
                   max_tokens: int = 1000,
                   tools: list | None = None,
                   task_id: str | None = None,
                   agent_id: str | None = None) -> LLMResponse:

    # BEFORE making the API call:
    if self.context_manager and task_id and agent_id:
        full_context = system_prompt + user_message
        model = self.router.route(complexity, loop_count)
        reduction = await self.context_manager.check_and_reduce(
            full_context, model, task_id, agent_id)

        if not reduction.under_limit:
            # Escalate — cannot reduce below limit
            raise ContextWindowExceededError(
                f"Context {reduction.final_tokens} tokens "
                f"exceeds limit after all reductions")

    # ... existing API call logic
```

### Add to exception hierarchy

```python
class ContextWindowExceededError(ForgeAIError):
    # Raised when context cannot be reduced below model limit.
    # Treated as Level 3 escalation per Req 20, criterion 5.
    pass
```

---

## FINAL REVIEW STUB — EXACT SPECIFICATION

### What it does

Lead_Agent performs a holistic review of all DONE task outputs
against the Master_Document. Checks for consistency,
completeness, and integration. Creates new tasks for any
inconsistencies found. (Req 13)

This is a stub in Phase 9 — the full delivery pipeline
(Deployment_Package generation, Git tagging) comes in Phase 10.

### FinalReviewer class — `intelligence/final_review.py`

```python
class FinalReviewer:
    def __init__(self, llm_client: LLMClient,
                 db_session):
        self.llm = llm_client
        self.db = db_session

    async def review(self,
                      project_id: str,
                      master_document: MasterDocument,
                      completed_tasks: list[TaskSummary]
                      ) -> FinalReviewResult:
        # LLM call — HIGH complexity
        # Check all completed task outputs against Master_Document:
        #   - Does every component have a corresponding task? ✓
        #   - Does every API endpoint have frontend + backend tasks? ✓
        #   - Are there any obvious integration gaps? ✓
        # Return FinalReviewResult
        pass
```

### FinalReviewResult schema

```python
class FinalReviewResult(BaseModel):
    project_id: str
    passed: bool
    consistency_checks: list[str]   # what was verified
    gaps_found: list[str]           # plain language gap descriptions
    remediation_tasks: list[str]    # new task titles to create
    reviewed_at: datetime
    reviewer: str = "lead_agent"
```

---

## UPDATED main.py

Add Runs 11 and 12. Keep all existing runs.

### Run 11 — Agent Memory upgrades demonstration

1. Write 3 lessons with different confidence levels:
   - Lesson A: resolved at escalation Level 2 → confidence "low"
   - Lesson B: resolved at escalation Level 3 → confidence "medium"
   - Lesson C: resolved at escalation Level 4 → confidence "high"

2. Retrieve lessons for a new task — show confidence labels

3. Demonstrate health score:
   - Record 3 successes and 1 failure on Lesson C
   - Print health score: 3/4 = 0.75

4. Demonstrate lesson flagging:
   - Record failure on Lesson A (already low confidence)
   - Show it gets flagged
   - Retrieve again — Lesson A absent from results

5. Demonstrate context guards:
   - Write Lesson D with context guard: framework="Django"
   - Query with current context: framework="React"
   - Show Lesson D is filtered out despite semantic match

6. Demonstrate APPLY/ADAPT/IGNORE:
   - Format lessons for prompt and print the structured section
   - Show that confidence levels appear as labels

### Run 12 — Confidence scoring and Context_Window_Manager

1. Backend_Agent scores its own output on a task:
   - Print: score, rationale, threshold, needs_peer_review

2. Simulate a low-confidence output (score=55, threshold=70):
   - PeerReviewer reviews the output
   - Print peer review result

3. Context_Window_Manager demonstration:
   - Build a large context string (simulate a long project)
   - Run check_and_reduce()
   - Print: tokens before, strategies applied, tokens after

### Run 13 — Final review stub

1. Lead_Agent performs holistic review of all DONE tasks
2. Print FinalReviewResult:
   - consistency checks passed
   - any gaps found
   - remediation tasks created if needed

### Expected terminal output (abbreviated)

```
=== RUN 11: AGENT MEMORY UPGRADES ===

[MEMORY] Lesson A written — confidence: low (resolved at Level 2)
[MEMORY] Lesson B written — confidence: medium (resolved at Level 3)
[MEMORY] Lesson C written — confidence: high (resolved at Level 4)

[MEMORY] Retrieving lessons for: "Build a task creation API endpoint"
  1. [HIGH CONFIDENCE] Always validate input before database insert
  2. [MEDIUM CONFIDENCE — verify applies] Use UTC timestamps
  3. [LOW CONFIDENCE — hint only] Add retry on connection timeout

[MEMORY] Health score update for Lesson C:
  Successes: 3 | Failures: 1 | Health: 0.75

[MEMORY] Flagging Lesson A after failure...
[MEMORY] Lesson A flagged — excluded from future results

[MEMORY] Context guard test:
  Lesson D (Django): filtered out — current framework is React
  Retrieved: 2 lessons (context-compatible only)

=== RUN 12: CONFIDENCE SCORING ===

[CONFIDENCE] Backend_Agent scored output: 82/100
  Rationale: "Implementation covers all required endpoints
              with proper error handling"
  Threshold: 70 | Needs peer review: False

[CONFIDENCE] Low-confidence output simulation: 55/100
  Below threshold (70) — triggering peer review...
[PEER REVIEW] Reviewing output...
  Approved: False
  Feedback: "Missing input validation on the request body"
  Returning to agent before QA gate

[CONTEXT MANAGER] Large context detected
  Tokens before: ~45,000
  Strategy 1: Task_Memory digest applied — saved ~8,000 tokens
  Strategy 2: Master_Doc section only — saved ~12,000 tokens
  Tokens after: ~25,000 | Under limit: True

=== RUN 13: FINAL REVIEW ===

[LEAD] Running holistic final review...
[FINAL REVIEW] Checking all 29 completed tasks against Master_Document
  ✓ All components have corresponding tasks
  ✓ All API endpoints have frontend + backend coverage
  ✓ Navigation contract fully implemented
  Gaps found: 0
  Remediation tasks created: 0
[FINAL REVIEW] Passed — project ready for delivery
```

---

## TESTS

### test_lesson_upgrades.py

- Test confidence_from_escalation_level() maps correctly for all levels
- Test human_verified=True always returns "high" confidence
- Test lesson written with correct confidence field
- Test record_success() increments success_count and recalculates health
- Test record_failure() increments fail_count and recalculates health
- Test lesson auto-flagged when health_score drops below 0.5
- Test lesson auto-unflagged when health_score recovers above 0.7
- Test flagged lessons excluded from retrieve_lessons()
- Test context_matches() returns True when all guards match
- Test context_matches() returns False when one guard mismatches
- Test context_matches() returns True when guard value is "any"
- Test retrieve_lessons() filters lessons by context guards
- Test format_lessons_for_prompt() returns non-empty string
- Test format_lessons_for_prompt() contains confidence labels
- Test LESSON_COMPATIBILITY_PROMPT included in formatted output

### test_confidence_scoring.py

- Test score() returns ConfidenceScore with score 0-100
- Test score() returns non-empty rationale
- Test needs_peer_review() returns False when score >= threshold
- Test needs_peer_review() returns True when score < threshold
- Test qa_agent threshold is 80 (strictest)
- Test research_agent threshold is 75
- Test frontend_agent threshold is 70 (default)
- Test peer_reviewer.review() returns PeerReviewResult
- Test PeerReviewResult.approved is bool
- Test PeerReviewResult.feedback is non-empty string
- Test peer review uses MEDIUM complexity LLM call

### test_context_manager.py

- Test estimate_tokens() returns reasonable count for known string
- Test check_and_reduce() returns unchanged context below threshold
- Test check_and_reduce() applies reduction above threshold
- Test ContextReductionResult.reduction_applied is False below limit
- Test ContextReductionResult.reduction_applied is True above limit
- Test strategies_used list populated when reduction applied
- Test reduction log records each event
- Test ContextWindowExceededError raised when cannot reduce
- Test _summarise_task_memory() calls LLM with LOW complexity

### test_final_review.py

- Test review() returns FinalReviewResult
- Test FinalReviewResult.passed is True when no gaps
- Test FinalReviewResult.passed is False when gaps found
- Test consistency_checks list is non-empty
- Test gaps_found is empty list when all checks pass
- Test remediation_tasks created when gaps found
- Test review() uses HIGH complexity LLM call

---

## REQUIREMENTS BEING IMPLEMENTED

- **Req 10** — Self-Learning via Lessons
  - Confidence levels on lessons ✓
  - Health score tracking ✓
  - Context guards ✓
  - Agent-driven compatibility check ✓
- **Req 12** — Confidence Scoring and Automatic Peer Review
  - Per-agent thresholds ✓
  - Automatic peer review trigger ✓
  - Pre-QA correction path ✓
- **Req 20** — Context Window Management
  - Token estimation before every call ✓
  - Three reduction strategies in priority order ✓
  - Reduction event logging ✓
  - ContextWindowExceededError as Level 3 escalation ✓
- **Req 13** — Final Project Review (stub)
  - Holistic consistency check ✓
  - Gap detection ✓
  - Remediation task creation ✓

---

## CODE QUALITY RULES

- All LLM calls in tests must be mocked — no real API calls
- LESSON_COMPATIBILITY_PROMPT must be a module-level constant
- All new LLM response parsing must apply BUILD_NOTES defensive
  normalisation
- Context reduction log must write to INFO level:
  INFO: "Context reduction: agent={id} strategy={s} saved={n} tokens"
- Confidence scoring prompt must be a module-level constant
- ContextWindowManager must be optional on LLMClient —
  existing tests must not break

---

## WHAT SUCCESS LOOKS LIKE

```bash
python main.py
pytest tests/ -v
```

- Run 11 shows real lesson health scores and context filtering
- Run 12 shows real confidence scores from LLM output
- Run 12 shows peer review firing on low-confidence output
- Run 13 shows holistic final review passing
- All 189 existing tests still pass
- New tests pass with mocked LLM
- Target: 230+ total tests passing

That is Phase 9 complete.
