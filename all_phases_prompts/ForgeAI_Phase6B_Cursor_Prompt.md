# ForgeAI — Phase 6B Cursor Prompt
# Paste this entire document into Cursor as your starting instruction

---

## CONTEXT

This is Phase 6B of the ForgeAI build plan — a focused extension
of Phase 6 that replaces the pytest placeholder for frontend tasks
with real browser-based Playwright testing.

Phase 6 is complete (133 tests passing). It delivered:
- Agent_Bootstrap_Protocol
- Navigation_Contract negotiation
- Component_Registry
- Root layout dependency enforcement
- Frontend_Agent producing real React code
- pytest Sandbox as placeholder for frontend QA

Phase 6B replaces the placeholder with real browser testing.

Do NOT modify any existing Phase 1-6 code unless a specific
instruction below requires it. Build on top of what exists.

---

## BUILD NOTES FROM PREVIOUS PHASES

These decisions must be respected:

1. All new agents attempt LOW tier first, fall back to MEDIUM on
   schema validation failure.
2. All agents use defensive normalisation before Pydantic validation.
3. Large structured documents: first attempt 16384, retry 32768.
4. Sandbox containers always destroyed after execution — success or fail.
5. network_mode="none" is mandatory on every container.
6. Layout specification has a deterministic fallback when LLM
   output fails schema validation.

---

## WHAT PHASE 6B BUILDS

Three things:

1. **Frontend Sandbox image** — a custom Docker image with Node.js,
   React, Tailwind CSS, Vite, and Playwright pre-installed. Avoids
   npm install on every test run.

2. **Playwright Test_Runner** — executes Playwright tests against
   a running React dev server inside the container. Returns
   RunnerOutput with the same schema as the existing pytest runner.

3. **QA_Agent frontend mode** — QA_Agent detects when a task is a
   FRONTEND task and automatically routes to Playwright mode instead
   of pytest mode. Generates Playwright tests from the layout spec.

---

## TECH STACK ADDITIONS — PHASE 6B ONLY

No new Python packages required.

New Docker image built locally — not pulled from a registry.

---

## NEW PROJECT STRUCTURE

Add these files. Do not remove any existing files:

```
forgeai/
├── sandbox/
│   ├── frontend_sandbox.py   # NEW — Playwright-based frontend runner
│   └── ...existing files...
├── docker/
│   ├── frontend-sandbox/
│   │   ├── Dockerfile        # Custom image with Node + Playwright
│   │   ├── package.json      # Base React + Tailwind + Vite + Playwright
│   │   └── vite.config.js    # Vite config for the sandbox project
│   └── ...
└── ...existing files...

tests/
├── test_frontend_sandbox.py  # Playwright execution tests
├── test_qa_frontend_mode.py  # QA routing and test generation tests
└── ...existing files...
```

---

## FRONTEND SANDBOX DOCKER IMAGE

### Dockerfile — `docker/frontend-sandbox/Dockerfile`

```dockerfile
FROM node:20-slim

# Install Playwright system dependencies
RUN apt-get update && apt-get install -y \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libgbm1 \
    libasound2 \
    libxrandr2 \
    libxfixes3 \
    libxcomposite1 \
    libxdamage1 \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /sandbox

# Copy base package.json with all dependencies pre-declared
COPY package.json .

# Pre-install all dependencies including Playwright browsers
RUN npm install
RUN npx playwright install chromium --with-deps

# Sandbox working directory for injected code
RUN mkdir -p /sandbox/src /sandbox/tests

EXPOSE 5173
```

### package.json — `docker/frontend-sandbox/package.json`

```json
{
  "name": "forgeai-frontend-sandbox",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "dev": "vite --host 0.0.0.0 --port 5173",
    "build": "vite build",
    "test:e2e": "playwright test --reporter=json"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.22.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.2.0",
    "@playwright/test": "^1.42.0",
    "autoprefixer": "^10.4.17",
    "postcss": "^8.4.35",
    "tailwindcss": "^3.4.1",
    "vite": "^5.1.0"
  }
}
```

### vite.config.js — `docker/frontend-sandbox/vite.config.js`

```javascript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173
  }
})
```

### Build the image

Add a build step to the project. Create `scripts/build_frontend_sandbox.sh`:

```bash
#!/bin/bash
docker build -t forgeai-frontend-sandbox:latest \
  ./docker/frontend-sandbox/
echo "Frontend sandbox image built successfully"
```

And `scripts/build_frontend_sandbox.ps1` for Windows:

```powershell
docker build -t forgeai-frontend-sandbox:latest `
  ./docker/frontend-sandbox/
Write-Host "Frontend sandbox image built successfully"
```

---

## FRONTEND_SANDBOX — EXACT SPECIFICATION

### What it does

Provisions a container from the pre-built frontend-sandbox image.
Writes React component code and Playwright tests into the container.
Starts the Vite dev server.
Runs Playwright tests against the running server.
Captures structured results.
Destroys the container.

### FrontendSandbox class — `sandbox/frontend_sandbox.py`

```python
class FrontendSandbox:
    def __init__(self, config: SandboxConfig):
        self.config = config
        self.image = "forgeai-frontend-sandbox:latest"

    async def run(self, component_code: str,
                  test_code: str,
                  entry_point: str = "App") -> RunnerOutput:
        # 1. Provision container from forgeai-frontend-sandbox:latest
        #    - network_mode="none" EXCEPT: allow localhost only
        #      (Playwright needs to connect to Vite server on same
        #      container — use host networking within container only)
        #    - mem_limit: 1g (React + Node needs more than Python)
        #    - NO external network egress
        # 2. Write component_code to /sandbox/src/App.jsx
        # 3. Write a minimal index.html and main.jsx entry point
        # 4. Write test_code to /sandbox/tests/app.spec.js
        # 5. Write playwright.config.js with baseURL: localhost:5173
        # 6. Start Vite dev server in background:
        #    container.exec_run("npm run dev &", detach=True)
        # 7. Wait for server to be ready (poll /sandbox health check)
        # 8. Run Playwright tests:
        #    container.exec_run("npx playwright test --reporter=json")
        # 9. Parse JSON output into RunnerOutput
        # 10. Destroy container unconditionally
        # 11. Return RunnerOutput
        pass

    async def _wait_for_server(self, container,
                                timeout: int = 30) -> bool:
        # Poll until Vite server responds on port 5173
        # Return True if ready, False if timeout exceeded
        # Use container.exec_run to check with wget or curl
        pass

    async def _write_entry_files(self, container) -> None:
        # Write minimal index.html and main.jsx so React mounts
        pass

    async def _write_playwright_config(self,
                                        container) -> None:
        # Write playwright.config.js with:
        # baseURL: 'http://localhost:5173'
        # testDir: './tests'
        # timeout: 10000
        pass

    def _parse_playwright_output(self,
                                  json_output: str,
                                  execution_time: float
                                  ) -> RunnerOutput:
        # Parse Playwright JSON reporter output
        # Map to RunnerOutput schema (same as pytest runner)
        # Playwright JSON format:
        # {
        #   "suites": [{
        #     "specs": [{
        #       "title": "test name",
        #       "ok": true/false,
        #       "tests": [{"status": "passed"/"failed"}]
        #     }]
        #   }],
        #   "stats": {
        #     "expected": N,
        #     "unexpected": N,
        #     "duration": N
        #   }
        # }
        pass
```

### Network isolation for frontend sandbox

Frontend tests require the Playwright browser to connect to the
Vite dev server — both running inside the same container.
This is localhost-only traffic, not external network access.

Use Docker's `--network none` but override with a custom network
that allows only loopback (127.0.0.1) traffic within the container.

Practical implementation: use `network_mode="host"` on Linux or
a bridge network with no external routing on Windows/Docker Desktop.
The key constraint: NO connections to external internet. Only
localhost traffic within the container is permitted.

Add to .env.example:
```
FRONTEND_SANDBOX_NETWORK=bridge
```

---

## QA_AGENT FRONTEND MODE

### Task type detection

QA_Agent determines execution mode from the task's Development_Phase:

```python
async def review(self, task_id: UUID,
                 code: str,
                 test_code: str,
                 development_phase: str = "BACKEND_PHASE"
                 ) -> RunnerOutput:
    if development_phase == "FRONTEND_PHASE":
        return await self._run_playwright(code, test_code)
    else:
        return await self._run_pytest(code, test_code)
```

### Playwright test generation

When QA_Agent reviews a frontend task, it generates Playwright
tests from the PageSpec (layout specification for that page).

```python
async def generate_playwright_tests(
        self,
        page_spec: PageSpec,
        navigation_contract: NavigationContract) -> str:
    # LLM call — LOW complexity
    # Input: page_spec (sections, interactions, acceptance_criteria)
    #        navigation_contract (routes, linking convention)
    # Output: complete Playwright test file as string
    # Tests must cover:
    #   - Page renders without errors
    #   - All sections defined in page_spec are visible
    #   - All interactions defined in page_spec work correctly
    #   - Navigation links go to correct routes per NavigationContract
    pass
```

### Playwright test template

QA_Agent uses this structure for generated tests:

```javascript
// Generated by QA_Agent for: {page_name}
// Route: {route}
// Spec: {acceptance_criteria}

import { test, expect } from '@playwright/test';

test.describe('{page_name}', () => {

  test('page renders without errors', async ({ page }) => {
    await page.goto('{route}');
    await expect(page).not.toHaveTitle(/error/i);
  });

  // One test per section in page_spec
  test('{section_name} is visible', async ({ page }) => {
    await page.goto('{route}');
    await expect(page.locator('{selector}')).toBeVisible();
  });

  // One test per interaction in page_spec
  test('{interaction_name}', async ({ page }) => {
    await page.goto('{route}');
    // interaction steps
  });

});
```

---

## UPDATED main.py

Add Run 6 to the existing main.py — do not replace existing runs.

### Run 6 — Frontend QA with Playwright

```python
# Simulates what happens when Frontend_Agent #1's
# root layout task goes through real Playwright QA

# Use the AppLayout code from Run 4's output
# QA_Agent generates Playwright tests from the layout spec
# Frontend Sandbox provisions, runs tests, destroys
```

Steps:
1. QA_Agent generates Playwright tests for the Dashboard page
   using the PageSpec from Run 2's layout specification
2. FrontendSandbox provisions a container from
   forgeai-frontend-sandbox:latest
3. React code (from Run 4) is written into the container
4. Vite dev server starts
5. Playwright tests execute
6. Results printed

### Expected terminal output

```
=== RUN 6: PLAYWRIGHT FRONTEND QA ===
[QA] Generating Playwright tests for Dashboard page...
[QA] Tests generated — 4 test cases
[FRONTEND SANDBOX] Provisioning container...
[FRONTEND SANDBOX] Writing React component...
[FRONTEND SANDBOX] Starting Vite dev server...
[FRONTEND SANDBOX] Server ready on port 5173
[FRONTEND SANDBOX] Running Playwright tests...
[FRONTEND SANDBOX] Destroying container...

--- PLAYWRIGHT RESULTS ---
Success: True (or False with details)
Total: 4 | Passed: X | Failed: X
  ✓/✗ page renders without errors
  ✓/✗ task list section is visible
  ✓/✗ add task form is visible
  ✓/✗ navigation links work correctly
```

---

## TESTS

### test_frontend_sandbox.py

Mock Docker client. Never spin up real containers in tests.

- Test FrontendSandbox uses forgeai-frontend-sandbox image
- Test container is destroyed after successful run
- Test container is destroyed after failed run
- Test _wait_for_server returns True when server responds
- Test _wait_for_server returns False on timeout
- Test _parse_playwright_output correctly maps passed tests
- Test _parse_playwright_output correctly maps failed tests
- Test RunnerOutput schema matches for both pytest and playwright

### test_qa_frontend_mode.py

Mock LLMClient and FrontendSandbox. No real API or Docker calls.

- Test review() routes to pytest when phase is BACKEND_PHASE
- Test review() routes to playwright when phase is FRONTEND_PHASE
- Test generate_playwright_tests() calls LLM with LOW complexity
- Test generated test string contains page route
- Test generated test string contains at least one test case
- Test QA_Agent uses PageSpec sections in generated tests

---

## BUILD INSTRUCTIONS

Before running main.py for Phase 6B, build the frontend sandbox image:

**Windows (PowerShell):**
```powershell
./scripts/build_frontend_sandbox.ps1
```

**Verify the image exists:**
```bash
docker images | grep forgeai-frontend-sandbox
```

You should see:
```
forgeai-frontend-sandbox   latest   <image-id>   <size>
```

The image will be roughly 1-2GB because it includes Node.js,
all npm dependencies, and Playwright with Chromium.
This is expected. The size is the tradeoff for fast test execution.

---

## IMPORTANT NOTES FOR WINDOWS + DOCKER DESKTOP

Docker Desktop on Windows runs Linux containers in a VM.
Playwright inside the container connects to localhost:5173
which is within the same container — this works correctly.

The container does NOT need external internet access.
All npm packages are pre-installed in the image.
Playwright browsers are pre-installed in the image.

If container startup is slow (>60 seconds), increase
SANDBOX_TIMEOUT_LOW in .env from 60 to 120.

---

## REQUIREMENTS BEING IMPLEMENTED

- **Req 18** — Code Execution Sandbox
  - Frontend tasks now use browser-based execution ✓
  - Same RunnerOutput schema regardless of execution mode ✓
- **Req 06** — Task Review and Quality Gate
  - QA_Agent executes real browser tests for frontend tasks ✓
  - No self-approval rule still enforced ✓
- **Req 22** — UI/UX Mockup Ingestion
  - PageSpec drives Playwright test generation ✓
  - Acceptance criteria become test assertions ✓

---

## CODE QUALITY RULES

- FrontendSandbox must log the same lifecycle events as Sandbox:
  INFO: Provisioning, Executing, Destroying
- Container name format: forgeai-frontend-sandbox-{uuid4()}
- Playwright test generation prompt must be a module-level constant
- All container operations must handle Windows path separators
  correctly when writing files into Linux containers via tar streams
- FrontendSandbox and Sandbox must both implement the same
  async run(code, test_code) → RunnerOutput interface so
  QA_Agent can use either interchangeably

---

## WHAT SUCCESS LOOKS LIKE

```bash
# First, build the frontend sandbox image
./scripts/build_frontend_sandbox.ps1

# Then verify
docker images | grep forgeai-frontend-sandbox

# Then run
python main.py
pytest tests/ -v
```

- Run 6 shows Playwright tests executing against a real
  React component in a real container
- All 133 existing tests still pass
- New Playwright and QA routing tests pass
- Target: 150+ total tests passing
- docker ps shows only the four ForgeAI infrastructure
  containers after completion — no leftover sandbox containers

That is Phase 6B complete.
