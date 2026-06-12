"""
Tool: Diagnosis
Reads system state — processes, logs, memory, disk, crashes.
Cross-platform: Windows + Linux + macOS.
"""

import os
import sys
import platform
import asyncio
from dataclasses import dataclass
from .shell import run as shell_run


@dataclass
class DiagnosisResult:
    success: bool
    report: str = ""
    issues: list[str] = None
    error: str | None = None

    def __post_init__(self):
        if self.issues is None:
            self.issues = []


OS = platform.system()  # "Windows" | "Linux" | "Darwin"


async def system_overview() -> DiagnosisResult:
    """Quick system snapshot — OS, CPU, RAM, disk."""
    try:
        import psutil

        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        issues = []
        if cpu > 85:
            issues.append(f"High CPU usage: {cpu}%")
        if ram.percent > 85:
            issues.append(f"High RAM usage: {ram.percent}% ({ram.used // 1e9:.1f}GB / {ram.total // 1e9:.1f}GB)")
        if disk.percent > 90:
            issues.append(f"Low disk space: {disk.free // 1e9:.1f}GB free")

        report = (
            f"System: {platform.system()} {platform.release()}\n"
            f"CPU   : {cpu}% used ({psutil.cpu_count()} cores)\n"
            f"RAM   : {ram.percent}% used — {ram.used // 1e9:.1f}GB / {ram.total // 1e9:.1f}GB\n"
            f"Disk  : {disk.percent}% used — {disk.free // 1e9:.1f}GB free\n"
        )

        return DiagnosisResult(success=True, report=report, issues=issues)

    except ImportError:
        return DiagnosisResult(success=False, error="psutil not installed. Run: pip install psutil")


async def top_processes(n: int = 10) -> DiagnosisResult:
    """List top CPU-consuming processes."""
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                procs.append(p.info)
            except Exception:
                pass

        procs.sort(key=lambda x: x.get("cpu_percent", 0), reverse=True)
        lines = ["Top processes by CPU:"]
        for p in procs[:n]:
            lines.append(f"  PID {p['pid']:>6} | CPU {p['cpu_percent']:>5.1f}% | RAM {p['memory_percent']:>4.1f}% | {p['name']}")

        return DiagnosisResult(success=True, report="\n".join(lines))

    except ImportError:
        return DiagnosisResult(success=False, error="psutil not installed.")


async def read_crash_logs() -> DiagnosisResult:
    """Read recent crash/error logs from the OS."""
    if OS == "Windows":
        result = await shell_run(
            'wevtutil qe System /c:20 /rd:true /f:text /q:"*[System[Level<=2]]"',
            timeout=15
        )
        if result.success and result.stdout:
            return DiagnosisResult(success=True, report=result.stdout[:4000])
        return DiagnosisResult(success=False, error="Could not read Windows event log.")

    elif OS == "Linux":
        result = await shell_run("journalctl -p err -n 30 --no-pager", timeout=10)
        if result.success:
            return DiagnosisResult(success=True, report=result.stdout[:4000])
        # Fallback to syslog
        result2 = await shell_run("tail -n 50 /var/log/syslog", timeout=10)
        return DiagnosisResult(success=result2.success, report=result2.stdout[:4000])

    elif OS == "Darwin":
        result = await shell_run("log show --predicate 'eventType == fault' --last 1h --style compact", timeout=15)
        return DiagnosisResult(success=result.success, report=result.stdout[:4000])

    return DiagnosisResult(success=False, error=f"Unsupported OS: {OS}")


async def check_startup_programs() -> DiagnosisResult:
    """List programs that run on startup."""
    if OS == "Windows":
        result = await shell_run(
            'reg query HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run', timeout=10
        )
    elif OS == "Linux":
        result = await shell_run("ls /etc/init.d/ && systemctl list-units --type=service --state=enabled", timeout=10)
    elif OS == "Darwin":
        result = await shell_run("launchctl list | head -30", timeout=10)
    else:
        return DiagnosisResult(success=False, error="Unsupported OS")

    return DiagnosisResult(success=result.success, report=result.stdout[:3000])


async def full_diagnosis() -> DiagnosisResult:
    """Run all checks and return a combined report."""
    parts = []
    all_issues = []

    for fn in [system_overview, top_processes, read_crash_logs]:
        r = await fn()
        if r.success:
            parts.append(r.report)
            all_issues.extend(r.issues)

    report = "\n\n---\n\n".join(parts)
    return DiagnosisResult(success=True, report=report, issues=all_issues)
