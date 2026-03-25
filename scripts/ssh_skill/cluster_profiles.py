from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .paths import cluster_profiles_path, ensure_cluster_profiles_home


BUILTIN_CLUSTER_PROFILES: dict[str, dict[str, Any]] = {
    "generic_slurm": {
        "id": "generic_slurm",
        "display_name": "Generic Slurm Cluster",
        "scheduler": "slurm",
        "description": "Generic Slurm add-on layered on top of ssh-skill for queue inspection, job submission, log checks, and safe cluster debugging.",
        "recommended_checks": [
            "whoami",
            "hostname",
            "pwd",
            "sinfo",
            "squeue -u \"$USER\"",
            "module avail",
            "conda env list",
            "nvidia-smi",
        ],
        "policies": [
            "Do not run heavy jobs on login nodes.",
            "Prefer sbatch for long-running workloads.",
            "Use srun only for short interactive debugging.",
            "Do not assume sudo, apt, or Docker are available.",
            "Declare GPU usage explicitly when requesting GPU resources.",
        ],
        "slurm": {
            "submit_command": "sbatch run.slurm",
            "interactive_example": "srun --pty bash",
            "cancel_command": "scancel <jobid>",
            "watch_commands": [
                "squeue -j <jobid>",
                "tail -n 200 <logfile>",
            ],
            "gpu_examples": [
                "--gres=gpu:1",
                "--gres=gpu:<model>:1",
            ],
        },
    },
}


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        result = deepcopy(base)
        for key, value in override.items():
            if key in result:
                result[key] = _deep_merge(result[key], value)
            else:
                result[key] = deepcopy(value)
        return result
    return deepcopy(override)


def _normalize_profile(raw: dict[str, Any], *, source: str) -> dict[str, Any]:
    profile = deepcopy(raw)
    profile_id = profile.get("id")
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise ValueError(f"cluster profile from {source} is missing a valid id")
    profile["id"] = profile_id.strip()
    if "display_name" in profile and not isinstance(profile["display_name"], str):
        raise ValueError(f"cluster profile {profile_id} has invalid display_name")
    if "scheduler" in profile and profile["scheduler"] is not None and not isinstance(profile["scheduler"], str):
        raise ValueError(f"cluster profile {profile_id} has invalid scheduler")
    profile["source"] = source
    return profile


def _load_custom_profiles() -> dict[str, dict[str, Any]]:
    directory = ensure_cluster_profiles_home()
    profiles: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"failed to load cluster profile {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"cluster profile file must contain a JSON object: {path}")
        profile = _normalize_profile(raw, source=str(path))
        profiles[profile["id"]] = profile
    return profiles


def _all_profiles_raw() -> dict[str, dict[str, Any]]:
    profiles = {
        profile_id: _normalize_profile(raw, source=f"builtin:{profile_id}")
        for profile_id, raw in BUILTIN_CLUSTER_PROFILES.items()
    }
    profiles.update(_load_custom_profiles())
    return profiles


def _resolve_profile(profile_id: str, *, profiles: dict[str, dict[str, Any]] | None = None, stack: tuple[str, ...] = ()) -> dict[str, Any]:
    all_profiles = profiles or _all_profiles_raw()
    if profile_id not in all_profiles:
        raise ValueError(f"unknown cluster profile: {profile_id}")
    if profile_id in stack:
        raise ValueError(f"cluster profile inheritance cycle detected: {' -> '.join(stack + (profile_id,))}")
    profile = deepcopy(all_profiles[profile_id])
    parent_id = profile.get("extends")
    if parent_id:
        if not isinstance(parent_id, str) or not parent_id.strip():
            raise ValueError(f"cluster profile {profile_id} has invalid extends value")
        parent = _resolve_profile(parent_id.strip(), profiles=all_profiles, stack=stack + (profile_id,))
        profile = _deep_merge(parent, profile)
    profile["id"] = profile_id
    return profile


def list_cluster_profiles(*, scheduler: str | None = None) -> dict[str, Any]:
    raw_profiles = _all_profiles_raw()
    profiles = []
    for profile_id in sorted(raw_profiles):
        profile = _resolve_profile(profile_id, profiles=raw_profiles)
        if scheduler and profile.get("scheduler") != scheduler:
            continue
        profiles.append(profile)
    return {
        "profiles": profiles,
        "custom_profile_dir": str(cluster_profiles_path()),
    }


def get_cluster_profile(profile_id: str) -> dict[str, Any]:
    raw_profiles = _all_profiles_raw()
    return {
        "profile": _resolve_profile(profile_id, profiles=raw_profiles),
        "custom_profile_dir": str(cluster_profiles_path()),
    }


def resolve_cluster_profile_for_server(server: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    explicit_profile = server.get("cluster_profile")
    if explicit_profile:
        profile = get_cluster_profile(str(explicit_profile))["profile"]
        return profile, "server.cluster_profile"
    if server.get("cluster_mode") and server.get("scheduler") == "slurm":
        profile = get_cluster_profile("generic_slurm")["profile"]
        return profile, "scheduler_fallback"
    return None, None


def get_cluster_profile_for_host(*, host: str | None = None, profile_id: str | None = None) -> dict[str, Any]:
    if profile_id:
        payload = get_cluster_profile(profile_id)
        payload["resolved_from"] = "profile_id"
        return payload
    if not host:
        raise ValueError("host or profile_id is required")
    from .registry import get_server_record

    server = get_server_record(host)
    if server is None:
        raise ValueError(f"managed server not found: {host}")
    profile, resolution_source = resolve_cluster_profile_for_server(server)
    return {
        "host": host,
        "server": server,
        "profile": profile,
        "resolved_from": resolution_source,
        "custom_profile_dir": str(cluster_profiles_path()),
    }
