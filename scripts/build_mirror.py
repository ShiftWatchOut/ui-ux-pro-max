#!/usr/bin/env python3
"""把上游 nextlevelbuilder/ui-ux-pro-max-skill 打包成 CC Switch 能直接用的镜像。

这个仓库为什么存在
------------------
CC Switch 安装技能的方式是：把「包含 SKILL.md 的那个目录」整体拷进
``~/.cc-switch/skills/<目录名>/``，再分发到各应用的 skills 目录
（``~/.claude/skills``、``~/.codex/skills``、``~/.gemini/skills``、
``~/.config/opencode/skills``、``~/.hermes/skills``）。上游仓库不是这个形状：

* 每个 SKILL.md 都存在两份（``.claude/skills/<name>/`` 给插件安装，
  ``cli/assets/skills/<name>/`` 给 CLI 安装），CC Switch 递归扫描会扫出 13 个
  条目，其中 6 个是重复的；
* 技能文档里的脚本路径在不同版本里用了几种「锚点」，只有「skill 内相对路径」在
  所有安装形态下都成立。上游 tag（如 v2.15.0）里大量使用 ``~/.claude/skills/<技能>/``
  和 ``.claude/skills/<技能>/``，核心技能还用
  ``${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max/``——这些变量/根目录在
  目录被拷进 skills 目录后都不存在，命令直接失效；
* 仓库里还带着大量只在开发时用得上的目录（``cli/``、``gallery/``、``stack/``、
  ``projects/``、``screenshots/``），技能安装并不需要。

本脚本把 ``.claude/skills/`` 下的 7 个技能导到 ``skills/``，剔除运行期用不到的
文件，把上述几种路径锚点统一改写成「skill 内相对路径」（上游 main 在 #474 之后
强制要求的形式），给少量功能性代码缺陷打定点补丁，写出 ``upstream.lock.json``，
最后做一遍校验。

``skills/`` 是生成产物：不要手改，要改就改本脚本或同步流程。

用法
----
    python3 scripts/build_mirror.py build --upstream-dir /path/to/upstream
    python3 scripts/build_mirror.py build --upstream-dir DIR --check   # 只报告漂移
    python3 scripts/build_mirror.py verify                             # 离线校验
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

#: 期望导出的技能目录名（顺序即构建顺序），每个目录里必须有 SKILL.md。
SKILL_NAMES: tuple[str, ...] = (
    "ui-ux-pro-max",
    "banner-design",
    "brand",
    "design",
    "design-system",
    "slides",
    "ui-styling",
)

#: 只在开发期有用、不需要进技能安装包的内容。
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

#: 明显是二进制的文件：兜底扫描时直接跳过，免得把字体/图片当文本读。
BINARY_SUFFIXES = frozenset(
    {
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".pdf",
        ".zip",
    }
)

#: 需要改写的路径锚点。历史上游用过四种写法，统一改写成 skill 内相对路径：
#:
#:   ``${CLAUDE_PLUGIN_ROOT}/.claude/skills/<技能>/``  插件安装专用（核心技能）
#:   ``~/.claude/skills/<技能>/``                       home 根，只有 Claude Code 成立
#:   ``${HOME}/.claude/skills/<技能>/`` / ``$HOME/...`` 同上，写法变体
#:   ``.claude/skills/<技能>/``                         项目根，取决于 cwd
#:
#: 本技能 → ``""``（于是文档里的命令变成 ``scripts/...``），兄弟技能 → ``../<技能>/``。
#: 末尾必须跟一个技能名和 ``/``，所以「装在 ~/.claude/skills/ 下」这类泛指不会被误改。
ROOTED_PATH_RE = re.compile(
    r"(?:\$\{?CLAUDE_PLUGIN_ROOT\}?|~|\$\{?HOME\}?|(?<![\w/.\-~$]))"
    r"/?\.claude/skills/(?P<skill>[A-Za-z0-9._-]+)/"
)
PLUGIN_TOKEN_RE = re.compile(r"\$\{?CLAUDE_PLUGIN_ROOT\}?")

#: 文档里用来标记「这行是命令」的模式，取自上游 check-asset-sync.yml 的路径契约。
#: 只有出现在「命令行位置」的才算硬要求（行首，允许缩进/列表符号/``$``/反引号，
#: 解释器允许带 venv 等前缀，例如 ``../.venv/bin/python3``）。
_COMMAND_PREFIX = r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?[`$]?\s*"
_INTERPRETER = r"(?:\S*/)?(?:python3?|py|node|bash)"
INVOCATION_LINE_RE = re.compile(
    _COMMAND_PREFIX + _INTERPRETER + r"\s+\"?([^\s\"`']+\.(?:py|cjs|js|mjs|sh))"
)
#: 任何位置的调用写法：散文/提示句里出现的降到告警，不阻断同步。
INVOCATION_ANY_RE = re.compile(
    r"(?<![\w/.-])(?:python3?|py|node|bash)\s+\"?([^\s\"`']+\.(?:py|cjs|js|mjs|sh))"
)
SIBLING_PATH_RE = re.compile(r"^\.\./(?P<skill>[A-Za-z0-9._-]+)/(?P<rest>.+)$")

#: 功能性代码缺陷的定点补丁。上游 main 已修、但旧 tag 里还在的问题：
#: 语义是「文本还在就替换，不在了说明上游已修好 → 静默跳过（补丁自动退休）」，
#: 因此在跟 tag 的日子里不会造成同步失败。
CODE_FIXES: tuple[dict, ...] = (
    {
        "skill": "brand",
        "path": "scripts/sync-brand-to-tokens.cjs",
        "reason": (
            "旧版把兄弟技能的脚本路径写死成项目相对路径并用 process.cwd() 解析，"
            "技能被装到 skills 目录后必然找不到；main 已改成基于 __dirname 解析"
        ),
        "replacements": (
            (
                "const GENERATE_TOKENS_SCRIPT = '.claude/skills/design-system/scripts/generate-tokens.cjs';",
                "const GENERATE_TOKENS_SCRIPT = path.resolve("
                "__dirname, '..', '..', 'design-system', 'scripts', 'generate-tokens.cjs');",
            ),
            (
                "path.resolve(process.cwd(), GENERATE_TOKENS_SCRIPT)",
                "GENERATE_TOKENS_SCRIPT",
            ),
        ),
    },
)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def iter_files(base: Path):
    """按确定顺序遍历 *base* 下的文件。"""
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
    """目录内容的确定性哈希（路径 + 字节）。"""
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


def git_output(cwd: Path, *args: str) -> str | None:
    """在上游 checkout 里跑 git 并拿到 stdout，失败返回 None。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def clean_pycache(base: Path) -> None:
    """清掉工具运行过程中在镜像里生成的 __pycache__/。"""
    for path in sorted(base.rglob("__pycache__"), reverse=True):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


class Report:
    """一次构建/校验的结果：problems 决定成败，warnings 只提示。"""

    def __init__(self) -> None:
        self.problems: list[str] = []
        self.warnings: list[str] = []
        self.external_refs: set[str] = set()
        self.applied_fixes: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.problems

    def extend(self, other: "Report") -> None:
        self.problems.extend(other.problems)
        self.warnings.extend(other.warnings)
        self.external_refs |= other.external_refs
        self.applied_fixes.extend(other.applied_fixes)


# --------------------------------------------------------------------------- #
# 路径改写（本镜像对上游文档唯一的内容性改动）
# --------------------------------------------------------------------------- #
def rewrite_rooted_paths(text: str, skill: str) -> tuple[str, list[str]]:
    """把各种「根目录锚点 + .claude/skills/<技能>/」改写成 skill 内相对路径。"""
    problems: list[str] = []

    def repl(match: re.Match[str]) -> str:
        other = match.group("skill")
        # 本技能 → ""，文档里的命令就变成 ``scripts/...``；兄弟技能 → ``../<技能>/``。
        return "" if other == skill else f"../{other}/"

    rewritten = ROOTED_PATH_RE.sub(repl, text)
    problems.extend(
        f"残留插件根变量：{m.group(0)!r}" for m in PLUGIN_TOKEN_RE.finditer(rewritten)
    )
    return rewritten, problems


# --------------------------------------------------------------------------- #
# 构建
# --------------------------------------------------------------------------- #
def apply_code_fixes(skill_dir: Path, report: Report) -> None:
    """给上游遗留的功能性代码缺陷打定点补丁。"""
    for fix in CODE_FIXES:
        if fix["skill"] != skill_dir.name:
            continue
        target = skill_dir / fix["path"]
        if not target.is_file():
            continue
        text = read_text(target)
        original = text
        missed: list[str] = []
        for broken, fixed in fix["replacements"]:
            if broken in text:
                text = text.replace(broken, fixed)
            elif fixed not in text:
                # 既不是旧写法也不是新写法：上游改写了这段代码，规则需要复核。
                missed.append(broken)
        if missed:
            report.problems.append(
                f"{rel(target)}：定点补丁锚点失效（上游改了这段代码，需要复核）：{missed[0][:80]!r}"
            )
            continue
        if text != original:
            target.write_text(text, encoding="utf-8")
            report.applied_fixes.append(rel(target))
            print(f"  - 定点补丁：{rel(target)}（{fix['reason']}）")


def copy_skill_tree(src: Path, dst: Path) -> Report:
    """拷贝单个技能目录，跳过只在开发期有用的文件。"""
    report = Report()
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
            except OSError as exc:  # pragma: no cover - 取决于文件系统
                report.problems.append(f"{rel(source)}：拷贝失败：{exc}")

    # 只改 Markdown 里记录的命令，不动代码。
    for md in sorted(dst.rglob("*.md")):
        original = read_text(md)
        rewritten, issues = rewrite_rooted_paths(original, dst.name)
        report.problems.extend(f"{rel(md)}：{issue}" for issue in issues)
        if rewritten != original:
            md.write_text(rewritten, encoding="utf-8")

    apply_code_fixes(dst, report)
    return report


def build_skills(upstream_dir: Path, target: Path) -> Report:
    """把 *target* 重建为全部技能的镜像目录。"""
    report = Report()
    source_root = upstream_dir / UPSTREAM_SKILLS_SUBDIR
    if not source_root.is_dir():
        report.problems.append(f"上游没有 {UPSTREAM_SKILLS_SUBDIR}/ 目录：{source_root}")
        return report

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    available = {p.name for p in source_root.iterdir() if p.is_dir()}
    missing = [name for name in SKILL_NAMES if name not in available]
    if missing:
        report.problems.append(f"上游缺少预期技能：{', '.join(missing)}")

    for name in SKILL_NAMES:
        src = source_root / name
        if not src.is_dir():
            continue
        report.extend(copy_skill_tree(src, target / name))
    return report


def resolve_version(upstream_dir: Path, explicit: str | None) -> str:
    """版本号优先级：显式传入 > 上游最近的 tag > skill.json > unknown。

    上游的 skill.json（例如 v2.15.0 时仍写 2.13.0）和 cli/package.json 都滞后于
    实际发版，所以 tag 才是唯一可信的版本来源；拿不到 tag 时再用 skill.json 兜底。
    """
    if explicit:
        return explicit
    tag = git_output(upstream_dir, "describe", "--tags", "--abbrev=0")
    if tag:
        return tag
    metadata = upstream_dir / "skill.json"
    if metadata.is_file():
        try:
            value = json.loads(read_text(metadata)).get("version")
        except (json.JSONDecodeError, AttributeError):
            value = None
        if value:
            return str(value)
    return "unknown"


def build_manifest(
    upstream_dir: Path,
    ref: str,
    ref_kind: str,
    commit: str,
    version: str | None,
    *,
    applied_fixes: list[str],
    external_refs: set[str],
) -> dict:
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
            "refKind": ref_kind,
            "commit": commit,
            "version": resolve_version(upstream_dir, version),
            "skillsSubdir": UPSTREAM_SKILLS_SUBDIR,
            "license": "MIT",
        },
        "generatedBy": "scripts/build_mirror.py",
        # 打过的定点补丁与「需要用户另行安装的外部技能」，便于核对镜像内容
        "localFixes": applied_fixes,
        "externalSkillRefs": sorted(external_refs),
        "skills": skills,
    }


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
def check_skill_md(skill_dir: Path) -> list[str]:
    problems: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"{rel(skill_dir)}：缺少 SKILL.md（CC Switch 不会识别这个目录）"]

    text = read_text(skill_md)
    if not text.startswith("---"):
        problems.append(f"{rel(skill_md)}：缺少 YAML frontmatter")
        return problems
    end = text.find("\n---", 3)
    if end == -1:
        problems.append(f"{rel(skill_md)}：YAML frontmatter 没有结束标记")
        return problems

    frontmatter = text[3:end]
    name_match = re.search(r"^name:\s*(.+?)\s*$", frontmatter, re.MULTILINE)
    if not name_match:
        problems.append(f"{rel(skill_md)}：frontmatter 里没有 name")
    elif name_match.group(1).strip().strip("\"'") != skill_dir.name:
        problems.append(
            f"{rel(skill_md)}：frontmatter 的 name {name_match.group(1).strip()!r} "
            f"与目录名 {skill_dir.name!r} 不一致"
        )
    if not re.search(r"^description:\s*\S", frontmatter, re.MULTILINE):
        problems.append(f"{rel(skill_md)}：frontmatter 里没有 description")
    return problems


def check_invocations(skill_dir: Path) -> Report:
    """在镜像里强制执行上游的「skill 内相对路径」契约。

    * 处在「命令行位置」的调用：路径形式或目标文件不对 → 问题，阻断同步；
    * 仅散文/提示句里提到的调用：同样的判断降为告警，避免因为一句 Windows 提示
      就让每周同步卡住（上游 v2.15.0 的 banner-design 就有这种陈旧提示）。
    """
    report = Report()
    seen_warnings: set[str] = set()

    def classify(documented: str, where: str, *, strict: bool) -> None:
        # strict=True 表格行命令；False 表示散文里提到，失败降为告警
        def fail(message: str) -> None:
            if strict:
                report.problems.append(message)
            elif message not in seen_warnings:
                seen_warnings.add(message)
                report.warnings.append(message)

        target: Path | None = None
        if PLUGIN_TOKEN_RE.search(documented):
            fail(f"{where}：插件根路径没被改掉：{documented}")
            return
        if documented.startswith(("scripts/", "references/")):
            target = skill_dir / documented
        else:
            sibling = SIBLING_PATH_RE.match(documented)
            if not sibling:
                fail(f"{where}：不是 skill 内相对路径：{documented}")
                return
            name = sibling.group("skill")
            if name not in SKILL_NAMES:
                # 上游文档引用了本仓库不包含的外部技能（例如 ai-artist）：
                # 路径形式没问题，但要用得上得用户另外装，只提示不拦。
                report.external_refs.add(name)
                warning = f"{where}：引用外部技能 {name!r}（需自行安装，镜像不含）"
                if warning not in seen_warnings:
                    seen_warnings.add(warning)
                    report.warnings.append(warning)
                return
            target = SKILLS_DIR / name / sibling.group("rest")
        if target is not None and not target.is_file():
            fail(f"{where}：文件不存在：{documented}")

    for md in sorted(skill_dir.rglob("*.md")):
        for lineno, line in enumerate(read_text(md).splitlines(), 1):
            where = f"{rel(md)}:{lineno}"
            as_command = {m.group(1) for m in INVOCATION_LINE_RE.finditer(line)}
            for documented in sorted(as_command):
                classify(documented, where, strict=True)
            for match in INVOCATION_ANY_RE.finditer(line):
                documented = match.group(1)
                if documented in as_command:
                    continue
                classify(documented, where, strict=False)
    return report


def check_no_rooted_paths() -> Report:
    """最后的兜底扫描：镜像里不该再有 CLAUDE_PLUGIN_ROOT 或 rooted 技能路径。"""
    report = Report()
    for path in iter_files(SKILLS_DIR):
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = read_text(path)
        except OSError:
            continue
        if "CLAUDE_PLUGIN_ROOT" in text:
            report.problems.append(f"{rel(path)}：仍残留 CLAUDE_PLUGIN_ROOT")
        # 非 Markdown 的代码文件只提示，不拦：改写代码需要逐处判断语义，
        # 上游 main 已修好这类问题，下一个 tag 就会自动消失。
        if path.suffix.lower() == ".md":
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if ROOTED_PATH_RE.search(line):
                report.warnings.append(
                    f"{rel(path)}:{lineno}：代码里仍有 rooted 技能路径（上游新 tag 修复后会消失）"
                )
    return report


def smoke_test() -> list[str]:
    """按技能使用者会用的方式，真正跑一遍核心搜索脚本。"""
    problems: list[str] = []
    script = SKILLS_DIR / CORE_SKILL / "scripts" / "search.py"
    if not script.is_file():
        return [f"{rel(script)}：不存在"]
    python = shutil.which("python3") or sys.executable
    # 禁止写 .pyc，否则会在 skills/ 里留下 __pycache__ 污染镜像。
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
                f"冒烟测试失败（{' '.join(args)}）：rc={result.returncode} "
                f"{(result.stderr or result.stdout).strip()[:300]}"
            )
        elif len(result.stdout.strip()) < 80:
            problems.append(f"冒烟测试没有有效输出：{' '.join(args)}")
    clean_pycache(SKILLS_DIR)
    return problems


def verify(skills_dir: Path = SKILLS_DIR, *, smoke: bool = True) -> Report:
    report = Report()
    if not skills_dir.is_dir():
        report.problems.append(f"{rel(skills_dir)}：还没构建")
        return report

    present = {p.name for p in skills_dir.iterdir() if p.is_dir()}
    expected = set(SKILL_NAMES)
    if present != expected:
        missing = sorted(expected - present)
        unexpected = sorted(present - expected)
        if missing:
            report.problems.append(f"缺少技能：{', '.join(missing)}")
        if unexpected:
            report.problems.append(f"多出技能：{', '.join(unexpected)}")

    for name in sorted(expected & present):
        report.problems.extend(check_skill_md(skills_dir / name))
        report.extend(check_invocations(skills_dir / name))

    report.extend(check_no_rooted_paths())

    if smoke:
        report.problems.extend(smoke_test())
    return report


# --------------------------------------------------------------------------- #
# 对比与摘要
# --------------------------------------------------------------------------- #
def compare_trees(left: Path, right: Path) -> list[str]:
    """给出两棵技能树的文件级差异摘要。"""
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


def print_report(
    report: Report, *, warnings_label: str = "告警", problems_label: str = "错误"
) -> None:
    if report.warnings:
        print(f"\n{warnings_label}（不阻断同步）：", file=sys.stderr)
        for warning in report.warnings:
            print(f"  - {warning}", file=sys.stderr)
    if report.problems:
        print(f"\n{problems_label}：", file=sys.stderr)
        for problem in report.problems:
            print(f"  - {problem}", file=sys.stderr)


def print_summary(manifest: dict) -> None:
    upstream = manifest["upstream"]
    total = sum(entry["files"] for entry in manifest["skills"].values())
    print(f"  上游仓库 : {UPSTREAM_REPOSITORY}")
    print(f"  上游 ref : {upstream['ref']}（{upstream['refKind']}）@ {upstream['commit'][:7]}")
    print(f"  版本     : {upstream['version']}")
    print(f"  技能     : {len(manifest['skills'])} 个，共 {total} 个文件")
    for name in SKILL_NAMES:
        entry = manifest["skills"].get(name)
        if entry:
            print(f"    - {name:<15} {entry['files']:>4} 个文件  {entry['sha256'][:12]}")
    if manifest["localFixes"]:
        print(f"  定点补丁 : {len(manifest['localFixes'])} 处")
        for item in manifest["localFixes"]:
            print(f"    - {item}（原因见 patches/README.md）")
    if manifest["externalSkillRefs"]:
        print(f"  外部技能引用（镜像不含，需自行安装）: {', '.join(manifest['externalSkillRefs'])}")


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_build(args: argparse.Namespace) -> int:
    upstream_dir = Path(args.upstream_dir).resolve()
    if not upstream_dir.is_dir():
        print(f"错误：上游 checkout 不存在：{upstream_dir}", file=sys.stderr)
        return 2

    if args.check:
        # 只报告漂移：在临时目录里构建，然后和现有 skills/ 比内容哈希。
        with tempfile.TemporaryDirectory(prefix="mirror-check-") as tmp:
            staged = Path(tmp) / "skills"
            build = build_skills(upstream_dir, staged)
            changes = compare_trees(SKILLS_DIR, staged)
            manifest = build_manifest(
                upstream_dir,
                args.ref,
                args.ref_kind,
                args.commit,
                args.version,
                applied_fixes=build.applied_fixes,
                external_refs=build.external_refs,
            )
            print_summary(manifest)
            print_report(build, warnings_label="构建告警")
            if build.problems:
                return 1
            if changes:
                print(f"\n镜像已落后（{len(changes)} 处文件差异）：")
                print("\n".join(changes[:200]))
                return 1
            print("\n镜像已是最新（内容哈希无变化）")
            return 0

    print("==> 构建 skills/")
    build = build_skills(upstream_dir, SKILLS_DIR)
    clean_pycache(SKILLS_DIR)
    if not build.ok:
        print_report(build, warnings_label="构建告警")
        return 1

    print("==> 校验")
    check = verify(SKILLS_DIR, smoke=not args.no_smoke)
    check.extend(build)
    manifest = build_manifest(
        upstream_dir,
        args.ref,
        args.ref_kind,
        args.commit,
        args.version,
        applied_fixes=build.applied_fixes,
        external_refs=build.external_refs | check.external_refs,
    )
    if not check.ok:
        # 校验不过就不写 manifest，工作区停在「可排查」状态，CI 也不会提交。
        print_report(check, warnings_label="构建告警", problems_label="校验失败")
        return 1

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("==> 完成")
    print_summary(manifest)
    print(f"  manifest : {rel(MANIFEST_PATH)}")
    print("  校验结果 : OK（frontmatter、路径契约、无 rooted 路径残留、冒烟测试）")
    print_report(check, warnings_label="告警")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    report = verify(smoke=not args.no_smoke)
    if not report.ok:
        print_report(report, warnings_label="告警", problems_label="校验失败")
        return 1
    skills = count_files(SKILLS_DIR)
    print(f"校验通过：{len(SKILL_NAMES)} 个技能、{skills} 个文件位于 {rel(SKILLS_DIR)}")
    print_report(report, warnings_label="告警")
    return 0


def main(argv: list[str] | None = None) -> int:
    # 输出被管道/CI 捕获时默认是块缓冲，会和 stderr 交错得很难读，改成行缓冲。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):  # pragma: no cover - 非标准流
            pass

    parser = argparse.ArgumentParser(
        description="构建/校验 ui-ux-pro-max-skill 的 CC Switch 镜像",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="从上游 checkout 重建 skills/")
    build.add_argument("--upstream-dir", required=True, help="上游 git checkout 路径")
    build.add_argument("--ref", default="unknown", help="写进 manifest 的上游 ref 名称")
    build.add_argument(
        "--ref-kind",
        default="local",
        choices=("tag", "branch", "local"),
        help="上游 ref 的类型，写进 manifest",
    )
    build.add_argument("--version", default=None, help="写进 manifest 的版本号（默认取上游最近 tag）")
    build.add_argument("--commit", default="unknown", help="写进 manifest 的上游 commit sha")
    build.add_argument("--check", action="store_true", help="只报告漂移，不改工作区")
    build.add_argument("--no-smoke", action="store_true", help="跳过 search.py 冒烟测试")
    build.set_defaults(func=cmd_build)

    check = sub.add_parser("verify", help="离线校验当前 skills/")
    check.add_argument("--no-smoke", action="store_true", help="跳过 search.py 冒烟测试")
    check.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
