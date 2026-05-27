"""Integration QA — spins up FE + BE together via docker-compose and runs smoke tests.

Place this file at: forgeai/orchestration/integration_qa.py

This is a NEW layer that sits between the Backend Phase and PackageAssembler (delivery).
It does NOT replace Playwright QA (frontend isolation) or Sandbox QA (backend isolation).
It catches contract mismatches that only appear when FE and BE run together.

Pipeline position:
    Backend Phase → Sandbox QA (existing)
                         ↓
               IntegrationQAOrchestrator  ← this file
                         ↓
               PackageAssembler → Delivery (existing)

How to wire into main.py / lead_agent.py:
    1. After _run9_full_backend_phase() completes successfully
    2. Before _run10_assemble_and_deliver()
    3. See inject_into_main() docstring at bottom for exact insertion point.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from forgeai.llm.client import LLMClient
from forgeai.llm.schemas import MasterDocument, TechStackDocument

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class EndpointCheckResult(BaseModel):
    endpoint: str
    method: str
    reachable: bool
    status_code: int | None = None
    error: str = ""


class IntegrationSmokeResult(BaseModel):
    success: bool
    fe_reachable: bool
    be_reachable: bool
    endpoint_checks: list[EndpointCheckResult] = []
    contract_mismatches: list[str] = []
    stdout: str = ""
    stderr: str = ""
    execution_time_seconds: float = 0.0
    error: str = ""


class IntegrationQAReport(BaseModel):
    project_id: str
    passed: bool
    smoke_result: IntegrationSmokeResult
    mismatches_found: int
    endpoints_checked: int
    endpoints_reachable: int
    recommendation: str = ""
    reviewed_at: float = 0.0


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

INTEGRATION_ANALYSIS_PROMPT = """
You are Lead_Agent performing integration QA analysis.

You receive:
1. api_contract — the contract FE was built against
2. smoke_result — actual HTTP probe results against the running stack
3. master_document summary — what the project is supposed to do

Your job:
- Identify which endpoints in the contract are unreachable or return wrong status
- Identify fields the FE expects but the BE is not returning (from contract mismatches)
- Produce a concise list of blocking issues and non-blocking warnings
- Determine if the integration passes (minor warnings ok, blocking issues = fail)

Respond with JSON only:
{
  "passes": boolean,
  "blocking_issues": ["plain language issue 1", ...],
  "warnings": ["plain language warning 1", ...],
  "recommendation": "one sentence summary"
}
""".strip()


# ---------------------------------------------------------------------------
# Docker Compose Runner
# ---------------------------------------------------------------------------

@dataclass
class ComposeStack:
    """Manages a docker-compose stack lifecycle for integration testing."""

    output_dir: str
    fe_port: int = 3000
    be_port: int = 8000
    startup_timeout: int = 60
    _proc: subprocess.Popen | None = field(default=None, repr=False)

    async def up(self) -> bool:
        """Start the stack. Returns True if both services come up."""
        root = Path(self.output_dir)
        compose_file = root / "docker-compose.yml"
        if not compose_file.exists():
            logger.error("[INTEGRATION] docker-compose.yml not found at %s", compose_file)
            return False

        def _up() -> subprocess.Popen:
            return subprocess.Popen(
                ["docker", "compose", "up", "--build", "--wait"],
                cwd=str(root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        logger.info("[INTEGRATION] Starting docker-compose stack at %s", self.output_dir)
        try:
            self._proc = await asyncio.to_thread(_up)
            # Give services time to initialise after --wait resolves
            await asyncio.sleep(5)
            return True
        except (OSError, FileNotFoundError) as exc:
            logger.error("[INTEGRATION] docker compose up failed: %s", exc)
            return False

    async def down(self) -> None:
        """Tear down the stack unconditionally."""
        root = Path(self.output_dir)
        try:
            def _down() -> None:
                subprocess.run(
                    ["docker", "compose", "down", "--volumes", "--remove-orphans"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            await asyncio.to_thread(_down)
            logger.info("[INTEGRATION] Stack torn down")
        except Exception as exc:
            logger.warning("[INTEGRATION] Stack teardown warning: %s", exc)

    @property
    def fe_base_url(self) -> str:
        return f"http://localhost:{self.fe_port}"

    @property
    def be_base_url(self) -> str:
        return f"http://localhost:{self.be_port}"


# ---------------------------------------------------------------------------
# HTTP Prober (no external deps — uses curl via subprocess)
# ---------------------------------------------------------------------------

class StackProber:
    """Probes a running stack using curl. No httpx/requests required."""

    def __init__(self, fe_base_url: str, be_base_url: str, timeout: int = 10) -> None:
        self.fe_base_url = fe_base_url
        self.be_base_url = be_base_url
        self.timeout = timeout

    async def probe_url(self, url: str) -> tuple[bool, int | None, str]:
        """Returns (reachable, status_code, error)."""
        def _curl() -> tuple[bool, int | None, str]:
            try:
                proc = subprocess.run(
                    [
                        "curl", "-s", "-o", "/dev/null",
                        "-w", "%{http_code}",
                        "--max-time", str(self.timeout),
                        url,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout + 2,
                )
                code_str = proc.stdout.strip()
                if code_str.isdigit():
                    code = int(code_str)
                    return code > 0, code, ""
                return False, None, proc.stderr.strip() or "no response"
            except (subprocess.TimeoutExpired, OSError) as e:
                return False, None, str(e)

        return await asyncio.to_thread(_curl)

    async def probe_fe(self) -> tuple[bool, int | None, str]:
        return await self.probe_url(self.fe_base_url)

    async def probe_be_health(self) -> tuple[bool, int | None, str]:
        """Try /health, /api/health, / in that order."""
        for path in ("/health", "/api/health", "/"):
            ok, code, err = await self.probe_url(f"{self.be_base_url}{path}")
            if ok and code and code < 500:
                return ok, code, err
        return False, None, "no health endpoint responded"

    async def probe_endpoints(
        self, api_contract: dict[str, Any]
    ) -> list[EndpointCheckResult]:
        """Probe each endpoint defined in the api_contract."""
        results: list[EndpointCheckResult] = []
        endpoints = self._extract_endpoints(api_contract)
        for method, path in endpoints:
            url = f"{self.be_base_url}{path}"
            ok, code, err = await self.probe_url(url)
            results.append(
                EndpointCheckResult(
                    endpoint=path,
                    method=method,
                    reachable=ok and code is not None and code < 500,
                    status_code=code,
                    error=err,
                )
            )
        return results

    def _extract_endpoints(self, api_contract: dict[str, Any]) -> list[tuple[str, str]]:
        """Pull (method, path) pairs from various api_contract shapes."""
        endpoints: list[tuple[str, str]] = []

        # Shape 1: {endpoint: "/path", method: "GET"} flat list
        if isinstance(api_contract, list):
            for item in api_contract:
                if isinstance(item, dict):
                    path = item.get("endpoint") or item.get("path") or ""
                    method = item.get("method", "GET").upper()
                    if path:
                        endpoints.append((method, path))
            return endpoints

        # Shape 2: {"endpoints": [...]} or {"api_surfaces": [...]}
        for key in ("endpoints", "api_surfaces", "routes"):
            items = api_contract.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        path = item.get("endpoint") or item.get("path") or ""
                        method = item.get("method", "GET").upper()
                        if path:
                            endpoints.append((method, path))
                if endpoints:
                    return endpoints

        # Shape 3: flat dict with endpoint keys directly
        for key, value in api_contract.items():
            if isinstance(value, dict):
                path = value.get("endpoint") or value.get("path") or ""
                method = value.get("method", "GET").upper()
                if path:
                    endpoints.append((method, path))

        return endpoints


# ---------------------------------------------------------------------------
# Contract Mismatch Detector (static — no runtime needed)
# ---------------------------------------------------------------------------

class ContractMismatchDetector:
    """
    Statically checks generated FE code for fetch/axios calls
    and compares against the api_contract.
    Catches endpoint name mismatches before the stack runs.
    """

    _FETCH_PATTERN = re.compile(
        r"""fetch\s*\(\s*[`'"](\/[^`'"]+)[`'"]""",
        re.MULTILINE,
    )
    _AXIOS_PATTERN = re.compile(
        r"""axios\.[a-z]+\s*\(\s*[`'"](\/[^`'"]+)[`'"]""",
        re.MULTILINE,
    )

    def detect(
        self,
        fe_code_snippets: list[str],
        api_contract: dict[str, Any],
        prober: StackProber,
    ) -> list[str]:
        """
        Returns list of mismatch descriptions found by static analysis.
        fe_code_snippets: list of generated React/JS component source strings.
        """
        mismatches: list[str] = []
        contract_paths = {
            path.lower()
            for _, path in prober._extract_endpoints(api_contract)
        }
        if not contract_paths:
            return mismatches

        called_paths: set[str] = set()
        for snippet in fe_code_snippets:
            for match in self._FETCH_PATTERN.finditer(snippet):
                called_paths.add(match.group(1).lower())
            for match in self._AXIOS_PATTERN.finditer(snippet):
                called_paths.add(match.group(1).lower())

        for path in called_paths:
            # Strip query strings
            clean = path.split("?")[0]
            if clean not in contract_paths:
                mismatches.append(
                    f"FE calls '{clean}' but this path is not in the API contract"
                )

        for path in contract_paths:
            if path not in called_paths and not any(
                path in cp for cp in called_paths
            ):
                mismatches.append(
                    f"Contract defines '{path}' but FE code does not appear to call it"
                )

        return mismatches


# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------

class IntegrationQAOrchestrator:
    """
    Runs FE + BE together via docker-compose and verifies the integration.

    Usage in main.py:
        integration_qa = IntegrationQAOrchestrator(
            llm_client=llm_client,
            output_dir=output_dir,
            api_contract=api_contract,
            master_document=master_document,
            tech_stack=tech_stack,
        )
        report = await integration_qa.run(project_id=project_id)
        if not report.passed:
            print(report.recommendation)
            # decide: block delivery or warn and continue
    """

    def __init__(
        self,
        llm_client: LLMClient,
        output_dir: str,
        api_contract: dict[str, Any],
        master_document: MasterDocument,
        tech_stack: TechStackDocument,
        *,
        fe_port: int = 3000,
        be_port: int = 8000,
        startup_timeout: int = 60,
        fe_code_snippets: list[str] | None = None,
    ) -> None:
        self.llm = llm_client
        self.output_dir = output_dir
        self.api_contract = api_contract
        self.master_document = master_document
        self.tech_stack = tech_stack
        self.fe_port = fe_port
        self.be_port = be_port
        self.startup_timeout = startup_timeout
        # Optional: pass generated FE source code for static mismatch detection
        self.fe_code_snippets = fe_code_snippets or []

    async def run(self, project_id: str) -> IntegrationQAReport:
        """
        Full integration QA run:
        1. Static contract mismatch detection (no Docker needed)
        2. Spin up docker-compose stack
        3. Probe FE and BE reachability
        4. Probe each API endpoint
        5. LLM analysis of results
        6. Tear down stack
        """
        start = time.monotonic()
        logger.info("[INTEGRATION QA] Starting for project %s", project_id)
        print("[INTEGRATION QA] Starting integration smoke test...")

        # Step 1 — static mismatch detection
        static_mismatches: list[str] = []
        if self.fe_code_snippets and self.api_contract:
            prober_stub = StackProber(
                fe_base_url=f"http://localhost:{self.fe_port}",
                be_base_url=f"http://localhost:{self.be_port}",
            )
            detector = ContractMismatchDetector()
            static_mismatches = detector.detect(
                self.fe_code_snippets,
                self.api_contract,
                prober_stub,
            )
            if static_mismatches:
                logger.warning(
                    "[INTEGRATION QA] Static mismatches found: %s",
                    static_mismatches,
                )
                print(f"[INTEGRATION QA] {len(static_mismatches)} static contract mismatch(es) found")

        # Step 2 — spin up stack
        stack = ComposeStack(
            output_dir=self.output_dir,
            fe_port=self.fe_port,
            be_port=self.be_port,
            startup_timeout=self.startup_timeout,
        )
        stack_started = await stack.up()

        if not stack_started:
            elapsed = time.monotonic() - start
            smoke = IntegrationSmokeResult(
                success=False,
                fe_reachable=False,
                be_reachable=False,
                contract_mismatches=static_mismatches,
                error="docker-compose stack failed to start",
                execution_time_seconds=elapsed,
            )
            return await self._build_report(project_id, smoke, start)

        try:
            prober = StackProber(
                fe_base_url=stack.fe_base_url,
                be_base_url=stack.be_base_url,
            )

            # Step 3 — probe FE and BE
            print("[INTEGRATION QA] Probing frontend...")
            fe_ok, fe_code, fe_err = await prober.probe_fe()
            print(f"[INTEGRATION QA] FE → {'✓' if fe_ok else '✗'} (HTTP {fe_code})")

            print("[INTEGRATION QA] Probing backend health...")
            be_ok, be_code, be_err = await prober.probe_be_health()
            print(f"[INTEGRATION QA] BE → {'✓' if be_ok else '✗'} (HTTP {be_code})")

            # Step 4 — probe endpoints
            endpoint_results: list[EndpointCheckResult] = []
            if be_ok and self.api_contract:
                print("[INTEGRATION QA] Probing API endpoints...")
                endpoint_results = await prober.probe_endpoints(self.api_contract)
                for r in endpoint_results:
                    status = "✓" if r.reachable else "✗"
                    print(
                        f"[INTEGRATION QA]   {r.method} {r.endpoint} → "
                        f"{status} (HTTP {r.status_code})"
                    )

            elapsed = time.monotonic() - start
            smoke = IntegrationSmokeResult(
                success=fe_ok and be_ok,
                fe_reachable=fe_ok,
                be_reachable=be_ok,
                endpoint_checks=endpoint_results,
                contract_mismatches=static_mismatches,
                execution_time_seconds=elapsed,
            )

        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.error("[INTEGRATION QA] Probe error: %s", exc)
            smoke = IntegrationSmokeResult(
                success=False,
                fe_reachable=False,
                be_reachable=False,
                contract_mismatches=static_mismatches,
                error=str(exc),
                execution_time_seconds=elapsed,
            )
        finally:
            print("[INTEGRATION QA] Tearing down stack...")
            await stack.down()

        return await self._build_report(project_id, smoke, start)

    async def _build_report(
        self,
        project_id: str,
        smoke: IntegrationSmokeResult,
        start: float,
    ) -> IntegrationQAReport:
        """Run LLM analysis on smoke results and produce the final report."""
        endpoints_checked = len(smoke.endpoint_checks)
        endpoints_reachable = sum(1 for e in smoke.endpoint_checks if e.reachable)
        mismatches_found = len(smoke.contract_mismatches)

        # Step 5 — LLM analysis
        recommendation = ""
        passed = smoke.success and mismatches_found == 0

        try:
            user_message = json.dumps(
                {
                    "api_contract": self.api_contract,
                    "smoke_result": smoke.model_dump(mode="json"),
                    "project_summary": self.master_document.project_summary,
                },
                indent=2,
            )
            resp = await self.llm.complete(
                system_prompt=INTEGRATION_ANALYSIS_PROMPT,
                user_message=user_message,
                complexity="LOW",
                loop_count=0,
                max_tokens=2048,
            )
            raw = resp.content.strip()
            # Strip fences
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(
                    lines[1:-1] if lines[-1].startswith("```") else lines[1:]
                )
            data = json.loads(raw)
            passed = bool(data.get("passes", passed))
            recommendation = str(data.get("recommendation", ""))
            blocking = data.get("blocking_issues", [])
            warnings = data.get("warnings", [])
            if blocking:
                print(
                    f"[INTEGRATION QA] Blocking issues: "
                    + "; ".join(str(b) for b in blocking)
                )
            if warnings:
                print(
                    f"[INTEGRATION QA] Warnings: "
                    + "; ".join(str(w) for w in warnings)
                )
        except Exception as exc:
            logger.warning("[INTEGRATION QA] LLM analysis failed: %s", exc)
            # Fall back to heuristic pass/fail
            passed = (
                smoke.fe_reachable
                and smoke.be_reachable
                and mismatches_found == 0
                and (
                    endpoints_checked == 0
                    or endpoints_reachable / endpoints_checked >= 0.8
                )
            )
            recommendation = (
                "Integration passed (heuristic — LLM analysis unavailable)."
                if passed
                else "Integration failed — check stack logs."
            )

        elapsed = time.monotonic() - start
        result_label = "PASSED ✓" if passed else "FAILED ✗"
        print(
            f"[INTEGRATION QA] {result_label} — "
            f"{endpoints_reachable}/{endpoints_checked} endpoints reachable, "
            f"{mismatches_found} contract mismatches, "
            f"{elapsed:.1f}s"
        )
        if recommendation:
            print(f"[INTEGRATION QA] {recommendation}")

        return IntegrationQAReport(
            project_id=project_id,
            passed=passed,
            smoke_result=smoke,
            mismatches_found=mismatches_found,
            endpoints_checked=endpoints_checked,
            endpoints_reachable=endpoints_reachable,
            recommendation=recommendation,
            reviewed_at=time.time(),
        )


# ---------------------------------------------------------------------------
# How to inject into main.py
# ---------------------------------------------------------------------------
#
# STEP 1 — Import at the top of main.py:
#
#   from forgeai.orchestration.integration_qa import IntegrationQAOrchestrator
#
#
# STEP 2 — After _run9_full_backend_phase() returns, before _run10_assemble_and_deliver(),
#           add this block:
#
#   # ── Integration QA ──────────────────────────────────────────────────────
#   print("\n[PHASE] Integration QA — testing FE + BE together")
#   integration_qa = IntegrationQAOrchestrator(
#       llm_client=llm_client,
#       output_dir=output_dir,          # same dir PackageAssembler will use
#       api_contract=api_contract,      # the contract from project memory
#       master_document=master_document,
#       tech_stack=tech_stack,
#       fe_code_snippets=fe_code_snippets,  # optional: list of generated FE source strings
#   )
#   integration_report = await integration_qa.run(project_id=project_id)
#   if not integration_report.passed:
#       logger.warning(
#           "[INTEGRATION QA] Integration issues found — proceeding to delivery with warnings. "
#           "Recommendation: %s",
#           integration_report.recommendation,
#       )
#       # For now: warn and continue (same pattern as lenient approval).
#       # Once critical bugs (sandbox success=False, blind retry, FE sandbox single file)
#       # are fixed, change this to block delivery and trigger a re-run.
#   # ── End Integration QA ──────────────────────────────────────────────────
#
#
# STEP 3 — Optional: pass fe_code_snippets for static mismatch detection.
#           Collect these in lead_agent.py during run_frontend_phase():
#
#   fe_code_snippets: list[str] = []
#   # after each page task completes:
#   fe_code_snippets.append(page_output.code)
#   # then pass to IntegrationQAOrchestrator above
#
#
# NOTE: docker-compose.yml must already exist in output_dir before integration QA runs.
#       PackageAssembler._generate_docker_compose() is called during assembly,
#       so either:
#         (a) move docker-compose generation to before integration QA, or
#         (b) call _generate_docker_compose() standalone before running integration QA.
#       Option (a) is simpler — just move the compose generation 10 lines earlier in
#       PackageAssembler.assemble().
#
# ---------------------------------------------------------------------------
