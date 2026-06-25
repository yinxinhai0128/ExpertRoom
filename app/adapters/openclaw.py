from __future__ import annotations

import json
import subprocess
from typing import Any

from app.adapters.base import AgentResult, AgentRuntimeError, BaseAdapter


class OpenClawAdapter(BaseAdapter):
    def run(self, prompt: str) -> AgentResult:
        model = self.config.get("model", "deepseek/deepseek-v4-flash")
        agent = self.config.get("agent", "main")
        distro = self.config.get("distro", "OpenClawGateway")
        timeout = int(self.config.get("timeout", 120))

        cmd = [
            "wsl", "-d", distro, "--",
            "openclaw", "agent",
            "-m", prompt,
            "--agent", agent,
            "--local",
            "--model", model,
            "--json",
        ]

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
            raise AgentRuntimeError(f"openclaw timed out after {timeout}s") from exc
        except Exception as exc:
            raise AgentRuntimeError(f"openclaw error: {exc}") from exc

        raw = proc.stdout or ""
        if proc.returncode != 0 and not raw.strip():
            raise AgentRuntimeError(
                f"openclaw exited {proc.returncode}: {(proc.stderr or '')[:300]}"
            )

        # Parse JSON output: {"payloads": [{"text": "..."}], "meta": {...}}
        text = raw.strip()
        try:
            data = json.loads(text)
            payloads = data.get("payloads", [])
            if payloads and isinstance(payloads[0], dict):
                text = payloads[0].get("text", "") or text
            else:
                text = data.get("meta", {}).get("finalAssistantVisibleText", text)
        except (json.JSONDecodeError, AttributeError, IndexError):
            pass

        return AgentResult(text=text.strip(), raw=raw)
