# patches — 对上游内容的改写规则

这里记录**本镜像对上游做的全部内容改动**。改动不是以 `.patch` 文件形式存在的，而是由
[`scripts/build_mirror.py`](../scripts/build_mirror.py) 在每次同步时重新计算并应用。

## 为什么用脚本而不是 .patch 文件

上游的 `SKILL.md`、`references/*.md` 每个版本都在改（文案、命令、新增章节）。固定上下文的
`.patch` 会频繁 rebase 失败，导致同步流水线在无人值守的定时任务里直接红掉。用**规则**表达改动
（"把这类路径锚点归一"）比用**行号/上下文**表达（"改第 42 行"）在上游频繁变更时稳定得多。

代价是：规则必须能被自动验证，否则会悄悄漏改。因此每次构建都会跑
`build_mirror.py verify`，其中包含对改写结果的强校验（见文末）。

## 当前规则

| # | 规则 | 位置 | 说明 |
| --- | --- | --- | --- |
| 1 | 只导出 `.claude/skills/<7 个技能>`，每个一份 | `SKILL_NAMES`、`build_skills()` | 上游同时发布 `.claude/skills/`（插件用）和 `cli/assets/skills/`（CLI 用），递归扫描会出现 13 个条目、6 个重复 |
| 2 | 排除 `tests`、`__pycache__`、`.pytest_cache`、`.ruff_cache`、`node_modules` 目录，以及 `.DS_Store`、`.coverage`、`*.pyc` 等文件 | `EXCLUDE_DIR_NAMES`、`EXCLUDE_FILE_NAMES`、`EXCLUDE_SUFFIXES` | 上游的 `tests/` 依赖仓库根布局（`test_skill_script_paths.py` 通过仓库根标记定位仓库），装在用户机器上无法运行；其余是构建产物/编辑器垃圾 |
| 3 | 四种「根目录锚点 + `.claude/skills/<技能名>/`」统一改写成 skill 内相对路径：本技能 → ``（空），兄弟技能 → `../<技能名>/` | `ROOTED_PATH_RE`、`rewrite_rooted_paths()` | 只改 Markdown 里的文档命令。这四种锚点在技能被拷进 skills 目录后都不成立（插件变量未定义 / 只有 Claude Code 有该 home 目录 / 取决于 cwd）。改写目标正是上游 main 在 #474 之后强制的、唯一跨安装布局可解析的形式 |
| 4 | 定点代码补丁（见下表） | `CODE_FIXES`、`apply_code_fixes()` | 上游 main 已修、但旧 tag 里还存在的功能性代码缺陷；语义是**自动退休**：文本还在就替换，不在了说明上游已修好，静默跳过 |

### 规则 3：路径锚点归一

上游历史上用过四种写法，v2.15.0 里都还在：

```diff
# 1) 插件安装专用（核心技能 SKILL.md，11 处）
-python "${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max/scripts/search.py" "<query>" --domain ux
+python "scripts/search.py" "<query>" --domain ux

# 2) home 根（v2.15.0 里 47 处，只有 Claude Code 的全局安装成立）
-python3 ~/.claude/skills/design/scripts/logo/search.py "tech startup modern"
+python3 scripts/logo/search.py "tech startup modern"

# 3) home 变量写法
-${HOME}/.claude/skills/design-system/scripts/search-slides.py "metrics dashboard" -d layout

# 4) 项目根（v2.15.0 里 19 处，取决于运行 cwd；兄弟技能同理）
-python .claude/skills/design-system/scripts/search-slides.py "traction slide"
+python ../design-system/scripts/search-slides.py "traction slide"
```

模式要求锚点后面必须跟「技能名 + `/`」，所以「装在 `~/.claude/skills/` 下」这类泛指不会被误改。

### 规则 4：定点代码补丁表

| 文件 | 上游旧写法（v2.15.0） | 镜像改写为 | 为什么 |
| --- | --- | --- | --- |
| `skills/brand/scripts/sync-brand-to-tokens.cjs` | `GENERATE_TOKENS_SCRIPT = '.claude/skills/design-system/scripts/generate-tokens.cjs'` + `path.resolve(process.cwd(), …)` | `path.resolve(__dirname, '..', '..', 'design-system', 'scripts', 'generate-tokens.cjs')`，并直接用该常量 | 用 `process.cwd()` 解析项目相对路径，技能被装到 `~/.claude/skills/` 等目录后必然找不到兄弟技能脚本；main 已按 `__dirname` 修好（#474 之后） |

补丁只在代码文件里做**精确字符串替换**，不做通用改写——代码里的路径需要逐处判断语义，通用替换猜不准。
如果哪天上游把这段代码又改了字形，补丁会因为「锚点失效」直接报错（而不是静默跳过），提示人工复核。

**不修、只提示的两处**（都是打印给用户看的提示字符串，不改行为）：
`skills/design/scripts/cip/generate.py` 与 `skills/brand/scripts/extract-colors.cjs` 里仍有 rooted 路径文本，
上游 main 已修，下一个 tag 同步后会自动消失。

## 加新规则的流程

1. 在 `scripts/build_mirror.py` 里实现规则（导出/排除写在 `build_skills()`/`copy_skill_tree()`，路径改写在 `rewrite_rooted_paths()`，代码补丁加进 `CODE_FIXES`）；
2. 让规则**可验证**：如果它修的是"装完之后跑不起来"的问题，就把它变成 `verify()` 里的一条断言；
3. 本地跑 `bash scripts/sync-upstream.sh`，确认 `verify` 通过、`git diff` 里只有预期改动；
4. 在本文件表格里补一行，说明规则、位置与理由。

## verify 的判定语义

`python3 scripts/build_mirror.py verify`（以及每次构建）区分两类结果：

**problems —— 阻断同步、不写 manifest、CI 不提交**

- 7 个技能目录齐全，每个 `SKILL.md` 的 frontmatter 可解析、`name` 与目录名一致；
- `skills/` 下不存在任何 `CLAUDE_PLUGIN_ROOT` 残留（含代码文件）；
- **命令行位置**的脚本调用必须是 skill 内相对路径（`scripts/…`、`references/…`、`../<技能名>/…`），
  且目标文件真实存在（本仓库技能之间也要存在）——等价于上游 `test_skill_script_paths.py` 与
  `check-asset-sync.yml` 的路径契约在镜像里的持续验证；
- `skills/ui-ux-pro-max/scripts/search.py` 能离线跑出结果（`--design-system` 与 `--domain ux` 两个探针）。

**warnings —— 只提示，不阻断**

- 上游文档引用了本仓库不包含的**外部技能**（v2.15.0 的 `banner-design` 引用 `ai-artist`、
  `ai-multimodal`、`chrome-devtools`）：路径形式已经是正确的 `../<技能名>/`，但要用得上得用户另外装；
  这类名字记录在 `upstream.lock.json` 的 `externalSkillRefs` 里；
- 散文/提示句里出现的命令写法（例如 "On Windows, use `python scripts/search.py`"）目标文件不存在——
  它不是真命令，不该让每周同步卡住，但值得看见；
- 非 Markdown 代码文件里仍有 rooted 技能路径（等待上游下个 tag 修掉）。
