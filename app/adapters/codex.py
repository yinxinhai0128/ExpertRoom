from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.adapters.base import AgentResult, AgentRuntimeError, BaseAdapter
from app.config_loader import ROOT_DIR


class CodexAdapter(BaseAdapter):
    def run(self, prompt: str) -> AgentResult:
        if not self.config.get("enabled", False):
            raise AgentRuntimeError("Codex adapter is disabled in config.yaml")

        command = self.config.get("command", "codex")
        model = self.config.get("model", "")
        timeout = int(self.config.get("timeout", 180))

        cmd_path = shutil.which(command)
        if not cmd_path:
            raise AgentRuntimeError(f"codex command not found: {command}")

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            out_file = tf.name

        # Collapse newlines: cmd.exe splits arguments at newlines, which would
        # drop the -o and --skip-git-repo-check flags from the command
        flat_prompt = " ".join(prompt.split())
        cmd = [cmd_path, "exec", flat_prompt, "-o", out_file, "--skip-git-repo-check"]
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
                input="",
                cwd=str(ROOT_DIR),
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentRuntimeError(f"codex timed out after {timeout}s") from exc
        except Exception as exc:
            raise AgentRuntimeError(f"codex error: {exc}") from exc

        text = ""
        try:
            text = Path(out_file).read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            pass
        finally:
            try:
                Path(out_file).unlink(missing_ok=True)
            except Exception:
                pass

        raw = proc.stdout or ""
        if not text:
            text = raw.strip()

        if proc.returncode != 0 and not text:
            raise AgentRuntimeError(
                f"codex exited {proc.returncode}: {(proc.stderr or '')[:300]}"
            )

        return AgentResult(text=text, raw=raw)
