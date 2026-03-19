from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HELPER_SRC = ROOT / "native" / "mobigo_ls_helper.cs"
HELPER_EXE = ROOT / "build" / "mobigo_ls_helper.exe"
CSC = Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe")


def ensure_helper_built() -> Path:
    if HELPER_EXE.exists() and HELPER_EXE.stat().st_mtime >= HELPER_SRC.stat().st_mtime:
        return HELPER_EXE
    if not CSC.exists():
        raise RuntimeError(f"csc.exe not found at {CSC}")
    cmd = [
        os.fspath(CSC),
        "/nologo",
        "/target:exe",
        "/platform:x86",
        f"/out:{HELPER_EXE}",
        os.fspath(HELPER_SRC),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "helper build failed")
    return HELPER_EXE


def run_helper(*args: str) -> subprocess.CompletedProcess[str]:
    helper = ensure_helper_built()
    result = subprocess.run([os.fspath(helper), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"{helper.name} failed")
    return result
