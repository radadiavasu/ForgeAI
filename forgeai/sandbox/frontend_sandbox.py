"""Docker-backed frontend sandbox: Vite + Playwright in one container."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import tarfile
import time
from uuid import uuid4

import docker
from docker.errors import DockerException

from forgeai.config import get_settings
from forgeai.exceptions import SandboxProvisionError, SandboxTimeoutError
from forgeai.sandbox.sandbox import SandboxConfig
from forgeai.sandbox.schemas import RunnerOutput, SandboxTestCaseResult

logger = logging.getLogger(__name__)

_VITE_READY_CMD = [
    "node",
    "-e",
    "require('http').get('http://127.0.0.1:5173/', (r) => process.exit(r.statusCode && r.statusCode < 500 ? 0 : 1)).on('error', () => process.exit(1))",
]


class FrontendSandbox:
    """Ephemeral Docker sandbox for React + Playwright against a local Vite server."""

    def __init__(self, complexity: str, config: SandboxConfig) -> None:
        self.complexity = complexity.upper().strip()
        self.config = config
        settings = get_settings()
        self.image = settings.frontend_sandbox_image
        self.network_mode = settings.frontend_sandbox_network
        self.mem_limit = settings.frontend_sandbox_memory_limit
        self._client = docker.from_env()

    async def run(
        self,
        component_code: str,
        test_code: str,
        entry_point: str = "App",
        component_registry: dict[str, str] | None = None,
    ) -> RunnerOutput:
        """Run Playwright tests against ``component_code`` mounted as the Vite app."""
        _ = entry_point  # reserved for future multi-entry wiring
        container = None
        start = time.perf_counter()
        timeout = self._timeout_for_complexity()
        settings = get_settings()

        try:
            name = f"forgeai-frontend-sandbox-{uuid4()}"
            nano_cpus = int(self.config.cpu_limit * 1_000_000_000)
            logger.info("[FRONTEND SANDBOX] Provisioning container %s", name)
            container = await asyncio.to_thread(
                self._client.containers.create,
                self.image,
                name=name,
                command=["sleep", "3600"],
                network_mode=self.network_mode,
                mem_limit=self.mem_limit,
                nano_cpus=nano_cpus,
                working_dir="/sandbox",
                auto_remove=False,
                detach=True,
            )
            await asyncio.to_thread(container.start)

            await self._write_file(container, "/sandbox/src/App.jsx", component_code)
            if component_registry:
                for name, code in component_registry.items():
                    await self._write_file(
                        container,
                        f"/sandbox/src/{name}.jsx",
                        code,
                    )
            logger.info("[FRONTEND SANDBOX] Writing React component...")
            await self._write_entry_files(container)
            await self._write_file(container, "/sandbox/tests/app.spec.js", test_code)
            await self._write_playwright_config(container)

            logger.info("[FRONTEND SANDBOX] Starting Vite dev server...")
            await asyncio.to_thread(
                container.exec_run,
                ["sh", "-c", "cd /sandbox && nohup npm run dev > /tmp/vite.log 2>&1 &"],
                detach=True,
            )

            ready = await self._wait_for_server(container, timeout=min(30, settings.sandbox_timeout_low))
            if not ready:
                elapsed = time.perf_counter() - start
                return RunnerOutput(
                    success=False,
                    total_tests=0,
                    passed_tests=0,
                    failed_tests=0,
                    test_cases=[],
                    stdout="",
                    stderr="",
                    execution_time_seconds=elapsed,
                    timed_out=True,
                    sandbox_error="Vite dev server did not become ready in time",
                )
            logger.info("[FRONTEND SANDBOX] Server ready on port 5173")

            logger.info("[FRONTEND SANDBOX] Running Playwright tests...")
            exec_start = time.perf_counter()
            try:
                exec_result = await asyncio.wait_for(
                    asyncio.to_thread(
                        container.exec_run,
                        ["sh", "-c", "cd /sandbox && npx playwright test --reporter=json"],
                        workdir="/sandbox",
                        demux=True,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError as exc:
                elapsed = time.perf_counter() - start
                logger.warning("Frontend sandbox Playwright timed out after %ss", timeout)
                raise SandboxTimeoutError("Frontend sandbox Playwright execution timed out") from exc

            stdout_b, stderr_b = exec_result.output if exec_result.output else (b"", b"")
            stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            exec_elapsed = time.perf_counter() - exec_start

            combined = stdout.strip()
            if not combined and stderr.strip():
                combined = stderr.strip()

            return self._parse_playwright_output(
                combined,
                execution_time=exec_elapsed,
                stderr=stderr,
                exit_code=exec_result.exit_code,
            )
        except asyncio.TimeoutError:
            raise
        except SandboxTimeoutError:
            raise
        except (DockerException, OSError) as exc:
            logger.error("Frontend sandbox provision failed: %s", exc)
            if container is not None:
                await self._destroy(container)
            raise SandboxProvisionError(str(exc)) from exc
        finally:
            if container is not None:
                await self._destroy(container)

    async def _wait_for_server(self, container, timeout: int = 30) -> bool:
        """Poll until Vite responds on port 5173."""
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            exec_result = await asyncio.to_thread(
                container.exec_run,
                _VITE_READY_CMD,
                workdir="/sandbox",
            )
            if exec_result.exit_code == 0:
                return True
            await asyncio.sleep(0.4)
        return False

    async def _write_entry_files(self, container) -> None:
        """Write minimal ``index.html``, ``main.jsx``, and Tailwind entry CSS."""
        index_html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ForgeAI Sandbox</title>
</head>
<body>
  <div id="root"></div>
  <script type="module" src="/src/main.jsx"></script>
</body>
</html>
"""
        main_jsx = """import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import App from './App.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/*" element={<App />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
"""
        index_css = """@tailwind base;
@tailwind components;
@tailwind utilities;
"""
        await self._write_file(container, "/sandbox/index.html", index_html)
        await self._write_file(container, "/sandbox/src/main.jsx", main_jsx)
        await self._write_file(container, "/sandbox/src/index.css", index_css)

    async def _write_playwright_config(self, container) -> None:
        """Write Playwright config with local base URL and test directory."""
        cfg = """import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 10000,
  use: {
    baseURL: 'http://localhost:5173',
    ...devices['Desktop Chrome'],
  },
});
"""
        await self._write_file(container, "/sandbox/playwright.config.js", cfg)

    def _parse_playwright_output(
        self,
        json_output: str,
        execution_time: float,
        stderr: str = "",
        exit_code: int | None = None,
    ) -> RunnerOutput:
        """Parse Playwright JSON reporter output into ``RunnerOutput``."""
        raw = json_output.strip()
        if not raw:
            return RunnerOutput(
                success=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                test_cases=[],
                stdout=json_output,
                stderr=stderr,
                execution_time_seconds=execution_time,
                timed_out=False,
                sandbox_error="Empty Playwright reporter output",
            )

        payload = self._extract_json_report(raw)
        if payload is None:
            return RunnerOutput(
                success=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                test_cases=[],
                stdout=json_output,
                stderr=stderr,
                execution_time_seconds=execution_time,
                timed_out=False,
                sandbox_error="Could not parse Playwright JSON output",
            )

        test_cases = self._collect_playwright_tests(payload)
        if not test_cases:
            stats = payload.get("stats") or {}
            expected = int(stats.get("expected") or 0)
            unexpected = int(stats.get("unexpected") or 0)
            if expected == 0 and unexpected == 0:
                return RunnerOutput(
                    success=(exit_code == 0),
                    total_tests=0,
                    passed_tests=0,
                    failed_tests=0,
                    test_cases=[],
                    stdout=json_output,
                    stderr=stderr,
                    execution_time_seconds=execution_time,
                    timed_out=False,
                    sandbox_error="No test cases found in Playwright JSON report",
                )

        passed_tests = sum(1 for c in test_cases if c.passed)
        total_tests = len(test_cases)
        failed_tests = total_tests - passed_tests
        success = failed_tests == 0

        return RunnerOutput(
            success=success,
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            test_cases=test_cases,
            stdout=json_output,
            stderr=stderr,
            execution_time_seconds=execution_time,
            timed_out=False,
            sandbox_error="",
        )

    def _extract_json_report(self, raw: str) -> dict | None:
        """Extract a JSON object from reporter output (handles stray log lines)."""
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    def _collect_playwright_tests(self, payload: dict) -> list[SandboxTestCaseResult]:
        """Flatten nested Playwright JSON suites into ``SandboxTestCaseResult`` rows."""
        cases: list[SandboxTestCaseResult] = []

        def walk_suites(suites: list, suite_prefix: str) -> None:
            for suite in suites or []:
                stitle = str(suite.get("title") or "").strip()
                prefix = f"{suite_prefix} :: {stitle}" if suite_prefix and stitle else (stitle or suite_prefix)
                for spec in suite.get("specs") or []:
                    spec_title = str(spec.get("title") or "spec").strip()
                    for test in spec.get("tests") or []:
                        test_title = str(test.get("title") or "test").strip()
                        name = " :: ".join(p for p in (prefix, spec_title, test_title) if p)
                        status = "failed"
                        err = ""
                        for res in test.get("results") or []:
                            status = str(res.get("status") or "failed")
                            err_parts: list[str] = []
                            if isinstance(res.get("error"), dict):
                                ep = res["error"]
                                msg = ep.get("message") or ep.get("stack")
                                if isinstance(msg, str) and msg.strip():
                                    err_parts.append(msg.strip()[:2000])
                            if isinstance(res.get("errors"), list):
                                for e in res["errors"]:
                                    if isinstance(e, dict) and isinstance(e.get("message"), str):
                                        err_parts.append(e["message"].strip()[:2000])
                            err = "\n".join(err_parts)[:4000]
                            break
                        passed = status == "passed"
                        cases.append(
                            SandboxTestCaseResult(name=name or "anonymous", passed=passed, stdout="", error=err)
                        )
                walk_suites(suite.get("suites") or [], prefix or suite_prefix)

        walk_suites(payload.get("suites") or [], "")
        return cases

    async def _write_file(self, container, path: str, content: str) -> None:
        """Write UTF-8 text into the container via a tar stream (Linux paths)."""
        data = content.encode("utf-8")
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as tar:
            info = tarfile.TarInfo(name=path.rsplit("/", maxsplit=1)[-1])
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        stream.seek(0)
        parent = path.rsplit("/", maxsplit=1)[0]
        await asyncio.to_thread(container.put_archive, parent, stream.read())

    async def _destroy(self, container) -> None:
        """Stop and remove the container unconditionally."""
        try:
            logger.info("[FRONTEND SANDBOX] Destroying container...")
            await asyncio.to_thread(container.stop, timeout=0)
        except Exception:
            pass
        try:
            await asyncio.to_thread(container.remove, force=True)
        except Exception:
            pass

    def _timeout_for_complexity(self) -> int:
        mapping = {
            "LOW": self.config.timeout_low,
            "MEDIUM": self.config.timeout_medium,
            "HIGH": self.config.timeout_high,
        }
        return mapping.get(self.complexity, self.config.timeout_medium)
