from __future__ import annotations

import json
import subprocess
import shutil
from typing import Any

from app.adapters.base import AgentResult, AgentRuntimeError, BaseAdapter, ToolCall, ToolResult


def _parse_stream_json(raw: str) -> AgentResult:
    """解析 claude --output-format stream-json 的输出，提取文本和工具调用。"""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    tool_results: list[ToolResult] = []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "assistant":
            msg = event.get("message") or {}
            for block in msg.get("content") or []:
                btype = block.get("type", "")
                if btype == "text":
                    t = block.get("text", "").strip()
                    if t:
                        text_parts.append(t)
                elif btype == "tool_use":
                    tool_calls.append(ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        input=block.get("input") or {},
                    ))

        elif event_type == "user":
            msg = event.get("message") or {}
            for block in msg.get("content") or []:
                if block.get("type") == "tool_result":
                    content = block.get("content", "")
                    if isinstance(content, list):
                        content = " ".join(
                            c.get("text", "") for c in content if c.get("type") == "text"
                        )
                    tool_results.append(ToolResult(
                        tool_use_id=block.get("tool_use_id", ""),
                        content=str(content),
                    ))

        elif event_type == "result":
            if event.get("subtype") == "success":
                result_text = event.get("result", "").strip()
                if result_text and result_text not in text_parts:
                    text_parts.append(result_text)

    return AgentResult(
        text="\n\n".join(text_parts),
        raw=raw,
        tool_calls=tool_calls,
        tool_results=tool_results,
    )


class ClaudeAdapter(BaseAdapter):
    def run(self, prompt: str) -> AgentResult:
        command = self.config.get("command", "claude.CMD")
        timeout = int(self.config.get("timeout", 120))

        cmd_path = shutil.which(command)
        if not cmd_path:
            raise AgentRuntimeError(f"claude command not found: {command}")

        # Flatten newlines: cmd.exe splits arguments at newlines, which would
        # cause flags after the prompt to be lost
        flat_prompt = " ".join(prompt.split())

        try:
            proc = subprocess.run(
                [cmd_path, "-p", flat_prompt, "--output-format", "stream-json", "--verbose"],
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentRuntimeError(f"claude timed out after {timeout}s") from exc
        except Exception as exc:
            raise AgentRuntimeError(f"claude subprocess error: {exc}") from exc

        raw = proc.stdout or ""
        if not raw.strip() and proc.returncode != 0:
            err = (proc.stderr or "").strip()
            raise AgentRuntimeError(f"claude exited {proc.returncode}: {err[:300]}")

        result = _parse_stream_json(raw)

        # 如果 stream-json 解析失败（旧版 claude），回退到纯文本
        if not result.text.strip() and proc.returncode == 0:
            result = AgentResult(text=raw.strip(), raw=raw)

        return result
