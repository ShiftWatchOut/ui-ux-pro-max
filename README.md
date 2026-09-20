# ui-ux-pro-max · CC Switch 可安装镜像

把上游 [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) 的 **7 个技能**整理成
[CC Switch](https://github.com/farion1231/cc-switch) 能直接扫描、安装、做更新检测的仓库结构，并自动跟随上游更新。

- 上游仓库 / 官网：<https://github.com/nextlevelbuilder/ui-ux-pro-max-skill> · <https://uupm.cc>
- 当前镜像：跟随上游最新 release tag（版本与上游 commit 记录在 [`upstream.lock.json`](upstream.lock.json)，每次同步自动更新）
- 许可证：MIT（原样保留上游 [`LICENSE`](LICENSE)，版权归 Next Level Builder）

---

## 为什么不能直接把上游仓库加进 CC Switch

CC Switch 的安装方式是「递归扫描仓库 → 找到含 `SKILL.md` 的目录 → 把该目录整体拷到 `~/.cc-switch/skills/<目录名>/` → 分发到各应用的 skills 目录」。
上游仓库不是这个形状：

| 上游的问题 | 本镜像的处理 |
| --- | --- |
| 每个 `SKILL.md` 存在两份（`.claude/skills/<name>/` 和 `cli/assets/skills/<name>/`），递归扫描得到 13 个条目、6 个重复 | 只导出 `.claude/skills/` 的 7 个技能，每个一份 |
| 核心技能的 `SKILL.md` 把脚本写成 `${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max/scripts/...`，只在 Claude Code **插件**安装下有效；被拷进 skills 目录后变量未定义，命令直接失效 | 11 处改写为 skill 内相对路径 `scripts/search.py`（见下） |
| 仓库混有 `cli/`、`gallery/`、`stack/`、`projects/`、`screenshots/`、`docs/` 等开发目录 | 只导出 `skills/`，其余不进入镜像 |

---

## 在 CC Switch 里添加

**Skills → 仓库管理 → 添加仓库**

| 字段 | 值 |
| --- | --- |
| Owner | 你的 GitHub 用户名 / 组织名（推送后填写） |
| Name | 本仓库名（推送后填写） |
| Branch | `main` |
| Subdirectory | `skills`（留空也可以：CC Switch 是递归扫描整个仓库的） |

然后点 **刷新** → 列表出现 7 个技能 → 逐个 **安装**。

安装后 CC Switch 会自动同步到当前应用（可同时启用多个）：

| 应用 | 安装目录 |
| --- | --- |
| Claude Code | `~/.claude/skills/` |
| Codex | `~/.codex/skills/` |
| Gemini CLI | `~/.gemini/skills/` |
| OpenCode | `~/.config/opencode/skills/` |
| Hermes | `~/.hermes/skills/` |

**更新**：CC Switch v3.13.0+ 用 SHA-256 内容哈希比对本地与远端，只要本仓库有新的提交，技能卡片就会显示「有新版本」，点 **更新** 或 **全部更新** 即可，不需要卸载重装。

> **建议 7 个技能一起装。** 上游的设计是「装在一起、互相调用」，仍有跨技能依赖：
> `design` 会调用 `../brand/scripts/` 与 `../design-system/scripts/`，`slides` 会调用
> `../design-system/scripts/`。只装其中一个的话，这些命令会找不到文件。
> 其余 5 个（`ui-ux-pro-max`、`banner-design`、`brand`、`design-system`、`ui-styling`）可独立使用。

---

## 目录结构

```
.
├── skills/                      # 生成目录：7 个技能，每个含 SKILL.md（不要手改）
│   ├── ui-ux-pro-max/           # 核心技能（data/ scripts/ references/）
│   ├── banner-design/
│   ├── brand/
│   ├── design/
│   ├── design-system/
│   ├── slides/
│   └── ui-styling/              # 含 canvas-fonts 字体资源
├── scripts/
│   ├── build_mirror.py          # 导出 + 路径改写 + 校验（唯一改内容的地方）
│   └── sync-upstream.sh         # 克隆上游 → 构建 → 校验 → 报告变更
├── patches/README.md            # 改写规则说明（为什么用脚本而不是 .patch 文件）
├── .github/workflows/
│   ├── sync-upstream.yml        # 每周定时 + 手动触发同步并自动提交
│   └── verify-mirror.yml        # push/PR 时校验镜像不变量
└── upstream.lock.json           # 记录上游 ref/commit/version + 每个技能的 sha256
```

`skills/` 是**生成产物**，请勿手改：任何手工修改都会被 `verify-mirror.yml` 或下次同步覆盖。

---

## 跟随上游更新

**同步策略：每周一次，跟最新 release tag。** 上游用 semantic-release 发版，`main` 上长期存在
已合并但未发版的提交（例如某次同步时 `main` 在 2026-09-19，而最新稳定 tag 只到
`v2.15.0` @ 2026-08-14）。跟 tag 意味着镜像里只会出现正式发布过的内容，行为更可预期；
上游一发新版本，下一次同步就会跟上。

> 版本号以 **tag** 为准。上游的 `skill.json`（v2.15.0 时仍写 2.13.0）和 `cli/package.json`
> 都滞后于实际发版，所以脚本不会把它们当作版本来源。

### GitHub Actions（默认）

`.github/workflows/sync-upstream.yml`

- 每周一 `04:00`（Asia/Shanghai / 周日 `20:00 UTC`，cron `0 20 * * 0`）自动运行
- 支持 **手动触发**（Actions → 同步上游镜像 → Run workflow）：`ref` 留空 = 自动取最新 release tag；
  也可填分支（如 `main`）或指定 tag（如 `v2.15.0`）
- 流程：解析最新 tag → `git clone --depth 1 --branch <tag>` → `build_mirror.py build`（导出 + 改写 + 校验）
  → 校验失败则不提交 → 有内容变化才 `git commit && git push`，提交信息带上游版本与短 sha
- 内容没变化就不产生提交，CC Switch 侧也不会有更新提示

解析 tag 用的是 `git ls-remote --sort=-v:refname` 并优先取 `vX.Y.Z` 稳定版
（上游 `dev` 分支会发 `beta` 预发布 tag，不会被误跟）。

两个运维细节（已踩过/已验证）：

- **用 `GITHUB_TOKEN` 推送不会触发其他 workflow**（GitHub 防递归），所以同步作业产生的提交不会让
  「校验镜像」再跑一遍。安全性由同步作业自身保证：它在提交前已经跑完同一套校验，校验不过就不会提交。
- **公开仓库 60 天无活动时，GitHub 会自动停用 schedule**。只要上游在 60 天内有发版，同步提交本身就是
  活动、计时会被重置；如果上游长期不发版，去 Actions → 同步上游镜像 → Run workflow 手动点一次即可
  （顺带也会把 schedule 重新启用）。

### 本地手动同步

```bash
# 默认：取上游最新 release tag，重建 skills/ 并校验（有变更需自己 commit/push）
bash scripts/sync-upstream.sh

# 跟分支（会带上未发版的提交），或固定某个 tag
bash scripts/sync-upstream.sh --ref main
bash scripts/sync-upstream.sh --ref v2.15.0

# 只看有没有漂移（不修改工作区，有差异时退出码 1）
bash scripts/sync-upstream.sh --check

# 复用本地已有的上游 clone，避免重复下载
bash scripts/sync-upstream.sh --upstream-dir ~/src/ui-ux-pro-max-skill
```

### 校验

```bash
python3 scripts/build_mirror.py verify     # 离线校验当前 skills/（含 search.py 冒烟测试）
```

校验内容：

1. 恰好 7 个技能目录，每个 `SKILL.md` 的 frontmatter 可解析且 `name` 与目录名一致（CC Switch 依赖它取名称/描述）；
2. `skills/` 下不存在任何 `CLAUDE_PLUGIN_ROOT` 残留；
3. **命令行位置**（行首）的脚本调用都满足「skill 内相对路径」契约，且指向真实存在的文件（`scripts/<file>` / `../<skill>/<file>`）；
4. 核心技能的 `scripts/search.py` 能真正跑出结果（离线、标准库）。

上面 1–4 条会**阻断同步**（不写 manifest、CI 不提交）。另有一类只提示、不阻断的情况：外部技能引用、
散文里提到的陈旧命令、代码里等待上游修复的 rooted 路径——判定细节见 [`patches/README.md`](patches/README.md)。

---

## 与上游的差异（可核对清单）

1. **只导出 `.claude/skills/` 的 7 个技能**，每个只保留一份（上游 `cli/assets/skills/` 里的 6 个副本不导出）。
2. **文档里的脚本路径统一归一为「skill 内相对路径」**。上游历史上用过四种锚点，在 v2.15.0 里都还在，
   它们的共同问题是：技能目录被拷进 skills 目录后这个锚点就不成立。

   | 上游写法 | v2.15.0 出现次数 | 为什么不成立 | 镜像改写为 |
   | --- | --- | --- | --- |
   | `${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max/…` | 11 | 该变量只在 Claude Code 插件安装下定义 | `scripts/…` |
   | `~/.claude/skills/design/…` | 47 | 只有 Claude Code 全局安装有该目录，Codex/Gemini/OpenCode/Hermes 下不存在 | `scripts/…` |
   | `${HOME}/.claude/skills/…` | — | 同上（写法变体，规则一并覆盖） | 同上 |
   | `.claude/skills/design-system/…` | 19 | 取决于运行时的 cwd | `../design-system/…` |

   基准目录就是 `SKILL.md` 所在目录。上游 main 在 #474 之后把这条写成了规则：
   `.github/workflows/check-asset-sync.yml` 明确要求 skill-relative paths（"resolve in every install
   context: plugin cache, project-level CLI install, CLI --global install, manual copy"），并有单测逐条断言。

   为什么非得改，实测对照（本机 `CLAUDE_PLUGIN_ROOT` 未定义）：

   ```console
   $ python3 "${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max/scripts/search.py" "keyboard focus modal" --domain ux
   python3: can't open file '/.claude/skills/ui-ux-pro-max/scripts/search.py': [Errno 2] No such file or directory

   $ python3 ~/.claude/skills/ui-ux-pro-max/scripts/search.py "keyboard focus modal" --domain ux
   - **Severity:** High            # 能用，但是巧合：见下
   ```

   `~/.claude/skills/...` 这类 home 根路径之所以有时能用，是因为 CC Switch 默认用**软链接模式**分发：
   `~/.claude/skills/design -> ~/.agents/skills/design`。一旦把同步方式改成「复制」、关掉 Claude Code
   目标，或者把技能同步给 Codex/Gemini/OpenCode/Hermes，这个目录就不存在了。而 skill 内相对路径是
   pi / Claude Code / agentskills.io 标准里 agent 拿到技能 `<location>` 后应有的写法，到处都成立。
3. **1 处定点代码补丁**：`brand/scripts/sync-brand-to-tokens.cjs` 旧版用 `process.cwd()` 解析兄弟技能的
   脚本路径（装到 skills 目录后必然找不到），镜像按上游 main 的修法改成基于 `__dirname` 解析。
   补丁会自动退休：上游修好后自动跳过。规则与原因见 [`patches/README.md`](patches/README.md)。
4. **排除** `**/tests/`、`__pycache__/`、`.coverage` 等非运行期文件。上游的 `tests/` 是仓库级回归测试，依赖仓库根目录布局（例如 `test_skill_script_paths.py` 靠仓库根标记定位），装到用户机器上无法运行。
5. **不含** `cli/`、`gallery/`、`stack/`、`projects/`、`screenshots/`、`docs/` 等开发目录。
6. 其余文件**字节级保持与上游一致**：`.gitattributes` 用 `* -text` 禁止检查时的换行转换，这样 `data/*.csv` 的 sha256 与上游相同，`scripts/validate_data.py` 里的快照校验才成立。

## 已知限制

- **外部技能引用**：v2.15.0 的 `banner-design` 文档引用了 `ai-artist`、`ai-multimodal`、`chrome-devtools`
  三个不属于上游仓库的技能（上游自己也没带）。镜像会把这些名字记在 [`upstream.lock.json`](upstream.lock.json) 的
  `externalSkillRefs` 里；用到对应功能时需要自己另外装那些技能。其余 6 个技能不依赖外部技能。
- 还有两处**打印给用户看的提示字符串**仍写着旧路径（`design/scripts/cip/generate.py`、
  `brand/scripts/extract-colors.cjs`）。它们不影响行为，上游 main 已修，下个 tag 同步后会自动消失。
- 文档中的脚本调用以「`SKILL.md` 所在目录」为基准（上游约定）。若你的客户端不告知技能目录，直接用绝对路径运行即可，例如
   `python3 ~/.claude/skills/ui-ux-pro-max/scripts/search.py "<query>" --domain ux`。
- 运行脚本需要 Python 3（标准库，无第三方依赖、无网络访问）。
- 只镜像技能内容，不镜像上游的 npm CLI（`ui-ux-pro-max-cli`）与多平台安装模板。
- 镜像默认只包含上游**已发版**的内容：`main` 上已合并但未发版的提交要等下一个 tag 才会进来。
  需要临时跟进时用 `bash scripts/sync-upstream.sh --ref main`。
- `ui-styling` 含约 5.6MB 字体资源，属于上游随技能发布的内容。

## 许可证与归属

技能内容版权归上游作者所有，MIT 许可（`Copyright (c) 2024 Next Level Builder`），本仓库原样保留上游 [`LICENSE`](LICENSE)。
本仓库仅为面向 CC Switch 的打包镜像，不修改技能行为，仅做上述结构性调整。
