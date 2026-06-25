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
    "sist_ai_cluster": {
        "id": "sist_ai_cluster",
        "extends": "generic_slurm",
        "display_name": "ShanghaiTech SIST AI Cluster (上科大信息学院 AI Cluster)",
        "aliases": ["skd", "skd_ai_cluster", "sist", "shanghaitech", "shanghaitech_sist"],
        "scheduler": "slurm",
        "description": (
            "ShanghaiTech (上科大) School of Information Science and Technology AI Cluster. "
            "Slurm-based; users hold unprivileged accounts with no sudo. The GPU debug nodes are "
            "reachable only by jumping through a login node, and Slurm jobs must be submitted from a "
            "login node, never from a debug node."
        ),
        "host_alias_prefix": "sist",
        "manual_urls": {
            "mirror_pypi": "https://mirrors.shanghaitech.edu.cn/help/pypi",
            "mirror_anaconda": "https://mirrors.shanghaitech.edu.cn/help/anaconda",
        },
        "access": {
            "ssh_port": 22112,
            "password_change_command": "yppasswd",
            "login_nodes": ["10.15.89.191", "10.15.89.192", "10.15.89.41"],
            "debug_nodes": ["10.15.88.73", "10.15.88.74"],
            "debug_requires_login_jump": True,
            "default_login_jump_index": 0,
            "key_permission": "chmod 600 (the manual suggests 500) on the private key",
            "notes": [
                "Log in to the cluster only through the three login nodes on port 22112.",
                "Debug nodes (GPU, 10.15.88.x) are reachable ONLY by jumping through a login node (ProxyJump).",
                "Debug nodes are for code editing and environment debugging; do NOT submit Slurm jobs from them.",
                "Do not run heavy workloads on login nodes; an admin may kill them.",
                "Do not hop from one login node to another; reconnect from your own client instead.",
                "Close the SSH session when work is done.",
                "Prefer key-based login and keep the private key permission tight (chmod 600, or 500 as the manual suggests).",
                "On a host-key conflict, remove the stale line from ~/.ssh/known_hosts and reconnect.",
                "Change your password with yppasswd.",
            ],
        },
        "policies": [
            "Log in only through the three login nodes on port 22112; do not hop between login nodes.",
            "Do not run heavy workloads on login nodes.",
            "GPU debug nodes (10.15.88.x) are reachable only by jumping through a login node (ProxyJump).",
            "Debug nodes are for code editing and environment debugging only; do not submit Slurm jobs from them.",
            "No sudo, apt, or system package installs; install into your home directory or use module.",
            "Docker is not supported; use Singularity 3.5.2 and build images locally before uploading.",
            "Prefer sbatch for real workloads; use srun only for short interactive debugging from a login node.",
            "Declare GPU usage explicitly (e.g. --gres=gpu:1); confirm partition and GPU-type names with sinfo.",
            "Use the ShanghaiTech mirror for faster pip/conda; change passwords with yppasswd; close sessions when done.",
        ],
        "software": {
            "sudo_allowed": False,
            "install_policy": (
                "No sudo/apt. Install into your home directory; almost all software offers a source build. "
                "Contact admins only for genuinely hard installs."
            ),
            "base_toolchain": {"make": "4.3.0", "gcc": "10.2.0", "glibc": "2.31"},
            "software_root": "/public/software",
            "depository": "/public/resources/depository",
            "module_tool": "module (use 'module avail' / 'module load <name>/<version>')",
            "modules": {
                "anaconda3": ["4.10.3"],
                "cmake": ["3.22.0"],
                "gcc": ["10.2.0", "7.5"],
                "automake": ["1.15", "1.16.5"],
                "cuda": ["8.0-12.5"],
                "make": ["4.3"],
                "singularity": ["3.5.2"],
            },
            "containers": {
                "docker_supported": False,
                "use": "singularity",
                "singularity_version": "3.5.2",
                "notes": [
                    "Docker is not supported; use Singularity, which can run Docker-format images.",
                    "There is no sudo inside containers either, so build a fully prepared image locally first.",
                    "Converting a local Docker image (.tar) to a Singularity image on the cluster needs no admin rights.",
                    "Building from a Dockerfile needs root: use Singularity --remote build or contact admins.",
                ],
            },
            "mirrors": {
                "pip": "https://mirrors.shanghaitech.edu.cn/help/pypi",
                "conda": "https://mirrors.shanghaitech.edu.cn/help/anaconda",
                "note": "Prefer the GeekPie/ShanghaiTech mirror for faster pip/conda downloads.",
            },
            "recommendation": (
                "AI users should maintain their own environment with conda or a self-built python; "
                "the anaconda installer is also under /public/resources/depository."
            ),
        },
        "slurm": {
            "submit_host_role": "login",
            "discover": (
                "Run sinfo on a login node to list real partitions, GPU types, and limits before "
                "requesting resources; partition and account names are site-specific."
            ),
            "notes": [
                "Submit from a login node, never from a debug node.",
                "Declare GPU explicitly, e.g. --gres=gpu:1 or --gres=gpu:<type>:<n>; confirm GPU-type names via sinfo.",
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


def _build_alias_map(profiles: dict[str, dict[str, Any]]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for profile_id, raw in profiles.items():
        for alias in raw.get("aliases") or []:
            if isinstance(alias, str) and alias.strip():
                alias_map.setdefault(alias.strip().lower(), profile_id)
    return alias_map


def _canonical_profile_id(profile_id: str, profiles: dict[str, dict[str, Any]]) -> str:
    if profile_id in profiles:
        return profile_id
    alias_map = _build_alias_map(profiles)
    return alias_map.get(profile_id.strip().lower(), profile_id)


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
    canonical = _canonical_profile_id(profile_id, raw_profiles)
    return {
        "profile": _resolve_profile(canonical, profiles=raw_profiles),
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
