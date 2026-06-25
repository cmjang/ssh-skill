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
from ssh_skill.cluster_profiles import get_cluster_profile, list_cluster_profiles
from ssh_skill.registry import provision_cluster
from ssh_skill.server import SSHSkillServer
from ssh_skill.ssh_ops import SSHRuntimeConfig


EXPECTED_AI_PROFILES = {"claude_code", "gemini_cli", "cursor_agent", "opencode"}
EXPECTED_CLUSTER_PROFILES = {"generic_slurm", "sist_ai_cluster"}
EXPECTED_CLUSTER_ALIASES = {"skd": "sist_ai_cluster", "shanghaitech": "sist_ai_cluster"}
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

    cluster_ids = {item["id"] for item in list_cluster_profiles()["profiles"]}
    missing_clusters = sorted(EXPECTED_CLUSTER_PROFILES - cluster_ids)
    if missing_clusters:
        raise SystemExit(f"missing cluster profiles: {', '.join(missing_clusters)}")

    cluster_alias_resolutions: dict[str, str] = {}
    for alias, expected in EXPECTED_CLUSTER_ALIASES.items():
        resolved = get_cluster_profile(alias)["profile"]["id"]
        if resolved != expected:
            raise SystemExit(f"cluster alias {alias!r} resolved to {resolved!r}, expected {expected!r}")
        cluster_alias_resolutions[alias] = resolved

    # Dry-run provisioning never writes the registry; it only proves the jump-host topology wires up.
    dry = provision_cluster("skd", user="validator", dry_run=True)
    planned_aliases = [server["alias"] for server in dry["planned_servers"]]
    expected_aliases = ["sist-login1", "sist-login2", "sist-login3", "sist-debug1", "sist-debug2"]
    if planned_aliases != expected_aliases:
        raise SystemExit(f"provision-cluster planned aliases {planned_aliases}, expected {expected_aliases}")
    debug1 = next(server for server in dry["planned_servers"] if server["alias"] == "sist-debug1")
    if debug1["proxy_jump"] != dry["jump_alias"] or debug1["role"] != "debug":
        raise SystemExit(f"provision-cluster debug node wiring is wrong: {debug1}")

    server = SSHSkillServer(SSHRuntimeConfig())
    tool_names = set(server.tools.keys())
    missing_tools = sorted(EXPECTED_TOOLS - tool_names)
    if missing_tools:
        raise SystemExit(f"missing runtime tools: {', '.join(missing_tools)}")

    payload = {
        "ok": True,
        "profile_ids": sorted(profile_ids),
        "alias_resolutions": alias_resolutions,
        "cluster_profile_ids": sorted(cluster_ids),
        "cluster_alias_resolutions": cluster_alias_resolutions,
        "provision_dry_run": {"jump_alias": dry["jump_alias"], "planned_aliases": planned_aliases},
        "checked_tools": sorted(EXPECTED_TOOLS),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
