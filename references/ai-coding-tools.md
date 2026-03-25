# AI Coding Tools

This file describes the AI coding add-on layer for `ssh-skill`.

## Purpose

- Keep SSH as the main abstraction.
- Treat remote coding agents as optional profiles layered on top of SSH, not as the whole identity of the skill.
- Make open-source extension easy: users can add support for another CLI by dropping a JSON profile into a local directory.

## Built-in profiles

- `claude_code`: resolves aliases `claude` and `claude-code`, expects the `claude` executable, and looks for `CLAUDE.md`.
- `gemini_cli`: resolves aliases `gemini` and `gemini-cli`, expects the `gemini` executable, and looks for `GEMINI.md`.
- `cursor_agent`: resolves aliases `cursor` and `cursor-agent`, expects the headless `cursor-agent` executable, and looks for `AGENTS.md`, `CLAUDE.md`, and `.cursor/rules/*.md`.
- `opencode`: expects the `opencode` executable, and looks for `AGENTS.md`, `opencode.json`, `opencode.jsonc`, `.opencode/agents/*.md`, and `.opencode/skills/*/SKILL.md`.

## Runtime tools

- `ssh_list_ai_tool_profiles`: list built-in and custom AI tool profiles.
- `ssh_get_ai_tool_profile`: resolve a profile by id or alias.
- `ssh_detect_ai_tools`: inspect the selected host for the CLI binary, version hint, auth env vars, and workspace files.
- `ssh_inspect_ai_workspace`: inspect the current project for agent instruction files, project markers, and missing helper tools.
- `ssh_run_ai_tool`: run a remote AI CLI in non-interactive mode through the selected profile.

## Default safety model

- Prefer `ssh_run_ai_tool(mode="analyze")` first. This prefixes the prompt with a read-only instruction block.
- Use `mode="execute"` only when the user explicitly wants the remote AI CLI to act.
- After any execution-oriented run, inspect the repo with direct SSH tools such as `git status`, `ssh_read_file`, `ssh_grep`, and `ssh_tail_file`.
- Keep delete operations blocked by the core SSH safety policy and prefer Git rollback.

## Common helper tools

When remote AI coding is important, these helper tools usually matter more than one more agent binary:

- `git`
- `rg`
- `jq`
- `python3`
- `uv`
- `conda`
- `node`, `npm`, `npx`
- `pnpm` or `yarn` when the repo uses them
- `nvidia-smi` for training hosts
- `sinfo`, `squeue`, `sacct`, `sbatch` for Slurm hosts

Use `ssh_inspect_ai_workspace` to see which of these are present or missing.

## Custom profile location

Add custom JSON files under:

```text
~/.codex/ssh-skill/ai_tool_profiles/
```

Each file should contain a single JSON object. The important fields are:

```json
{
  "id": "my_agent",
  "display_name": "My Agent",
  "aliases": ["my-agent"],
  "description": "Custom remote coding CLI.",
  "executables": ["my-agent"],
  "version_args": ["--version"],
  "workspace_files": ["AGENTS.md"],
  "workspace_globs": [".my-agent/rules/*.md"],
  "config_paths": ["$HOME/.config/my-agent/config.json"],
  "auth_envs": ["MY_AGENT_API_KEY"],
  "supports_non_interactive": true,
  "supports_json_output": true,
  "run_variants": {
    "text": [["my-agent", "run", "{prompt}"]],
    "json": [["my-agent", "run", "--format", "json", "{prompt}"]]
  }
}
```

## Current source alignment

These built-in profiles were shaped against the tools' official docs or upstream repos as of March 25, 2026:

- Claude Code overview: [code.claude.com/docs/en/overview](https://code.claude.com/docs/en/overview)
- Gemini CLI repo: [github.com/google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli)
- Cursor CLI docs: [docs.cursor.com/en/cli/using](https://docs.cursor.com/en/cli/using)
- OpenCode docs: [opencode.ai/docs](https://opencode.ai/docs/)
