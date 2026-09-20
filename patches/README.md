# patches — 对上游内容的改写规则

这里记录**本镜像对上游做的全部内容改动**。改动不是以 `.patch` 文件形式存在的，而是由
[`scripts/build_mirror.py`](../scripts/build_mirror.py) 在每次同步时重新计算并应用。

## 为什么用脚本而不是 .patch 文件

上游的 `SKILL.md`、`references/*.md` 每个版本都在改（文案、命令、新增章节）。固定上下文的
`.patch` 会频繁 rebase 失败，导致同步流水线在无人值守的定时任务里直接红掉。用**规则**表达改动
（"把插件根路径前缀改写掉"）比用**行号/上下文**表达（"改第 42 行"）在上游频繁变更时稳定得多。

代价是：规则必须能被自动验证，否则会悄悄漏改。因此每次构建都会跑
`build_mirror.py verify`，其中包含对改写结果的强校验（见文末）。

## 当前规则

| # | 规则 | 位置 | 说明 |
| --- | --- | --- | --- |
| 1 | 只导出 `.claude/skills/<7 个技能>`，每个一份 | `SKILL_NAMES`、`build_skills()` | 上游同时发布 `.claude/skills/`（插件用）和 `cli/assets/skills/`（CLI 用），递归扫描会出现 13 个条目、6 个重复 |
| 2 | 排除 `tests`、`__pycache__`、`.pytest_cache`、`.ruff_cache`、`node_modules` 目录，以及 `.DS_Store`、`.coverage`、`*.pyc` 等文件 | `EXCLUDE_DIR_NAMES`、`EXCLUDE_FILE_NAMES`、`EXCLUDE_SUFFIXES` | 上游的 `tests/` 依赖仓库根布局（`test_skill_script_paths.py` 通过仓库根标记定位仓库），装在用户机器上无法运行；其余是构建产物/编辑器垃圾 |
| 3 | `${CLAUDE_PLUGIN_ROOT}/.claude/skills/<技能名>/` → 本技能内为 ``（空），同仓库其它技能为 `../<技能名>/` | `PLUGIN_PATH_RE`、`rewrite_plugin_paths()` | 只改 Markdown 中的文档命令。`CLAUDE_PLUGIN_ROOT` 只在 Claude Code 插件安装下被定义，技能被拷贝到 skills 目录后命令失效；改成的相对形式正是上游 CI（`.github/workflows/check-asset-sync.yml`）强制的唯一跨安装布局可解析形式 |

规则 3 的示例：

```diff
-python "${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max/scripts/search.py" "<query>" --domain ux
+python "scripts/search.py" "<query>" --domain ux
```

```diff
-python "${CLAUDE_PLUGIN_ROOT}/.claude/skills/design-system/scripts/search-slides.py" "traction slide"
+python "../design-system/scripts/search-slides.py" "traction slide"
```

## 加新规则的流程

1. 在 `scripts/build_mirror.py` 里实现规则（导出规则写在 `build_skills()`/`copy_skill_tree()`，改写规则写在 `rewrite_plugin_paths()`）；
2. 让规则**可验证**：如果它修的是"装完之后跑不起来"的问题，就把它变成 `verify()` 里的一条断言；
3. 本地跑 `bash scripts/sync-upstream.sh`，确认 `verify` 通过、`git diff` 里只有预期改动；
4. 在本文件表格里补一行，说明规则、位置与理由。

## 规则不能破坏的东西（verify 的断言）

`python3 scripts/build_mirror.py verify` 每次构建都会跑，定时同步失败时**不会提交**：

- 7 个技能目录齐全，每个 `SKILL.md` 的 frontmatter 可解析，`name` 与目录名一致；
- `skills/` 下不存在任何 `CLAUDE_PLUGIN_ROOT` 残留（含代码文件，不只 Markdown）；
- 所有 Markdown 里的脚本调用都是 skill 内相对路径（`scripts/…`、`references/…`、`../<技能名>/…`）且文件真实存在——这条等价于上游 `test_skill_script_paths.py` + `check-asset-sync.yml` 的路径契约在镜像里的持续验证；
- `skills/ui-ux-pro-max/scripts/search.py` 能离线跑出结果（`--design-system` 与 `--domain ux` 两个探针）。
