import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.markup import escape
from rich.prompt import Confirm, Prompt
from rich.text import Text

console = Console()


def clone(
    tmp: bool = False,
    link: str = "",
    tmp_dir: str = "",
    base_dir: str = "",
    depth: int = 1,
) -> Optional[Path]:
    link = link.strip()
    if not link:
        console.print("[red]   Link required![/]")
        return None

    if "://" not in link and not link.startswith("git@"):
        link = f"https://github.com/{link.strip('/')}"

    repo_name = link.rstrip("/").split("/")[-1].removesuffix(".git")
    if not repo_name:
        console.print(f"[red]   Could not work out a repo name from {link}[/]")
        return None

    if tmp:
        temp_root = Path(tmp_dir) if tmp_dir else None
        if temp_root:
            temp_root.mkdir(parents=True, exist_ok=True)
        parent = Path(tempfile.mkdtemp(prefix="clone_", dir=temp_root))
    else:
        parent = Path(base_dir) if base_dir else Path.cwd()
        parent.mkdir(parents=True, exist_ok=True)

    target = parent / repo_name
    if target.exists():
        console.print(f"[red]   {target} already exists![/]")
        return None

    console.print(f"[bright_black]{link}[/]")
    console.print(f"[cyan]   Cloning into {target}...[/]")

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", f"{depth}", link, str(target)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        console.print("[red]   git is not installed or not on PATH![/]")
        return None

    if result.returncode != 0:
        console.print(
            f"[red]   Clone failed:[/] {result.stderr.strip()}",
            markup=True,
            highlight=False,
        )
        return None

    console.print(f"[green]   Done: {target}[/]")
    return target


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True
    )


def _git_toplevel(path: Path) -> Path | None:
    result = _git(path, "rev-parse", "--show-toplevel")
    if result.returncode:
        return None
    return Path(result.stdout.strip()).resolve()


def initial_commit(target: Path) -> bool:
    """Create a git repo if needed and commit everything as 'Initial commit'."""
    if not shutil.which("git"):
        console.print("[yellow]! git is not installed; skipping commit[/]")
        return False
    created_repo = _git_toplevel(target) != target.resolve()
    if created_repo:
        result = _git(target, "init")
        if result.returncode:
            console.print(
                f"[red]x git init failed: {escape(result.stderr.strip())}[/]"
            )
            return False
    result = _git(target, "add", "-A")
    if result.returncode:
        console.print(f"[red]x git add failed: {escape(result.stderr.strip())}[/]")
        return False
    if not _git(target, "status", "--porcelain").stdout.strip():
        if not created_repo:
            console.print("[dim]nothing to commit[/]")
            return True
        result = _git(target, "commit", "--allow-empty", "-m", "Initial commit")
    else:
        result = _git(target, "commit", "-m", "Initial commit")
    if result.returncode:
        console.print(f"[red]x git commit failed: {escape(result.stderr.strip())}[/]")
        return False
    console.print("[green]ok[/] created initial commit")
    return True


def maybe_publish(target: Path, yes: bool, commit: bool | None) -> None:
    """Offer an initial commit, then optionally create a GitHub repo and push."""
    if commit is False:
        return
    if commit is None and not yes:
        if not Confirm.ask(
            "[bold]Create an initial commit?[/]", default=True, console=console
        ):
            return
    if not initial_commit(target):
        return
    if yes or not shutil.which("gh"):
        return
    if not Confirm.ask(
        "[bold]Create a GitHub repository and push?[/]",
        default=False,
        console=console,
    ):
        return
    visibility = Prompt.ask(
        "Visibility",
        choices=["private", "public"],
        default="private",
        console=console,
    )
    cmd = [
        "gh",
        "repo",
        "create",
        target.name,
        "--source",
        ".",
        "--remote",
        "origin",
        "--push",
        f"--{visibility}",
    ]
    console.print(Text(f"$ {' '.join(cmd)}", style="dim"))
    result = subprocess.run(cmd, cwd=target)
    if result.returncode:
        console.print(f"[red]x gh exited with {result.returncode}[/]")
    else:
        console.print("[green]ok[/] repository created and pushed")
