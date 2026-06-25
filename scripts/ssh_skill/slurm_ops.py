from __future__ import annotations

import re
import shlex
import uuid
from pathlib import PurePosixPath
from typing import Any

from .cluster_profiles import get_cluster_profile_for_host
from .ssh_ops import (
    SSHError,
    SSHRuntimeConfig,
    _clamp_max_results,
    _run_ssh,
    _safe_decode,
    write_file,
)


SBATCH_JOB_ID_PATTERN = re.compile(r"(?P<job_id>\d+)")


NON_SUBMIT_ROLES = {"debug", "compute"}


def _ensure_slurm_host(host: str) -> dict[str, Any]:
    payload = get_cluster_profile_for_host(host=host)
    profile = payload.get("profile")
    if not profile or profile.get("scheduler") != "slurm":
        raise SSHError(f"managed host is not configured as a Slurm cluster: {host}")
    return payload


def _ensure_submit_host(host: str) -> dict[str, Any]:
    payload = _ensure_slurm_host(host)
    server = payload.get("server") or {}
    role = str(server.get("role") or "").strip().lower()
    if role in NON_SUBMIT_ROLES:
        raise SSHError(
            f"host '{host}' has role={role}; submitting or canceling Slurm jobs from {role} nodes is not allowed. "
            f"Submit from a login node instead (a managed host with role=login, e.g. <prefix>-login1)."
        )
    return payload


def _run_slurm_remote(
    config: SSHRuntimeConfig,
    *,
    host: str,
    script: str,
    timeout_sec: int,
) -> dict[str, Any]:
    completed = _run_ssh(config, host, f"bash -lc {shlex.quote(script)}", timeout=timeout_sec)
    stdout, stdout_trunc = _safe_decode(completed.stdout, config.max_output_bytes)
    stderr, stderr_trunc = _safe_decode(completed.stderr, config.max_output_bytes)
    if completed.returncode != 0:
        raise SSHError(stderr.strip() or stdout.strip() or "Slurm command failed")
    return {
        "stdout": stdout,
        "stderr": stderr,
        "truncated": stdout_trunc or stderr_trunc,
    }


def _require_commands_prelude(*commands: str) -> str:
    lines = ["set -e"]
    for command in commands:
        lines.append(
            f'if ! command -v {shlex.quote(command)} >/dev/null 2>&1; then echo "{command} is not installed on remote host" >&2; exit 17; fi'
        )
    return "\n".join(lines)


def _sacct_prelude() -> str:
    return "\n".join(
        [
            "set -e",
            'if ! command -v sacct >/dev/null 2>&1; then echo "sacct is not installed on remote host" >&2; exit 17; fi',
        ]
    )


def _scontrol_prelude() -> str:
    return "\n".join(
        [
            "set -e",
            'if ! command -v scontrol >/dev/null 2>&1; then echo "scontrol is not installed on remote host" >&2; exit 17; fi',
        ]
    )


def _render_conda_activation_lines(
    *,
    conda_env_name: str | None,
    conda_env_prefix: str | None,
) -> list[str]:
    if conda_env_name and conda_env_prefix:
        raise SSHError("set either conda_env_name or conda_env_prefix, not both")
    if not conda_env_name and not conda_env_prefix:
        return []
    target = conda_env_name or conda_env_prefix or ""
    return [
        'conda_base="$(conda info --base)"',
        'source "$conda_base/etc/profile.d/conda.sh"',
        f"conda activate {shlex.quote(target)}",
    ]


def _parse_sbatch_directives(script_content: str) -> dict[str, str]:
    directives: dict[str, str] = {}
    for raw_line in script_content.splitlines():
        line = raw_line.strip()
        if not line.startswith("#SBATCH"):
            continue
        body = line[len("#SBATCH") :].strip()
        if not body:
            continue
        try:
            tokens = shlex.split(body)
        except ValueError:
            tokens = body.split()
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.startswith("--") and "=" in token:
                key, value = token.split("=", 1)
                directives[key] = value
            elif token.startswith("--"):
                if index + 1 < len(tokens):
                    directives[token] = tokens[index + 1]
                    index += 1
            elif token.startswith("-") and len(token) > 2:
                directives[token[:2]] = token[2:]
            elif token.startswith("-"):
                if index + 1 < len(tokens):
                    directives[token] = tokens[index + 1]
                    index += 1
            index += 1
    return directives


def _best_effort_log_path(
    *,
    directives: dict[str, str],
    workdir: str | None,
    job_id: str | None,
    job_name: str | None,
) -> str | None:
    template = directives.get("--output") or directives.get("-o")
    if not template:
        return None
    resolved = template
    if workdir and not resolved.startswith("/"):
        resolved = str(PurePosixPath(workdir) / resolved)
    if job_id:
        resolved = resolved.replace("%j", job_id)
    if job_name:
        resolved = resolved.replace("%x", job_name)
    return resolved


def list_partitions(
    config: SSHRuntimeConfig,
    *,
    host: str,
    partition: str | None = None,
    states: list[str] | None = None,
) -> dict[str, Any]:
    profile_payload = _ensure_slurm_host(host)
    args = ["sinfo", "-h", "-o", "%P\t%a\t%l\t%D\t%t\t%G\t%m\t%c\t%N"]
    if partition:
        args.extend(["-p", partition])
    if states:
        args.extend(["-t", ",".join(states)])
    script = "\n".join([_require_commands_prelude("sinfo"), " ".join(shlex.quote(part) for part in args)])
    result = _run_slurm_remote(config, host=host, script=script, timeout_sec=60)
    entries = []
    for line in result["stdout"].splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 9:
            continue
        partition_name = parts[0]
        entries.append(
            {
                "partition": partition_name.rstrip("*"),
                "is_default": partition_name.endswith("*"),
                "availability": parts[1],
                "time_limit": parts[2],
                "nodes": parts[3],
                "state": parts[4],
                "gres": parts[5],
                "memory_mb": parts[6],
                "cpus": parts[7],
                "node_list": parts[8],
            }
        )
    return {
        "host": host,
        "profile": profile_payload["profile"],
        "partitions": entries,
        "stderr": result["stderr"],
        "truncated": result["truncated"],
    }


def list_queue(
    config: SSHRuntimeConfig,
    *,
    host: str,
    job_id: str | None = None,
    user: str | None = None,
    current_user_only: bool = True,
    states: list[str] | None = None,
    max_results: int = 200,
) -> dict[str, Any]:
    profile_payload = _ensure_slurm_host(host)
    limit = _clamp_max_results(max_results)
    limit_plus_one = limit + 1
    args = ["squeue", "-h", "-o", "%i\t%j\t%P\t%T\t%M\t%l\t%D\t%C\t%R"]
    if job_id:
        args.extend(["-j", job_id])
    if user:
        args.extend(["-u", user])
    elif current_user_only:
        args.extend(["-u", "$USER"])
    if states:
        args.extend(["-t", ",".join(states)])
    base_command = " ".join(shlex.quote(part) if part != "$USER" else part for part in args)
    script = "\n".join([_require_commands_prelude("squeue"), f"{base_command} | head -n {limit_plus_one}"])
    result = _run_slurm_remote(config, host=host, script=script, timeout_sec=60)
    raw_lines = [line for line in result["stdout"].splitlines() if line.strip()]
    jobs = []
    for line in raw_lines[:limit]:
        parts = line.split("\t")
        if len(parts) != 9:
            continue
        jobs.append(
            {
                "job_id": parts[0],
                "name": parts[1],
                "partition": parts[2],
                "state": parts[3],
                "time_used": parts[4],
                "time_limit": parts[5],
                "nodes": parts[6],
                "cpus": parts[7],
                "reason_or_node": parts[8],
            }
        )
    return {
        "host": host,
        "profile": profile_payload["profile"],
        "jobs": jobs,
        "truncated": len(raw_lines) > limit or result["truncated"],
        "stderr": result["stderr"],
    }


def get_job_accounting(
    config: SSHRuntimeConfig,
    *,
    host: str,
    job_id: str,
) -> dict[str, Any]:
    profile_payload = _ensure_slurm_host(host)
    args = [
        "sacct",
        "-P",
        "-n",
        "-j",
        job_id,
        "-o",
        "JobID,JobName,Partition,Account,AllocTRES,State,ExitCode,Elapsed,MaxRSS,NodeList",
    ]
    script = "\n".join([_sacct_prelude(), " ".join(shlex.quote(part) for part in args)])
    result = _run_slurm_remote(config, host=host, script=script, timeout_sec=60)
    records = []
    for line in result["stdout"].splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) != 10:
            continue
        records.append(
            {
                "job_id": parts[0],
                "job_name": parts[1],
                "partition": parts[2],
                "account": parts[3],
                "alloc_tres": parts[4],
                "state": parts[5],
                "exit_code": parts[6],
                "elapsed": parts[7],
                "max_rss": parts[8],
                "node_list": parts[9],
            }
        )
    return {
        "host": host,
        "profile": profile_payload["profile"],
        "job_id": job_id,
        "records": records,
        "stderr": result["stderr"],
        "truncated": result["truncated"],
    }


def get_job_info(
    config: SSHRuntimeConfig,
    *,
    host: str,
    job_id: str,
) -> dict[str, Any]:
    profile_payload = _ensure_slurm_host(host)
    args = ["scontrol", "show", "job", "-o", job_id]
    script = "\n".join([_scontrol_prelude(), " ".join(shlex.quote(part) for part in args)])
    result = _run_slurm_remote(config, host=host, script=script, timeout_sec=60)
    line = next((raw for raw in result["stdout"].splitlines() if raw.strip()), "")
    info: dict[str, str] = {}
    for token in line.split():
        key, sep, value = token.partition("=")
        if sep:
            info[key] = value
    return {
        "host": host,
        "profile": profile_payload["profile"],
        "job_id": job_id,
        "info": info,
        "stderr": result["stderr"],
        "truncated": result["truncated"],
    }


def render_slurm_script(
    *,
    command: str,
    job_name: str = "train",
    host: str | None = None,
    profile_id: str | None = None,
    workdir: str | None = None,
    partition: str | None = None,
    account: str | None = None,
    time_limit: str = "24:00:00",
    nodes: int = 1,
    ntasks_per_node: int | None = None,
    cpus_per_task: int | None = None,
    mem: str | None = None,
    gpus_per_node: int | None = None,
    gres: str | None = None,
    output_path: str = "logs/slurm-%j.out",
    error_path: str | None = None,
    module_loads: list[str] | None = None,
    setup_commands: list[str] | None = None,
    extra_sbatch: list[str] | None = None,
    conda_env_name: str | None = None,
    conda_env_prefix: str | None = None,
) -> dict[str, Any]:
    if not command.strip():
        raise SSHError("command is required")
    profile_payload = None
    if host or profile_id:
        profile_payload = get_cluster_profile_for_host(host=host, profile_id=profile_id)
        profile = profile_payload.get("profile")
        if profile and profile.get("scheduler") != "slurm":
            raise SSHError("selected host or profile is not a Slurm cluster")
    else:
        profile = None

    lines = ["#!/bin/bash"]
    if profile:
        lines.append(f"# cluster-profile: {profile['id']}")
    lines.append(f"#SBATCH --job-name={job_name}")
    lines.append(f"#SBATCH --time={time_limit}")
    lines.append(f"#SBATCH --nodes={max(nodes, 1)}")
    if partition:
        lines.append(f"#SBATCH --partition={partition}")
    if account:
        lines.append(f"#SBATCH --account={account}")
    if ntasks_per_node is not None:
        lines.append(f"#SBATCH --ntasks-per-node={max(ntasks_per_node, 1)}")
    if cpus_per_task is not None:
        lines.append(f"#SBATCH --cpus-per-task={max(cpus_per_task, 1)}")
    if mem:
        lines.append(f"#SBATCH --mem={mem}")
    if gres:
        lines.append(f"#SBATCH --gres={gres}")
    elif gpus_per_node is not None:
        lines.append(f"#SBATCH --gres=gpu:{max(gpus_per_node, 1)}")
    if output_path:
        lines.append(f"#SBATCH --output={output_path}")
    if error_path:
        lines.append(f"#SBATCH --error={error_path}")
    for item in extra_sbatch or []:
        stripped = item.strip()
        if not stripped:
            continue
        if stripped.startswith("#SBATCH"):
            lines.append(stripped)
        else:
            lines.append(f"#SBATCH {stripped}")

    lines.append("")
    lines.append("set -euo pipefail")
    if workdir:
        lines.append(f"cd {shlex.quote(workdir)}")
    if output_path:
        log_dir = str(PurePosixPath(output_path).parent)
        if log_dir not in ("", "."):
            lines.append(f"mkdir -p {shlex.quote(log_dir)}")
    for module_name in module_loads or []:
        if module_name.strip():
            lines.append(f"module load {shlex.quote(module_name.strip())}")
    lines.extend(_render_conda_activation_lines(conda_env_name=conda_env_name, conda_env_prefix=conda_env_prefix))
    for item in setup_commands or []:
        if item.strip():
            lines.append(item)
    lines.append(command)
    content = "\n".join(lines).rstrip() + "\n"

    profile_data = profile_payload.get("profile") if profile_payload else None
    return {
        "script_content": content,
        "job_name": job_name,
        "output_path": output_path,
        "error_path": error_path,
        "profile": profile_data,
        "notes": [
            "Sync code first with ssh_sync_dir, then submit with ssh_sbatch_submit.",
            "Use ssh_squeue and ssh_tail_file right after submission to confirm the job is healthy.",
        ],
    }


def submit_batch(
    config: SSHRuntimeConfig,
    *,
    host: str,
    script_path: str | None = None,
    script_content: str | None = None,
    remote_path: str | None = None,
    workdir: str | None = None,
    additional_args: list[str] | None = None,
    test_only: bool = False,
    timeout_sec: int = 120,
) -> dict[str, Any]:
    profile_payload = _ensure_submit_host(host)
    if bool(script_path) == bool(script_content):
        raise SSHError("set exactly one of script_path or script_content")
    if additional_args and any(not isinstance(item, str) for item in additional_args):
        raise SSHError("additional_args must be a list of strings")

    resolved_script_path = script_path
    directives: dict[str, str] = {}
    if script_content is not None:
        resolved_script_path = remote_path or f"/tmp/ssh-skill-slurm/{uuid.uuid4().hex}.slurm"
        write_file(
            config,
            host=host,
            path=resolved_script_path,
            content=script_content,
            overwrite=True,
            create_dirs=True,
            mode="700",
        )
        directives = _parse_sbatch_directives(script_content)

    if not resolved_script_path:
        raise SSHError("failed to resolve script path")

    args = ["sbatch", "--parsable"]
    if test_only:
        args.append("--test-only")
    for item in additional_args or []:
        if item.strip():
            args.append(item.strip())
    args.append(resolved_script_path)
    command = " ".join(shlex.quote(part) for part in args)
    lines = [_require_commands_prelude("sbatch")]
    if workdir:
        lines.append(f"cd {shlex.quote(workdir)}")
    lines.append(command)
    result = _run_slurm_remote(config, host=host, script="\n".join(lines), timeout_sec=max(timeout_sec, config.default_timeout_sec))

    stdout_line = next((line.strip() for line in result["stdout"].splitlines() if line.strip()), "")
    match = SBATCH_JOB_ID_PATTERN.search(stdout_line) if not test_only else None
    job_id = match.group("job_id") if match else None
    job_name = directives.get("--job-name") or directives.get("-J")
    log_path = _best_effort_log_path(
        directives=directives,
        workdir=workdir,
        job_id=job_id,
        job_name=job_name,
    )
    return {
        "host": host,
        "profile": profile_payload["profile"],
        "script_path": resolved_script_path,
        "job_id": job_id,
        "submitted": job_id is not None and not test_only,
        "test_only": test_only,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "truncated": result["truncated"],
        "log_path_hint": log_path,
    }


def cancel_job(
    config: SSHRuntimeConfig,
    *,
    host: str,
    job_ids: list[str],
    signal: str | None = None,
    allow_cancel: bool = False,
) -> dict[str, Any]:
    profile_payload = _ensure_submit_host(host)
    if not allow_cancel:
        raise SSHError(
            "job cancellation is blocked by default. Set allow_cancel=true only when the user explicitly requested cancellation."
        )
    cleaned_ids = [str(job_id).strip() for job_id in job_ids if str(job_id).strip()]
    if not cleaned_ids:
        raise SSHError("at least one job id is required")
    args = ["scancel"]
    if signal:
        args.extend(["--signal", signal])
    args.extend(cleaned_ids)
    script = "\n".join(
        [
            "set -e",
            'if ! command -v scancel >/dev/null 2>&1; then echo "scancel is not installed on remote host" >&2; exit 17; fi',
            " ".join(shlex.quote(part) for part in args),
        ]
    )
    result = _run_slurm_remote(config, host=host, script=script, timeout_sec=60)
    return {
        "host": host,
        "profile": profile_payload["profile"],
        "job_ids": cleaned_ids,
        "signal": signal,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "truncated": result["truncated"],
    }
