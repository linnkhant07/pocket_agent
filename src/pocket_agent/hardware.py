"""OS and RAM detection for `pocket-agent init`."""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass

from pocket_agent.models import TIERS, ModelSpec


@dataclass(frozen=True)
class Hardware:
    os_name: str
    ram_gb: float | None
    tier: str
    model: ModelSpec

    @property
    def summary(self) -> str:
        ram = "unknown" if self.ram_gb is None else f"{self.ram_gb:.1f} GB"
        return f"{self.os_name}, {ram}, tier {self.tier}, model {self.model.tag} ({self.model.license})"


def detect_os() -> str:
    name = platform.system()
    return name or "unknown"


def detect_ram_gb() -> float | None:
    system = detect_os()
    try:
        if system == "Linux":
            for line in open("/proc/meminfo", encoding="utf-8"):
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb / (1024 * 1024)
        elif system == "Darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, timeout=5)
            return int(out.strip()) / (1024 ** 3)
        elif system == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return stat.ullTotalPhys / (1024 ** 3)
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return None
    return None


def select_tier(ram_gb: float | None) -> str:
    """Map installed RAM to a model tier.

    8GB machines report about 8. 16GB machines report about 16. Cutoffs sit
    at 12 and 24 so a laptop that is a little under its sticker size still
    lands on the tier that fits.
    """
    if ram_gb is None:
        return "16gb"
    if ram_gb < 12:
        return "8gb"
    if ram_gb < 24:
        return "16gb"
    return "32gb"


def detect_hardware(model_tag: str | None = None) -> Hardware:
    os_name = detect_os()
    ram = detect_ram_gb()
    tier = select_tier(ram)
    spec = TIERS[tier]
    if model_tag and model_tag != spec.tag:
        from pocket_agent.models import spec_for_tag

        overridden = spec_for_tag(model_tag)
        if overridden is None:
            spec = ModelSpec(
                tag=model_tag,
                hf_repo="",
                license="unverified",
                approx_download="unknown",
                notes="Custom tag. Confirm the license yourself before using it commercially.",
            )
        else:
            spec = overridden
    return Hardware(os_name=os_name, ram_gb=ram, tier=tier, model=spec)
