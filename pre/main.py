#!/usr/bin/env python3

import argparse
import sys

try:
    from . import comments, config, git, projects
except ImportError:  # running as a plain script: python pre/main.py
    import comments
    import config
    import git
    import projects


def run_strip(args, cfg):
    strip_cfg = cfg["strip"]
    keep = [*strip_cfg["keep"], *(args.keep or [])]
    squeeze = strip_cfg["squeeze"] if args.squeeze is None else args.squeeze
    show_deleted = (
        strip_cfg["show_deleted"]
        if args.show_deleted is None
        else args.show_deleted
    )
    return comments.strip_files(
        args.paths or ["."],
        lang=args.lang,
        keep=keep,
        squeeze=squeeze,
        dry_run=args.dry_run,
        check=args.check,
        show_deleted=show_deleted,
    )


def run_config(args, cfg):
    if args.path:
        print(config.config_path(args.config))
        return 0
    if args.init or args.force:
        if not config.write_default_config(force=args.force, override=args.config):
            return 1
        cfg = config.load_config(args.config)
    config.show_config(cfg, override=args.config)
    return 0


def run_clone(args, cfg):
    if args.tmp:
        target = git.clone(
            tmp=True,
            link=args.link,
            tmp_dir=args.path or cfg["paths"]["tmp"],
            depth=args.depth,
        )
    else:
        target = git.clone(
            link=args.link,
            base_dir=args.path or cfg["paths"]["base"],
            depth=args.depth,
        )
    return 0 if target else 1


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pre",
        description="Create projects and keep source trees tidy.",
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="use an alternate config file (default: ~/.config/pre/config.toml)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    strip = sub.add_parser(
        "strip",
        help="remove comments from source files",
        description=(
            "Remove comments from source files, detecting the language from the "
            "file extension or shebang."
        ),
    )
    strip.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="files or directories to process (default: current directory)",
    )
    strip.add_argument(
        "-l",
        "--lang",
        choices=comments.LANGS,
        help="force a language instead of detecting it",
    )
    strip.add_argument(
        "-k",
        "--keep",
        action="append",
        metavar="REGEX",
        help="keep comments matching REGEX (repeatable)",
    )
    strip.add_argument(
        "-s",
        "--squeeze",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="collapse blank lines left behind by removed comments",
    )
    strip.add_argument(
        "--show-deleted",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="print a diff of the removed lines",
    )
    mode = strip.add_mutually_exclusive_group()
    mode.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="show what would change without writing",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit 1 if any file would change",
    )
    strip.set_defaults(func=run_strip)

    new = sub.add_parser(
        "new",
        help="create a project from a template",
        description="Create a project from a template in ~/.config/pre or a builtin.",
    )
    new.add_argument(
        "template",
        nargs="?",
        help="template or builtin name (prompts if omitted)",
    )
    new.add_argument(
        "name",
        nargs="?",
        help="project name, or destination path (prompts if omitted)",
    )
    destination = new.add_mutually_exclusive_group()
    destination.add_argument(
        "-d",
        "--dir",
        default=None,
        help="parent directory (default: config paths.base)",
    )
    destination.add_argument(
        "-p",
        "--path",
        default=None,
        metavar="PATH",
        help="exact destination directory for the new project",
    )
    new.add_argument(
        "-D",
        "--var",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="set a template variable",
    )
    new.add_argument(
        "-y", "--yes", action="store_true", help="accept defaults, no prompts"
    )
    new.add_argument(
        "--no-hooks", action="store_true", help="skip post-create commands"
    )
    new.add_argument(
        "--commit",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="create an initial commit (prompts by default)",
    )
    new.add_argument(
        "-l", "--list", action="store_true", help="list templates and builtins"
    )
    new.add_argument("--syntax", action="store_true", help="show the template syntax")
    new.add_argument(
        "--sample", action="store_true", help="install the sample template"
    )
    new.add_argument(
        "--new-template", metavar="NAME", help="scaffold an empty template"
    )
    new.set_defaults(func=projects.run_new)

    git_cmd = sub.add_parser(
        "git",
        help="git helpers",
        description="Clone repositories into the configured base or temp directory.",
    )
    git_sub = git_cmd.add_subparsers(dest="git_command", required=True)
    clone = git_sub.add_parser("clone", help="clone a repository")
    clone.add_argument(
        "link", help="repository URL, or owner/repo for a GitHub repository"
    )
    clone.add_argument(
        "-t",
        "--tmp",
        action="store_true",
        help="clone into a fresh directory under config paths.tmp",
    )
    clone.add_argument(
        "-p",
        "--path",
        default=None,
        metavar="PATH",
        help="parent directory (default: config paths.base, or paths.tmp with --tmp)",
    )
    clone.add_argument(
        "--depth", type=int, default=1, help="clone depth (default: 1)"
    )
    clone.set_defaults(func=run_clone)

    cfg = sub.add_parser(
        "config",
        help="show or initialise the config file",
        description="Show the effective settings from the TOML config file.",
    )
    cfg.add_argument(
        "--init", action="store_true", help="write the default config file"
    )
    cfg.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing config file (implies --init)",
    )
    cfg.add_argument(
        "--path", action="store_true", help="print the config file path only"
    )
    cfg.set_defaults(func=run_config)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = config.load_config(args.config)
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
