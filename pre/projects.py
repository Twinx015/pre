from __future__ import annotations

import fnmatch
import getpass
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

try:
    from . import git
    from .config import config_dir
except ImportError:  # running as a plain script
    import git
    from config import config_dir

console = Console()

META_FILE = "pre.toml"
ALWAYS_IGNORE = [".git", "__pycache__", ".DS_Store", "node_modules", ".venv"]
TREE_HIDE = {".git", "node_modules", ".venv", "target", "__pycache__"}
SAMPLE_NAME = "python-cli"

PLACEHOLDER = re.compile(r"(\\)?\{\{\s*([A-Za-z_]\w*)((?:\s*\|\s*\w+)*)\s*\}\}")


SYNTAX_HELP = """\
LAYOUT
  ~/.config/pre/<template>/      one directory per template
      pre.toml                   optional metadata (never copied)
      ...everything else         copied into the new project

PLACEHOLDERS   (in file contents AND in file/directory names)
  {{ name }}                     the value of a variable
  {{ name | snake }}             apply a filter; chain with more pipes
  \\{{ name }}                    escaped: writes a literal {{ name }}

BUILT-IN VARIABLES
  name      project name (last part of the target path)
  author    git config user.name, else $USER
  year      current year          date   current date (YYYY-MM-DD)

FILTERS
  snake    my_app          kebab   my-app         pascal   MyApp
  camel    myApp           title   My App         upper    MY APP
  lower    my app

pre.toml
  description = "shown in the list"
  post   = ["git init", "uv sync"]     commands run in the project (asked first)
  ignore = ["*.bak", "drafts"]         globs, never copied
  raw    = [".github/**", "*.png"]     globs copied byte-for-byte, no rendering
                                       (use for files that contain literal {{ }})

  [vars]                               extra variables, prompted at creation
  description = "{{ name | title }} tool"          # plain string = default
  license = { default = "MIT", choices = ["MIT", "GPL-3.0"], prompt = "License" }

  Defaults may use placeholders from variables defined above them.

NOTES
  * Binary files are detected automatically and copied as-is.
  * Existing files in the target are never overwritten.
  * Override variables non-interactively with: -D key=value
  * Name a template like a builtin (uv, npm, ...) and the template wins.
"""


SAMPLE_FILES = {
    "pre.toml": """\
description = "Python CLI package (uv, src layout, rich)"
ignore = ["*.pyc"]
raw = [".github/**"]
post = ["git init", "uv sync"]

[vars]
description = "{{ name | title }} command line tool"
python = { default = "3.12", choices = ["3.11", "3.12", "3.13"], prompt = "Python version" }
license = { default = "MIT", choices = ["MIT", "Apache-2.0", "GPL-3.0"], prompt = "License" }
""",
    "pyproject.toml": """\
[project]
name = "{{ name | kebab }}"
version = "0.1.0"
description = "{{ description }}"
readme = "README.md"
license = { text = "{{ license }}" }
authors = [{ name = "{{ author }}" }]
requires-python = ">={{ python }}"
dependencies = ["rich"]

[project.scripts]
{{ name | kebab }} = "{{ name | snake }}.cli:run"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
""",
    "README.md": """\
# {{ name | title }}

{{ description }}

```
uv run {{ name | kebab }}
```

License: {{ license }} - {{ author }}, {{ year }}
""",
    ".gitignore": """\
__pycache__/
.venv/
dist/
*.egg-info/
""",
    "src/{{name|snake}}/__init__.py": '''\
"""{{ description }}"""

__version__ = "0.1.0"
''',
    "src/{{name|snake}}/cli.py": '''\
"""{{ description }}"""
from rich.console import Console

console = Console()


def greet(who: str = "world") -> None:
    console.print(f"[bold green]Hello[/], {who}!")


def run() -> None:
    greet("{{ name | title }}")


if __name__ == "__main__":
    run()
''',
    ".github/workflows/ci.yml": """\
name: ci
on: [push]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - run: echo "ref is ${{ github.ref }}"
""",
}

TEMPLATE_SKELETON = """\
description = "{desc}"
# post = ["git init"]
# ignore = ["*.bak"]
# raw = ["assets/**"]

[vars]
# description = "{{{{ name | title }}}} project"
# license = {{ default = "MIT", choices = ["MIT", "GPL-3.0"] }}
"""


BUILTINS = {
    "uv": {
        "desc": "uv init (application)",
        "cmd": ["uv", "init", "--name", "{{ name | kebab }}"],
    },
    "uv-lib": {
        "desc": "uv init --lib",
        "cmd": ["uv", "init", "--lib", "--name", "{{ name | kebab }}"],
    },
    "uv-pkg": {
        "desc": "uv init --package",
        "cmd": ["uv", "init", "--package", "--name", "{{ name | kebab }}"],
    },
    "npm": {"desc": "npm init (interactive)", "cmd": ["npm", "init"]},
    "npm-y": {"desc": "npm init -y", "cmd": ["npm", "init", "-y"]},
    "cargo": {
        "desc": "cargo init",
        "cmd": ["cargo", "init", "--name", "{{ name | snake }}"],
    },
    "go": {"desc": "go mod init", "cmd": ["go", "mod", "init", "{{ name }}"]},
    "git": {"desc": "git init", "cmd": ["git", "init"]},
    "empty": {"desc": "empty directory", "cmd": []},
}


def split_words(text: str) -> list[str]:
    return [w for w in re.split(r"[^0-9a-zA-Z]+|(?<=[a-z0-9])(?=[A-Z])", text) if w]


FILTERS = {
    "snake": lambda s: "_".join(split_words(s)).lower(),
    "kebab": lambda s: "-".join(split_words(s)).lower(),
    "lower": lambda s: " ".join(split_words(s)).lower(),
    "upper": lambda s: " ".join(split_words(s)).upper(),
    "title": lambda s: " ".join(w.capitalize() for w in split_words(s)),
    "pascal": lambda s: "".join(w.capitalize() for w in split_words(s)),
    "camel": lambda s: (
        lambda ws: ws[0].lower() + "".join(w.capitalize() for w in ws[1:]) if ws else ""
    )(split_words(s)),
}


def detect_author() -> str:
    try:
        out = subprocess.run(
            ["git", "config", "user.name"], capture_output=True, text=True, timeout=3
        ).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    return getpass.getuser()


def build_context(name: str, overrides: dict | None = None) -> dict:
    ctx = {
        "name": name,
        "author": detect_author(),
        "year": str(date.today().year),
        "date": date.today().isoformat(),
    }
    ctx.update(overrides or {})
    return ctx


def render(text: str, ctx: dict) -> str:
    def sub(m: re.Match) -> str:
        if m.group(1):
            return m.group(0)[1:]
        key, chain = m.group(2), m.group(3)
        if key not in ctx:
            return m.group(0)
        value = str(ctx[key])
        for f in (x.strip() for x in chain.split("|")):
            if f in FILTERS:
                value = FILTERS[f](value)
        return value

    return PLACEHOLDER.sub(sub, text)


def matches_any(rel: Path, patterns: list[str]) -> bool:
    posix = rel.as_posix()
    return any(
        fnmatch.fnmatch(posix, pat)
        or any(fnmatch.fnmatch(part, pat) for part in rel.parts)
        for pat in patterns
    )


def list_templates() -> dict[str, Path]:
    root = config_dir()
    if not root.is_dir():
        return {}
    return {
        p.name: p
        for p in sorted(root.iterdir())
        if p.is_dir() and not p.name.startswith(".")
    }


def load_meta(path: Path) -> dict:
    meta = {"description": "", "post": [], "ignore": [], "raw": [], "vars": {}}
    f = path / META_FILE
    if f.is_file():
        try:
            meta.update(tomllib.loads(f.read_text("utf-8")))
        except (tomllib.TOMLDecodeError, OSError) as e:
            console.print(
                f"[yellow]! bad {META_FILE} in {escape(path.name)}: {escape(str(e))}[/]"
            )
    return meta


def normalize_var(key: str, spec) -> dict:
    if isinstance(spec, dict):
        return {
            "default": str(spec.get("default", "")),
            "prompt": str(spec.get("prompt", key)),
            "choices": [str(c) for c in spec.get("choices", [])],
        }
    return {"default": str(spec), "prompt": key, "choices": []}


def builtin_available(spec: dict) -> bool:
    return not spec["cmd"] or shutil.which(spec["cmd"][0]) is not None


def resolve_source(name: str):
    templates = list_templates()
    if name in templates:
        return ("template", templates[name])
    if name in BUILTINS:
        return ("builtin", BUILTINS[name])
    return None


def show_sources() -> None:
    table = Table(title="pre", title_style="bold", header_style="bold")
    table.add_column("name", style="cyan", no_wrap=True)
    table.add_column("kind")
    table.add_column("description")
    templates = list_templates()
    for name, path in templates.items():
        table.add_row(escape(name), "template", escape(load_meta(path)["description"]))
    for key, spec in BUILTINS.items():
        if key in templates:
            continue
        note = "" if builtin_available(spec) else " [red](not installed)[/]"
        table.add_row(key, "[dim]builtin[/]", escape(spec["desc"]) + note)
    console.print(table)
    console.print(f"[dim]templates dir: {escape(str(config_dir()))}[/]")


def pick_source():
    templates = list_templates()
    names = list(templates) + [
        k
        for k, spec in BUILTINS.items()
        if k not in templates and builtin_available(spec)
    ]
    show_sources()
    choice = Prompt.ask("Template", choices=names, default=names[0], console=console)
    return resolve_source(choice)


def prompt_name() -> str:
    while True:
        name = Prompt.ask("Project name", console=console).strip()
        if name:
            return name


def check_target(target: Path, yes: bool) -> bool:
    if target.exists() and not target.is_dir():
        console.print(f"[red]x {escape(str(target))} exists and is not a directory[/]")
        return False
    if target.is_dir() and any(target.iterdir()):
        if yes:
            return True
        return Confirm.ask(
            f"[yellow]{escape(str(target))} is not empty.[/] Continue (existing files are kept)?",
            default=False,
            console=console,
        )
    return True


def ask_vars(meta: dict, ctx: dict, yes: bool, overrides: dict) -> None:
    for key, spec in meta["vars"].items():
        if key in overrides:
            ctx[key] = overrides[key]
            continue
        v = normalize_var(key, spec)
        default = render(v["default"], ctx)
        if yes:
            ctx[key] = default
            continue
        ctx[key] = Prompt.ask(
            f"  {escape(v['prompt'])}",
            default=default,
            choices=v["choices"] or None,
            console=console,
        )


def write_rendered(src: Path, out: Path, ctx: dict, raw: bool) -> None:
    if not raw:
        try:
            text = src.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raw = True
    if raw:
        shutil.copy2(src, out)
        return
    out.write_bytes(render(text, ctx).encode("utf-8"))
    shutil.copymode(src, out)


def copy_template(
    src: Path, dest: Path, ctx: dict, meta: dict
) -> tuple[list[Path], list[Path]]:
    ignore = ALWAYS_IGNORE + [str(p) for p in meta["ignore"]]
    raw = [str(p) for p in meta["raw"]]
    created, skipped = [], []
    dest_root = dest.resolve()
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if rel.as_posix() == META_FILE or matches_any(rel, ignore):
            continue
        out = dest.joinpath(*(render(part, ctx) for part in rel.parts))
        if dest_root not in out.resolve().parents and out.resolve() != dest_root:
            console.print(
                f"[yellow]! skipped {escape(str(rel))} (renders outside the project)[/]"
            )
            continue
        if path.is_dir():
            out.mkdir(parents=True, exist_ok=True)
            continue
        if out.exists():
            skipped.append(out)
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        write_rendered(path, out, ctx, matches_any(rel, raw))
        created.append(out)
    return created, skipped


def run_hooks(post: list[str], target: Path, ctx: dict, yes: bool) -> None:
    cmds = [render(str(c), ctx) for c in post]
    if not cmds:
        return
    console.print(
        Panel(
            Text("\n".join(f"$ {c}" for c in cmds)),
            title="post-create",
            border_style="dim",
        )
    )
    if not yes and not Confirm.ask("Run these?", default=True, console=console):
        return
    for c in cmds:
        console.print(Text(f"$ {c}", style="dim"))
        result = subprocess.run(c, shell=True, cwd=target)
        if result.returncode:
            console.print(
                f"[yellow]! `{escape(c)}` exited with {result.returncode}, stopping hooks[/]"
            )
            break


def run_builtin(spec: dict, target: Path, ctx: dict) -> bool:
    cmd = [render(part, ctx) for part in spec["cmd"]]
    if not cmd:
        return True
    if shutil.which(cmd[0]) is None:
        console.print(f"[red]x `{escape(cmd[0])}` is not installed[/]")
        return False
    console.rule(Text(" ".join(cmd), style="dim"), style="dim")
    result = subprocess.run(cmd, cwd=target)
    console.rule(style="dim")
    if result.returncode:
        console.print(f"[red]x `{escape(cmd[0])}` exited with {result.returncode}[/]")
        return False
    return True


def show_tree(root: Path, max_depth: int = 3) -> None:
    def add_children(node: Tree, directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        for p in sorted(directory.iterdir(), key=lambda x: (x.is_file(), x.name)):
            if p.name in TREE_HIDE:
                continue
            if p.is_dir():
                add_children(node.add(f"[blue]{escape(p.name)}/[/]"), p, depth + 1)
            else:
                node.add(escape(p.name))

    tree = Tree(f"[bold]{escape(root.name)}/[/]")
    add_children(tree, root, 1)
    console.print(tree)


def create_project(
    source,
    name: str,
    dest_root: str = ".",
    path: str | None = None,
    yes: bool = False,
    hooks: bool = True,
    overrides: dict | None = None,
) -> Path | None:
    kind, obj = source
    overrides = overrides or {}
    if path:
        target = Path(path).expanduser().resolve()
    else:
        target = (Path(dest_root).expanduser() / name).resolve()
    if not check_target(target, yes):
        return None
    target.mkdir(parents=True, exist_ok=True)
    ctx = build_context(name, overrides)

    if kind == "template":
        meta = load_meta(obj)
        ask_vars(meta, ctx, yes, overrides)
        created, skipped = copy_template(obj, target, ctx, meta)
        console.print(
            f"[green]ok[/] wrote {len(created)} files"
            + (f", kept {len(skipped)} existing" if skipped else "")
        )
        if hooks:
            run_hooks(meta["post"], target, ctx, yes)
    elif not run_builtin(obj, target, ctx):
        return None

    show_tree(target)
    console.print(Panel(f"[green]created[/] {escape(str(target))}", expand=False))
    return target


def write_files(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        out = root / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")


def install_sample() -> Path | None:
    path = config_dir() / SAMPLE_NAME
    if path.exists():
        console.print(
            f"[yellow]! {escape(str(path))} already exists, not touching it[/]"
        )
        return None
    write_files(path, SAMPLE_FILES)
    console.print(f"[green]ok[/] sample template installed: {escape(str(path))}")
    return path


def scaffold_template(name: str) -> Path | None:
    path = config_dir() / name
    if path.exists():
        console.print(f"[red]x {escape(str(path))} already exists[/]")
        return None
    write_files(
        path,
        {
            META_FILE: TEMPLATE_SKELETON.format(desc=f"{name} template"),
            "README.md": "# {{ name | title }}\n",
        },
    )
    console.print(f"[green]ok[/] new template: {escape(str(path))}")
    return path


def ensure_config() -> None:
    root = config_dir()
    if root.exists() or not sys.stdin.isatty():
        return
    if Confirm.ask(
        f"[dim]{escape(str(root))}[/] doesn't exist. Install the sample template?",
        default=True,
        console=console,
    ):
        install_sample()
    else:
        root.mkdir(parents=True, exist_ok=True)


def show_syntax() -> None:
    console.print(
        Panel(Text(SYNTAX_HELP), title="template syntax", border_style="cyan")
    )


def parse_overrides(items: list[str]) -> dict:
    out = {}
    for item in items:
        key, sep, value = item.partition("=")
        if sep and key.strip():
            out[key.strip()] = value
        else:
            console.print(
                f"[yellow]! ignoring -D {escape(item)} (expected KEY=VALUE)[/]"
            )
    return out


def looks_like_path(value: str) -> bool:
    """True when a project name should be treated as a destination path."""
    return value.startswith(("~", ".")) or "/" in value or "\\" in value


def run_new(args, cfg: dict) -> int:
    """Run the `pre new` command using an already-parsed namespace."""
    try:
        if args.syntax:
            show_syntax()
            return 0
        if args.sample:
            return 0 if install_sample() else 1
        if args.new_template:
            return 0 if scaffold_template(args.new_template) else 1
        if args.list:
            show_sources()
            return 0

        ensure_config()
        template = args.template or cfg["projects"]["default_template"]
        if template:
            source = resolve_source(template)
            if source is None:
                console.print(
                    f"[red]x unknown template or builtin: {escape(template)}[/]"
                )
                show_sources()
                return 1
        else:
            source = pick_source()

        path = getattr(args, "path", None)
        name = args.name
        if path is None and name and looks_like_path(name):
            path, name = name, None
        if path and not name:
            name = Path(path).expanduser().resolve().name
        name = name or prompt_name()

        target = create_project(
            source,
            name,
            dest_root=args.dir or cfg["paths"]["base"],
            path=path,
            yes=args.yes,
            hooks=not args.no_hooks,
            overrides=parse_overrides(args.var),
        )
        if target:
            git.maybe_publish(target, args.yes, getattr(args, "commit", None))
        return 0 if target else 1
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]cancelled[/]")
        return 130
