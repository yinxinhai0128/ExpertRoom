from __future__ import annotations

import shutil
import subprocess
from typing import Any

from app.adapters.base import AgentResult, AgentRuntimeError, BaseAdapter


class HermesAdapter(BaseAdapter):
    def run(self, prompt: str) -> AgentResult:
        command = self.config.get("command", "hermes.exe")
        model = self.config.get("model", "")
        timeout = int(self.config.get("timeout", 120))

        cmd_path = shutil.which(command)
        if not cmd_path:
            raise AgentRuntimeError(f"hermes command not found: {command}")

        # hermes flags: -z PROMPT, optionally -m MODEL
        cmd = [command, "-z", prompt]
        if model:
            cmd += ["-m", model]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentRuntimeError(f"hermes timed out after {timeout}s") from exc
        except Exception as exc:
            raise AgentRuntimeError(f"hermes error: {exc}") from exc

        raw = proc.stdout or ""
        if proc.returncode != 0 and not raw.strip():
            raise AgentRuntimeError(f"hermes exited {proc.returncode}: {(proc.stderr or '')[:300]}")

        return AgentResult(text=raw.strip(), raw=raw)
