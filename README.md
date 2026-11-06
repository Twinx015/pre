# pre

A small command-line toolbox for everyday project work:

- **`pre new`** — scaffold projects from templates or builtins, then optionally commit and publish them.
- **`pre strip`** — remove comments from source files, safely and across many languages.
- **`pre git clone`** — clone repositories into your configured base or a throwaway temp directory.
- **`pre config`** — inspect and create the TOML configuration.

Requires Python 3.12+ and [rich](https://github.com/Textualize/rich).

## Install

```bash
# from this repository
uv sync

# or install the `pre` command globally
uv tool install .
# or: pipx install .
```

Run it directly from the checkout with `uv run pre ...`.

## Quick start

```bash
pre config --init                 # create ~/.config/pre/config.toml
pre new --sample                  # optional: install the python-cli example template
pre new uv my-app                 # scaffold a project from a builtin
pre strip --check .               # see whether comments would be removed
pre strip --show-deleted .        # strip and print a diff of removed lines
pre git clone owner/repo          # clone into paths.base
```

## Configuration

Configuration lives at `~/.config/pre/config.toml` (honours `$XDG_CONFIG_HOME`).
Override the path with `--config FILE` or `$PRE_CONFIG`.

```toml
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

[strip]
# Print a diff of removed lines for every changed file.
show_deleted = false
# Collapse blank lines left behind by removed comments.
squeeze = false
# Extra regexes for comments that must never be removed.
keep = []
```

Manage it with:

```bash
pre config              # show effective settings and the config path
pre config --init       # write the default config
pre config --force      # overwrite an existing config
pre config --path       # print the config path only
```

Invalid or unknown settings are reported as warnings and ignored; defaults are
used instead.

## `pre new`

Create a project from a user template or a builtin:

```bash
pre new                          # prompt for template and name
pre new uv-pkg my-app            # builtin template
pre new python-cli my-app -d ~/Code
pre new python-cli ~/Code/my-app # path-like names work too
pre new uv my-app --path ~/Code/my-app
```

Options:

| Option | Meaning |
| --- | --- |
| `-d, --dir DIR` | Parent directory (default: `paths.base`) |
| `-p, --path PATH` | Exact destination directory (mutually exclusive with `--dir`) |
| `-D, --var KEY=VALUE` | Set a template variable non-interactively (repeatable) |
| `-y, --yes` | Accept defaults and skip prompts |
| `--no-hooks` | Do not run `post` commands |
| `--commit / --no-commit` | Create an initial commit without asking / skip git entirely |
| `-l, --list` | List templates and builtins |
| `--syntax` | Show the full template syntax |
| `--sample` | Install the bundled `python-cli` sample template |
| `--new-template NAME` | Scaffold an empty template |

### Builtins

`uv`, `uv-lib`, `uv-pkg`, `npm`, `npm-y`, `cargo`, `go`, `git`, `empty`.

A user template with the same name as a builtin takes precedence.

### User templates

Templates are directories under `~/.config/pre/`:

```text
~/.config/pre/<template>/
    pre.toml          optional metadata; never copied
    ...               everything else is copied into the new project
```

Optional `pre.toml`:

```toml
description = "shown by `pre new --list`"
post = ["git init", "uv sync"]        # commands run in the project (asked first)
ignore = ["*.bak", "drafts"]          # globs that are never copied
raw = [".github/**", "*.png"]         # copied byte-for-byte, no rendering

[vars]                                # extra variables, prompted at creation
description = "{{ name | title }} tool"   # plain string = default
license = { default = "MIT", choices = ["MIT", "GPL-3.0"], prompt = "License" }
```

### Placeholders

Placeholders work in file contents and in file/directory names:

```text
{{ name }}            value of a variable
{{ name | snake }}    apply a filter; chain with more pipes
\{{ name }}           escaped: writes a literal {{ name }}
```

Built-in variables: `name`, `author` (git `user.name`, else `$USER`), `year`,
`date`.

Filters: `snake`, `kebab`, `pascal`, `camel`, `title`, `upper`, `lower`.

Binary files are detected and copied as-is. Existing files in the target are
never overwritten, and rendered paths that would escape the project directory are
skipped.

### Git after creation

Once the project is written:

1. `pre new` asks whether to create an initial commit (`git init` + `git add -A` +
   `Initial commit`).
2. If the [GitHub CLI](https://cli.github.com/) (`gh`) is installed, it then asks
   whether to create a new repository and push it, prompting for
   `private`/`public` visibility.

`-y` always creates the local commit but never creates or pushes a GitHub
repository. Use `--no-commit` to skip git entirely.

## `pre strip`

Remove comments from files or directories. The language is detected from the
file extension, or from the shebang for extensionless files.

```bash
pre strip src/
pre strip -l js build/script
pre strip --check .              # exit 1 if anything would change
pre strip --dry-run .            # show what would change, write nothing
pre strip --show-deleted .       # print a diff of removed lines
pre strip -k '^//go:' .          # keep matching comments (repeatable)
pre strip --squeeze .            # collapse blank lines left behind
```

Supported extensions: `.py`, `.js`/`.mjs`/`.cjs`, `.ts`/`.mts`/`.cts`, `.css`,
`.html`/`.htm`, `.json`, `.jsonc`, `.sh`/`.bash`/`.zsh`, `.lua`, `.qml`, `.c`,
`.cpp`/`.cc`/`.cxx`/`.h`/`.hpp`/`.hh`/`.hxx`, `.java`, `.cs`, `.go`, `.kt`/`.kts`,
`.swift`, `.rs`.

Details:

- Strings, template literals, regexes, heredocs, and raw strings are respected
  per language, so `"http://..."` is not mistaken for a comment.
- Language-specific directives are preserved automatically (for example Go
  `//go:` and build tags, TypeScript `/// <reference>` and `@ts-ignore`, QML
  `//@`, Swift tools-version lines).
- Shebangs, encoding declarations, and the BOM/CRLF formatting are preserved.
- Python files are checked with `ast` before writing: if stripping comments would
  change the parsed AST, the file is left untouched.
- Directory scans skip hidden directories and `node_modules`, `__pycache__`,
  `venv`, `env`, `dist`, and `build`.
- Files containing the private-use character `U+E000` are rejected.

Exit codes: `0` success, `1` changes found with `--check`, `2` one or more files
failed.

## `pre git clone`

```bash
pre git clone owner/repo           # GitHub shorthand
pre git clone https://...          # any git URL
pre git clone --tmp owner/repo     # fresh directory under paths.tmp
pre git clone -p ~/Code owner/repo # override the parent directory
pre git clone --depth 10 owner/repo
```

Shallow clones with `--depth 1` by default. Without `--tmp`, the repository is
cloned into `paths.base`; with `--tmp`, a new `clone_*` directory is created under
`paths.tmp`.

## Exit codes

| Command | Codes |
| --- | --- |
| `strip` | `0` ok, `1` `--check` found changes, `2` file errors |
| `new` | `0` ok, `1` failure, `130` cancelled |
| `git clone` | `0` ok, `1` failure |
| `config` | `0` ok, `1` could not write config |

## Development

```bash
uv sync
uv run pre --help
uv run pre strip --help
uv run pre new --help
```

Package layout:

```text
pre/
    main.py       CLI wiring and subcommands
    config.py     TOML config loading/validation
    projects.py   templates, scaffolding, project prompts
    comments.py   comment-stripping engine
    git.py        clone, initial commit, GitHub publishing
```
