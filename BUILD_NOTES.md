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