"""Cut a zen build: set the version to <upstream version>+zen.<N>, commit, and tag.

Usage, from the repository root on the zen branch with no uncommitted changes:
    uv run python scripts/zen_release.py [--dry-run] [--upstream-ref upstream/main]

The base version is read from the upstream ref's pyproject.toml. N is one more
than the highest existing v<base>+zen.<N> tag, starting at 1 for each new base.
This script exists only on the zen branch; it never goes into upstream PRs.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Each pattern must match exactly once; group 1 and 2 surround the version.
VERSION_LINES = [
    ("pyproject.toml", r'^(version = ")[^"]+(")$'),
    ("cli/pyproject.toml", r'^(version = ")[^"]+(")$'),
    ("src/pyghidra_mcp/__init__.py", r'^(__version__ = ")[^"]+(")$'),
    ("cli/src/pyghidra_mcp_cli/__init__.py", r'^(__version__ = ")[^"]+(")$'),
    ("uv.lock", r'^(name = "pyghidra-mcp"\nversion = ")[^"]+(")$'),
    ("cli/uv.lock", r'^(name = "pyghidra-mcp"\nversion = ")[^"]+(")$'),
    ("cli/uv.lock", r'^(name = "pyghidra-mcp-cli"\nversion = ")[^"]+(")$'),
]
CHECK_COMMANDS = [
    "uv lock --check".split(),
    "uv lock --check --directory cli".split(),
    "uv run python -m pytest tests/unit/test_version.py -q".split(),
    "uv run --directory cli --extra dev python -m pytest tests/unit/test_version.py -q".split(),
]


def git(*args: str) -> str:
    """Run a git command and return its output."""
    result = subprocess.run(["git", *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def fail(message: str) -> None:
    """Print an error and stop."""
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def base_version(upstream_ref: str) -> str:
    """Read the version from the upstream ref's pyproject.toml."""
    text = git("show", f"{upstream_ref}:pyproject.toml")
    match = re.search(r'^version = "([^"]+)"$', text, re.MULTILINE)
    if match is None:
        fail(f"no version line in {upstream_ref}:pyproject.toml")
    return match.group(1)


def next_build(base: str) -> int:
    """Return one more than the highest zen build number tagged for this base."""
    pattern = re.compile(rf"v{re.escape(base)}\+zen\.(\d+)")
    numbers = []
    for tag in git("tag", "--list").splitlines():
        match = pattern.fullmatch(tag)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def set_version(version: str) -> None:
    """Write the version into every place that holds it, exactly once each."""
    for path_name, pattern in VERSION_LINES:
        path = Path(path_name)
        text = path.read_text()
        new_text, count = re.subn(
            pattern, lambda m: f"{m.group(1)}{version}{m.group(2)}", text, flags=re.MULTILINE
        )
        if count != 1:
            fail(f"expected one match in {path} for {pattern}, found {count}")
        path.write_text(new_text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cut a zen build.")
    parser.add_argument("--upstream-ref", default="upstream/main")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan; change nothing.")
    args = parser.parse_args()

    if git("rev-parse", "--abbrev-ref", "HEAD") != "zen":
        fail("run this on the zen branch")
    if git("status", "--porcelain", "--untracked-files=no"):
        fail("commit or stash your changes first")

    base = base_version(args.upstream_ref)
    version = f"{base}+zen.{next_build(base)}"
    tag = f"v{version}"
    print(f"upstream version: {base}")
    print(f"new version:      {version}  (tag {tag})")
    if args.dry_run:
        return

    files = sorted({path_name for path_name, _ in VERSION_LINES})
    set_version(version)
    for command in CHECK_COMMANDS:
        if subprocess.run(command).returncode != 0:
            git("checkout", "--", *files)
            fail(f"check failed: {' '.join(command)}; files restored")

    git("add", *files)
    git("commit", "-m", f"Release {version}")
    git("tag", "-s", tag, "-m", f"Release {version}")
    print(f"committed and tagged {tag}")
    print(f"publish with: git push origin zen {tag}")


if __name__ == "__main__":
    main()
