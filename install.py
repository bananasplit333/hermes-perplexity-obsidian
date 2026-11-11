#!/usr/bin/env python3
"""Install the allowlisted root skill payload without overwriting."""
import argparse
import shutil
from pathlib import Path


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", required=True, type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent
    destination = args.hermes_home.expanduser().resolve() / "skills" / "perplexity-obsidian-research"
    if not (repo / "SKILL.md").is_file():
        parser.error(f"missing skill manifest: {repo}/SKILL.md")
    if destination.exists():
        parser.error(f"refusing to overwrite existing destination: {destination}")
    destination.mkdir(parents=True)
    shutil.copy2(repo / "SKILL.md", destination / "SKILL.md")
    for directory in ("scripts", "references", "templates"):
        copy_tree(repo / directory, destination / directory)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
