# Cluster Profiles

This file describes the small, extensible cluster-profile module used by `ssh-skill`.

## Purpose

- Keep SSH as the main abstraction.
- Treat Slurm or cluster-specific rules as attachable profiles instead of hard-coding them into the whole skill.
- Make open-source extension easy: users can add support for other clusters without rewriting the runtime.

## Built-in profiles

- `generic_slurm`: generic fallback for any managed host marked as `cluster_mode=true` and `scheduler=slurm`
- `sist_ai_cluster`: ShanghaiTech SIST / 上科大 (`skd`) AI Cluster — a worked example of a login-to-debug jump-host Slurm cluster. Aliases: `skd`, `skd_ai_cluster`, `sist`, `shanghaitech`, `shanghaitech_sist`. See [sist-ai-cluster.md](sist-ai-cluster.md).

## Profile aliases

A profile may declare an `aliases` list. `ssh_get_cluster_profile` and the registry resolve aliases to the canonical id, so `--cluster-profile skd` and `provision-cluster skd` both resolve to `sist_ai_cluster`. The canonical id is what gets stored on the server record.

## Jump-host topology and `provision-cluster`

A profile can describe an access topology under an `access` block so the whole cluster can be registered in one step:

```json
"access": {
  "ssh_port": 22112,
  "login_nodes": ["10.15.89.191", "10.15.89.192", "10.15.89.41"],
  "debug_nodes": ["10.15.88.73", "10.15.88.74"],
  "debug_requires_login_jump": true,
  "default_login_jump_index": 0
}
```

Then:

```bash
python3 $SSH_SKILL_DIR/scripts/ssh_registry.py provision-cluster skd \
    --user <username> --identity-file ~/.ssh/<key>
```

registers each login node as a direct submit host (`role=login`, `cluster_mode=true`) and each debug node with `ProxyJump <first-login-alias>` and `role=debug`. Optional flags: `--dry-run`, `--prefix`, `--login-jump-index`, `--debug-port`.

## Roles and Slurm submission

The `role` field on a server record (`login`, `debug`, `compute`, ...) marks how a host is used. Hosts with `role=debug` or `role=compute` keep their cluster profile for software and policy context, but the runtime refuses `ssh_sbatch_submit` and `ssh_scancel` against them — submit from a login node.

## Binding a profile to a host

Store the profile id on the server record as `cluster_profile`.

Example:

```bash
python3 $SSH_SKILL_DIR/scripts/ssh_registry.py import-alias my-cluster --cluster-mode --scheduler slurm --cluster-profile my_lab_slurm
```

A `cluster_profile` can be bound regardless of `cluster_mode`; pair it with `--role debug` for nodes that should carry the profile but never submit jobs.

## Runtime tools

- `ssh_list_cluster_profiles`: list built-in and custom profiles
- `ssh_get_cluster_profile`: resolve a profile by id, alias, or managed host alias

## Custom profile location

Add custom JSON files under:

```text
$SSH_SKILL_HOME/cluster_profiles/
```

Each file should contain a single JSON object with at least:

```json
{
  "id": "my_lab_slurm",
  "extends": "generic_slurm",
  "display_name": "My Lab Slurm",
  "scheduler": "slurm",
  "description": "Cluster-specific notes for my lab.",
  "manual_urls": {
    "base": "https://cluster.example.edu/docs"
  },
  "slurm": {
    "account_rules": [
      "Use -A mygroup-normal for the normal queue."
    ]
  }
}
```

## Design notes

- Prefer `extends: "generic_slurm"` for most Slurm clusters so only the local differences need to be added.
- Bind the profile to a login-node alias that actually has Slurm CLI tools such as `sbatch`, `squeue`, and `sinfo` in PATH.
- Keep durable rules in the profile, and keep changing operational details in a private local manual or institution-specific reference that is not committed publicly.
- If a host is marked as Slurm but no explicit profile is bound, `ssh-skill` should fall back to `generic_slurm`.
