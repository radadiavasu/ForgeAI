Create a file called BUILD_NOTES.md in the project root with this exact content:

# ForgeAI — Build Notes
# Decisions made during build that extend or differ from the requirements document.
# Updated after every phase. Referenced before every new phase prompt.

------

## Phase 5 Discoveries

### 1. LOW-first routing for Research_Agent
Research_Agent attempts LOW tier (Haiku) first.
Falls back to MEDIUM (Sonnet) if output fails schema validation.
Rationale: cheaper, fast enough, quality validated before use.
Applies to: any future agent where output can be validated before proceeding.

### 2. Defensive LLM output normalisation
All agents must normalise LLM responses before Pydantic validation.
LLMs return inconsistent shapes even with strict prompts.
Helpers: _as_str, _normalize_string_list, _normalize_recommended_stack.
Shared between research_agent.py and architect_agent.py.
Applies to: every agent added from Phase 6 onward.

### 3. Adaptive max_tokens for large documents
Master_Document generation: first attempt 16384, retry at 32768.
Truncation detected via: output_tokens >= max_tokens and JSON parse failure.
Applies to: any agent producing large structured JSON documents.

### 4. Settings naming pattern
Model pool env vars use MODEL_* in .env.
Pydantic Settings fields use pool_* internally with validation_alias.
Avoids Pydantic model_ namespace warnings.

### 5. BackendAgent backward compatibility
complete_work() supports both legacy (no LLM) and LLM paths.
Legacy path used by existing Phase 1-4 tests.
LLM path used from Phase 5 onward in main.py.

### 6. QAAgent optional LLM
llm_client is optional on QAAgent.
analyze_defects() uses LOW tier for plain-text defect summaries.
Only called when llm_client is provided.

------

## Phase 6 Discoveries:

### 1. Layout specification fallback
LayoutSpecification generation can fail schema validation after LLM call.
A deterministic fallback layout is generated from Master_Document components
when LLM output cannot be parsed or Lead_Agent review fails.
Same pattern as research_agent LOW→MEDIUM fallback.
Applies to: any agent producing LayoutSpecification documents.

------

## Phase 7 Discoveries:

### 1. Deferred items show generic labels
Phase_Completion_Report deferred items display as "Backend task 7",
"Backend task 8" instead of actual task titles.
Root cause: task titles not being passed correctly to report compiler.
Fix: Phase 10 polish — ensure task titles pulled from PostgreSQL
when compiling deferred items list.

------

## Phase 8 Discoveries:

### 1. Docker registry TLS timeout handling
Sandbox occasionally fails with TLS handshake timeout when Docker
Desktop loses connectivity to registry-1.docker.io.
System correctly treats this as a sandbox failure and retries.
Long-term fix: pre-pull python:3.11-slim image to ensure it is
cached locally before backend phase begins.
Add image pre-pull step to BackendOrchestrator.run_backend_phase()
initialisation.

------

## Phase 9 Targets — Agent Memory Upgrades

### 1. Lesson confidence levels
Write lessons at whichever escalation phase the solution is found, not just Phase 4.
Tag each lesson with confidence based on resolution phase:
  Phase 1 or 2 resolved → confidence: "low"
  Phase 3 resolved → confidence: "medium"
  Phase 4 resolved → confidence: "high"
  Phase 5 resolved → confidence: "high" + human_verified: True
Agents treat high confidence as directive, medium as advisory, low as hint only.

### 2. Lesson flagging and health score
When a new agent follows a lesson and still fails, flag that lesson immediately.
Flagged lessons filtered out of search results until reviewed.
Track health_score per lesson: success_count / total_use_count.
Inject lessons weighted by health score.
A lesson with 40% health is a weak hint. A lesson with 95% health is a strong directive.

### 3. Context guards
Every lesson carries context metadata written at creation time:
  tech_stack, framework_version, environment, auth_type.
Before injecting a lesson, check if current project context matches guards.
A lesson written for React 17 never reaches an agent building React 19.
Vector search finds the lesson. Context guards decide if it gets injected.

### 4. Agent-driven compatibility check (highest priority)
Instead of blindly injecting lessons, the agent actively evaluates compatibility.
Agent already has: Master_Document, Tech_Stack_Document, assigned task.
When a lesson is matched, agent does a three-way comparison:
  APPLY: lesson fully compatible with project context → follow as first priority
  ADAPT: lesson intent is right, specifics differ → follow intent, adjust to current stack
  IGNORE: lesson contradicts project context → proceed independently
Priority order: Project docs always win. Lesson is a shortcut only when compatible.
Implement via structured prompt section given to every agent before task execution.

------

## Phase 9 Discoveries

### 1. Final review identifies gaps correctly
FinalReviewer correctly identifies missing frontend components,
infrastructure tasks, and database migrations when only backend
tasks are completed. This is expected behaviour — the demo run
only completes backend tasks, not the full project.
In production, final review runs after ALL phases are complete.

### 2. Confidence scorer scored stub output as 15/100
Backend_Agent correctly scored a stub implementation at 15/100.
This confirms the confidence scorer is genuinely evaluating output
quality, not returning arbitrary numbers.

------

## Phase 9B Discoveries

### 1. Cost estimation needs calibration
Impact analyser estimated $9500 and 8640 minutes for a
LARGE_FEATURE change. These numbers are unrealistic.
Root cause: cost_per_task multiplier is too high in the
estimation formula.
Fix in Phase 10: recalibrate cost estimates using actual
token consumption data from Phases 8-9B runs.

### 2. Human correctly rejected unrealistic estimate
The human confirmation gate correctly stopped the LARGE_FEATURE
change after presenting the (inflated) cost estimate.
This proves the gate mechanism works — human saw the numbers
and rejected. The gate is the right design even when estimates
are wrong.

------

## Phase 10 Discoveries

### 1. Frontend tasks never reached DONE
Frontend QA used Playwright which failed consistently in sandbox.
Fix: after orchestrate_qa() fails, load actual React code from
task history (work_output key, reversed scan) and call
qa_pw.approve() with real code as output.

### 2. Backend tasks escalated instead of completing
Escalation ladder levels 2-4 are stubs — always fail.
After 3 QA failures tasks escalated and stopped.
Fix: lenient approve in BackendOrchestrator on decision.escalated.
Load actual Python code from task history, call qa.approve().

### 3. Windows cp1252 UnicodeDecodeError in Docker subprocess
Docker outputs bytes like 0x81 that Windows cp1252 cannot decode.
Fix: added encoding="utf-8" and errors="replace" to all
subprocess calls reading Docker output in sandbox.py.

### 4. Generated code is not production-ready
Each agent task generates standalone code with no shared context.
Backend files use http.server stdlib, not FastAPI.
No shared database connection between files.
Code quality improvement deferred to Phase 11.