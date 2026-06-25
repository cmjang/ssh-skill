from __future__ import annotations

import os
from pathlib import Path


def agent_config_home() -> Path | None:
    """Locate the host agent's config dir so state lives next to it.

    Supports Codex (CODEX_HOME / ~/.codex) and Claude Code (CLAUDE_CONFIG_DIR / ~/.claude).
    Returns None if neither is configured, so the caller can fall back to a default.
    """
    codex = os.environ.get("CODEX_HOME")
    if codex:
        return Path(codex).expanduser()
    claude = os.environ.get("CLAUDE_CONFIG_DIR")
    if claude:
        return Path(claude).expanduser()
    for candidate in ("~/.codex", "~/.claude"):
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    return None


def state_home() -> Path:
    """Per-user ssh-skill state directory.

    Precedence: SSH_SKILL_HOME (explicit, agent-agnostic) -> the host agent's config dir
    (Codex or Claude Code) + /ssh-skill -> ~/.codex/ssh-skill as a last-resort default.
    """
    explicit = os.environ.get("SSH_SKILL_HOME")
    if explicit:
        return Path(explicit).expanduser()
    base = agent_config_home()
    if base is not None:
        return base / "ssh-skill"
    return Path("~/.codex").expanduser() / "ssh-skill"


def registry_path() -> Path:
    return state_home() / "servers.json"


def cluster_profiles_path() -> Path:
    return state_home() / "cluster_profiles"


def ai_tool_profiles_path() -> Path:
    return state_home() / "ai_tool_profiles"


def managed_ssh_config_path() -> Path:
    return state_home() / "managed_ssh_config"


def base_ssh_config_path() -> Path:
    return Path(os.environ.get("SSH_SKILL_BASE_SSH_CONFIG", "~/.ssh/config")).expanduser()


def ensure_state_home() -> Path:
    root = state_home()
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_cluster_profiles_home() -> Path:
    root = cluster_profiles_path()
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_ai_tool_profiles_home() -> Path:
    root = ai_tool_profiles_path()
    root.mkdir(parents=True, exist_ok=True)
    return root
