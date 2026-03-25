#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from ssh_skill.ai_tool_profiles import get_ai_tool_profile, list_ai_tool_profiles
from ssh_skill.server import SSHSkillServer
from ssh_skill.ssh_ops import SSHRuntimeConfig


EXPECTED_AI_PROFILES = {"claude_code", "gemini_cli", "cursor_agent", "opencode"}
EXPECTED_ALIAS_RESOLUTIONS = {
    "claude": "claude_code",
    "gemini": "gemini_cli",
    "cursor": "cursor_agent",
    "cursor-agent": "cursor_agent",
    "opencode": "opencode",
}
EXPECTED_TOOLS = {
    "ssh_list_ai_tool_profiles",
    "ssh_get_ai_tool_profile",
    "ssh_detect_ai_tools",
    "ssh_inspect_ai_workspace",
    "ssh_run_ai_tool",
    "ssh_list_cluster_profiles",
    "ssh_get_cluster_profile",
    "ssh_exec",
    "ssh_sync_dir",
    "ssh_uv_sync",
}


def main() -> int:
    profiles = list_ai_tool_profiles()["profiles"]
    profile_ids = {item["id"] for item in profiles}
    missing_profiles = sorted(EXPECTED_AI_PROFILES - profile_ids)
    if missing_profiles:
        raise SystemExit(f"missing AI profiles: {', '.join(missing_profiles)}")

    alias_resolutions: dict[str, str] = {}
    for alias, expected in EXPECTED_ALIAS_RESOLUTIONS.items():
        resolved = get_ai_tool_profile(alias)["resolved_profile_id"]
        if resolved != expected:
            raise SystemExit(f"alias {alias!r} resolved to {resolved!r}, expected {expected!r}")
        alias_resolutions[alias] = resolved

    server = SSHSkillServer(SSHRuntimeConfig())
    tool_names = set(server.tools.keys())
    missing_tools = sorted(EXPECTED_TOOLS - tool_names)
    if missing_tools:
        raise SystemExit(f"missing runtime tools: {', '.join(missing_tools)}")

    payload = {
        "ok": True,
        "profile_ids": sorted(profile_ids),
        "alias_resolutions": alias_resolutions,
        "checked_tools": sorted(EXPECTED_TOOLS),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
