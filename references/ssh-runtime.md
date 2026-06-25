# SSH Runtime Reference

This file describes the preferred runtime shape for `ssh-skill`.

## Identity

- The user-facing concept is `ssh-skill`, not a standalone product with a separate name.
- SSH is the primary capability.
- Slurm or cluster behavior is an optional add-on for servers marked as cluster-oriented.

## Managed server state

`ssh-skill` should remember managed servers locally. `$SSH_SKILL_HOME` is the per-user state directory; it defaults to the host agent's config dir plus `/ssh-skill` (`~/.codex/ssh-skill` under Codex via `CODEX_HOME`, `~/.claude/ssh-skill` under Claude Code via `CLAUDE_CONFIG_DIR`) and can be overridden by the `SSH_SKILL_HOME` environment variable.

- Registry file: `$SSH_SKILL_HOME/servers.json`
- Generated SSH config: `$SSH_SKILL_HOME/managed_ssh_config`
- Base SSH config: `~/.ssh/config`

The generated config should include the user's base SSH config so both personal aliases and skill-managed aliases are available.

Cluster-specific behavior should be attached through a reusable `cluster_profile` field on the server record. Built-in profiles ship with the skill, and user-defined profiles can be added under `$SSH_SKILL_HOME/cluster_profiles/`.

Remote AI coding tools should stay modular too. Built-in profiles ship with the skill for OpenCode, Claude Code, Gemini CLI, and Cursor Agent CLI, and user-defined profiles can be added under `$SSH_SKILL_HOME/ai_tool_profiles/`.

## Preferred scripts

- `scripts/ssh_registry.py`: add, update, remove, show, list, and render managed servers
- `scripts/ssh_skill_server.py`: runtime entrypoint that exposes SSH tools to the host agent (Claude Code or Codex)
- `scripts/quick_validate.py`: lightweight release smoke test for built-in profiles, alias resolution, and runtime tool registration

## Preferred tool contract

The runtime should expose:

- `ssh_list_hosts`
- `ssh_list_cluster_profiles`
- `ssh_get_cluster_profile`
- `ssh_list_ai_tool_profiles`
- `ssh_get_ai_tool_profile`
- `ssh_detect_ai_tools`
- `ssh_inspect_ai_workspace`
- `ssh_run_ai_tool`
- `ssh_sinfo`
- `ssh_squeue`
- `ssh_sacct`
- `ssh_slurm_job_info`
- `ssh_render_slurm_script`
- `ssh_sbatch_submit`
- `ssh_scancel`
- `ssh_exec`
- `ssh_upload`
- `ssh_download`
- `ssh_sync_dir`
- `ssh_list_conda_envs`
- `ssh_uv_sync`
- `ssh_list_dir`
- `ssh_find_files`
- `ssh_grep`
- `ssh_read_file`
- `ssh_write_file`
- `ssh_tail_file`
- `ssh_start_process`
- `ssh_check_process`
- `ssh_stop_process`
- `ssh_check_port`

## Registry fields

Each managed server can store:

- `alias`
- `host`
- `user`
- `port`
- `identity_file`
- `proxy_jump`
- `description`
- `tags`
- `default_workdir`
- `shell`
- `cluster_mode`
- `scheduler`
- `cluster_profile`
- `role`
- `notes`

`role` marks how a host is used (`login`, `debug`, `compute`, ...). Hosts with `role=debug` or `role=compute` keep their cluster profile for context but are blocked from Slurm job submission and cancellation; submit from a `login` node.

This keeps SSH generic while still allowing cluster-specific behavior to be attached to selected servers.

## Operating guidance

- Prefer adding frequently used servers to the registry instead of relying only on raw IPs in prompts.
- Prefer `cluster_mode=true` and `scheduler=slurm` only for actual cluster targets.
- Prefer `cluster_profile=<profile_id>` for recurring clusters so host-specific queue, storage, and policy rules stay modular.
- Prefer `ssh_read_file` and `ssh_list_dir` for code inspection.
- Prefer `ssh_find_files` and `ssh_grep` for remote codebase search instead of hand-writing long shell pipelines.
- Prefer `ssh_write_file` for small config changes and `ssh_upload` for larger assets.
- Prefer `ssh_sync_dir` when the user has a local working tree that should be pushed to the remote machine before running or debugging.
- Prefer `ssh_get_cluster_profile` before assuming Slurm account or partition rules on a managed cluster host.
- Prefer `ssh_detect_ai_tools` before assuming a remote AI CLI exists or is authenticated on the selected host.
- Prefer `ssh_inspect_ai_workspace` before invoking a remote coding CLI so missing helper tools and instruction files are visible.
- Prefer `ssh_run_ai_tool` in `mode="analyze"` for read-only remote AI help, and keep direct SSH edits as the default for deterministic changes.
- Prefer `ssh_sinfo` before choosing partitions, GPU counts, or 8-card requests.
- Prefer `ssh_render_slurm_script` plus `ssh_sbatch_submit` for the local-code to remote-training workflow.
- Prefer `ssh_squeue`, `ssh_sacct`, and `ssh_slurm_job_info` for structured monitoring instead of parsing raw shell output every time.
- Prefer `ssh_list_conda_envs` when the remote host may use Conda, then pass `conda_env_name` or `conda_env_prefix` into `ssh_exec` or `ssh_start_process`.
- Prefer `ssh_uv_sync` after syncing a `uv`-managed project so the remote `.venv` stays independent from any local virtual environment.
- Prefer `ssh_tail_file` for service and training logs.
- Prefer `ssh_start_process`, `ssh_check_process`, `ssh_stop_process`, and `ssh_check_port` for service-style debug runs.

## Extensible cluster profiles

- Built-in profiles currently include `generic_slurm` and `sist_ai_cluster` (the ShanghaiTech SIST / `skd` AI Cluster, a worked jump-host example).
- `ssh_list_cluster_profiles` should show both built-in and custom profiles.
- `ssh_get_cluster_profile(host="<alias>")` should resolve the bound profile for a managed server, with a fallback to `generic_slurm` when a host is marked as Slurm but has no specific profile.
- Profiles may declare an `aliases` list (e.g. `skd` -> `sist_ai_cluster`) and an `access` block describing login/debug nodes and the jump rule.
- The registry `provision-cluster <profile>` command reads that `access` block and registers all login and debug hosts with the correct `ProxyJump` and `role` in one step.
- Custom profiles should live as JSON files under `$SSH_SKILL_HOME/cluster_profiles/` so open-source users can add support for other clusters without rewriting the whole skill.

## Extensible AI tool profiles

- Built-in profiles currently include `claude_code`, `gemini_cli`, `cursor_agent`, and `opencode`.
- `ssh_list_ai_tool_profiles` should show both built-in and custom profiles.
- `ssh_get_ai_tool_profile(profile_id="<id-or-alias>")` should resolve aliases such as `claude`, `gemini`, `cursor`, or `cursor-agent`.
- `ssh_detect_ai_tools` should report executable availability, version hints, auth-env presence, and workspace instruction files.
- `ssh_inspect_ai_workspace` should report common helper tools that matter for remote coding, such as Git, ripgrep, Python, Node.js, uv, conda, and Slurm CLIs.
- Custom profiles should live as JSON files under `$SSH_SKILL_HOME/ai_tool_profiles/` so open-source users can add support for other remote coding agents without changing the whole runtime.

## Training workflow helpers

- `ssh_render_slurm_script` should make it easy to generate a clean `run.slurm` for common cases such as single-node multi-GPU training.
- `ssh_sbatch_submit` should accept either an existing remote script path or inline script content, and should parse the job id on successful submission.
- `ssh_scancel` should be blocked by default unless cancellation was explicitly requested by the user.

## Local and remote env separation

- `ssh_sync_dir` should exclude local virtual environments and caches by default, including `.venv`, `venv`, `__pycache__`, `.mypy_cache`, `.pytest_cache`, and `node_modules`.
- `ssh_uv_sync` should run on the remote host after sync, usually with `env_dir=.venv`, so dependency resolution and interpreter state remain remote-specific.
- Conda environments should also stay remote-specific; use the remote env name or prefix instead of trying to transfer a local Conda installation.
- Do not copy a local `.venv` to the remote machine unless the user explicitly asks for that unusual workflow.
- Remote AI tool installs should also stay remote-specific. Detect or install them on the remote host independently from any local CLI setup.

## Destructive action policy

- `ssh_exec` and `ssh_start_process` should block `rm` and `rm -rf` by default.
- `ssh_sync_dir(delete=true)` should also be blocked by default because it can remove remote files.
- Only allow destructive deletion when the user explicitly asked for it, and prefer Git-based rollback for code changes whenever possible.
- `ssh_run_ai_tool` should default to a read-only prompt mode and should not be treated as implicit permission to let a remote agent edit files.
