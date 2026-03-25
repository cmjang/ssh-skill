# Cluster Manual Guidance

This file is intentionally sanitized for public release. Keep institution-specific hostnames, internal URLs, ports, storage roots, and queue policies in a private local note instead of committing them to the repository.

## What to keep private

- Internal documentation URLs
- Login aliases and SSH jump paths
- Site-specific ports
- Shared storage layouts
- Group or account naming conventions
- Any queue rules or service addresses that are not meant for public distribution

## What is safe to keep here

- General Slurm workflow guidance
- Generic scheduler safety rules
- Reminders to verify live docs before submitting jobs
- Pointers to use custom cluster profiles for institution-specific details

## Recommended private note format

If you need a local note outside Git, keep these sections:

- Login: host alias, port, MFA notes, jump-host notes
- Storage: home root, scratch roots, quota, shared paths
- Scheduler: partition names, account format, GPU request examples, default time limits
- Software: module rules, Conda policy, container support
- Publishing: allowed ports, service nodes, ingress or reverse-proxy rules

## Generic operational reminders

- Prefer `sbatch`, `srun`, `squeue`, `sinfo`, and `scancel` over ad-hoc long-running shell jobs.
- Do not run heavy workloads on login nodes.
- Use explicit time limits in job scripts.
- Prefer user-managed installs such as modules, Conda, `uv`, or source builds unless the site docs say otherwise.
- Re-check your institution's live docs before relying on queue names, resource limits, or account rules.
