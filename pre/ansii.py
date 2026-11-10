"""Text-to-ANSI art banners, powered by pyfiglet."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pyfiglet
from rich.color import Color, ColorType
from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt
from rich.table import Table

console = Console()

# Curated style name -> pyfiglet font. Any other pyfiglet font name is accepted
# too; see the note printed by `pre ansii --list-styles`.
STYLES = {
    "block": "block",
    "bloody": "sblood",
    "3d": "3-d",
    "3d-ascii": "3d-ascii",
    "3d-diagonal": "3d_diagonal",
    "3d-henry": "henry_3d",
    "3d-larry": "larry3d",
    "shadow": "shadow",
    "bubble": "bubble",
    "digital": "digital",
    "slant": "slant",
    "small": "small",
    "big": "big",
    "mini": "mini",
    "ansi": "ansi_regular",
    "ansi-shadow": "ansi_shadow",
    "banner": "banner",
    "banner3d": "banner3-D",
    "barbwire": "barbwire",
    "binary": "binary",
    "blocky": "blocky",
    "braille": "smbraille",
    "broadway": "broadway",
    "caligraphy": "caligraphy",
    "catwalk": "catwalk",
    "chunky": "chunky",
    "circle": "circle",
    "coinstak": "coinstak",
    "colossal": "colossal",
    "computer": "computer",
    "contessa": "contessa",
    "cricket": "cricket",
    "cursive": "cursive",
    "cyberlarge": "cyberlarge",
    "diamond": "diamond",
    "doom": "doom",
    "dotmatrix": "dotmatrix",
    "double": "double",
    "drpepper": "drpepper",
    "emboss": "emboss",
    "epic": "epic",
    "fender": "fender",
    "fraktur": "fraktur",
    "fuzzy": "fuzzy",
    "gothic": "gothic",
    "graffiti": "graffiti",
    "hollywood": "hollywood",
    "isometric": "isometric1",
    "italic": "italic",
    "katakana": "katakana",
    "lcd": "lcd",
    "lean": "lean",
    "letters": "letters",
    "linux": "linux",
    "madrid": "madrid",
    "marquee": "marquee",
    "morse": "morse",
    "ogre": "ogre",
    "poison": "poison",
    "puffy": "puffy",
    "rectangles": "rectangles",
    "relief": "relief",
    "roman": "roman",
    "rounded": "rounded",
    "runic": "runic",
    "script": "script",
    "serifcap": "serifcap",
    "short": "short",
    "slide": "slide",
    "small-caps": "small_caps",
    "small-shadow": "small_shadow",
    "speed": "speed",
    "stacey": "stacey",
    "standard": "standard",
    "starwars": "starwars",
    "stencil": "stencil1",
    "stop": "stop",
    "straight": "straight",
    "tanja": "tanja",
    "thick": "thick",
    "thin": "thin",
    "ticks": "ticks",
    "tinker-toy": "tinker-toy",
    "tombstone": "tombstone",
    "train": "train",
    "trek": "trek",
    "tubular": "tubular",
    "twisted": "twisted",
    "univers": "univers",
    "usaflag": "usaflag",
    "wavy": "wavy",
    "weird": "weird",
    "whimsy": "whimsy",
    "zigzag": "zig_zag_",
}

# Extra names users may type; they resolve to an existing style name.
ALIASES = {
    "ansii": "ansi",
    "ansii-regular": "ansi",
    "ansii-shadow": "ansi-shadow",
    "3d-ansii": "3d-ascii",
    "three-d": "3d",
}


def _normalize(value: str) -> str:
    return "-".join(value.strip().lower().replace("_", " ").split())


_CURATED = {_normalize(name): font for name, font in STYLES.items()}
_PYFIGLET_FONTS = list(pyfiglet.FigletFont.getFonts())
_ALL_FONTS = {_normalize(font): font for font in _PYFIGLET_FONTS}


def resolve_font(style: str) -> str | None:
    """Resolve a style name, alias, or raw pyfiglet font name to a font."""
    if style in _PYFIGLET_FONTS:
        return style
    key = _normalize(style)
    if key in _CURATED:
        return _CURATED[key]
    if key in ALIASES:
        return _CURATED.get(ALIASES[key]) or _ALL_FONTS.get(key)
    return _ALL_FONTS.get(key)


def resolve_text(words: list[str]) -> str:
    """Use the positional words, piped stdin, or an interactive prompt."""
    if words:
        return " ".join(words).strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return Prompt.ask("Text", console=console).strip()


def render(text: str, style: str = "block") -> str:
    """Render text with the selected style. Raises ValueError for bad styles."""
    font = resolve_font(style)
    if font is None:
        raise ValueError(f"unknown style: {style} (try `pre ansii --list-styles`)")
    figlet = pyfiglet.Figlet(font=font, width=10000)
    return figlet.renderText(text).rstrip("\n")


def parse_color(value: str) -> tuple[int, int, int]:
    """Parse a hex or rich color name into an RGB triplet."""
    try:
        color = Color.parse(value)
    except Exception as exc:
        raise ValueError(f"invalid color: {value}") from exc
    if color is None or color.type == ColorType.DEFAULT:
        raise ValueError(f"invalid color: {value}")
    return color.get_truecolor()


def colorize(text: str, value: str) -> str:
    """Wrap each line in a 24-bit ANSI foreground color."""
    red, green, blue = parse_color(value)
    prefix = f"\x1b[38;2;{red};{green};{blue}m"
    reset = "\x1b[0m"
    return "\n".join(f"{prefix}{line}{reset}" for line in text.splitlines())


def render_view(sample: str = "pre", style: str | None = None, color: str | None = None) -> str:
    """Render one or all curated styles as a plain-text gallery."""
    names = [style] if style else list(STYLES)
    chunks = []
    for name in names:
        font = resolve_font(name)
        if font is None:
            raise ValueError(f"unknown style: {name} (try `pre ansii --list-styles`)")
        art = render(sample, name)
        if color:
            art = colorize(art, color)
        chunks.append(f"=== {name} ({font}) ===\n{art}")
    return "\n\n".join(chunks)


def write_output(text: str, path: str | None, force: bool = False) -> bool:
    """Write art to stdout (`None`/`-`) or atomically to a file."""
    if not path or path == "-":
        print(text)
        return True

    target = Path(path).expanduser()
    if target.exists() and not force:
        console.print(
            f"[red]x {escape(str(target))} already exists (use --force)[/]"
        )
        return False

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".ansii_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    except OSError as exc:
        console.print(
            f"[red]x could not write {escape(str(target))}: {escape(str(exc))}[/]"
        )
        return False

    console.print(f"[green]ok[/] wrote {escape(str(target))}")
    return True


def show_styles() -> None:
    table = Table(title="ansii styles", title_style="bold", header_style="bold")
    table.add_column("style", style="cyan", no_wrap=True)
    table.add_column("font")
    for style, font in STYLES.items():
        table.add_row(style, font)
    console.print(table)
    console.print(
        f"[dim]{len(STYLES)} curated styles; any of the "
        f"{len(_PYFIGLET_FONTS)} pyfiglet font names also works.[/]"
    )
    console.print("[dim]view them all: pre ansii view[/]")
