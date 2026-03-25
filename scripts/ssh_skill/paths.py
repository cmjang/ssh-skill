from __future__ import annotations

import os
from pathlib import Path


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def state_home() -> Path:
    return codex_home() / "ssh-skill"


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
