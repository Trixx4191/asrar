"""
Process manager — list, inspect, and (with confirmation) kill processes.
Uses psutil when available, falls back to ps/pgrep.
"""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass


@dataclass
class ProcessResult:
    success: bool
    content: str = ""
    error: str | None = None


def _psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def list_processes(limit: int = 30, sort_by: str = "cpu") -> ProcessResult:
    psutil = _psutil()
    if not psutil:
        return ProcessResult(success=False, error="psutil not installed")
    try:
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status", "username"]):
            try:
                info = p.info
                procs.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        key = "cpu_percent" if sort_by == "cpu" else "memory_percent"
        procs.sort(key=lambda x: x.get(key) or 0, reverse=True)
        lines = [f"{'PID':>7}  {'CPU%':>6}  {'MEM%':>6}  {'STATUS':<10}  NAME"]
        for info in procs[:limit]:
            lines.append(
                f"{info.get('pid', 0):>7}  "
                f"{(info.get('cpu_percent') or 0):>6.1f}  "
                f"{(info.get('memory_percent') or 0):>6.1f}  "
                f"{str(info.get('status') or ''):<10}  "
                f"{info.get('name') or '?'}"
            )
        return ProcessResult(success=True, content="\n".join(lines))
    except Exception as e:
        return ProcessResult(success=False, error=str(e))


def inspect_process(pid: int) -> ProcessResult:
    psutil = _psutil()
    if not psutil:
        return ProcessResult(success=False, error="psutil not installed")
    try:
        p = psutil.Process(pid)
        info = {
            "pid": p.pid,
            "name": p.name(),
            "status": p.status(),
            "cpu_percent": p.cpu_percent(interval=0.1),
            "memory_percent": p.memory_percent(),
            "cmdline": " ".join(p.cmdline() or []),
            "create_time": p.create_time(),
            "username": p.username() if hasattr(p, "username") else "?",
        }
        lines = [f"{k}: {v}" for k, v in info.items()]
        return ProcessResult(success=True, content="\n".join(lines))
    except Exception as e:
        return ProcessResult(success=False, error=str(e))


def kill_process(pid: int, force: bool = False) -> ProcessResult:
    """Destructive — must be gated by hooks / confirmation."""
    psutil = _psutil()
    try:
        if psutil:
            p = psutil.Process(pid)
            if force:
                p.kill()
            else:
                p.terminate()
            return ProcessResult(success=True, content=f"{'Killed' if force else 'Terminated'} PID {pid}")
        else:
            sig = signal.SIGKILL if force else signal.SIGTERM
            os.kill(pid, sig)
            return ProcessResult(success=True, content=f"Sent signal to PID {pid}")
    except Exception as e:
        return ProcessResult(success=False, error=str(e))


def format_process_result(r: ProcessResult) -> str:
    if not r.success:
        return f"❌ Process error: {r.error}"
    return r.content or "(empty)"
