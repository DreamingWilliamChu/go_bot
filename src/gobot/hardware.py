"""Detect the host hardware (Apple Silicon details on macOS, generic elsewhere)."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from typing import Any

import psutil


def _run(cmd: list[str], timeout: float = 20) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _sysctl(name: str) -> str:
    return _run(["sysctl", "-n", name])


def detect() -> dict[str, Any]:
    info: dict[str, Any] = {
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
        "physical_cpus": psutil.cpu_count(logical=False),
        "memory_bytes": psutil.virtual_memory().total,
    }
    info["memory_gb"] = round(info["memory_bytes"] / 2**30, 1)
    if info["system"] == "Darwin":
        info["chip"] = _sysctl("machdep.cpu.brand_string")
        info["model"] = _sysctl("hw.model")
        info["performance_cores"] = _int(_sysctl("hw.perflevel0.physicalcpu"))
        info["efficiency_cores"] = _int(_sysctl("hw.perflevel1.physicalcpu"))
        info["macos_version"] = _run(["sw_vers", "-productVersion"])
        info["apple_silicon"] = info["machine"] == "arm64"
        disp = _run(["system_profiler", "-json", "SPDisplaysDataType"], timeout=40)
        try:
            gpus = json.loads(disp).get("SPDisplaysDataType", [])
            if gpus:
                info["gpu"] = gpus[0].get("sppci_model")
                cores = gpus[0].get("sppci_cores")
                info["gpu_cores"] = _int(cores) if cores else None
                info["metal_support"] = gpus[0].get("spdisplays_mtlgpufamilysupport")
        except (ValueError, AttributeError):
            pass
    else:
        cpu = ""
        try:
            with open("/proc/cpuinfo") as f:
                text = f.read()
            m = re.search(r"model name\s*:\s*(.+)", text)
            cpu = m.group(1).strip() if m else ""
            info["avx2"] = " avx2 " in text.replace("\n", " ")
        except OSError:
            pass
        info["chip"] = cpu or platform.processor()
        info["apple_silicon"] = False
    return info


def _int(s: str | None) -> int | None:
    try:
        return int(str(s).strip()) if s not in (None, "") else None
    except ValueError:
        return None


def summary(info: dict[str, Any]) -> str:
    parts = [str(info.get("chip") or info.get("machine"))]
    if info.get("gpu_cores"):
        parts.append(f"{info['gpu_cores']}-core GPU")
    parts.append(f"{info.get('memory_gb')} GB RAM")
    if info.get("macos_version"):
        parts.append(f"macOS {info['macos_version']}")
    return ", ".join(parts)
