# Legacy Transport Reference

This file keeps a small note about the older Go transport that originally inspired this skill. The current public identity is `ssh-skill`, with a Python-first runtime and SSH as the main abstraction.

## Historical note

- The earlier transport was a standalone Go server commonly referred to as `ssh-mcp`.
- It used stdio transport and executed remote commands through `bash -lc`.
- It supported command execution plus file upload and download.
- The current skill keeps that practical SSH behavior, but layers in server registry support, remote file inspection, AI coding tool profiles, Conda and `uv` helpers, and optional Slurm workflows.

## Why this file still exists

- It explains older references users may still have in private notes or configs.
- It helps compare the new Python runtime against the previous Go-based transport.
- It should not be treated as the primary naming or workflow for this repository.

## Current direction

- Use `ssh-skill` as the main name.
- Keep cluster support as an add-on, not the product identity.
- Keep remote AI coding CLIs and cluster profiles modular so the repository stays open-source friendly.
