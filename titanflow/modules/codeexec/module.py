"""TitanFlow Code Execution Module — sandboxed subprocess execution."""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from titanflow.modules.base import BaseModule

logger = logging.getLogger("titanflow.codeexec")

# Patterns that are never allowed
BLOCKED_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*)?r",   # rm -r, rm -rf, rm -fr
    r"\bdd\b",
    r"\bmkfs\b",
    r"\bchmod\s+777\b",
    r"\bsudo\b",
    r"\bcurl\b",
    r"\bwget\b",
    r"\bnc\b",
    r"\bncat\b",
    r"\bpython.*-c.*import\s+(socket|http|urllib|requests)",
    r">\s*/dev/sd",
    r">\s*/etc/",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bsystemctl\b",
    r"\bkill\s+-9\s+1\b",
    r"\bfork\s*bomb",
    r":\(\)\s*\{\s*:\|:\s*&\s*\}",  # fork bomb pattern
]

BLOCKED_RE = re.compile("|".join(BLOCKED_PATTERNS), re.IGNORECASE)


class CodeExecModule(BaseModule):
    """Sandboxed code/command execution for Papa only."""

    name = "codeexec"
    description = "Sandboxed code execution — /run command"

    async def start(self) -> None:
        logger.info("Code execution module started (Papa only)")

    async def stop(self) -> None:
        logger.info("Code execution module stopped")

    async def handle_telegram(
        self, command: str, args: str, context: Any
    ) -> str | None:
        if command != "run":
            return None

        if not args.strip():
            return "Usage: /run <command>\nExample: /run echo hello"

        return await self._execute(args.strip())

    async def _execute(self, code: str) -> str:
        """Execute code in a sandboxed subprocess."""
        # Check for blocked patterns
        if BLOCKED_RE.search(code):
            logger.warning(f"Blocked dangerous command: {code[:100]}")
            return "⛔ Command blocked — contains a prohibited pattern."

        timeout = self.config.modules.codeexec.timeout
        max_output = self.config.modules.codeexec.max_output

        with tempfile.TemporaryDirectory(prefix="tf_exec_") as tmpdir:
            # Build sandboxed command:
            # - unshare --net: no network access
            # - timeout: hard kill after N seconds
            # - chdir to temp dir
            # - bash -c with restricted env
            sandbox_cmd = [
                "unshare", "--net",
                "timeout", "--kill-after=5", str(timeout),
                "bash", "-c",
                f"cd {tmpdir} && {code}",
            ]

            t0 = time.monotonic()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *sandbox_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=tmpdir,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "HOME": tmpdir,
                        "TMPDIR": tmpdir,
                        "LANG": "C.UTF-8",
                    },
                )

                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout + 10
                )

                elapsed = time.monotonic() - t0
                output = stdout.decode("utf-8", errors="replace")

                # Truncate output
                if len(output) > max_output:
                    output = output[:max_output] + f"\n\n... (truncated at {max_output} chars)"

                rc = proc.returncode
                status = "✓" if rc == 0 else f"✗ exit {rc}"
                if rc == 124 or rc == 137:
                    status = "⏱ timeout"

                return (
                    f"```\n{output.rstrip()}\n```\n"
                    f"{status} · {elapsed:.1f}s"
                )

            except asyncio.TimeoutError:
                elapsed = time.monotonic() - t0
                return f"⏱ Execution timed out after {elapsed:.0f}s"
            except Exception as e:
                logger.error(f"Code exec error: {e}")
                return f"⚠ Execution error: {str(e)[:200]}"
