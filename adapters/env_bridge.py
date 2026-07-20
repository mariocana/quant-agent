"""Subprocess runner for the user's tools — the shared plumbing of Layer 2.

Not a cross-env bridge anymore: datasea + algo_framework live in ONE conda env
(`workbench`, see algo_framework/workbench-environment.yml). This module just:

  - runs a python command from a given folder (the tools import each other by
    relative path, so cwd matters);
  - forces UTF-8 / unbuffered output (Windows pipes default to cp1252 and choke
    on the tools' emoji/box-drawing chars — same fix workbench.py uses);
  - captures stdout/stderr with a timeout;
  - pulls out the `JSON_EXPORT: <path>` line the tools print (the contract added
    on the algo_framework `feat/machine-readable-contracts` branch).

If you run the orchestrator from OUTSIDE the workbench env, set conda_env so each
command is wrapped in `conda run -n <env> ...`.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

try:  # keep the module importable outside the full orchestrator env (for tests)
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover
    import logging
    logger = logging.getLogger("adapters.env_bridge")

JSON_EXPORT_PREFIX = "JSON_EXPORT:"


class ToolError(RuntimeError):
    """A tool subprocess failed, timed out, or produced no usable result."""


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    json_path: Optional[str] = None  # from the last JSON_EXPORT line, if any

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def build_argv(
    script_args: Sequence[str],
    python_exec: str = "python",
    conda_env: Optional[str] = None,
) -> list[str]:
    """Build the full argv. With conda_env, wrap in `conda run -n <env>`."""
    base = list(script_args)
    if conda_env:
        # --no-capture-output: let the child's stdout/stderr stream straight
        # through so we still see the JSON_EXPORT line and live logs.
        return ["conda", "run", "--no-capture-output", "-n", conda_env, python_exec, *base]
    return [python_exec, *base]


def parse_json_export(stdout: str) -> Optional[str]:
    """Return the path from the last `JSON_EXPORT: <path>` line (or None)."""
    path = None
    for line in stdout.splitlines():
        s = line.strip()
        if s.startswith(JSON_EXPORT_PREFIX):
            path = s[len(JSON_EXPORT_PREFIX):].strip()
    return path


def run(
    script_args: Sequence[str],
    cwd: os.PathLike | str,
    timeout: float = 1800,
    python_exec: str = "python",
    conda_env: Optional[str] = None,
    extra_env: Optional[dict] = None,
) -> CommandResult:
    """Run a python command and capture the result. Never raises on tool failure
    (inspect `.ok` / `.returncode`); only OS-level errors propagate."""
    argv = build_argv(script_args, python_exec, conda_env)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    if extra_env:
        env.update(extra_env)

    logger.info(f"$ (cwd={cwd}) {' '.join(argv)}")
    start = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd), env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        timed_out = True
        rc = -1
        out = e.stdout or ""
        err = (e.stderr or "") + f"\n[TIMEOUT after {timeout}s]"
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")

    dur = round(time.time() - start, 2)
    res = CommandResult(
        argv=argv, returncode=rc, stdout=out, stderr=err,
        duration_s=dur, timed_out=timed_out, json_path=parse_json_export(out),
    )
    if not res.ok:
        logger.warning(f"command failed (rc={rc}, timed_out={timed_out}, {dur}s)")
    return res


def read_json_result(res: CommandResult) -> dict:
    """Load the JSON the tool exported via --export-json. Raises ToolError if the
    export line is missing or the file can't be read."""
    if not res.json_path:
        raise ToolError(
            "No JSON_EXPORT line in tool output — did the command include "
            f"--export-json?\nstderr tail:\n{res.stderr[-2000:]}"
        )
    p = Path(res.json_path)
    if not p.is_file():
        raise ToolError(f"JSON_EXPORT path does not exist: {p}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)
