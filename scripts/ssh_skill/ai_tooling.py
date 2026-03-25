from __future__ import annotations

import json
import shlex
import time
from typing import Any

from .ai_tool_profiles import get_ai_tool_profile, list_ai_tool_profiles
from .ssh_ops import SSHError, SSHRuntimeConfig, _run_ssh, _safe_decode, exec_command


COMMON_REMOTE_TOOLS: list[dict[str, str]] = [
    {"name": "git", "category": "vcs", "recommendation": "Install Git so the agent can inspect diffs, branches, and perform safe rollback instead of deleting files."},
    {"name": "rg", "category": "search", "recommendation": "Install ripgrep (`rg`) for fast codebase search on the remote host."},
    {"name": "fd", "category": "search", "recommendation": "Install fd for faster filename discovery, or provide fdfind as a compatibility alias."},
    {"name": "fdfind", "category": "search", "recommendation": "Ubuntu and Debian often package fd as `fdfind`; expose one of fd/fdfind for remote discovery."},
    {"name": "jq", "category": "json", "recommendation": "Install jq for structured log, config, and API inspection."},
    {"name": "yq", "category": "yaml", "recommendation": "Install yq when the workflow uses YAML-heavy config or batch manifests."},
    {"name": "python3", "category": "python", "recommendation": "Expose python3 for Python tooling, quick debug scripts, and environment setup."},
    {"name": "uv", "category": "python", "recommendation": "Install uv for fast remote sync of Python environments when the project uses pyproject.toml or uv.lock."},
    {"name": "conda", "category": "python", "recommendation": "Install or load conda if remote Python environments are managed with conda or environment.yml."},
    {"name": "node", "category": "node", "recommendation": "Install Node.js because Claude Code, Gemini CLI, Cursor Agent CLI, and OpenCode are typically distributed as Node-based CLIs."},
    {"name": "npm", "category": "node", "recommendation": "Expose npm so Node-based AI CLIs and project dependencies can be installed or updated."},
    {"name": "npx", "category": "node", "recommendation": "Expose npx for one-off execution of Node-based utilities and installers."},
    {"name": "pnpm", "category": "node", "recommendation": "Install pnpm when the project or AI toolchain uses pnpm-based package management."},
    {"name": "yarn", "category": "node", "recommendation": "Install Yarn when the repo uses yarn.lock or project scripts depend on it."},
    {"name": "tmux", "category": "terminal", "recommendation": "Install tmux on login nodes if you want resilient interactive debug sessions without long-lived compute-node shells."},
    {"name": "nvidia-smi", "category": "gpu", "recommendation": "Expose nvidia-smi on GPU-capable hosts for quick device visibility checks before training."},
    {"name": "sinfo", "category": "slurm", "recommendation": "Use a real Slurm login node that has sinfo in PATH before scheduling cluster jobs."},
    {"name": "squeue", "category": "slurm", "recommendation": "Use a real Slurm login node that has squeue in PATH before monitoring jobs."},
    {"name": "sacct", "category": "slurm", "recommendation": "Expose sacct if you need structured job accounting and postmortem state inspection."},
    {"name": "sbatch", "category": "slurm", "recommendation": "Use a real Slurm login node that has sbatch in PATH before submitting long-running jobs."},
]

COMMON_WORKSPACE_HINTS = [
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "opencode.json",
    "opencode.jsonc",
    ".cursor/rules/*.md",
    ".opencode/agents/*.md",
    ".opencode/skills/*/SKILL.md",
]

WORKSPACE_MARKERS = [
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    "requirements-dev.txt",
    "environment.yml",
    "environment.yaml",
    "conda.yml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "compose.yml",
    "run.slurm",
]

READ_ONLY_PREFIX = """You are running through ssh-skill in read-only analysis mode.
Do not modify files, write patches, create commits, delete files, install packages, start services, or run shell commands.
Inspect the workspace and return analysis, findings, or recommended commands only.

User request:
"""


def _run_remote_script(config: SSHRuntimeConfig, *, host: str, script: str, timeout_sec: int) -> dict[str, Any]:
    start = time.monotonic()
    completed = _run_ssh(config, host, f"bash -lc {shlex.quote(script)}", timeout=timeout_sec)
    duration_ms = int((time.monotonic() - start) * 1000)
    stdout, stdout_trunc = _safe_decode(completed.stdout, config.max_output_bytes)
    stderr, stderr_trunc = _safe_decode(completed.stderr, config.max_output_bytes)
    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": stdout_trunc or stderr_trunc,
        "duration_ms": duration_ms,
    }


def _bash_array(name: str, values: list[str]) -> str:
    return f"{name}=({' '.join(shlex.quote(value) for value in values)})"


def _expand_prompt(prompt: str, *, mode: str) -> str:
    if mode == "execute":
        return prompt
    if mode != "analyze":
        raise ValueError("mode must be either 'analyze' or 'execute'")
    return READ_ONLY_PREFIX + prompt


def _render_argv(variant: list[str], *, prompt: str, extra_args: list[str]) -> list[str]:
    rendered = [token.replace("{prompt}", prompt) for token in variant]
    rendered.extend(extra_args)
    return rendered


def _command_preview(argv: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


def detect_ai_tools(
    config: SSHRuntimeConfig,
    *,
    host: str,
    workdir: str = ".",
    profile_ids: list[str] | None = None,
    timeout_sec: int = 120,
) -> dict[str, Any]:
    requested_ids = profile_ids or [item["id"] for item in list_ai_tool_profiles()["profiles"]]
    detections: list[dict[str, Any]] = []
    for requested_id in requested_ids:
        profile_payload = get_ai_tool_profile(requested_id)
        profile = profile_payload["profile"]
        script = "\n".join(
            [
                "set -e",
                f"cd {shlex.quote(workdir)}",
                "shopt -s nullglob globstar",
                _bash_array("executables", list(profile.get("executables", []))),
                _bash_array("version_args", list(profile.get("version_args", []))),
                _bash_array("workspace_files", list(profile.get("workspace_files", []))),
                _bash_array("workspace_globs", list(profile.get("workspace_globs", []))),
                _bash_array("config_paths", list(profile.get("config_paths", []))),
                _bash_array("auth_envs", list(profile.get("auth_envs", []))),
                'printf "cwd\\t%s\\n" "$PWD"',
                'found=""',
                'for exe in "${executables[@]}"; do',
                '  if command -v "$exe" >/dev/null 2>&1; then',
                '    found="$exe"',
                "    break",
                "  fi",
                "done",
                'if [ -n "$found" ]; then',
                '  printf "available\\ttrue\\n"',
                '  printf "executable\\t%s\\n" "$found"',
                '  printf "path\\t%s\\n" "$(command -v "$found")"',
                '  if version_text="$("$found" "${version_args[@]}" 2>/dev/null | head -n 1)"; then',
                '    if [ -n "$version_text" ]; then printf "version\\t%s\\n" "$version_text"; fi',
                "  fi",
                "else",
                '  printf "available\\tfalse\\n"',
                "fi",
                'for item in "${workspace_files[@]}"; do',
                '  if [ -e "$item" ]; then printf "workspace\\t%s\\n" "$item"; fi',
                "done",
                'for pattern in "${workspace_globs[@]}"; do',
                "  for match in $pattern; do",
                '    if [ -e "$match" ]; then printf "workspace\\t%s\\n" "$match"; fi',
                "  done",
                "done",
                'for item in "${config_paths[@]}"; do',
                '  expanded="${item//\\$HOME/$HOME}"',
                '  if [ -e "$expanded" ]; then printf "config\\t%s\\n" "$expanded"; fi',
                "done",
                'for env_key in "${auth_envs[@]}"; do',
                '  if [ -n "${!env_key+x}" ] && [ -n "${!env_key}" ]; then',
                '    printf "auth\\t%s\\tpresent\\n" "$env_key"',
                '  elif [ -n "${!env_key+x}" ]; then',
                '    printf "auth\\t%s\\tempty\\n" "$env_key"',
                "  else",
                '    printf "auth\\t%s\\tmissing\\n" "$env_key"',
                "  fi",
                "done",
            ]
        )
        result = _run_remote_script(config, host=host, script=script, timeout_sec=timeout_sec)
        if not result["ok"]:
            raise SSHError(result["stderr"].strip() or result["stdout"].strip() or f"failed to inspect AI tool {requested_id}")

        available = False
        cwd = None
        executable = None
        binary_path = None
        version = None
        workspace_files: list[str] = []
        config_files: list[str] = []
        auth: dict[str, str] = {}

        for raw_line in result["stdout"].splitlines():
            parts = raw_line.split("\t")
            if not parts:
                continue
            kind = parts[0]
            if kind == "cwd" and len(parts) == 2:
                cwd = parts[1]
            elif kind == "available" and len(parts) == 2:
                available = parts[1] == "true"
            elif kind == "executable" and len(parts) == 2:
                executable = parts[1]
            elif kind == "path" and len(parts) == 2:
                binary_path = parts[1]
            elif kind == "version" and len(parts) == 2:
                version = parts[1]
            elif kind == "workspace" and len(parts) == 2:
                if parts[1] not in workspace_files:
                    workspace_files.append(parts[1])
            elif kind == "config" and len(parts) == 2:
                if parts[1] not in config_files:
                    config_files.append(parts[1])
            elif kind == "auth" and len(parts) == 3:
                auth[parts[1]] = parts[2]

        detections.append(
            {
                "requested_profile_id": requested_id,
                "resolved_profile_id": profile_payload["resolved_profile_id"],
                "profile": profile,
                "workdir": cwd or workdir,
                "available": available,
                "executable": executable,
                "path": binary_path,
                "version": version,
                "workspace_files": workspace_files,
                "config_files": config_files,
                "auth": auth,
                "supports_non_interactive": bool(profile.get("supports_non_interactive")),
                "supports_json_output": bool(profile.get("supports_json_output")),
            }
        )

    return {
        "host": host,
        "workdir": workdir,
        "detections": detections,
    }


def inspect_ai_workspace(
    config: SSHRuntimeConfig,
    *,
    host: str,
    path: str = ".",
    timeout_sec: int = 120,
) -> dict[str, Any]:
    script = "\n".join(
        [
            "set -e",
            f"cd {shlex.quote(path)}",
            "shopt -s nullglob globstar",
            _bash_array("markers", list(WORKSPACE_MARKERS)),
            _bash_array("instruction_patterns", list(COMMON_WORKSPACE_HINTS)),
            _bash_array("tools", [item["name"] for item in COMMON_REMOTE_TOOLS]),
            'printf "cwd\\t%s\\n" "$PWD"',
            'if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then',
            '  printf "git_repo\\ttrue\\n"',
            '  printf "git_root\\t%s\\n" "$(git rev-parse --show-toplevel)"',
            '  if git_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"; then printf "git_branch\\t%s\\n" "$git_branch"; fi',
            '  git_status="$(git status --short --branch 2>/dev/null || true)"',
            '  while IFS= read -r line; do',
            '    if [ -n "$line" ]; then printf "git_status\\t%s\\n" "$line"; fi',
            "  done <<< \"$git_status\"",
            "else",
            '  printf "git_repo\\tfalse\\n"',
            "fi",
            'for marker in "${markers[@]}"; do',
            '  if [ -e "$marker" ]; then printf "marker\\t%s\\n" "$marker"; fi',
            "done",
            'for pattern in "${instruction_patterns[@]}"; do',
            "  for match in $pattern; do",
            '    if [ -e "$match" ]; then printf "instruction\\t%s\\n" "$match"; fi',
            "  done",
            "done",
            'for tool in "${tools[@]}"; do',
            '  if command -v "$tool" >/dev/null 2>&1; then',
            '    printf "tool\\t%s\\t%s\\n" "$tool" "$(command -v "$tool")"',
            "  else",
            '    printf "missing_tool\\t%s\\n" "$tool"',
            "  fi",
            "done",
        ]
    )
    result = _run_remote_script(config, host=host, script=script, timeout_sec=timeout_sec)
    if not result["ok"]:
        raise SSHError(result["stderr"].strip() or result["stdout"].strip() or "workspace inspection failed")

    cwd = path
    git: dict[str, Any] = {"is_repo": False, "status": []}
    markers: list[str] = []
    instructions: list[str] = []
    available_tools: dict[str, dict[str, str]] = {}
    missing_tools: list[str] = []

    for raw_line in result["stdout"].splitlines():
        parts = raw_line.split("\t")
        if not parts:
            continue
        kind = parts[0]
        if kind == "cwd" and len(parts) == 2:
            cwd = parts[1]
        elif kind == "git_repo" and len(parts) == 2:
            git["is_repo"] = parts[1] == "true"
        elif kind == "git_root" and len(parts) == 2:
            git["root"] = parts[1]
        elif kind == "git_branch" and len(parts) == 2:
            git["branch"] = parts[1]
        elif kind == "git_status" and len(parts) == 2:
            git.setdefault("status", []).append(parts[1])
        elif kind == "marker" and len(parts) == 2 and parts[1] not in markers:
            markers.append(parts[1])
        elif kind == "instruction" and len(parts) == 2 and parts[1] not in instructions:
            instructions.append(parts[1])
        elif kind == "tool" and len(parts) == 3:
            available_tools[parts[1]] = {"name": parts[1], "path": parts[2]}
        elif kind == "missing_tool" and len(parts) == 2 and parts[1] not in missing_tools:
            missing_tools.append(parts[1])

    marker_set = set(markers)
    available_names = set(available_tools)
    suggestions: list[str] = []
    if "git" not in available_names:
        suggestions.append("Install or expose Git on the remote host so code rollback can stay Git-based instead of deleting files.")
    if "rg" not in available_names:
        suggestions.append("Install ripgrep (`rg`) for fast remote code search before handing the repo to an agent.")
    if {"pyproject.toml", "uv.lock"} & marker_set and "uv" not in available_names:
        suggestions.append("This workspace looks Python-first. Install uv or keep using ssh_uv_sync for remote dependency sync.")
    if {"environment.yml", "environment.yaml", "conda.yml"} & marker_set and "conda" not in available_names:
        suggestions.append("This repo includes a Conda manifest. Expose conda on the remote host so ssh_exec and batch jobs can activate the right env.")
    if "package.json" in marker_set and "node" not in available_names:
        suggestions.append("This repo includes package.json. Install Node.js so AI CLIs and frontend tooling can run remotely.")
    if "package.json" in marker_set and not {"npm", "pnpm", "yarn"} & available_names:
        suggestions.append("Expose at least one Node package manager (`npm`, `pnpm`, or `yarn`) on the remote host.")
    if "run.slurm" in marker_set and not {"sbatch", "squeue", "sinfo"} <= available_names:
        suggestions.append("This repo has a Slurm script, but the current host is missing one or more Slurm CLIs. Use a real Slurm login node for submit and monitor workflows.")
    if not instructions:
        suggestions.append("No shared agent instruction files were found. Adding AGENTS.md or tool-specific files like CLAUDE.md and GEMINI.md will make remote agents more consistent.")

    categorized_missing = []
    for item in COMMON_REMOTE_TOOLS:
        name = item["name"]
        if name in missing_tools:
            categorized_missing.append(
                {
                    "name": name,
                    "category": item["category"],
                    "recommendation": item["recommendation"],
                }
            )

    return {
        "host": host,
        "path": cwd,
        "git": git,
        "markers": markers,
        "instruction_files": instructions,
        "available_tools": [
            {
                "name": name,
                "path": payload["path"],
                "category": next((item["category"] for item in COMMON_REMOTE_TOOLS if item["name"] == name), "other"),
            }
            for name, payload in sorted(available_tools.items())
        ],
        "missing_tools": categorized_missing,
        "suggestions": suggestions,
    }


def run_ai_tool(
    config: SSHRuntimeConfig,
    *,
    host: str,
    profile_id: str,
    prompt: str,
    workdir: str = ".",
    mode: str = "analyze",
    output_format: str = "text",
    extra_args: list[str] | None = None,
    env: dict[str, str] | None = None,
    timeout_sec: int = 1800,
    conda_env_name: str | None = None,
    conda_env_prefix: str | None = None,
) -> dict[str, Any]:
    profile_payload = get_ai_tool_profile(profile_id)
    profile = profile_payload["profile"]
    variants = profile.get("run_variants") or {}
    if output_format not in variants:
        raise ValueError(f"ai tool profile does not support output_format={output_format}: {profile['id']}")
    if extra_args is not None and any(not isinstance(item, str) for item in extra_args):
        raise ValueError("extra_args must be a list of strings")

    effective_prompt = _expand_prompt(prompt, mode=mode)
    extra_args = extra_args or []
    attempts: list[dict[str, Any]] = []
    last_error: str | None = None

    for variant in variants[output_format]:
        argv = _render_argv(variant, prompt=effective_prompt, extra_args=extra_args)
        command = _command_preview(argv)
        result = exec_command(
            config,
            host=host,
            command=command,
            workdir=workdir,
            env=env,
            timeout_sec=timeout_sec,
            conda_env_name=conda_env_name,
            conda_env_prefix=conda_env_prefix,
        )
        attempts.append(
            {
                "argv": argv,
                "command": command,
                "exit_code": result["exit_code"],
            }
        )
        if result["ok"]:
            parsed_json = None
            if output_format in {"json", "stream-json"}:
                try:
                    if output_format == "json":
                        parsed_json = json.loads(result["stdout"]) if result["stdout"].strip() else None
                    else:
                        parsed_json = [
                            json.loads(line)
                            for line in result["stdout"].splitlines()
                            if line.strip()
                        ]
                except json.JSONDecodeError:
                    parsed_json = None
            return {
                "host": host,
                "profile": profile,
                "requested_profile_id": profile_id,
                "resolved_profile_id": profile_payload["resolved_profile_id"],
                "workdir": workdir,
                "mode": mode,
                "output_format": output_format,
                "effective_prompt": effective_prompt,
                "command": command,
                "attempts": attempts,
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "exit_code": result["exit_code"],
                "truncated": result["truncated"],
                "duration_ms": result["duration_ms"],
                "parsed_output": parsed_json,
                "conda_env_name": conda_env_name,
                "conda_env_prefix": conda_env_prefix,
            }
        last_error = result["stderr"].strip() or result["stdout"].strip() or f"command failed with exit code {result['exit_code']}"

    raise SSHError(last_error or f"failed to run ai tool {profile['id']}")
