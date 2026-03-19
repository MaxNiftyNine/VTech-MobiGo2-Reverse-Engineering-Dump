from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HELPER_SRC = ROOT / "native" / "mobigo_ls_helper.cs"
HELPER_EXE = ROOT / "build" / "mobigo_ls_helper.exe"
VENDOR_DLL = ROOT / "vendor" / "VTech2010USBDllU.dll"


def resolve_csc() -> Path:
    csc_from_path = shutil.which("csc")
    if csc_from_path:
        return Path(csc_from_path)

    candidates = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("csc.exe not found; install .NET Framework build tools or place csc on PATH")


def ensure_helper_built() -> Path:
    if not HELPER_SRC.exists():
        raise RuntimeError(f"helper source not found at {HELPER_SRC}")
    if not VENDOR_DLL.exists():
        raise RuntimeError(f"vendor DLL not found at {VENDOR_DLL}")

    HELPER_EXE.parent.mkdir(parents=True, exist_ok=True)
    if HELPER_EXE.exists() and HELPER_EXE.stat().st_mtime >= HELPER_SRC.stat().st_mtime:
        return HELPER_EXE
    csc = resolve_csc()
    cmd = [
        os.fspath(csc),
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
