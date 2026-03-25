from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .paths import ai_tool_profiles_path, ensure_ai_tool_profiles_home


BUILTIN_AI_TOOL_PROFILES: dict[str, dict[str, Any]] = {
    "claude_code": {
        "id": "claude_code",
        "display_name": "Claude Code",
        "aliases": ["claude", "claude-code"],
        "description": "Anthropic's terminal coding agent. Best for project-aware coding, review, and scripted prompts through the `claude` CLI.",
        "docs_url": "https://code.claude.com/docs/en/overview",
        "executables": ["claude"],
        "version_args": ["--version"],
        "workspace_files": ["CLAUDE.md"],
        "workspace_globs": [],
        "config_paths": ["$HOME/.claude/settings.json"],
        "auth_envs": ["ANTHROPIC_API_KEY"],
        "supports_non_interactive": True,
        "supports_json_output": False,
        "run_variants": {
            "text": [["claude", "-p", "{prompt}"]],
        },
        "notes": [
            "Claude Code reads CLAUDE.md at project start.",
            "Use text output mode here because the official overview documents `claude -p` but not a stable JSON mode.",
        ],
    },
    "gemini_cli": {
        "id": "gemini_cli",
        "display_name": "Gemini CLI",
        "aliases": ["gemini", "gemini-cli"],
        "description": "Google's terminal coding agent with headless scripting support and project context via GEMINI.md.",
        "docs_url": "https://github.com/google-gemini/gemini-cli",
        "executables": ["gemini"],
        "version_args": ["--version"],
        "workspace_files": ["GEMINI.md"],
        "workspace_globs": [],
        "config_paths": ["$HOME/.gemini/settings.json", "$HOME/.gemini/oauth_creds.json"],
        "auth_envs": [
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_GENAI_USE_VERTEXAI",
        ],
        "supports_non_interactive": True,
        "supports_json_output": True,
        "run_variants": {
            "text": [["gemini", "-p", "{prompt}"]],
            "json": [["gemini", "-p", "{prompt}", "--output-format", "json"]],
            "stream-json": [["gemini", "-p", "{prompt}", "--output-format", "stream-json"]],
        },
        "notes": [
            "Gemini CLI supports non-interactive scripting with `-p` and structured output via `--output-format`.",
            "The remote host can authenticate through browser login, Gemini API key, or Vertex AI env vars.",
        ],
    },
    "cursor_agent": {
        "id": "cursor_agent",
        "display_name": "Cursor Agent CLI",
        "aliases": ["cursor", "cursor-agent"],
        "description": "Cursor's headless agent CLI. This profile targets the `cursor-agent` terminal interface rather than the GUI editor.",
        "docs_url": "https://docs.cursor.com/en/cli/using",
        "executables": ["cursor-agent"],
        "version_args": ["--version"],
        "workspace_files": ["AGENTS.md", "CLAUDE.md"],
        "workspace_globs": [".cursor/rules/*.md"],
        "config_paths": [],
        "auth_envs": ["CURSOR_API_KEY"],
        "supports_non_interactive": True,
        "supports_json_output": True,
        "run_variants": {
            "text": [["cursor-agent", "--print", "--output-format", "text", "{prompt}"]],
            "json": [["cursor-agent", "--print", "--output-format", "json", "{prompt}"]],
            "stream-json": [["cursor-agent", "--print", "--output-format", "stream-json", "{prompt}"]],
        },
        "notes": [
            "Cursor CLI reads AGENTS.md and CLAUDE.md alongside .cursor/rules.",
            "Cursor has full write access in non-interactive mode, so ssh-skill should default to read-only prompting unless execution was explicitly requested.",
        ],
    },
    "opencode": {
        "id": "opencode",
        "display_name": "OpenCode",
        "aliases": ["open-code"],
        "description": "OpenCode terminal agent with JSON config, project AGENTS.md bootstrapping, reusable agents, and non-interactive `opencode run` support.",
        "docs_url": "https://opencode.ai/docs/",
        "executables": ["opencode"],
        "version_args": ["--version"],
        "workspace_files": ["AGENTS.md", "opencode.json", "opencode.jsonc"],
        "workspace_globs": [".opencode/agents/*.md", ".opencode/skills/*/SKILL.md"],
        "config_paths": ["$HOME/.config/opencode/opencode.json"],
        "auth_envs": ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "supports_non_interactive": True,
        "supports_json_output": True,
        "run_variants": {
            "text": [["opencode", "run", "{prompt}"]],
            "json": [["opencode", "run", "--format", "json", "{prompt}"]],
        },
        "notes": [
            "OpenCode can initialize a repo by creating AGENTS.md in the project root.",
            "OpenCode exposes a dedicated plan mode and permission system, but ssh-skill keeps the default integration generic and transport-agnostic.",
        ],
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


def _normalize_string_list(value: Any, *, field: str, source: str, profile_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"ai tool profile {profile_id} from {source} has invalid {field}")
    return [item.strip() for item in value]


def _normalize_run_variants(value: Any, *, source: str, profile_id: str) -> dict[str, list[list[str]]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"ai tool profile {profile_id} from {source} has invalid run_variants")
    normalized: dict[str, list[list[str]]] = {}
    for output_format, variants in value.items():
        if not isinstance(output_format, str) or not output_format.strip():
            raise ValueError(f"ai tool profile {profile_id} from {source} has invalid run_variants key")
        if not isinstance(variants, list) or not variants:
            raise ValueError(f"ai tool profile {profile_id} from {source} has invalid run_variants[{output_format}]")
        normalized_variants: list[list[str]] = []
        for variant in variants:
            if not isinstance(variant, list) or any(not isinstance(token, str) or not token for token in variant):
                raise ValueError(f"ai tool profile {profile_id} from {source} has invalid argv template for {output_format}")
            normalized_variants.append(list(variant))
        normalized[output_format.strip()] = normalized_variants
    return normalized


def _normalize_profile(raw: dict[str, Any], *, source: str) -> dict[str, Any]:
    profile = deepcopy(raw)
    profile_id = profile.get("id")
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise ValueError(f"ai tool profile from {source} is missing a valid id")
    profile_id = profile_id.strip()
    profile["id"] = profile_id
    if "display_name" in profile and not isinstance(profile["display_name"], str):
        raise ValueError(f"ai tool profile {profile_id} from {source} has invalid display_name")
    if "description" in profile and not isinstance(profile["description"], str):
        raise ValueError(f"ai tool profile {profile_id} from {source} has invalid description")
    if "docs_url" in profile and profile["docs_url"] is not None and not isinstance(profile["docs_url"], str):
        raise ValueError(f"ai tool profile {profile_id} from {source} has invalid docs_url")
    profile["aliases"] = _normalize_string_list(profile.get("aliases"), field="aliases", source=source, profile_id=profile_id)
    profile["executables"] = _normalize_string_list(
        profile.get("executables"),
        field="executables",
        source=source,
        profile_id=profile_id,
    )
    profile["version_args"] = _normalize_string_list(
        profile.get("version_args") or ["--version"],
        field="version_args",
        source=source,
        profile_id=profile_id,
    )
    profile["workspace_files"] = _normalize_string_list(
        profile.get("workspace_files"),
        field="workspace_files",
        source=source,
        profile_id=profile_id,
    )
    profile["workspace_globs"] = _normalize_string_list(
        profile.get("workspace_globs"),
        field="workspace_globs",
        source=source,
        profile_id=profile_id,
    )
    profile["config_paths"] = _normalize_string_list(
        profile.get("config_paths"),
        field="config_paths",
        source=source,
        profile_id=profile_id,
    )
    profile["auth_envs"] = _normalize_string_list(
        profile.get("auth_envs"),
        field="auth_envs",
        source=source,
        profile_id=profile_id,
    )
    profile["notes"] = _normalize_string_list(profile.get("notes"), field="notes", source=source, profile_id=profile_id)
    profile["run_variants"] = _normalize_run_variants(profile.get("run_variants"), source=source, profile_id=profile_id)
    profile["supports_non_interactive"] = bool(profile.get("supports_non_interactive", bool(profile["run_variants"])))
    profile["supports_json_output"] = bool(profile.get("supports_json_output", "json" in profile["run_variants"]))
    profile["source"] = source
    return profile


def _load_custom_profiles() -> dict[str, dict[str, Any]]:
    directory = ensure_ai_tool_profiles_home()
    profiles: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"failed to load ai tool profile {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"ai tool profile file must contain a JSON object: {path}")
        profile = _normalize_profile(raw, source=str(path))
        profiles[profile["id"]] = profile
    return profiles


def _all_profiles_raw() -> dict[str, dict[str, Any]]:
    profiles = {
        profile_id: _normalize_profile(raw, source=f"builtin:{profile_id}")
        for profile_id, raw in BUILTIN_AI_TOOL_PROFILES.items()
    }
    profiles.update(_load_custom_profiles())
    return profiles


def _resolve_profile(profile_id: str, *, profiles: dict[str, dict[str, Any]] | None = None, stack: tuple[str, ...] = ()) -> dict[str, Any]:
    all_profiles = profiles or _all_profiles_raw()
    if profile_id not in all_profiles:
        raise ValueError(f"unknown ai tool profile: {profile_id}")
    if profile_id in stack:
        raise ValueError(f"ai tool profile inheritance cycle detected: {' -> '.join(stack + (profile_id,))}")
    profile = deepcopy(all_profiles[profile_id])
    parent_id = profile.get("extends")
    if parent_id:
        if not isinstance(parent_id, str) or not parent_id.strip():
            raise ValueError(f"ai tool profile {profile_id} has invalid extends value")
        parent = _resolve_profile(parent_id.strip(), profiles=all_profiles, stack=stack + (profile_id,))
        profile = _deep_merge(parent, profile)
    profile["id"] = profile_id
    return profile


def _alias_lookup(profiles: dict[str, dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for profile_id in sorted(profiles):
        profile = _resolve_profile(profile_id, profiles=profiles)
        for key in [profile_id, *profile.get("aliases", [])]:
            normalized = key.strip().lower()
            if normalized in lookup and lookup[normalized] != profile_id:
                raise ValueError(f"duplicate ai tool alias detected: {key}")
            lookup[normalized] = profile_id
    return lookup


def list_ai_tool_profiles(*, supports_json_output: bool | None = None) -> dict[str, Any]:
    raw_profiles = _all_profiles_raw()
    profiles = []
    for profile_id in sorted(raw_profiles):
        profile = _resolve_profile(profile_id, profiles=raw_profiles)
        if supports_json_output is not None and bool(profile.get("supports_json_output")) != supports_json_output:
            continue
        profiles.append(profile)
    return {
        "profiles": profiles,
        "custom_profile_dir": str(ai_tool_profiles_path()),
    }


def resolve_ai_tool_profile(profile_id: str) -> tuple[dict[str, Any], str]:
    raw_profiles = _all_profiles_raw()
    lookup = _alias_lookup(raw_profiles)
    key = profile_id.strip().lower()
    if key not in lookup:
        raise ValueError(f"unknown ai tool profile: {profile_id}")
    resolved_id = lookup[key]
    return _resolve_profile(resolved_id, profiles=raw_profiles), resolved_id


def get_ai_tool_profile(profile_id: str) -> dict[str, Any]:
    profile, resolved_id = resolve_ai_tool_profile(profile_id)
    return {
        "profile": profile,
        "requested_profile_id": profile_id,
        "resolved_profile_id": resolved_id,
        "custom_profile_dir": str(ai_tool_profiles_path()),
    }
