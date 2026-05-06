"""Docker-backed sandbox for isolated test execution."""

from __future__ import annotations

import asyncio
import io
import logging
import tarfile
import time
from dataclasses import dataclass
from uuid import uuid4

import docker
from docker.errors import DockerException

from forgeai.exceptions import SandboxProvisionError, SandboxTimeoutError
from forgeai.sandbox.schemas import RunnerOutput

logger = logging.getLogger(__name__)

_PYTEST_STUB = """\
import importlib
import time
import traceback


def main() -> int:
    start = time.perf_counter()
    module = importlib.import_module("test_main")
    test_funcs = [
        (name, obj)
        for name, obj in module.__dict__.items()
        if name.startswith("test_") and callable(obj)
    ]
    total = len(test_funcs)
    passed = 0
    failed = 0
    for idx, (name, func) in enumerate(test_funcs, start=1):
        try:
            func()
            passed += 1
            print(f"test_main.py::{name} PASSED [{int((idx/total)*100) if total else 100}%]")
        except Exception:
            failed += 1
            print(f"test_main.py::{name} FAILED [{int((idx/total)*100) if total else 100}%]")
            traceback.print_exc()

    elapsed = time.perf_counter() - start
    if failed:
        print(f"======================== {passed} passed, {failed} failed in {elapsed:.2f}s ========================")
        return 1
    print(f"======================== {passed} passed in {elapsed:.2f}s ========================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


@dataclass(frozen=True)
class SandboxConfig:
    """Configuration for sandbox resources and execution limits."""

    image: str
    cpu_limit: float
    memory_limit: str
    timeout_low: int
    timeout_medium: int
    timeout_high: int
    working_dir: str


class Sandbox:
    """Ephemeral Docker sandbox for executing submitted code and tests."""

    def __init__(self, complexity: str, config: SandboxConfig):
        self.complexity = complexity.upper().strip()
        self.config = config
        self._client = docker.from_env()

    async def run(self, code: str, test_code: str) -> RunnerOutput:
        """Run tests for submitted code in an isolated Docker container."""
        if self.config.image.endswith(":latest"):
            raise SandboxProvisionError("Sandbox image tag ':latest' is not allowed")

        container = None
        start = time.perf_counter()
        timeout = self._timeout_for_complexity()

        try:
            await asyncio.to_thread(self._client.images.pull, self.config.image)
            name = f"forgeai-sandbox-{uuid4()}"
            nano_cpus = int(self.config.cpu_limit * 1_000_000_000)
            logger.info("Provisioning sandbox container %s", name)
            container = await asyncio.to_thread(
                self._client.containers.create,
                self.config.image,
                name=name,
                command=["sleep", "3600"],
                network_mode="none",
                mem_limit=self.config.memory_limit,
                nano_cpus=nano_cpus,
                working_dir=self.config.working_dir,
                auto_remove=False,
                detach=True,
            )
            await asyncio.to_thread(container.start)

            await self._write_file(container, f"{self.config.working_dir}/main.py", code)
            await self._write_file(
                container, f"{self.config.working_dir}/test_main.py", test_code
            )
            await self._write_file(
                container, f"{self.config.working_dir}/pytest.py", _PYTEST_STUB
            )

            logger.info("Executing pytest in sandbox container %s", name)
            exec_result = await asyncio.wait_for(
                asyncio.to_thread(
                    container.exec_run,
                    [
                        "python",
                        "-m",
                        "pytest",
                        "test_main.py",
                        "-v",
                        "--tb=short",
                        "--no-header",
                    ],
                    workdir=self.config.working_dir,
                    demux=True,
                ),
                timeout=timeout,
            )
            stdout_b, stderr_b = exec_result.output if exec_result.output else (b"", b"")
            stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            elapsed = time.perf_counter() - start
            return RunnerOutput(
                success=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                test_cases=[],
                stdout=stdout,
                stderr=stderr,
                execution_time_seconds=elapsed,
                timed_out=False,
                sandbox_error="",
            )
        except asyncio.TimeoutError as exc:
            elapsed = time.perf_counter() - start
            logger.warning("Sandbox execution timed out after %ss", timeout)
            if container is not None:
                await self._destroy(container)
            raise SandboxTimeoutError("Sandbox execution timed out") from exc
        except (DockerException, OSError) as exc:
            logger.error("Sandbox provision failed: %s", exc)
            if container is not None:
                await self._destroy(container)
            raise SandboxProvisionError(str(exc)) from exc
        finally:
            if container is not None:
                await self._destroy(container)

    async def _write_file(self, container, path: str, content: str) -> None:
        """Write UTF-8 text file to container using tar archive stream."""
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
        """Stop and remove container unconditionally without raising."""
        try:
            logger.info("Destroying sandbox container %s", container.name)
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
