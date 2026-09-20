#!/usr/bin/env python3
"""Build and verify a CC Switch-installable mirror of nextlevelbuilder/ui-ux-pro-max-skill.

Why this repository exists
--------------------------
CC Switch installs a skill by copying *one directory that contains SKILL.md* into
``~/.cc-switch/skills/<dir>/`` and then distributing it to each app's skills
directory (``~/.claude/skills``, ``~/.codex/skills``, ``~/.gemini/skills``,
``~/.config/opencode/skills``, ``~/.hermes/skills``).  The upstream repository is
not shaped for that:

* it ships every SKILL.md twice (``.claude/skills/<name>/`` for plugin installs and
  ``cli/assets/skills/<name>/`` for CLI installs), so CC Switch's recursive scan
  finds 13 skills, 6 of them duplicates;
* the core skill's SKILL.md documents its scripts as
  ``${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max/scripts/...``, which only
  resolves inside a Claude Code *plugin* install and breaks as soon as the folder
  is copied into a skills directory;
* it carries large development-only trees (``cli/``, ``gallery/``, ``stack/``,
  ``projects/``, ``screenshots/``) that a skill install has no use for.

This script copies the 7 real skills from ``.claude/skills/`` into ``skills/``,
drops non-runtime files, rewrites the core skill's plugin-root paths into the
skill-relative form that upstream's own CI enforces
(``.github/workflows/check-asset-sync.yml``), writes ``upstream.lock.json`` and
then verifies the result.

``skills/`` is generated output: never edit it by hand, change this script or the
sync pipeline instead.

Usage
-----
    python3 scripts/build_mirror.py build --upstream-dir /path/to/upstream-checkout
    python3 scripts/build_mirror.py build --upstream-dir DIR --check   # dry run
    python3 scripts/build_mirror.py verify                             # no network
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
MANIFEST_PATH = REPO_ROOT / "upstream.lock.json"

UPSTREAM_REPOSITORY = "https://github.com/nextlevelbuilder/ui-ux-pro-max-skill"
UPSTREAM_SKILLS_SUBDIR = ".claude/skills"
CORE_SKILL = "ui-ux-pro-max"

#: Directory name -> must contain SKILL.md.  Order is the build order.
SKILL_NAMES: tuple[str, ...] = (
    "ui-ux-pro-max",
    "banner-design",
    "brand",
    "design",
    "design-system",
    "slides",
    "ui-styling",
)

#: Development-only content that is not part of a runtime skill install.
EXCLUDE_DIR_NAMES = frozenset(
    {
        "tests",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        ".git",
        ".github",
    }
)
EXCLUDE_FILE_NAMES = frozenset(
    {".DS_Store", ".coverage", "Thumbs.db", ".gitkeep", ".gitignore"}
)
EXCLUDE_SUFFIXES = frozenset({".pyc", ".pyo", ".orig", ".rej"})

#: ``${CLAUDE_PLUGIN_ROOT}/.claude/skills/<skill>/`` -> ```` (own skill) or ``../<skill>/``
PLUGIN_PATH_RE = re.compile(
    r"\$\{?CLAUDE_PLUGIN_ROOT\}?/(?P<mid>\.claude/skills/)?(?P<skill>[A-Za-z0-9._-]+)/"
)
PLUGIN_TOKEN_RE = re.compile(r"\$\{?CLAUDE_PLUGIN_ROOT\}?")

#: Path contract copied from upstream's .github/workflows/check-asset-sync.yml:
#: every documented script invocation must be skill-relative, either the skill's
#: own ``scripts/<file>`` or a sibling's ``../<skill>/scripts/<file>``.
INVOCATION_RE = re.compile(
    r"(?<![\w/.-])(?:python3?|node|bash)\s+\"?([^\s\"`']+\.(?:py|cjs|js|mjs|sh))"
)
SIBLING_PATH_RE = re.compile(r"^\.\./(?P<skill>[A-Za-z0-9._-]+)/(?P<rest>.+)$")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def iter_files(base: Path):
    """Yield files under *base* in a deterministic order."""
    for root, dirs, files in os.walk(base):
        dirs.sort()
        for name in sorted(files):
            yield Path(root) / name


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(base: Path) -> str:
    """Deterministic hash of a directory's contents (paths + bytes)."""
    digest = hashlib.sha256()
    for path in iter_files(base):
        digest.update(str(path.relative_to(base)).replace(os.sep, "/").encode())
        digest.update(b"\0")
        digest.update(sha256_file(path).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def count_files(base: Path) -> int:
    return sum(1 for _ in iter_files(base))


def is_excluded_file(path: Path) -> bool:
    return path.name in EXCLUDE_FILE_NAMES or path.suffix.lower() in EXCLUDE_SUFFIXES


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# path rewriting (the only intentional deviation from upstream)
# --------------------------------------------------------------------------- #
def rewrite_plugin_paths(text: str, skill: str) -> tuple[str, list[str]]:
    """Rewrite ``${CLAUDE_PLUGIN_ROOT}/.claude/skills/...`` to skill-relative paths."""
    problems: list[str] = []

    def repl(match: re.Match[str]) -> str:
        other = match.group("skill")
        if match.group("mid") is None:
            problems.append(
                f"plugin-root path does not point into a skill: {match.group(0)!r}"
            )
            return match.group(0)
        # Own skill -> "" so the documented command becomes ``scripts/...``;
        # a sibling -> ``../<skill>/``.
        return "" if other == skill else f"../{other}/"

    rewritten = PLUGIN_PATH_RE.sub(repl, text)
    problems.extend(
        f"leftover plugin-root token: {m.group(0)!r}" for m in PLUGIN_TOKEN_RE.finditer(rewritten)
    )
    return rewritten, problems


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def copy_skill_tree(src: Path, dst: Path) -> tuple[int, list[str]]:
    """Copy one skill directory, skipping development-only files."""
    problems: list[str] = []
    copied = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIR_NAMES)
        rel_root = Path(root).relative_to(src)
        (dst / rel_root).mkdir(parents=True, exist_ok=True)
        for name in sorted(files):
            source = Path(root) / name
            if is_excluded_file(source):
                continue
            try:
                shutil.copyfile(source, dst / rel_root / name)
            except OSError as exc:  # pragma: no cover - filesystem dependent
                problems.append(f"{rel(source)}: copy failed: {exc}")
                continue
            copied += 1

    # Rewrite documented script paths in markdown only.
    for md in sorted((dst).rglob("*.md")):
        original = read_text(md)
        rewritten, issues = rewrite_plugin_paths(original, dst.name)
        problems.extend(f"{rel(md)}: {issue}" for issue in issues)
        if rewritten != original:
            md.write_text(rewritten, encoding="utf-8")
    return copied, problems


def build_skills(upstream_dir: Path, target: Path) -> list[str]:
    """Populate *target* with the mirrors of all upstream skills."""
    problems: list[str] = []
    source_root = upstream_dir / UPSTREAM_SKILLS_SUBDIR
    if not source_root.is_dir():
        return [f"upstream has no {UPSTREAM_SKILLS_SUBDIR}/ directory: {source_root}"]

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    available = {p.name for p in source_root.iterdir() if p.is_dir()}
    missing = [name for name in SKILL_NAMES if name not in available]
    if missing:
        problems.append(f"upstream is missing expected skills: {', '.join(missing)}")

    for name in SKILL_NAMES:
        src = source_root / name
        if not src.is_dir():
            continue
        _, issues = copy_skill_tree(src, target / name)
        problems.extend(issues)
    return problems


def build_manifest(upstream_dir: Path, ref: str, commit: str) -> dict:
    version = "unknown"
    metadata = upstream_dir / "skill.json"
    if metadata.is_file():
        try:
            version = json.loads(read_text(metadata)).get("version", "unknown")
        except (json.JSONDecodeError, AttributeError):
            version = "unknown"

    skills: dict[str, dict] = {}
    for name in SKILL_NAMES:
        skill_dir = SKILLS_DIR / name
        if not skill_dir.is_dir():
            continue
        skills[name] = {
            "path": f"skills/{name}",
            "files": count_files(skill_dir),
            "sha256": tree_hash(skill_dir),
        }

    return {
        "upstream": {
            "repository": UPSTREAM_REPOSITORY,
            "ref": ref,
            "commit": commit,
            "version": version,
            "skillsSubdir": UPSTREAM_SKILLS_SUBDIR,
            "license": "MIT",
        },
        "generatedBy": "scripts/build_mirror.py",
        "skills": skills,
    }


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #
def check_skill_md(skill_dir: Path) -> list[str]:
    problems: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"{rel(skill_dir)}: missing SKILL.md (CC Switch would not detect this folder)"]

    text = read_text(skill_md)
    if not text.startswith("---"):
        problems.append(f"{rel(skill_md)}: missing YAML frontmatter")
        return problems
    end = text.find("\n---", 3)
    if end == -1:
        problems.append(f"{rel(skill_md)}: unterminated YAML frontmatter")
        return problems

    frontmatter = text[3:end]
    name_match = re.search(r"^name:\s*(.+?)\s*$", frontmatter, re.MULTILINE)
    if not name_match:
        problems.append(f"{rel(skill_md)}: frontmatter has no 'name'")
    elif name_match.group(1).strip().strip("\"'") != skill_dir.name:
        problems.append(
            f"{rel(skill_md)}: frontmatter name {name_match.group(1).strip()!r} "
            f"differs from directory {skill_dir.name!r}"
        )
    if not re.search(r"^description:\s*\S", frontmatter, re.MULTILINE):
        problems.append(f"{rel(skill_md)}: frontmatter has no 'description'")
    return problems


def check_invocations(skill_dir: Path) -> list[str]:
    """Enforce upstream's skill-relative path contract inside the mirror."""
    problems: list[str] = []
    for md in sorted(skill_dir.rglob("*.md")):
        for lineno, line in enumerate(read_text(md).splitlines(), 1):
            for match in INVOCATION_RE.finditer(line):
                documented = match.group(1)
                if PLUGIN_TOKEN_RE.search(documented):
                    problems.append(
                        f"{rel(md)}:{lineno}: plugin-root path survived: {documented}"
                    )
                    continue
                if documented.startswith("scripts/") or documented.startswith("references/"):
                    target = skill_dir / documented
                else:
                    sibling = SIBLING_PATH_RE.match(documented)
                    if not sibling or sibling.group("skill") not in SKILL_NAMES:
                        problems.append(
                            f"{rel(md)}:{lineno}: not skill-relative: {documented}"
                        )
                        continue
                    target = SKILLS_DIR / sibling.group("skill") / sibling.group("rest")
                if not target.is_file():
                    problems.append(f"{rel(md)}:{lineno}: no such file: {documented}")
    return problems


def check_no_plugin_root() -> list[str]:
    problems: list[str] = []
    for path in iter_files(SKILLS_DIR):
        try:
            text = read_text(path)
        except OSError:
            continue
        if "CLAUDE_PLUGIN_ROOT" in text:
            problems.append(f"{rel(path)}: still references CLAUDE_PLUGIN_ROOT")
    return problems


def clean_pycache(base: Path) -> None:
    """Drop __pycache__/ that a tool run may have created inside the mirror."""
    for path in sorted(base.rglob("__pycache__"), reverse=True):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


def smoke_test() -> list[str]:
    """Run the core search tool the way a skill consumer would."""
    problems: list[str] = []
    script = SKILLS_DIR / CORE_SKILL / "scripts" / "search.py"
    if not script.is_file():
        return [f"{rel(script)}: missing"]
    python = shutil.which("python3") or sys.executable
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    probes = [
        ["beauty spa wellness service", "--design-system", "-p", "Mirror Smoke"],
        ["keyboard focus modal", "--domain", "ux"],
    ]
    for args in probes:
        result = subprocess.run(
            [python, str(script), *args],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
        if result.returncode != 0:
            problems.append(
                f"smoke test failed ({' '.join(args)}): rc={result.returncode} "
                f"{(result.stderr or result.stdout).strip()[:300]}"
            )
        elif len(result.stdout.strip()) < 80:
            problems.append(f"smoke test produced no useful output: {' '.join(args)}")
    clean_pycache(SKILLS_DIR)
    return problems


def verify(skills_dir: Path = SKILLS_DIR, *, smoke: bool = True) -> list[str]:
    problems: list[str] = []
    if not skills_dir.is_dir():
        return [f"{rel(skills_dir)}: not built yet"]

    present = {p.name for p in skills_dir.iterdir() if p.is_dir()}
    expected = set(SKILL_NAMES)
    if present != expected:
        missing = sorted(expected - present)
        unexpected = sorted(present - expected)
        if missing:
            problems.append(f"missing skills: {', '.join(missing)}")
        if unexpected:
            problems.append(f"unexpected skills: {', '.join(unexpected)}")

    for name in sorted(expected & present):
        problems.extend(check_skill_md(skills_dir / name))
        problems.extend(check_invocations(skills_dir / name))

    problems.extend(check_no_plugin_root())

    if smoke:
        problems.extend(smoke_test())
    return problems


# --------------------------------------------------------------------------- #
# comparison / reporting
# --------------------------------------------------------------------------- #
def compare_trees(left: Path, right: Path) -> list[str]:
    """File-level diff summary between two skill trees."""
    def snapshot(base: Path) -> dict[str, str]:
        return {
            str(p.relative_to(base)).replace(os.sep, "/"): sha256_file(p)
            for p in iter_files(base)
        }

    left_files = snapshot(left) if left.is_dir() else {}
    right_files = snapshot(right) if right.is_dir() else {}
    changes: list[str] = []
    for name in sorted(set(left_files) | set(right_files)):
        if name not in left_files:
            changes.append(f"  + skills/{name}")
        elif name not in right_files:
            changes.append(f"  - skills/{name}")
        elif left_files[name] != right_files[name]:
            changes.append(f"  ~ skills/{name}")
    return changes


def summarize(upstream_dir: Path, ref: str, commit: str) -> dict:
    manifest = build_manifest(upstream_dir, ref, commit)
    total = sum(entry["files"] for entry in manifest["skills"].values())
    print(f"  upstream : {UPSTREAM_REPOSITORY}")
    print(f"  ref      : {ref} @ {commit[:7]}")
    print(f"  version  : {manifest['upstream']['version']}")
    print(f"  skills   : {len(manifest['skills'])} ({total} files)")
    for name in SKILL_NAMES:
        entry = manifest["skills"].get(name)
        if entry:
            print(f"    - {name:<15} {entry['files']:>4} files  {entry['sha256'][:12]}")
    return manifest


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_build(args: argparse.Namespace) -> int:
    upstream_dir = Path(args.upstream_dir).resolve()
    if not upstream_dir.is_dir():
        print(f"error: no such upstream checkout: {upstream_dir}", file=sys.stderr)
        return 2

    if args.check:
        with tempfile.TemporaryDirectory(prefix="mirror-check-") as tmp:
            staged = Path(tmp) / "skills"
            problems = build_skills(upstream_dir, staged)
            changes = compare_trees(SKILLS_DIR, staged)
            summarize(upstream_dir, args.ref, args.commit)
            if problems:
                print("\nbuild warnings:", file=sys.stderr)
                for problem in problems:
                    print(f"  - {problem}", file=sys.stderr)
            if changes:
                print(f"\nmirror is OUT OF DATE ({len(changes)} file changes):")
                print("\n".join(changes[:200]))
                return 1
            print("\nmirror is up to date (content hashes unchanged)")
            return 0

    print("==> building skills/")
    problems = build_skills(upstream_dir, SKILLS_DIR)
    clean_pycache(SKILLS_DIR)
    manifest = build_manifest(upstream_dir, args.ref, args.commit)

    print("==> verifying")
    problems.extend(verify(SKILLS_DIR, smoke=not args.no_smoke))
    if problems:
        print("\nverification FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("==> done")
    summarize(upstream_dir, args.ref, args.commit)
    print(f"  manifest : {rel(MANIFEST_PATH)}")
    print("  verify   : OK (frontmatter, path contract, no plugin-root paths, smoke test)")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    problems = verify(smoke=not args.no_smoke)
    if problems:
        print("verification FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    skills = count_files(SKILLS_DIR)
    print(f"verification OK: {len(SKILL_NAMES)} skills, {skills} files in {rel(SKILLS_DIR)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build/verify the CC Switch mirror of ui-ux-pro-max-skill",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="rebuild skills/ from an upstream checkout")
    build.add_argument("--upstream-dir", required=True, help="path to upstream git checkout")
    build.add_argument("--ref", default="main", help="upstream ref label recorded in the manifest")
    build.add_argument("--commit", default="unknown", help="upstream commit sha recorded in the manifest")
    build.add_argument("--check", action="store_true", help="dry run: report drift, change nothing")
    build.add_argument("--no-smoke", action="store_true", help="skip the search.py smoke test")
    build.set_defaults(func=cmd_build)

    check = sub.add_parser("verify", help="validate the current skills/ tree (offline)")
    check.add_argument("--no-smoke", action="store_true", help="skip the search.py smoke test")
    check.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
