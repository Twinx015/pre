"""User configuration for pre, stored in ~/.config/pre/config.toml."""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

console = Console()

CONFIG_FILE = "config.toml"

DEFAULTS = {
    "paths": {
        "base": ".",
        "tmp": "/tmp",
    },
    "projects": {
        "default_template": "",
    },
    "ansii": {
        "style": "block",
    },
    "strip": {
        "show_deleted": False,
        "squeeze": False,
        "keep": [],
    },
}

DEFAULT_CONFIG = """\
# pre configuration
#
# Edit this file or run `pre config` to see the effective values.

[paths]
# Base directory for new projects and regular clones.
base = "."
# Parent for temporary clones (`pre git clone --tmp`).
# Leave empty to use the system temp directory.
tmp = "/tmp"

[projects]
# Template or builtin used when `pre new` gets no template argument.
# Leave empty to always prompt.
default_template = ""

[ansii]
# Default style for `pre ansii` (see `pre ansii --list-styles`).
style = "block"

[strip]
# Print a diff of removed lines for every changed file.
show_deleted = false
# Collapse blank lines left behind by removed comments.
squeeze = false
# Extra regexes for comments that must never be removed.
keep = []
"""


def config_dir() -> Path:
    """Directory holding templates and the pre config file."""
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "pre"


def config_path(override: str | None = None) -> Path:
    """Path of the config file, honouring --config / $PRE_CONFIG."""
    if override:
        return Path(override).expanduser()
    env = os.environ.get("PRE_CONFIG")
    if env:
        return Path(env).expanduser()
    return config_dir() / CONFIG_FILE


def _warn(message: str) -> None:
    console.print(f"[yellow]! {message}[/]")


def _apply(cfg: dict, section: str, key: str, value, source: Path) -> None:
    default = cfg[section][key]
    where = f"{escape(str(source))}: {section}.{key}"
    if isinstance(default, bool):
        if not isinstance(value, bool):
            _warn(f"{where} must be true or false, ignoring")
            return
    elif isinstance(default, list):
        if not isinstance(value, list):
            _warn(f"{where} must be a list, ignoring")
            return
        value = [str(v) for v in value]
    elif isinstance(default, str):
        if not isinstance(value, str):
            _warn(f"{where} must be a string, ignoring")
            return
    elif not isinstance(value, type(default)):
        _warn(f"{where} has an unexpected type, ignoring")
        return
    cfg[section][key] = value


def load_config(override: str | None = None) -> dict:
    """Load the TOML config, falling back to defaults for missing values."""
    cfg = copy.deepcopy(DEFAULTS)
    path = config_path(override)
    if not path.is_file():
        return cfg
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _warn(f"could not read {escape(str(path))}: {escape(str(exc))}")
        return cfg

    for section, values in data.items():
        if section not in cfg:
            _warn(
                f"{escape(str(path))}: unknown section {escape(f'[{section}]')}, "
                "ignoring"
            )
            continue
        if not isinstance(values, dict):
            _warn(
                f"{escape(str(path))}: {escape(f'[{section}]')} must be a table, "
                "ignoring"
            )
            continue
        for key, value in values.items():
            if key not in cfg[section]:
                _warn(f"{escape(str(path))}: unknown setting {section}.{key}, ignoring")
                continue
            _apply(cfg, section, key, value, path)
    return cfg


def write_default_config(force: bool = False, override: str | None = None) -> bool:
    """Create the default config file; returns False if it already exists."""
    path = config_path(override)
    if path.exists() and not force:
        console.print(
            f"[yellow]! {escape(str(path))} already exists (use --force to overwrite)[/]"
        )
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    except OSError as exc:
        console.print(f"[red]x could not write {escape(str(path))}: {escape(str(exc))}[/]")
        return False
    console.print(f"[green]ok[/] wrote {escape(str(path))}")
    return True


def show_config(cfg: dict | None = None, override: str | None = None) -> None:
    """Print the effective configuration and where it comes from."""
    cfg = cfg or load_config(override)
    path = config_path(override)
    table = Table(title="pre config", title_style="bold", header_style="bold")
    table.add_column("setting", style="cyan", no_wrap=True)
    table.add_column("value")
    for section, values in cfg.items():
        for key, value in values.items():
            if isinstance(value, list):
                value = escape(", ".join(value)) or "[dim](empty)[/]"
            elif isinstance(value, bool):
                value = "[green]true[/]" if value else "[dim]false[/]"
            else:
                value = escape(str(value))
            table.add_row(f"{section}.{key}", value)
    console.print(table)
    if path.is_file():
        console.print(f"[dim]config: {escape(str(path))}[/]")
    else:
        console.print(
            f"[dim]config: {escape(str(path))} (not found, using defaults; "
            "run `pre config --init`)[/]"
        )
