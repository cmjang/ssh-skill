# ssh-skill

An agent skill for operating SSH-accessible Linux hosts and clusters. It treats generic SSH work as
the primary capability — register servers, inspect and edit code, sync local↔remote, run commands,
manage `uv`/`conda` environments, debug and publish services — and layers cluster (Slurm) and remote
AI-coding-CLI support on top as optional, modular add-ons.

It works in both **Claude Code** and **Codex**. The skill instructions live in [`SKILL.md`](SKILL.md);
the runtime is a small stdio MCP server in [`scripts/ssh_skill_server.py`](scripts/ssh_skill_server.py).

## Features

- **Server registry** — remember hosts in `servers.json` and generate an SSH config that `Include`s
  your own `~/.ssh/config`, so personal and skill-managed aliases coexist.
- **Code & files over SSH** — list/find/grep/read/write/tail remote files without shell-quoting pain.
- **Sync & environments** — `rsync` a local tree (excluding `.venv`, caches, etc.), then build the
  remote env with `uv` or `conda` — the remote environment stays independent of your local one.
- **Remote AI coding CLIs** — detect and drive Claude Code, Gemini CLI, Cursor Agent, or OpenCode on
  the remote host, read-only by default.
- **Cluster profiles** — attach reusable Slurm/policy rules per host; extend with JSON, no code edits.
- **Jump-host clusters** — model "compute/debug nodes reachable only through a login node" as a
  first-class topology and provision the whole cluster (with `ProxyJump`) in one command.
- **Safe by default** — `rm`, `rsync --delete`, `scancel`, and job submission from debug nodes are
  blocked unless explicitly allowed.

## Repository layout

```
SKILL.md                      # skill instructions (YAML frontmatter: name + description)
README.md                     # this file
agents/openai.yaml            # Codex skill manifest
scripts/ssh_skill_server.py   # MCP runtime entrypoint (exposes the ssh_* tools)
scripts/ssh_registry.py       # server registry CLI
scripts/quick_validate.py     # release smoke test
scripts/ssh_skill/            # runtime package (registry, ssh ops, slurm ops, profiles, server)
references/                   # deeper docs, loaded on demand
  ssh-runtime.md              #   runtime contract & registry fields
  cluster-profiles.md         #   cluster-profile system, aliases, roles, provisioning
  ai-coding-tools.md          #   AI tool profiles & helper tools
  cluster-manual.md           #   generic cluster guidance
  sist-ai-cluster.md          #   ShanghaiTech SIST / 上科大 (skd) AI Cluster, worked example
  legacy-transport.md         #   historical note
```

## Requirements

- Python 3.10+ (standard library only — no third-party Python dependencies).
- `ssh`, `scp`, and `rsync` clients on the **local** machine.

## Install

`<repo>` below is wherever you cloned this repository.

### Claude Code

1. Make the skill discoverable — copy or symlink it into your skills dir:
   ```bash
   ln -s <repo> ~/.claude/skills/ssh-skill
   ```
2. Register the runtime as an MCP server:
   ```bash
   claude mcp add ssh -- python3 ~/.claude/skills/ssh-skill/scripts/ssh_skill_server.py
   ```

### Codex

1. Copy or symlink the skill into your Codex skills dir:
   ```bash
   ln -s <repo> ~/.codex/skills/ssh-skill
   ```
2. Register the same runtime as an MCP server named `ssh` in your Codex config.
   [`agents/openai.yaml`](agents/openai.yaml) declares the `ssh` MCP dependency; point it at:
   ```
   python3 ~/.codex/skills/ssh-skill/scripts/ssh_skill_server.py
   ```

## State directory

Per-user state (`servers.json`, `managed_ssh_config`, `cluster_profiles/`, `ai_tool_profiles/`)
lives in `$SSH_SKILL_HOME`, resolved as:

1. `SSH_SKILL_HOME` if set (explicit, agent-agnostic);
2. else the host agent's config dir + `/ssh-skill` — `$CODEX_HOME/ssh-skill` (Codex) or
   `$CLAUDE_CONFIG_DIR/ssh-skill` (Claude Code), falling back to `~/.codex` or `~/.claude` if it
   already exists;
3. else `~/.codex/ssh-skill`.

The generated `managed_ssh_config` `Include`s your existing `~/.ssh/config`, so personal and
skill-managed aliases both keep working.

## Core concepts

### Server registry (`scripts/ssh_registry.py`)

A managed server record carries: `alias`, `host`, `user`, `port`, `identity_file`, `proxy_jump`,
`description`, `tags`, `default_workdir`, `shell`, `cluster_mode`, `scheduler`, `cluster_profile`,
`role`, and `notes`.

```bash
# In examples below, SSH_SKILL_DIR is this skill's directory:
SSH_SKILL_DIR=~/.claude/skills/ssh-skill        # or ~/.codex/skills/ssh-skill
REG="python3 $SSH_SKILL_DIR/scripts/ssh_registry.py"

$REG list                                        # list managed servers
$REG add box --host 10.0.0.12 --user ubuntu --identity-file ~/.ssh/id_ed25519
$REG import-alias my-host                         # import an existing ~/.ssh/config alias
$REG update box --description "prod api" --tags prod api
$REG show box
$REG remove box
$REG render                                       # regenerate managed_ssh_config
```

### Cluster profiles

Slurm/policy rules are attachable profiles, not hard-coded behavior. Built-in profiles:

- `generic_slurm` — generic fallback for any host marked `cluster_mode=true`, `scheduler=slurm`.
- `sist_ai_cluster` — ShanghaiTech SIST / 上科大 (`skd`) AI Cluster, a worked jump-host example
  (aliases: `skd`, `skd_ai_cluster`, `sist`, `shanghaitech`, `shanghaitech_sist`). See
  [`references/sist-ai-cluster.md`](references/sist-ai-cluster.md).

Add other sites as JSON files under `$SSH_SKILL_HOME/cluster_profiles/` (set `extends:
"generic_slurm"` to inherit the basics). A profile may declare `aliases` and an `access` block
(login/debug nodes, port, jump rule) that powers one-command provisioning. See
[`references/cluster-profiles.md`](references/cluster-profiles.md).

The `role` field (`login` / `debug` / `compute`) marks how a host is used. Hosts with `role=debug`
or `role=compute` keep their profile for context, but the runtime refuses to submit or cancel Slurm
jobs from them — submit from a login node.

### AI tool profiles

Built-in profiles for `claude_code`, `gemini_cli`, `cursor_agent`, and `opencode` (resolved by
aliases like `claude`, `gemini`, `cursor`). Add more as JSON under `$SSH_SKILL_HOME/ai_tool_profiles/`.
See [`references/ai-coding-tools.md`](references/ai-coding-tools.md).

## Runtime tools

The MCP server exposes:

| Group | Tools |
| --- | --- |
| Hosts & profiles | `ssh_list_hosts`, `ssh_list_cluster_profiles`, `ssh_get_cluster_profile`, `ssh_list_ai_tool_profiles`, `ssh_get_ai_tool_profile` |
| Exec & files | `ssh_exec`, `ssh_list_dir`, `ssh_find_files`, `ssh_grep`, `ssh_read_file`, `ssh_write_file`, `ssh_tail_file` |
| Transfer & env | `ssh_upload`, `ssh_download`, `ssh_sync_dir`, `ssh_list_conda_envs`, `ssh_uv_sync` |
| Processes & ports | `ssh_start_process`, `ssh_check_process`, `ssh_stop_process`, `ssh_check_port` |
| Remote AI CLIs | `ssh_detect_ai_tools`, `ssh_inspect_ai_workspace`, `ssh_run_ai_tool` |
| Slurm | `ssh_sinfo`, `ssh_squeue`, `ssh_sacct`, `ssh_slurm_job_info`, `ssh_render_slurm_script`, `ssh_sbatch_submit`, `ssh_scancel` |

## Common workflows

**Local code → remote Slurm training**

```
ssh_sync_dir → ssh_uv_sync (or ssh_list_conda_envs) → ssh_sinfo
  → ssh_render_slurm_script → ssh_sbatch_submit → ssh_squeue → ssh_tail_file → ssh_sacct
```

**Remote AI-assisted inspection**

```
ssh_detect_ai_tools → ssh_inspect_ai_workspace → ssh_find_files / ssh_grep
  → ssh_run_ai_tool(mode="analyze")   # read-only by default
```

**Provision a jump-host cluster (one command)**

```bash
# Registers sist-login1..3 (direct submit hosts) and sist-debug1..2
# (ProxyJump through sist-login1, role=debug). Add --dry-run to preview.
python3 $SSH_SKILL_DIR/scripts/ssh_registry.py provision-cluster skd \
    --user <username> --identity-file ~/.ssh/<key>
```

After that, `ssh sist-debug1` — and every tool with `host="sist-debug1"` — transparently hops
through a login node. Submit jobs against a login alias (`sist-login1`).

## Safety policy

- `rm` / `rm -rf` are blocked inside `ssh_exec` and `ssh_start_process` unless
  `allow_destructive=true`.
- `ssh_sync_dir(delete=true)` is blocked unless `allow_destructive=true`.
- `ssh_scancel` is blocked unless `allow_cancel=true`.
- Slurm job submission/cancellation is refused on `role=debug` / `role=compute` hosts.
- `ssh_run_ai_tool` defaults to read-only `analyze` mode; switch to `execute` only on explicit request.

Prefer Git-based rollback over deleting files.

## Validate

```bash
python3 scripts/quick_validate.py
```

Checks built-in AI and cluster profiles, alias resolution, the jump-host provision dry-run, and that
the runtime registers its expected tools.
