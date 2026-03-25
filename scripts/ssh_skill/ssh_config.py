from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .paths import base_ssh_config_path, managed_ssh_config_path
from .registry import ensure_state_files


@dataclass(frozen=True)
class HostEntry:
    alias: str
    source_file: str
    line_no: int


def expand_user(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def effective_ssh_config_path() -> str:
    ensure_state_files()
    managed = managed_ssh_config_path()
    if managed.exists():
        return str(managed)
    return str(base_ssh_config_path())


def _iter_config_files(path: Path, seen: set[Path]) -> Iterable[Path]:
    resolved = path.expanduser().resolve()
    if resolved in seen or not resolved.exists():
        return
    seen.add(resolved)
    yield resolved

    try:
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return

    base_dir = resolved.parent
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        if key.lower() != "include":
            continue
        for pattern in value.split():
            expanded = expand_user(pattern)
            if not os.path.isabs(expanded):
                expanded = str(base_dir / expanded)
            for match in sorted(glob.glob(expanded)):
                yield from _iter_config_files(Path(match), seen)


def _is_real_alias(pattern: str) -> bool:
    return not any(ch in pattern for ch in "*?!")


def list_host_entries(config_path: str | None = None) -> list[HostEntry]:
    root = Path(config_path or effective_ssh_config_path())
    seen_files: set[Path] = set()
    results: dict[str, HostEntry] = {}
    for config_file in _iter_config_files(root, seen_files):
        try:
            lines = config_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for idx, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition(" ")
            if key.lower() != "host":
                continue
            for alias in value.split():
                if alias and _is_real_alias(alias):
                    results.setdefault(alias, HostEntry(alias=alias, source_file=str(config_file), line_no=idx))
    return sorted(results.values(), key=lambda item: item.alias.lower())
