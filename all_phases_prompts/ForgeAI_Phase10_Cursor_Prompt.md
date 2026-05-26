# ForgeAI — Phase 10 Cursor Prompt

# Paste this entire document into Cursor as your starting instruction

---

## CONTEXT

This is Phase 10 of the ForgeAI build plan — the core delivery phase.

Phases 1-9B are complete (269 tests passing).

Do NOT modify any existing Phase 1-9B code unless a specific
instruction below requires it. Build on top of what exists.

---

## BUILD NOTES — MUST RESPECT AND FIX

### From previous phases (carry-forward rules):

1. All agents attempt LOW tier first, fall back to MEDIUM.
2. All agents use defensive normalisation before Pydantic validation.
3. Large structured documents: first attempt 16384, retry 32768.
4. ContextWindowManager is optional on LLMClient.
5. Human approval simulated via auto_approve() callback in main.py.

### Phase 10 FIXES — implement all three:

**Fix 1 — Task titles in Phase_Completion_Report (Phase 7)**
Deferred items currently show "Backend task 7" etc.
Fix: when compiling PhaseCompletionReport, query task titles
from PostgreSQL using task_id. Never use placeholder labels.
The report must show actual task titles for every completed
and deferred item.

**Fix 2 — Pre-pull python:3.11-slim (Phase 8)**
Add image pre-pull step to BackendOrchestrator.run_backend_phase()
before any task execution begins:

```python
async def _prepull_sandbox_image(self) -> None:
    # docker pull python:3.11-slim
    # Called once at the start of run_backend_phase()
    # Ensures image is cached before tasks run
    # Logs: INFO "Pre-pulling sandbox image python:3.11-slim"
    # If pull fails: log WARNING, continue (image may already exist)
```

**Fix 3 — Cost estimate calibration (Phase 9B)**
Impact_Analyser estimated $9500 for a large feature change.
Fix: replace the placeholder multiplier with realistic values
based on actual token consumption observed across Phases 8-9B:
  BUGFIX: affected_tasks * $0.05
  SMALL_FEATURE: (affected + new) * $0.08
  LARGE_FEATURE: $0.25 (research+arch) + (affected + new) * $0.10
  ARCHITECTURAL: "Requires assessment — contact your team"
Time estimates (minutes):
  BUGFIX: affected_tasks * 5
  SMALL_FEATURE: (affected + new) * 8
  LARGE_FEATURE: 30 + (affected + new) * 12
  ARCHITECTURAL: "Requires assessment"

---

## WHAT PHASE 10 BUILDS

Four things:

1. **Deployment_Package assembly** — reads all DONE task outputs
  from PostgreSQL, writes them to actual files on disk, generates
   Dockerfile, docker-compose.yml, .env.example, README, and final
   summary report. QA_Agent validates the Docker build. (Req 25)
2. **Git_Repository integration** — every completed task gets a
  real Git commit. Final delivery tags the repository as
   release-v1. Rollback_Points created at each milestone. (Req 19)
3. **Final summary report** — human-readable document listing all
  completed tasks, lessons accumulated, total cost, Rollback_Points,
   and any deferred items. Included in the Deployment_Package. (Req 13)
4. **BUILD_NOTES polish** — all three fixes listed above applied.

---

## NEW PROJECT STRUCTURE

Add these files. Do not remove any existing files:

```
forgeai/
├── delivery/
│   ├── __init__.py
│   ├── package_assembler.py  # Assembles Deployment_Package
   ├── git_manager.py        # Git operations — commit, tag, rollback
│   ├── readme_generator.py  # Generates plain-language README
│   └── schemas.py           # DeploymentPackage, GitCommit,
│                            # FinalSummaryReport, RollbackPoint
└── ...existing files...

tests/
├── test_package_assembler.py
├── test_git_manager.py
├── test_readme_generator.py
└── ...existing files...
```

---

## GIT_MANAGER — EXACT SPECIFICATION

### What it does

Manages a real Git repository for the project. Every DONE task
gets one commit. Milestones get tags. Rollbacks restore to
named tags.

### GitManager class — `delivery/git_manager.py`

```python
import subprocess
from pathlib import Path

class GitManager:
    def __init__(self, repo_path: str):
        # repo_path: absolute path to the project output directory
        # e.g. /home/claude/forgeai-output/{project_id}/
        self.repo_path = Path(repo_path)

    def init_repo(self) -> None:
        # git init in repo_path
        # git config user.email "forgeai@local"
        # git config user.name "ForgeAI"
        # Create initial .gitignore
        pass

    def commit(self, task_id: str,
                agent_id: str,
                master_doc_section: str,
                files: list[str]) -> GitCommit:
        # git add {files}
        # git commit -m "task:{task_id} agent:{agent_id}
        #               section:{master_doc_section}"
        # Return GitCommit with hash, message, timestamp
        pass

    def create_tag(self, tag_name: str,
                    message: str) -> RollbackPoint:
        # git tag -a {tag_name} -m "{message}"
        # Return RollbackPoint
        pass

    def rollback_to_tag(self, tag_name: str) -> None:
        # git checkout {tag_name}
        # WARNING: destructive — only called on explicit human request
        pass

    def get_log(self, max_entries: int = 50) -> list[GitCommit]:
        # git log --oneline -n {max_entries}
        # Parse and return list of GitCommit
        pass

    def get_tags(self) -> list[RollbackPoint]:
        # git tag -l
        # Return list of RollbackPoint
        pass
```

### GitCommit and RollbackPoint schemas — `delivery/schemas.py`

```python
class GitCommit(BaseModel):
    hash: str
    message: str
    author: str
    timestamp: datetime
    task_id: str | None = None
    agent_id: str | None = None

class RollbackPoint(BaseModel):
    tag_name: str
    message: str
    created_at: datetime
    commit_hash: str
```

---

## PACKAGE_ASSEMBLER — EXACT SPECIFICATION

### What it does

Reads all DONE task outputs from Project_Memory (PostgreSQL).
Determines the correct file path for each output based on:

- Task domain (frontend → src/pages/ or src/components/)
- Tech stack (React → .jsx, Python → .py)
- Task title (used to derive the filename)

Writes all files to the output directory. Generates supporting
files. Validates the Docker build.

### PackageAssembler class — `delivery/package_assembler.py`

```python
class PackageAssembler:
    def __init__(self,
                 db_session,
                 git_manager: GitManager,
                 qa_agent: QAAgent,
                 llm_client: LLMClient):
        self.db = db_session
        self.git = git_manager
        self.qa = qa_agent
        self.llm = llm_client

    async def assemble(self,
                        project_id: str,
                        master_document: MasterDocument,
                        tech_stack: TechStackDocument,
                        output_dir: str) -> DeploymentPackage:
        # 1. Create output directory structure
        # 2. Query all DONE tasks from PostgreSQL
        # 3. For each task: write output to correct file path
        # 4. Generate Dockerfile
        # 5. Generate docker-compose.yml
        # 6. Generate .env.example
        # 7. Generate README.md
        # 8. Generate final summary report
        # 9. Git commit each file as it's written
        # 10. Create release-v1 Git tag
        # 11. QA_Agent validates Docker build
        # 12. If build fails: create remediation task
        # 13. Return DeploymentPackage
        pass

    def _derive_file_path(self,
                           task_title: str,
                           task_domain: str,
                           tech_stack: TechStackDocument) -> str:
        # Derive file path from task metadata
        # Frontend React tasks → src/pages/ or src/components/
        # Backend Python tasks → src/api/ or src/models/
        # Test tasks → tests/
        # Returns relative path e.g. "src/pages/Dashboard.jsx"
        pass

    def _create_directory_structure(self,
                                     output_dir: str,
                                     tech_stack: TechStackDocument
                                     ) -> None:
        # Create standard directory structure:
        # src/pages/, src/components/, src/api/, src/models/
        # tests/, docs/
        pass

    async def _generate_dockerfile(self,
                                    tech_stack: TechStackDocument,
                                    output_dir: str) -> str:
        # LLM call — MEDIUM complexity
        # Generate Dockerfile appropriate for tech stack
        # React + Python → multi-stage: node build + python serve
        # Returns Dockerfile content as string
        pass

    async def _generate_docker_compose(self,
                                        tech_stack: TechStackDocument,
                                        output_dir: str) -> str:
        # LLM call — LOW complexity
        # Generate docker-compose.yml with:
        #   app service, db service (if PostgreSQL)
        #   health checks, volume mounts, env vars
        # Returns docker-compose.yml content as string
        pass

    def _generate_env_example(self,
                               tech_stack: TechStackDocument) -> str:
        # Generate .env.example listing all required credentials
        # Based on tech stack (DB_URL, API keys, etc.)
        # No real values — just keys with placeholder descriptions
        pass

    async def _validate_docker_build(self,
                                      output_dir: str) -> bool:
        # QA_Agent builds Docker image inside Sandbox
        # Returns True if build succeeds, False if fails
        # On failure: create remediation task
        pass
```

### DeploymentPackage schema

```python
class DeploymentPackage(BaseModel):
    project_id: str
    output_dir: str             # absolute path on disk
    files_written: list[str]    # relative paths of all files
    dockerfile_path: str
    docker_compose_path: str
    env_example_path: str
    readme_path: str
    summary_report_path: str
    release_tag: str            # "release-v1"
    git_log: list[GitCommit]
    rollback_points: list[RollbackPoint]
    docker_build_passed: bool
    assembled_at: datetime
    total_size_bytes: int
```

---

## README_GENERATOR — EXACT SPECIFICATION

### What it does

Generates a plain-language README appropriate for a non-technical
user. No jargon. Clear setup instructions.

### ReadmeGenerator class — `delivery/readme_generator.py`

```python
class ReadmeGenerator:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def generate(self,
                        project_name: str,
                        project_brief: str,
                        tech_stack: TechStackDocument,
                        deployment_package: DeploymentPackage
                        ) -> str:
        # LLM call — MEDIUM complexity
        # Generate README with sections:
        #   - What this project does (plain language)
        #   - Requirements (Docker Desktop)
        #   - Setup (3 steps maximum)
        #   - How to run
        #   - How to stop
        #   - Environment variables (from .env.example)
        # Plain language throughout
        # Maximum 3 steps in setup
        # No technical jargon
        pass
```

### README structure (enforced)

```markdown
# {project_name}

{one-sentence plain-language description}

## What You Need
- Docker Desktop (download at docker.com)

## Setup
1. Copy `.env.example` to `.env` and fill in your values
2. Run: `docker compose up`
3. Open: http://localhost:3000

## Stopping
Run: `docker compose down`

## Environment Variables
{table from .env.example with descriptions}

## Built by ForgeAI
```

---

## FINAL SUMMARY REPORT — EXACT SPECIFICATION

### What it is

A human-readable document included in the Deployment_Package.
Tells the user what was built, how long it took, and what
lessons the system learned.

### FinalSummaryReport schema

```python
class FinalSummaryReport(BaseModel):
    project_id: str
    project_name: str
    project_brief: str
    total_tasks_completed: int
    total_qa_cycles: int
    total_cost_usd: float
    total_duration_minutes: float
    tasks_by_phase: dict            # FRONTEND_PHASE: N, BACKEND_PHASE: N
    escalations_total: int
    escalations_resolved_automatically: int
    escalations_requiring_human: int
    lessons_accumulated: int        # total lessons in Agent_Memory
    rollback_points: list[str]      # tag names
    release_tag: str
    generated_at: datetime
```

### FinalSummaryReport plain-language format

```
ForgeAI Project Summary
=======================
Project: {project_name}
Delivered: {date}

What was built:
{brief in plain language}

What was completed:
  {N} pages and components built
  {N} API endpoints implemented
  All code tested and verified

How it went:
  {N} tasks completed
  {N} quality issues caught and fixed automatically
  {N} issues required your input
  Total time: ~{N} minutes
  Estimated API cost: ~${N}

Knowledge gained:
  {N} lessons written for future projects

Delivery:
  Git tag: {release_tag}
  To run: docker compose up
```

---

## LEAD_AGENT DELIVERY METHOD

Add to LeadAgent:

```python
async def deliver_project(self,
                           project_id: str,
                           output_dir: str,
                           human_approval_callback) -> DeploymentPackage:
    # 1. Run FinalReviewer — check all outputs vs Master_Document
    # 2. If gaps: create remediation tasks, re-run affected work
    # 3. When FinalReviewer passes:
    #    a. PackageAssembler.assemble()
    #    b. ReadmeGenerator.generate()
    #    c. FinalSummaryReport generated
    #    d. Git tag release-v1
    #    e. Present to human via Human_Interface
    #    f. ProjectRegistry.set_live() on human approval
    # 4. Return DeploymentPackage
    pass
```

---

## OUTPUT DIRECTORY

All generated files go to:

```
H:/forgeai-output/{project_id}/
├── src/
│   ├── pages/
│   │   ├── Dashboard.jsx
│   │   ├── History.jsx
│   │   └── Settings.jsx
│   ├── components/
│   │   ├── NavBar.jsx
│   │   ├── Footer.jsx
│   │   └── TaskCard.jsx
│   └── api/
│       ├── tasks.py
│       ├── auth.py
│       └── health.py
├── tests/
│   └── (test files)
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── README.md
└── SUMMARY.md
```

---

## UPDATED main.py

Add Run 17. Keep all existing runs.

### Run 17 — Full delivery

Steps:

1. FinalReviewer runs holistic check
2. PackageAssembler assembles all DONE task outputs
3. Files written to output directory
4. Dockerfile generated
5. docker-compose.yml generated
6. .env.example generated
7. README.md generated
8. SUMMARY.md generated
9. Docker build validated in Sandbox
10. Git commits for each file
11. release-v1 tag created
12. Human confirmation — auto-approve
13. Project set to LIVE

### Expected terminal output

```
=== RUN 17: DEPLOYMENT PACKAGE ===

[DELIVERY] Running final review...
[FINAL REVIEW] X tasks checked — gaps found / no gaps ✓

[DELIVERY] Assembling Deployment_Package...
[DELIVERY] Writing src/pages/Dashboard.jsx...
[DELIVERY] Writing src/pages/History.jsx...
[DELIVERY] Writing src/pages/Settings.jsx...
[DELIVERY] Writing src/components/NavBar.jsx...
[DELIVERY] Writing src/api/tasks.py...
[DELIVERY] Writing src/api/health.py...
... (all files)

[DELIVERY] Generating Dockerfile...
[DELIVERY] Generating docker-compose.yml...
[DELIVERY] Generating .env.example...
[DELIVERY] Generating README.md...
[DELIVERY] Generating SUMMARY.md...

[GIT] Committing task files...
[GIT] 12 files committed
[GIT] Tag created: release-v1

[DELIVERY] Validating Docker build...
[SANDBOX] Building Docker image...
[SANDBOX] Build: success ✓  (or: failed — remediation task created)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 DELIVERY READY

 Your project is packaged and ready to deploy.

 Location: H:/forgeai-output/{project_id}/
 Git tag: release-v1
 Docker build: verified ✓

 To deploy:
   cd {project_id}
   cp .env.example .env
   docker compose up

 Approve delivery →
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[DELIVERY] Human approved
[REGISTRY] Project: ACTIVE → LIVE
[DELIVERY] Package complete

--- FINAL SUMMARY ---
Project: Personal Task Manager
Tasks completed: X
QA cycles: X
Cost: ~$X.XX
Lessons accumulated: X
Release: release-v1
```

---

## TESTS

### test_package_assembler.py

Mock LLMClient, GitManager, QAAgent. No real Docker calls.

- Test assemble() creates output directory
- Test assemble() writes files for all DONE tasks
- Test _derive_file_path() returns correct path for frontend task
- Test _derive_file_path() returns correct path for backend task
- Test _generate_dockerfile() calls LLM with MEDIUM complexity
- Test _generate_docker_compose() calls LLM with LOW complexity
- Test _generate_env_example() returns non-empty string
- Test _validate_docker_build() called during assembly
- Test DeploymentPackage.files_written list is non-empty
- Test release-v1 tag created after assembly
- Test docker_build_passed reflected in DeploymentPackage

### test_git_manager.py

Use a real temporary directory for Git operations.
No mocking — these are real Git commands.

- Test init_repo() creates a .git directory
- Test commit() creates a real Git commit
- Test commit() returns GitCommit with valid hash
- Test create_tag() creates a real Git tag
- Test get_log() returns commits in reverse chronological order
- Test get_tags() returns created tags
- Test rollback_to_tag() restores repository state

### test_readme_generator.py

Mock LLMClient. No real API calls.

- Test generate() calls LLM with MEDIUM complexity
- Test generated README contains project name
- Test generated README contains "docker compose up"
- Test generated README contains no jargon
(no: "agent", "LLM", "PostgreSQL", "Chroma", "artefact")
- Test generated README has maximum 3 setup steps

---

## REQUIREMENTS BEING IMPLEMENTED

- **Req 25** — Deployment Packaging
  - All source code assembled ✓
  - Dockerfile generated ✓
  - .env.example generated ✓
  - Plain-language README ✓
  - Docker build validated by QA_Agent ✓
  - Final Git tag release-v1 ✓
- **Req 19** — Version Control Integration
  - Each task corresponds to one Git commit ✓
  - Rollback_Points tagged at milestones ✓
  - Git log accessible in summary report ✓
- **Req 13** — Final Project Review (completed)
  - Holistic review runs before assembly ✓
  - FinalSummaryReport generated ✓
- **BUILD_NOTES fixes**
  - Task titles in reports fixed ✓
  - Pre-pull python:3.11-slim added ✓
  - Cost estimates calibrated ✓

---

## CODE QUALITY RULES

- All LLM calls in tests must be mocked — no real API calls
- test_git_manager.py uses real temporary directories and
real Git commands — no mocking
- README must pass no-jargon test (same standard as
Phase_Completion_Report)
- PackageAssembler must log every file written:
INFO: "Writing {file_path} ({bytes} bytes)"
- GitManager must log every commit and tag:
INFO: "Committed {hash}: {message}"
INFO: "Tagged {tag_name}"
- All file paths must use pathlib.Path — no string concatenation
- output_dir defaults to:  
H:/forgeai-output/{project_id}/

---

## WHAT SUCCESS LOOKS LIKE

```bash
python main.py
pytest tests/ -v
ls /home/claude/forgeai-output/
```

- Run 17 shows files being written to disk
- Output directory exists with correct structure
- README.md contains plain-language instructions
- Git repository has commits and release-v1 tag
- Docker build validation runs (pass or fail with remediation)
- All 269 existing tests still pass
- New tests pass (git tests use real Git)
- Target: 295+ total tests passing

That is Phase 10 complete.