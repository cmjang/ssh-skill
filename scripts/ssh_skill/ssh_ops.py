from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .ssh_config import effective_ssh_config_path, list_host_entries


class SSHError(RuntimeError):
    pass


class SSHRuntimeConfig:
    def __init__(self, ssh_config_path: str | None = None, default_timeout_sec: int = 120, max_output_bytes: int = 1 << 20) -> None:
        self.ssh_config_path = ssh_config_path or effective_ssh_config_path()
        self.default_timeout_sec = default_timeout_sec
        self.max_output_bytes = max_output_bytes


DEFAULT_SYNC_EXCLUDES = [".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"]
BLOCKED_RM_PATTERN = re.compile(r"(^|[;|&()\s])(?:sudo\s+)?(?:(?:/usr/bin|/bin)/)?rm(?:\s|$)")


def _clamp_max_results(max_results: int | None, default: int = 200) -> int:
    if not max_results or max_results <= 0:
        return default
    return min(max_results, 2000)


def _safe_decode(data: bytes, limit: int) -> tuple[str, bool]:
    if len(data) <= limit:
        return data.decode("utf-8", errors="replace"), False
    return data[:limit].decode("utf-8", errors="replace"), True


def _count_local_tree(path: Path) -> tuple[int, int, int]:
    if path.is_file():
        return 1, int(path.stat().st_size), 0
    files = 0
    size = 0
    dirs = 0
    for child in path.rglob("*"):
        if child.is_dir():
            dirs += 1
        elif child.is_file():
            files += 1
            try:
                size += int(child.stat().st_size)
            except OSError:
                pass
    return files, size, dirs


def _shell_script(command: str, workdir: str | None, env: dict[str, str] | None) -> str:
    lines: list[str] = []
    if workdir:
        lines.append(f"cd {shlex.quote(workdir)}")
    if env:
        for key, value in env.items():
            if not key or not key.replace("_", "a").isalnum() or key[0].isdigit():
                raise SSHError(f"invalid env key: {key}")
            lines.append(f"export {key}={shlex.quote(str(value))}")
    lines.append(command)
    return "\n".join(lines)


def _with_conda_env(script: str, *, conda_env_name: str | None = None, conda_env_prefix: str | None = None) -> str:
    if conda_env_name and conda_env_prefix:
        raise SSHError("set either conda_env_name or conda_env_prefix, not both")
    if not conda_env_name and not conda_env_prefix:
        return script
    target = shlex.quote(conda_env_name or conda_env_prefix or "")
    return "\n".join(
        [
            'if ! command -v conda >/dev/null 2>&1; then echo "conda is not installed on remote host" >&2; exit 17; fi',
            'conda_base="$(conda info --base)"',
            'if [ ! -f "$conda_base/etc/profile.d/conda.sh" ]; then echo "conda.sh not found on remote host" >&2; exit 17; fi',
            'source "$conda_base/etc/profile.d/conda.sh"',
            f"conda activate {target}",
            script,
        ]
    )


def _ssh_base_args(config: SSHRuntimeConfig, host: str) -> list[str]:
    args = ["ssh"]
    if config.ssh_config_path:
        args.extend(["-F", config.ssh_config_path])
    args.extend(
        [
            "-o",
            "BatchMode=yes",
            "-o",
            "RemoteCommand=none",
            "-o",
            "RequestTTY=no",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            host,
        ]
    )
    return args


def _scp_base_args(config: SSHRuntimeConfig) -> list[str]:
    args = ["scp"]
    if config.ssh_config_path:
        args.extend(["-F", config.ssh_config_path])
    args.extend(["-o", "BatchMode=yes"])
    return args


def _rsync_ssh_command(config: SSHRuntimeConfig) -> str:
    parts = ["ssh"]
    if config.ssh_config_path:
        parts.extend(["-F", config.ssh_config_path])
    parts.extend(
        [
            "-o",
            "BatchMode=yes",
            "-o",
            "RemoteCommand=none",
            "-o",
            "RequestTTY=no",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
        ]
    )
    return " ".join(shlex.quote(part) for part in parts)


def _run_subprocess(args: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SSHError(f"command timed out after {timeout} seconds") from exc
    except FileNotFoundError as exc:
        raise SSHError(f"command not found: {args[0]}") from exc


def _run_ssh(config: SSHRuntimeConfig, host: str, remote_command: str, *, timeout: int | None = None) -> subprocess.CompletedProcess[bytes]:
    args = _ssh_base_args(config, host)
    args.append(remote_command)
    return _run_subprocess(args, timeout=timeout)


def _assert_safe_remote_command(command: str, *, allow_destructive: bool = False) -> None:
    if allow_destructive:
        return
    if BLOCKED_RM_PATTERN.search(command):
        raise SSHError(
            "rm/rm -rf is blocked by ssh-skill safety policy. Use git-based rollback instead, "
            "or set allow_destructive=true only when the user explicitly requested deletion."
        )


def list_hosts(config: SSHRuntimeConfig) -> dict[str, Any]:
    entries = list_host_entries(config.ssh_config_path)
    return {
        "hosts": [
            {
                "name": entry.alias,
                "source_file": entry.source_file,
                "line_no": entry.line_no,
            }
            for entry in entries
        ],
        "ssh_config_path": config.ssh_config_path,
    }


def find_files(
    config: SSHRuntimeConfig,
    *,
    host: str,
    path: str = ".",
    glob: str | None = None,
    max_results: int = 200,
    include_hidden: bool = True,
) -> dict[str, Any]:
    limit = _clamp_max_results(max_results)
    limit_plus_one = limit + 1
    rg_hidden = "--hidden" if include_hidden else ""
    find_hidden = "" if include_hidden else r"! -path '*/.*'"
    rg_glob = f"-g {shlex.quote(glob)}" if glob else ""
    find_glob = f"-name {shlex.quote(glob)}" if glob else ""
    script = f"""
set -e
target={shlex.quote(path)}
limit={limit_plus_one}
if command -v rg >/dev/null 2>&1; then
  if [ -d "$target" ]; then
    rg --files {rg_hidden} "$target" {rg_glob} | head -n "$limit"
  elif [ -f "$target" ]; then
    printf '%s\\n' "$target"
  else
    echo "path not found: $target" >&2
    exit 17
  fi
else
  if [ -d "$target" ]; then
    find "$target" {find_hidden} -type f {find_glob} | sort | head -n "$limit"
  elif [ -f "$target" ]; then
    printf '%s\\n' "$target"
  else
    echo "path not found: $target" >&2
    exit 17
  fi
fi
""".strip()
    completed = _run_ssh(config, host, f"bash -lc {shlex.quote(script)}", timeout=60)
    if completed.returncode != 0:
        raise SSHError(completed.stderr.decode("utf-8", errors="replace").strip() or "find files failed")
    lines = [line for line in completed.stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
    truncated = len(lines) > limit
    return {
        "host": host,
        "path": path,
        "glob": glob,
        "files": lines[:limit],
        "truncated": truncated,
        "returned_count": min(len(lines), limit),
    }


def grep_text(
    config: SSHRuntimeConfig,
    *,
    host: str,
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    max_results: int = 200,
    ignore_case: bool = False,
    include_hidden: bool = True,
) -> dict[str, Any]:
    limit = _clamp_max_results(max_results)
    limit_plus_one = limit + 1
    case_flag = "-i" if ignore_case else "--smart-case"
    hidden_flag = "--hidden" if include_hidden else ""
    glob_flag = f"-g {shlex.quote(glob)}" if glob else ""
    grep_case = "-i" if ignore_case else ""
    grep_hidden = "" if include_hidden else r"! -path '*/.*'"
    script = f"""
set -e
target={shlex.quote(path)}
limit={limit_plus_one}
pattern={shlex.quote(pattern)}
if command -v rg >/dev/null 2>&1; then
  if [ -e "$target" ]; then
    (rg -n --no-heading --color never {case_flag} {hidden_flag} {glob_flag} -- "$pattern" "$target" || test $? -eq 1) | head -n "$limit"
  else
    echo "path not found: $target" >&2
    exit 17
  fi
else
  if [ -d "$target" ]; then
    (find "$target" {grep_hidden} -type f -print0 | xargs -0 grep -nH {grep_case} -- "$pattern" || test $? -eq 1) | head -n "$limit"
  elif [ -f "$target" ]; then
    (grep -nH {grep_case} -- "$pattern" "$target" || test $? -eq 1) | head -n "$limit"
  else
    echo "path not found: $target" >&2
    exit 17
  fi
fi
""".strip()
    completed = _run_ssh(config, host, f"bash -lc {shlex.quote(script)}", timeout=60)
    if completed.returncode != 0:
        raise SSHError(completed.stderr.decode("utf-8", errors="replace").strip() or "grep failed")
    raw_lines = [line for line in completed.stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
    truncated = len(raw_lines) > limit
    hits: list[dict[str, Any]] = []
    for line in raw_lines[:limit]:
        parts = line.split(":", 2)
        if len(parts) >= 2 and parts[0].isdigit():
            line_no = parts[0]
            text = ":".join(parts[1:])
            hits.append({"path": path, "line": int(line_no), "text": text})
        elif len(parts) == 3 and parts[1].isdigit():
            file_path, line_no, text = parts
            hits.append({"path": file_path, "line": int(line_no), "text": text})
        else:
            hits.append({"path": None, "line": None, "text": line})
    return {
        "host": host,
        "pattern": pattern,
        "path": path,
        "glob": glob,
        "hits": hits,
        "truncated": truncated,
        "returned_count": len(hits),
    }


def exec_command(
    config: SSHRuntimeConfig,
    *,
    host: str,
    command: str,
    workdir: str | None = None,
    env: dict[str, str] | None = None,
    timeout_sec: int | None = None,
    allow_destructive: bool = False,
    conda_env_name: str | None = None,
    conda_env_prefix: str | None = None,
) -> dict[str, Any]:
    _assert_safe_remote_command(command, allow_destructive=allow_destructive)
    script = _shell_script(command, workdir, env)
    script = _with_conda_env(script, conda_env_name=conda_env_name, conda_env_prefix=conda_env_prefix)
    remote = f"bash -lc {shlex.quote(script)}"
    start = time.monotonic()
    completed = _run_ssh(config, host, remote, timeout=timeout_sec or config.default_timeout_sec)
    duration_ms = int((time.monotonic() - start) * 1000)
    stdout, stdout_trunc = _safe_decode(completed.stdout, config.max_output_bytes)
    stderr, stderr_trunc = _safe_decode(completed.stderr, config.max_output_bytes)
    return {
        "ok": completed.returncode == 0,
        "host": host,
        "command": command,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": stdout_trunc or stderr_trunc,
        "duration_ms": duration_ms,
        "conda_env_name": conda_env_name,
        "conda_env_prefix": conda_env_prefix,
    }


def _remote_exists(config: SSHRuntimeConfig, host: str, remote_path: str) -> bool:
    remote = f"bash -lc {shlex.quote(f'test -e {shlex.quote(remote_path)}')}"
    return _run_ssh(config, host, remote, timeout=30).returncode == 0


def _remote_is_dir(config: SSHRuntimeConfig, host: str, remote_path: str) -> bool:
    remote = f"bash -lc {shlex.quote(f'test -d {shlex.quote(remote_path)}')}"
    return _run_ssh(config, host, remote, timeout=30).returncode == 0


def upload(
    config: SSHRuntimeConfig,
    *,
    host: str,
    local_path: str,
    remote_path: str,
    overwrite: bool = False,
    preserve_mode: bool = False,
    recursive: bool | None = None,
) -> dict[str, Any]:
    local = Path(local_path).expanduser()
    if not local.exists():
        raise SSHError(f"local path does not exist: {local}")
    is_dir = local.is_dir()
    if recursive is False and is_dir:
        raise SSHError("local path is a directory but recursive is false")
    if not overwrite:
        candidate = remote_path
        if not is_dir and (remote_path.endswith("/") or _remote_is_dir(config, host, remote_path)):
            candidate = remote_path.rstrip("/") + "/" + local.name
        if _remote_exists(config, host, candidate):
            raise SSHError(f"remote path already exists: {candidate}")

    args = _scp_base_args(config)
    if is_dir or recursive:
        args.append("-r")
    if preserve_mode:
        args.append("-p")
    args.extend([str(local), f"{host}:{remote_path}"])
    start = time.monotonic()
    completed = _run_subprocess(args)
    duration_ms = int((time.monotonic() - start) * 1000)
    if completed.returncode != 0:
        raise SSHError(completed.stderr.decode("utf-8", errors="replace").strip() or "upload failed")
    files, size, dirs = _count_local_tree(local)
    return {
        "host": host,
        "local_path": str(local),
        "remote_path": remote_path,
        "files": files,
        "bytes": size,
        "directories": dirs,
        "duration_ms": duration_ms,
    }


def sync_dir(
    config: SSHRuntimeConfig,
    *,
    host: str,
    local_path: str,
    remote_path: str,
    delete: bool = False,
    exclude: list[str] | None = None,
    use_default_excludes: bool = True,
    dry_run: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    local = Path(local_path).expanduser()
    if not local.exists():
        raise SSHError(f"local path does not exist: {local}")
    if not local.is_dir():
        raise SSHError("ssh_sync_dir requires a local directory")
    if delete and not allow_destructive:
        raise SSHError(
            "ssh_sync_dir(delete=true) is blocked by default because it can remove remote files. "
            "Set allow_destructive=true only when the user explicitly requested deletion."
        )

    exclude_patterns: list[str] = []
    if use_default_excludes:
        exclude_patterns.extend(DEFAULT_SYNC_EXCLUDES)
    if exclude:
        for item in exclude:
            if item not in exclude_patterns:
                exclude_patterns.append(item)

    mkdir_script = f"mkdir -p {shlex.quote(remote_path)}"
    mkdir_result = _run_ssh(config, host, f"bash -lc {shlex.quote(mkdir_script)}", timeout=30)
    if mkdir_result.returncode != 0:
        raise SSHError(mkdir_result.stderr.decode("utf-8", errors="replace").strip() or "failed to prepare remote path")

    args = ["rsync", "-az", "--itemize-changes", "--stats"]
    if delete:
        args.append("--delete")
    if dry_run:
        args.append("--dry-run")
    for pattern in exclude_patterns:
        args.extend(["--exclude", pattern])
    args.extend(["-e", _rsync_ssh_command(config), str(local) + "/", f"{host}:{remote_path.rstrip('/')}/"])

    start = time.monotonic()
    completed = _run_subprocess(args, timeout=max(config.default_timeout_sec, 600))
    duration_ms = int((time.monotonic() - start) * 1000)
    if completed.returncode != 0:
        raise SSHError(completed.stderr.decode("utf-8", errors="replace").strip() or "sync failed")

    stdout = completed.stdout.decode("utf-8", errors="replace")
    changed = [line for line in stdout.splitlines() if line[:1] in {">", "<", ".", "c", "h", "*"}]
    files, size, dirs = _count_local_tree(local)
    return {
        "host": host,
        "local_path": str(local),
        "remote_path": remote_path,
        "delete": delete,
        "exclude": exclude_patterns,
        "dry_run": dry_run,
        "changed": changed[:500],
        "truncated": len(changed) > 500,
        "files": files,
        "bytes": size,
        "directories": dirs,
        "duration_ms": duration_ms,
    }


def uv_sync(
    config: SSHRuntimeConfig,
    *,
    host: str,
    project_dir: str,
    env_dir: str = ".venv",
    frozen: bool = False,
    no_dev: bool = False,
    extra_args: list[str] | None = None,
    timeout_sec: int = 1800,
) -> dict[str, Any]:
    extra_args = extra_args or []
    if any(not isinstance(item, str) for item in extra_args):
        raise SSHError("extra_args must be a list of strings")

    uv_args = ["uv", "sync"]
    if frozen:
        uv_args.append("--frozen")
    if no_dev:
        uv_args.append("--no-dev")
    uv_args.extend(extra_args)
    uv_command = " ".join(shlex.quote(part) for part in uv_args)

    script = f"""
set -e
project_dir={shlex.quote(project_dir)}
env_dir={shlex.quote(env_dir)}
if [ ! -d "$project_dir" ]; then
  echo "project directory not found: $project_dir" >&2
  exit 17
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed on remote host" >&2
  exit 17
fi
cd "$project_dir"
unset VIRTUAL_ENV
unset CONDA_PREFIX
export UV_PROJECT_ENVIRONMENT="$env_dir"
{uv_command}
if [ "${{UV_PROJECT_ENVIRONMENT#/}}" != "$UV_PROJECT_ENVIRONMENT" ]; then
  resolved_env="$UV_PROJECT_ENVIRONMENT"
else
  resolved_env="$PWD/$UV_PROJECT_ENVIRONMENT"
fi
printf 'project_dir\\t%s\\n' "$PWD"
printf 'env_dir\\t%s\\n' "$resolved_env"
printf 'uv_bin\\t%s\\n' "$(command -v uv)"
if [ -x "$resolved_env/bin/python" ]; then
  printf 'python_bin\\t%s\\n' "$resolved_env/bin/python"
fi
""".strip()
    start = time.monotonic()
    completed = _run_ssh(config, host, f"bash -lc {shlex.quote(script)}", timeout=max(timeout_sec, config.default_timeout_sec))
    duration_ms = int((time.monotonic() - start) * 1000)
    stdout, stdout_trunc = _safe_decode(completed.stdout, config.max_output_bytes)
    stderr, stderr_trunc = _safe_decode(completed.stderr, config.max_output_bytes)
    if completed.returncode != 0:
        raise SSHError(stderr.strip() or stdout.strip() or "uv sync failed")

    metadata: dict[str, str] = {}
    sync_output_lines: list[str] = []
    for line in stdout.splitlines():
        key, sep, value = line.partition("\t")
        if sep and key in {"project_dir", "env_dir", "uv_bin", "python_bin"}:
            metadata[key] = value
        else:
            sync_output_lines.append(line)

    return {
        "host": host,
        "project_dir": metadata.get("project_dir", project_dir),
        "env_dir": metadata.get("env_dir", env_dir),
        "uv_bin": metadata.get("uv_bin"),
        "python_bin": metadata.get("python_bin"),
        "command": uv_args,
        "frozen": frozen,
        "no_dev": no_dev,
        "extra_args": extra_args,
        "separate_from_local_env": True,
        "stdout": "\n".join(sync_output_lines).strip(),
        "stderr": stderr,
        "truncated": stdout_trunc or stderr_trunc,
        "duration_ms": duration_ms,
    }


def list_conda_envs(config: SSHRuntimeConfig, *, host: str) -> dict[str, Any]:
    script = """
set -e
if ! command -v conda >/dev/null 2>&1; then
  echo "conda is not installed on remote host" >&2
  exit 17
fi
conda env list --json
printf '\n__SSH_SKILL_SPLIT__\n'
conda info --json
""".strip()
    completed = _run_ssh(config, host, f"bash -lc {shlex.quote(script)}", timeout=120)
    stdout, stdout_trunc = _safe_decode(completed.stdout, config.max_output_bytes)
    stderr, stderr_trunc = _safe_decode(completed.stderr, config.max_output_bytes)
    if completed.returncode != 0:
        raise SSHError(stderr.strip() or stdout.strip() or "conda env list failed")
    envs_text, marker, info_text = stdout.partition("\n__SSH_SKILL_SPLIT__\n")
    if not marker:
        raise SSHError("failed to parse conda info output")
    try:
        payload = json.loads(envs_text)
        info_payload = json.loads(info_text)
    except json.JSONDecodeError as exc:
        raise SSHError("failed to parse conda env list output") from exc
    env_paths = payload.get("envs") or []
    default_prefix = info_payload.get("root_prefix") or info_payload.get("default_prefix")
    envs: list[dict[str, Any]] = []
    for env_path in env_paths:
        env_path_text = str(env_path)
        name = "base" if default_prefix == env_path_text else Path(env_path_text).name
        is_default = default_prefix == env_path_text or name == "base"
        envs.append({"name": name, "prefix": env_path_text, "is_default": is_default})
    return {
        "host": host,
        "envs": envs,
        "default_prefix": default_prefix,
        "truncated": stdout_trunc or stderr_trunc,
    }


def download(
    config: SSHRuntimeConfig,
    *,
    host: str,
    remote_path: str,
    local_path: str,
    overwrite: bool = False,
    preserve_mode: bool = False,
    recursive: bool | None = None,
) -> dict[str, Any]:
    local = Path(local_path).expanduser()
    if local.exists() and not overwrite:
        raise SSHError(f"local path already exists: {local}")
    args = _scp_base_args(config)
    if recursive:
        args.append("-r")
    if preserve_mode:
        args.append("-p")
    args.extend([f"{host}:{remote_path}", str(local)])
    start = time.monotonic()
    completed = _run_subprocess(args)
    duration_ms = int((time.monotonic() - start) * 1000)
    if completed.returncode != 0:
        raise SSHError(completed.stderr.decode("utf-8", errors="replace").strip() or "download failed")
    target = local
    files, size, dirs = _count_local_tree(target) if target.exists() else (0, 0, 0)
    return {
        "host": host,
        "remote_path": remote_path,
        "local_path": str(target),
        "files": files,
        "bytes": size,
        "directories": dirs,
        "duration_ms": duration_ms,
    }


def list_dir(config: SSHRuntimeConfig, *, host: str, path: str = ".", max_entries: int = 200) -> dict[str, Any]:
    script = (
        "set -e\n"
        f"target={shlex.quote(path)}\n"
        'if [ ! -d "$target" ]; then echo "not a directory" >&2; exit 17; fi\n'
        'find "$target" -mindepth 1 -maxdepth 1 '
        "-printf '%P\\0%y\\0%s\\0%M\\0%TY-%Tm-%TdT%TH:%TM:%TS\\0'"
    )
    completed = _run_ssh(config, host, f"bash -lc {shlex.quote(script)}", timeout=60)
    if completed.returncode != 0:
        raise SSHError(completed.stderr.decode("utf-8", errors="replace").strip() or "list dir failed")
    parts = completed.stdout.decode("utf-8", errors="replace").split("\0")
    raw_entries: list[dict[str, Any]] = []
    for index in range(0, len(parts) - 1, 5):
        name = parts[index]
        if not name:
            continue
        entry_type = parts[index + 1]
        size_text = parts[index + 2]
        mode = parts[index + 3]
        mod_time = parts[index + 4]
        try:
            size = int(size_text)
        except ValueError:
            size = 0
        raw_entries.append(
            {
                "name": name,
                "path": path.rstrip("/") + "/" + name if path not in ("", ".") else name,
                "is_dir": entry_type == "d",
                "size": size,
                "mode": mode,
                "mod_time": mod_time,
            }
        )
    raw_entries.sort(key=lambda item: item["name"].lower())
    limited = raw_entries[: max(max_entries, 0)]
    return {
        "host": host,
        "path": path,
        "entries": limited,
        "truncated": len(raw_entries) > len(limited),
        "total_count": len(raw_entries),
    }


def read_file(
    config: SSHRuntimeConfig,
    *,
    host: str,
    path: str,
    start_line: int = 1,
    line_count: int = 200,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    start = max(start_line, 1)
    line_count = max(line_count, 1)
    end = start + line_count - 1
    script = f"sed -n '{start},{end}p' -- {shlex.quote(path)}"
    completed = _run_ssh(config, host, f"bash -lc {shlex.quote(script)}", timeout=60)
    if completed.returncode != 0:
        raise SSHError(completed.stderr.decode("utf-8", errors="replace").strip() or "read file failed")
    limit = max_bytes if max_bytes and max_bytes > 0 else config.max_output_bytes
    content, truncated = _safe_decode(completed.stdout, limit)
    returned_lines = len(content.splitlines())
    end_line = start + returned_lines - 1 if returned_lines else start - 1
    return {
        "host": host,
        "path": path,
        "start_line": start,
        "end_line": end_line,
        "returned_lines": returned_lines,
        "content": content,
        "truncated": truncated,
    }


def tail_file(
    config: SSHRuntimeConfig,
    *,
    host: str,
    path: str,
    lines: int = 200,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    lines = max(lines, 1)
    script = f"tail -n {lines} -- {shlex.quote(path)}"
    completed = _run_ssh(config, host, f"bash -lc {shlex.quote(script)}", timeout=60)
    if completed.returncode != 0:
        raise SSHError(completed.stderr.decode("utf-8", errors="replace").strip() or "tail file failed")
    limit = max_bytes if max_bytes and max_bytes > 0 else config.max_output_bytes
    content, truncated = _safe_decode(completed.stdout, limit)
    return {
        "host": host,
        "path": path,
        "lines": lines,
        "returned_lines": len(content.splitlines()),
        "content": content,
        "truncated": truncated,
    }


def start_process(
    config: SSHRuntimeConfig,
    *,
    host: str,
    command: str,
    workdir: str | None = None,
    env: dict[str, str] | None = None,
    log_path: str | None = None,
    pid_file: str | None = None,
    allow_destructive: bool = False,
    conda_env_name: str | None = None,
    conda_env_prefix: str | None = None,
) -> dict[str, Any]:
    _assert_safe_remote_command(command, allow_destructive=allow_destructive)
    process_id = uuid.uuid4().hex
    resolved_log = log_path or f"/tmp/ssh-skill-{process_id}.log"
    resolved_pid_file = pid_file or f"/tmp/ssh-skill-{process_id}.pid"
    inner = _shell_script(command, workdir, env)
    inner = _with_conda_env(inner, conda_env_name=conda_env_name, conda_env_prefix=conda_env_prefix)
    script = f"""
set -e
mkdir -p {shlex.quote(os.path.dirname(resolved_log) or '.')}
mkdir -p {shlex.quote(os.path.dirname(resolved_pid_file) or '.')}
nohup bash -lc {shlex.quote(inner)} >> {shlex.quote(resolved_log)} 2>&1 < /dev/null &
pid=$!
echo "$pid" > {shlex.quote(resolved_pid_file)}
printf 'pid\\t%s\\nlog_path\\t%s\\npid_file\\t%s\\n' "$pid" {shlex.quote(resolved_log)} {shlex.quote(resolved_pid_file)}
""".strip()
    completed = _run_ssh(config, host, f"bash -lc {shlex.quote(script)}", timeout=30)
    if completed.returncode != 0:
        raise SSHError(completed.stderr.decode("utf-8", errors="replace").strip() or "start process failed")
    values: dict[str, str] = {}
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        key, _, value = line.partition("\t")
        if key and value:
            values[key] = value
    pid_text = values.get("pid")
    if not pid_text or not pid_text.isdigit():
        raise SSHError("failed to parse remote pid")
    return {
        "host": host,
        "command": command,
        "pid": int(pid_text),
        "log_path": values.get("log_path", resolved_log),
        "pid_file": values.get("pid_file", resolved_pid_file),
        "workdir": workdir,
        "conda_env_name": conda_env_name,
        "conda_env_prefix": conda_env_prefix,
    }


def check_process(
    config: SSHRuntimeConfig,
    *,
    host: str,
    pid: int | None = None,
    pid_file: str | None = None,
) -> dict[str, Any]:
    if pid is None and not pid_file:
        raise SSHError("pid or pid_file is required")
    resolve_lines = ["set -e"]
    if pid is not None:
        resolve_lines.append(f"pid={int(pid)}")
    else:
        resolve_lines.append(f"if [ ! -f {shlex.quote(pid_file or '')} ]; then echo 'pid file not found' >&2; exit 17; fi")
        resolve_lines.append(f"pid=$(cat {shlex.quote(pid_file or '')})")
    resolve_lines.extend(
        [
            'if kill -0 "$pid" 2>/dev/null; then',
            '  printf "running\\t1\\n"',
            '  printf "pid\\t%s\\n" "$pid"',
            '  printf "ppid\\t%s\\n" "$(ps -o ppid= -p "$pid" | tr -d " ")"',
            '  printf "etime\\t%s\\n" "$(ps -o etime= -p "$pid" | sed \'s/^ *//\')"',
            '  printf "state\\t%s\\n" "$(ps -o state= -p "$pid" | tr -d " ")"',
            '  printf "command\\t%s\\n" "$(ps -o command= -p "$pid" | sed \'s/^ *//\')"',
            "else",
            '  printf "running\\t0\\n"',
            '  printf "pid\\t%s\\n" "$pid"',
            "fi",
        ]
    )
    script = "\n".join(resolve_lines)
    completed = _run_ssh(config, host, f"bash -lc {shlex.quote(script)}", timeout=30)
    if completed.returncode != 0:
        raise SSHError(completed.stderr.decode("utf-8", errors="replace").strip() or "check process failed")
    values: dict[str, str] = {}
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        key, _, value = line.partition("\t")
        if key and value:
            values[key] = value
    return {
        "host": host,
        "pid": int(values["pid"]) if values.get("pid", "").isdigit() else pid,
        "running": values.get("running") == "1",
        "ppid": int(values["ppid"]) if values.get("ppid", "").isdigit() else None,
        "etime": values.get("etime"),
        "state": values.get("state"),
        "command": values.get("command"),
        "pid_file": pid_file,
    }


def stop_process(
    config: SSHRuntimeConfig,
    *,
    host: str,
    pid: int | None = None,
    pid_file: str | None = None,
    signal: str = "TERM",
    wait_sec: int = 2,
) -> dict[str, Any]:
    if pid is None and not pid_file:
        raise SSHError("pid or pid_file is required")
    script_lines = ["set -e"]
    if pid is not None:
        script_lines.append(f"pid={int(pid)}")
    else:
        script_lines.append(f"if [ ! -f {shlex.quote(pid_file or '')} ]; then echo 'pid file not found' >&2; exit 17; fi")
        script_lines.append(f"pid=$(cat {shlex.quote(pid_file or '')})")
    script_lines.extend(
        [
            'printf "pid\\t%s\\n" "$pid"',
            'if kill -0 "$pid" 2>/dev/null; then',
            f'  kill -s {shlex.quote(signal)} "$pid"',
            f"  sleep {max(wait_sec, 0)}",
            '  if kill -0 "$pid" 2>/dev/null; then running=1; else running=0; fi',
            "else",
            "  running=0",
            "fi",
            'printf "running\\t%s\\n" "$running"',
        ]
    )
    if pid_file:
        script_lines.append(f'if [ "$running" = "0" ]; then rm -f {shlex.quote(pid_file)}; fi')
    script = "\n".join(script_lines)
    completed = _run_ssh(config, host, f"bash -lc {shlex.quote(script)}", timeout=30)
    if completed.returncode != 0:
        raise SSHError(completed.stderr.decode("utf-8", errors="replace").strip() or "stop process failed")
    values: dict[str, str] = {}
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        key, _, value = line.partition("\t")
        if key and value:
            values[key] = value
    return {
        "host": host,
        "pid": int(values["pid"]) if values.get("pid", "").isdigit() else pid,
        "running": values.get("running") == "1",
        "signal": signal,
        "pid_file": pid_file,
    }


def check_port(
    config: SSHRuntimeConfig,
    *,
    host: str,
    port: int,
    listen_only: bool = True,
) -> dict[str, Any]:
    target_port = int(port)
    mode_filter = "-ltn" if listen_only else "-tn"
    script = f"""
set -e
if command -v ss >/dev/null 2>&1; then
  ss {mode_filter} '( sport = :{target_port} )' | sed '1d'
elif command -v netstat >/dev/null 2>&1; then
  netstat {mode_filter} 2>/dev/null | awk '$4 ~ /:{target_port}$/ {{print}}'
elif command -v lsof >/dev/null 2>&1; then
  lsof -nP -iTCP:{target_port} {"-sTCP:LISTEN" if listen_only else ""} 2>/dev/null | sed '1d'
else
  echo "no ss, netstat, or lsof available" >&2
  exit 17
fi
""".strip()
    completed = _run_ssh(config, host, f"bash -lc {shlex.quote(script)}", timeout=20)
    if completed.returncode != 0:
        raise SSHError(completed.stderr.decode("utf-8", errors="replace").strip() or "check port failed")
    lines = [line for line in completed.stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
    return {
        "host": host,
        "port": target_port,
        "listen_only": listen_only,
        "open": bool(lines),
        "details": lines,
    }


def write_file(
    config: SSHRuntimeConfig,
    *,
    host: str,
    path: str,
    content: str,
    overwrite: bool = False,
    append: bool = False,
    create_dirs: bool = False,
    mode: str | None = None,
) -> dict[str, Any]:
    remote_tmp = f"/tmp/ssh-skill-{uuid.uuid4().hex}.tmp"
    local_tmp: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(content)
            local_tmp = handle.name
        upload(config, host=host, local_path=local_tmp, remote_path=remote_tmp, overwrite=True, preserve_mode=False, recursive=False)
        quoted_path = shlex.quote(path)
        quoted_tmp = shlex.quote(remote_tmp)
        lines: list[str] = ["set -e"]
        if create_dirs:
            lines.append(f"mkdir -p {shlex.quote(os.path.dirname(path) or '.')}")
        if append:
            lines.append(f"cat {quoted_tmp} >> {quoted_path}")
        else:
            if not overwrite:
                lines.append(f'if [ -e {quoted_path} ]; then echo "remote file exists" >&2; exit 17; fi')
            lines.append(f"cat {quoted_tmp} > {quoted_path}")
        if mode:
            lines.append(f"chmod {shlex.quote(mode)} {quoted_path}")
        lines.append(f"rm -f {quoted_tmp}")
        completed = _run_ssh(config, host, f"bash -lc {shlex.quote(chr(10).join(lines))}", timeout=60)
        if completed.returncode != 0:
            raise SSHError(completed.stderr.decode("utf-8", errors="replace").strip() or "write file failed")
        return {
            "host": host,
            "path": path,
            "bytes_written": len(content.encode('utf-8')),
            "appended": append,
            "overwritten": overwrite and not append,
        }
    finally:
        if local_tmp and os.path.exists(local_tmp):
            os.unlink(local_tmp)
        try:
            _run_ssh(config, host, f"bash -lc {shlex.quote(f'rm -f {shlex.quote(remote_tmp)}')}", timeout=15)
        except SSHError:
            pass
