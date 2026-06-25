from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable

from . import __version__
from .ai_tool_profiles import get_ai_tool_profile, list_ai_tool_profiles
from .ai_tooling import detect_ai_tools, inspect_ai_workspace, run_ai_tool
from .cluster_profiles import get_cluster_profile_for_host, list_cluster_profiles
from .registry import ensure_state_files
from .slurm_ops import (
    cancel_job,
    get_job_accounting,
    get_job_info,
    list_partitions,
    list_queue,
    render_slurm_script,
    submit_batch,
)
from .ssh_ops import (
    SSHError,
    SSHRuntimeConfig,
    check_port,
    check_process,
    download,
    exec_command,
    find_files,
    grep_text,
    list_conda_envs,
    list_dir,
    list_hosts,
    read_file,
    sync_dir,
    start_process,
    stop_process,
    tail_file,
    upload,
    uv_sync,
    write_file,
)


JSON = dict[str, Any]


def _tool_content(payload: Any, *, is_error: bool = False) -> JSON:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "structuredContent": payload if isinstance(payload, dict) else {"result": payload},
        "isError": is_error,
    }


class StdioJSONRPC:
    def __init__(self) -> None:
        self.reader = sys.stdin.buffer
        self.writer = sys.stdout.buffer
        self.mode: str | None = None

    def _detect_mode(self) -> str:
        if self.mode is not None:
            return self.mode
        preview = self.reader.peek(64)
        stripped = preview.lstrip()
        self.mode = "header" if stripped.startswith(b"Content-Length:") else "ndjson"
        return self.mode

    def read_message(self) -> JSON | None:
        return self._read_header_message() if self._detect_mode() == "header" else self._read_ndjson_message()

    def _read_ndjson_message(self) -> JSON | None:
        line = self.reader.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            return self._read_ndjson_message()
        return json.loads(line.decode("utf-8"))

    def _read_header_message(self) -> JSON | None:
        content_length = None
        while True:
            line = self.reader.readline()
            if not line:
                return None
            line_text = line.decode("utf-8", errors="replace").strip()
            if not line_text:
                break
            if line_text.lower().startswith("content-length:"):
                content_length = int(line_text.split(":", 1)[1].strip())
        if content_length is None:
            raise ValueError("missing Content-Length header")
        raw = self.reader.read(content_length)
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def write_message(self, message: JSON) -> None:
        raw = json.dumps(message, ensure_ascii=False).encode("utf-8")
        if self._detect_mode() == "header":
            self.writer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii"))
            self.writer.write(raw)
        else:
            self.writer.write(raw + b"\n")
        self.writer.flush()


class SSHSkillServer:
    def __init__(self, runtime: SSHRuntimeConfig) -> None:
        self.runtime = runtime
        self.transport = StdioJSONRPC()
        self.tools = self._build_tools()

    def _build_tools(self) -> dict[str, tuple[JSON, Callable[[JSON], JSON]]]:
        return {
            "ssh_list_hosts": (
                {
                    "name": "ssh_list_hosts",
                    "description": "List available SSH host aliases from the effective ssh-skill configuration.",
                    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                },
                lambda args: list_hosts(self.runtime),
            ),
            "ssh_list_cluster_profiles": (
                {
                    "name": "ssh_list_cluster_profiles",
                    "description": "List built-in and custom cluster profiles that extend ssh-skill for specific Slurm or cluster environments.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "scheduler": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                lambda args: list_cluster_profiles(**args),
            ),
            "ssh_list_ai_tool_profiles": (
                {
                    "name": "ssh_list_ai_tool_profiles",
                    "description": "List built-in and custom AI coding tool profiles, including OpenCode, Claude Code, Gemini CLI, and Cursor Agent CLI.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "supports_json_output": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                },
                lambda args: list_ai_tool_profiles(**args),
            ),
            "ssh_get_ai_tool_profile": (
                {
                    "name": "ssh_get_ai_tool_profile",
                    "description": "Resolve an AI coding tool profile by id or alias, including custom JSON-defined profiles under the skill's ai_tool_profiles state directory.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "profile_id": {"type": "string"},
                        },
                        "required": ["profile_id"],
                        "additionalProperties": False,
                    },
                },
                lambda args: get_ai_tool_profile(**args),
            ),
            "ssh_get_cluster_profile": (
                {
                    "name": "ssh_get_cluster_profile",
                    "description": "Resolve a cluster profile by profile id or managed host alias. Useful for host-specific Slurm rules and open-source profile extensions.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "profile_id": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                lambda args: get_cluster_profile_for_host(**args),
            ),
            "ssh_detect_ai_tools": (
                {
                    "name": "ssh_detect_ai_tools",
                    "description": "Detect remote AI coding CLIs, auth hints, and workspace instruction files for hosts that may run Claude Code, Gemini CLI, Cursor, or OpenCode.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "workdir": {"type": "string"},
                            "profile_ids": {"type": "array", "items": {"type": "string"}},
                            "timeout_sec": {"type": "integer"},
                        },
                        "required": ["host"],
                        "additionalProperties": False,
                    },
                },
                lambda args: detect_ai_tools(self.runtime, **args),
            ),
            "ssh_inspect_ai_workspace": (
                {
                    "name": "ssh_inspect_ai_workspace",
                    "description": "Inspect a remote project for agent instruction files, repo markers, and missing helper tools such as git, rg, uv, node, or Slurm CLIs.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "path": {"type": "string"},
                            "timeout_sec": {"type": "integer"},
                        },
                        "required": ["host"],
                        "additionalProperties": False,
                    },
                },
                lambda args: inspect_ai_workspace(self.runtime, **args),
            ),
            "ssh_run_ai_tool": (
                {
                    "name": "ssh_run_ai_tool",
                    "description": "Run a non-interactive remote AI coding CLI through a named profile. Defaults to analyze mode so the prompt stays read-only unless execution was explicitly requested.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "profile_id": {"type": "string"},
                            "prompt": {"type": "string"},
                            "workdir": {"type": "string"},
                            "mode": {"type": "string"},
                            "output_format": {"type": "string"},
                            "extra_args": {"type": "array", "items": {"type": "string"}},
                            "env": {"type": "object", "additionalProperties": {"type": "string"}},
                            "timeout_sec": {"type": "integer"},
                            "conda_env_name": {"type": "string"},
                            "conda_env_prefix": {"type": "string"},
                        },
                        "required": ["host", "profile_id", "prompt"],
                        "additionalProperties": False,
                    },
                },
                lambda args: run_ai_tool(self.runtime, **args),
            ),
            "ssh_sinfo": (
                {
                    "name": "ssh_sinfo",
                    "description": "List Slurm partitions and basic GPU or node availability for a managed cluster host.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "partition": {"type": "string"},
                            "states": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["host"],
                        "additionalProperties": False,
                    },
                },
                lambda args: list_partitions(self.runtime, **args),
            ),
            "ssh_squeue": (
                {
                    "name": "ssh_squeue",
                    "description": "List Slurm jobs on a managed cluster host, usually for the current user.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "job_id": {"type": "string"},
                            "user": {"type": "string"},
                            "current_user_only": {"type": "boolean"},
                            "states": {"type": "array", "items": {"type": "string"}},
                            "max_results": {"type": "integer"},
                        },
                        "required": ["host"],
                        "additionalProperties": False,
                    },
                },
                lambda args: list_queue(self.runtime, **args),
            ),
            "ssh_sacct": (
                {
                    "name": "ssh_sacct",
                    "description": "Inspect Slurm accounting records for a job, including state, exit code, and allocated resources.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "job_id": {"type": "string"},
                        },
                        "required": ["host", "job_id"],
                        "additionalProperties": False,
                    },
                },
                lambda args: get_job_accounting(self.runtime, **args),
            ),
            "ssh_slurm_job_info": (
                {
                    "name": "ssh_slurm_job_info",
                    "description": "Show detailed Slurm job information from scontrol for a specific job id.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "job_id": {"type": "string"},
                        },
                        "required": ["host", "job_id"],
                        "additionalProperties": False,
                    },
                },
                lambda args: get_job_info(self.runtime, **args),
            ),
            "ssh_render_slurm_script": (
                {
                    "name": "ssh_render_slurm_script",
                    "description": "Render a reusable Slurm batch script for remote training jobs such as single-node multi-GPU runs.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "job_name": {"type": "string"},
                            "host": {"type": "string"},
                            "profile_id": {"type": "string"},
                            "workdir": {"type": "string"},
                            "partition": {"type": "string"},
                            "account": {"type": "string"},
                            "time_limit": {"type": "string"},
                            "nodes": {"type": "integer"},
                            "ntasks_per_node": {"type": "integer"},
                            "cpus_per_task": {"type": "integer"},
                            "mem": {"type": "string"},
                            "gpus_per_node": {"type": "integer"},
                            "gres": {"type": "string"},
                            "output_path": {"type": "string"},
                            "error_path": {"type": "string"},
                            "module_loads": {"type": "array", "items": {"type": "string"}},
                            "setup_commands": {"type": "array", "items": {"type": "string"}},
                            "extra_sbatch": {"type": "array", "items": {"type": "string"}},
                            "conda_env_name": {"type": "string"},
                            "conda_env_prefix": {"type": "string"},
                        },
                        "required": ["command"],
                        "additionalProperties": False,
                    },
                },
                lambda args: render_slurm_script(**args),
            ),
            "ssh_sbatch_submit": (
                {
                    "name": "ssh_sbatch_submit",
                    "description": "Submit a Slurm batch script on a managed cluster host, using an existing remote script path or inline script content.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "script_path": {"type": "string"},
                            "script_content": {"type": "string"},
                            "remote_path": {"type": "string"},
                            "workdir": {"type": "string"},
                            "additional_args": {"type": "array", "items": {"type": "string"}},
                            "test_only": {"type": "boolean"},
                            "timeout_sec": {"type": "integer"},
                        },
                        "required": ["host"],
                        "additionalProperties": False,
                    },
                },
                lambda args: submit_batch(self.runtime, **args),
            ),
            "ssh_scancel": (
                {
                    "name": "ssh_scancel",
                    "description": "Cancel one or more Slurm jobs. This is blocked by default unless allow_cancel=true is explicitly provided.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "job_ids": {"type": "array", "items": {"type": "string"}},
                            "signal": {"type": "string"},
                            "allow_cancel": {"type": "boolean"},
                        },
                        "required": ["host", "job_ids"],
                        "additionalProperties": False,
                    },
                },
                lambda args: cancel_job(self.runtime, **args),
            ),
            "ssh_exec": (
                {
                    "name": "ssh_exec",
                    "description": "Execute a shell command on a remote host over SSH.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "command": {"type": "string"},
                            "workdir": {"type": "string"},
                            "env": {"type": "object", "additionalProperties": {"type": "string"}},
                            "timeout_sec": {"type": "integer"},
                            "allow_destructive": {"type": "boolean"},
                            "conda_env_name": {"type": "string"},
                            "conda_env_prefix": {"type": "string"},
                        },
                        "required": ["host", "command"],
                        "additionalProperties": False,
                    },
                },
                lambda args: exec_command(self.runtime, **args),
            ),
            "ssh_upload": (
                {
                    "name": "ssh_upload",
                    "description": "Upload a local file or directory to a remote host.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "local_path": {"type": "string"},
                            "remote_path": {"type": "string"},
                            "overwrite": {"type": "boolean"},
                            "preserve_mode": {"type": "boolean"},
                            "recursive": {"type": "boolean"},
                        },
                        "required": ["host", "local_path", "remote_path"],
                        "additionalProperties": False,
                    },
                },
                lambda args: upload(self.runtime, **args),
            ),
            "ssh_download": (
                {
                    "name": "ssh_download",
                    "description": "Download a remote file or directory from a host.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "remote_path": {"type": "string"},
                            "local_path": {"type": "string"},
                            "overwrite": {"type": "boolean"},
                            "preserve_mode": {"type": "boolean"},
                            "recursive": {"type": "boolean"},
                        },
                        "required": ["host", "remote_path", "local_path"],
                        "additionalProperties": False,
                    },
                },
                lambda args: download(self.runtime, **args),
            ),
            "ssh_sync_dir": (
                {
                    "name": "ssh_sync_dir",
                    "description": "Synchronize a local directory to a remote directory using rsync. Useful for pushing local code changes before remote runs.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "local_path": {"type": "string"},
                            "remote_path": {"type": "string"},
                            "delete": {"type": "boolean"},
                            "exclude": {"type": "array", "items": {"type": "string"}},
                            "use_default_excludes": {"type": "boolean"},
                            "dry_run": {"type": "boolean"},
                            "allow_destructive": {"type": "boolean"},
                        },
                        "required": ["host", "local_path", "remote_path"],
                        "additionalProperties": False,
                    },
                },
                lambda args: sync_dir(self.runtime, **args),
            ),
            "ssh_list_dir": (
                {
                    "name": "ssh_list_dir",
                    "description": "List entries in a remote directory.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "path": {"type": "string"},
                            "max_entries": {"type": "integer"},
                        },
                        "required": ["host"],
                        "additionalProperties": False,
                    },
                },
                lambda args: list_dir(self.runtime, **args),
            ),
            "ssh_list_conda_envs": (
                {
                    "name": "ssh_list_conda_envs",
                    "description": "List Conda environments available on the remote host.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                        },
                        "required": ["host"],
                        "additionalProperties": False,
                    },
                },
                lambda args: list_conda_envs(self.runtime, **args),
            ),
            "ssh_uv_sync": (
                {
                    "name": "ssh_uv_sync",
                    "description": "Run uv sync inside a remote project directory while keeping the remote virtual environment independent from any local .venv.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "project_dir": {"type": "string"},
                            "env_dir": {"type": "string"},
                            "frozen": {"type": "boolean"},
                            "no_dev": {"type": "boolean"},
                            "extra_args": {"type": "array", "items": {"type": "string"}},
                            "timeout_sec": {"type": "integer"},
                        },
                        "required": ["host", "project_dir"],
                        "additionalProperties": False,
                    },
                },
                lambda args: uv_sync(self.runtime, **args),
            ),
            "ssh_find_files": (
                {
                    "name": "ssh_find_files",
                    "description": "Find files on a remote host, optionally filtered by filename glob.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "path": {"type": "string"},
                            "glob": {"type": "string"},
                            "max_results": {"type": "integer"},
                            "include_hidden": {"type": "boolean"},
                        },
                        "required": ["host"],
                        "additionalProperties": False,
                    },
                },
                lambda args: find_files(self.runtime, **args),
            ),
            "ssh_grep": (
                {
                    "name": "ssh_grep",
                    "description": "Search remote code or text files for a pattern, similar to grep or ripgrep.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "pattern": {"type": "string"},
                            "path": {"type": "string"},
                            "glob": {"type": "string"},
                            "max_results": {"type": "integer"},
                            "ignore_case": {"type": "boolean"},
                            "include_hidden": {"type": "boolean"},
                        },
                        "required": ["host", "pattern"],
                        "additionalProperties": False,
                    },
                },
                lambda args: grep_text(self.runtime, **args),
            ),
            "ssh_read_file": (
                {
                    "name": "ssh_read_file",
                    "description": "Read a remote text file by line range.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "path": {"type": "string"},
                            "start_line": {"type": "integer"},
                            "line_count": {"type": "integer"},
                            "max_bytes": {"type": "integer"},
                        },
                        "required": ["host", "path"],
                        "additionalProperties": False,
                    },
                },
                lambda args: read_file(self.runtime, **args),
            ),
            "ssh_write_file": (
                {
                    "name": "ssh_write_file",
                    "description": "Write or append a small remote file.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                            "overwrite": {"type": "boolean"},
                            "append": {"type": "boolean"},
                            "create_dirs": {"type": "boolean"},
                            "mode": {"type": "string"},
                        },
                        "required": ["host", "path", "content"],
                        "additionalProperties": False,
                    },
                },
                lambda args: write_file(self.runtime, **args),
            ),
            "ssh_tail_file": (
                {
                    "name": "ssh_tail_file",
                    "description": "Read the tail of a remote text file.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "path": {"type": "string"},
                            "lines": {"type": "integer"},
                            "max_bytes": {"type": "integer"},
                        },
                        "required": ["host", "path"],
                        "additionalProperties": False,
                    },
                },
                lambda args: tail_file(self.runtime, **args),
            ),
            "ssh_start_process": (
                {
                    "name": "ssh_start_process",
                    "description": "Start a remote background process for service runs or debug sessions and return its pid, pid file, and log path.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "command": {"type": "string"},
                            "workdir": {"type": "string"},
                            "env": {"type": "object", "additionalProperties": {"type": "string"}},
                            "log_path": {"type": "string"},
                            "pid_file": {"type": "string"},
                            "allow_destructive": {"type": "boolean"},
                            "conda_env_name": {"type": "string"},
                            "conda_env_prefix": {"type": "string"},
                        },
                        "required": ["host", "command"],
                        "additionalProperties": False,
                    },
                },
                lambda args: start_process(self.runtime, **args),
            ),
            "ssh_check_process": (
                {
                    "name": "ssh_check_process",
                    "description": "Check whether a remote process is still running by pid or pid file.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "pid": {"type": "integer"},
                            "pid_file": {"type": "string"},
                        },
                        "required": ["host"],
                        "additionalProperties": False,
                    },
                },
                lambda args: check_process(self.runtime, **args),
            ),
            "ssh_stop_process": (
                {
                    "name": "ssh_stop_process",
                    "description": "Stop a remote background process by pid or pid file.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "pid": {"type": "integer"},
                            "pid_file": {"type": "string"},
                            "signal": {"type": "string"},
                            "wait_sec": {"type": "integer"},
                        },
                        "required": ["host"],
                        "additionalProperties": False,
                    },
                },
                lambda args: stop_process(self.runtime, **args),
            ),
            "ssh_check_port": (
                {
                    "name": "ssh_check_port",
                    "description": "Check whether a TCP port is open or listening on the remote host.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "port": {"type": "integer"},
                            "listen_only": {"type": "boolean"},
                        },
                        "required": ["host", "port"],
                        "additionalProperties": False,
                    },
                },
                lambda args: check_port(self.runtime, **args),
            ),
        }

    def run(self) -> int:
        while True:
            message = self.transport.read_message()
            if message is None:
                return 0
            response = self._handle_message(message)
            if response is not None:
                self.transport.write_message(response)

    def _handle_message(self, message: JSON) -> JSON | None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            protocol_version = params.get("protocolVersion") or "2024-11-05"
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "ssh-skill", "version": __version__},
                    "instructions": "Use ssh-skill to manage registered servers, inspect code, edit configs, detect remote AI CLIs, and operate remote environments safely.",
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": [tool for tool, _ in self.tools.values()]}}
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name not in self.tools:
                return {"jsonrpc": "2.0", "id": request_id, "result": _tool_content({"error": f"unknown tool: {name}"}, is_error=True)}
            _, handler = self.tools[name]
            try:
                result = handler(arguments)
                return {"jsonrpc": "2.0", "id": request_id, "result": _tool_content(result)}
            except SSHError as exc:
                return {"jsonrpc": "2.0", "id": request_id, "result": _tool_content({"error": str(exc)}, is_error=True)}
            except ValueError as exc:
                return {"jsonrpc": "2.0", "id": request_id, "result": _tool_content({"error": str(exc)}, is_error=True)}
            except TypeError as exc:
                return {"jsonrpc": "2.0", "id": request_id, "result": _tool_content({"error": f"invalid arguments: {exc}"}, is_error=True)}
            except Exception as exc:
                return {"jsonrpc": "2.0", "id": request_id, "result": _tool_content({"error": f"internal error: {exc}"}, is_error=True)}
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ssh-skill runtime")
    parser.add_argument("--ssh-config")
    parser.add_argument("--default-timeout-sec", type=int, default=120)
    parser.add_argument("--max-output-bytes", type=int, default=1 << 20)
    parser.add_argument("--list-hosts", action="store_true")
    args = parser.parse_args(argv)

    ensure_state_files()
    runtime = SSHRuntimeConfig(
        ssh_config_path=args.ssh_config,
        default_timeout_sec=args.default_timeout_sec,
        max_output_bytes=args.max_output_bytes,
    )
    if args.list_hosts:
        sys.stdout.write(json.dumps(list_hosts(runtime), ensure_ascii=False, indent=2) + "\n")
        return 0
    return SSHSkillServer(runtime).run()


if __name__ == "__main__":
    raise SystemExit(main())
