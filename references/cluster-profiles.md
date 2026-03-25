# Cluster Profiles

This file describes the small, extensible cluster-profile module used by `ssh-skill`.

## Purpose

- Keep SSH as the main abstraction.
- Treat Slurm or cluster-specific rules as attachable profiles instead of hard-coding them into the whole skill.
- Make open-source extension easy: users can add support for other clusters without rewriting the runtime.

## Built-in profiles

- `generic_slurm`: generic fallback for any managed host marked as `cluster_mode=true` and `scheduler=slurm`

## Binding a profile to a host

Store the profile id on the server record as `cluster_profile`.

Example:

```bash
python3 ~/.codex/skills/ssh-skill/scripts/ssh_registry.py import-alias my-cluster --cluster-mode --scheduler slurm --cluster-profile my_lab_slurm
```

## Runtime tools

- `ssh_list_cluster_profiles`: list built-in and custom profiles
- `ssh_get_cluster_profile`: resolve a profile by id or by managed host alias

## Custom profile location

Add custom JSON files under:

```text
~/.codex/ssh-skill/cluster_profiles/
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
