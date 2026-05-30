"""Environment checks and setup guidance for Tabuflow."""

from __future__ import annotations

from dataclasses import dataclass
import shutil
import sys
from typing import Any

RIPGREP_INSTALL_URL = "https://github.com/BurntSushi/ripgrep#installation"


@dataclass(frozen=True)
class InstallGuidance:
    """One package-manager install instruction."""

    manager: str
    command: list[str]

    def as_payload(self) -> dict[str, Any]:
        """Return JSON-friendly install guidance."""
        return {
            "manager": self.manager,
            "argv": self.command,
            "display": " ".join(self.command),
        }


def _available(command: str) -> bool:
    """Return whether one command is available on PATH."""
    return shutil.which(command) is not None


def _ripgrep_guidance() -> InstallGuidance | None:
    """Return the most likely ripgrep install instruction for this machine."""
    if sys.platform == "darwin":
        if _available("brew"):
            return InstallGuidance(manager="homebrew", command=["brew", "install", "ripgrep"])
        if _available("port"):
            return InstallGuidance(manager="macports", command=["sudo", "port", "install", "ripgrep"])
        return None

    if sys.platform == "win32":
        if _available("winget"):
            return InstallGuidance(manager="winget", command=["winget", "install", "BurntSushi.ripgrep.MSVC"])
        if _available("scoop"):
            return InstallGuidance(manager="scoop", command=["scoop", "install", "ripgrep"])
        if _available("choco"):
            return InstallGuidance(manager="chocolatey", command=["choco", "install", "ripgrep"])
        return None

    if sys.platform.startswith("linux"):
        if _available("apt-get"):
            return InstallGuidance(manager="apt", command=["sudo", "apt-get", "install", "ripgrep"])
        if _available("dnf"):
            return InstallGuidance(manager="dnf", command=["sudo", "dnf", "install", "ripgrep"])
        if _available("pacman"):
            return InstallGuidance(manager="pacman", command=["sudo", "pacman", "-S", "ripgrep"])
        if _available("zypper"):
            return InstallGuidance(manager="zypper", command=["sudo", "zypper", "install", "ripgrep"])
        if _available("apk"):
            return InstallGuidance(manager="apk", command=["apk", "add", "ripgrep"])
        return None

    return None


def _ripgrep_check() -> dict[str, Any]:
    """Return the ripgrep dependency check and install guidance."""
    existing_path = shutil.which("rg")
    if existing_path:
        return {
            "name": "ripgrep",
            "command": "rg",
            "status": "ok",
            "path": existing_path,
            "required_for": ["artifacts search --scope files", "artifacts search --scope all"],
            "summary": "ripgrep is available.",
        }

    guidance = _ripgrep_guidance()
    payload: dict[str, Any] = {
        "name": "ripgrep",
        "command": "rg",
        "status": "missing",
        "required_for": ["artifacts search --scope files", "artifacts search --scope all"],
        "degraded_behavior": "artifacts search keeps the same public usage while falling back to grep or bounded Python UTF-8 scanning, but rg is the required file-search dependency.",
        "manual_url": RIPGREP_INSTALL_URL,
        "summary": "ripgrep is missing; install rg for supported artifact file search.",
    }
    if guidance is None:
        payload["install"] = {
            "available": False,
            "message": "No supported package manager was detected. Install ripgrep manually from the official guide.",
        }
    else:
        payload["install"] = {
            "available": True,
            "recommended": guidance.as_payload(),
            "message": "Run this command to install the required rg binary.",
        }
    return payload


def doctor() -> dict[str, Any]:
    """Check Tabuflow's local tool dependencies."""
    checks = [_ripgrep_check()]
    missing = [check for check in checks if check["status"] == "missing"]
    status = "ok" if not missing else "error"
    summary = "All checked dependencies are available."
    if missing:
        summary = f"{len(missing)} required dependency is missing."
    return {
        "status": status,
        "checks": checks,
        "summary": summary,
    }
