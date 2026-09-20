#!/usr/bin/env bash
# 把 skills/ 与上游 nextlevelbuilder/ui-ux-pro-max-skill 对齐。
#
#   bash scripts/sync-upstream.sh                  # 默认：跟最新 release tag
#   bash scripts/sync-upstream.sh --ref main       # 跟分支（会带上未发版的提交）
#   bash scripts/sync-upstream.sh --ref v2.15.0    # 固定某个 tag
#   bash scripts/sync-upstream.sh --check          # 只报告漂移，不改工作区
#   bash scripts/sync-upstream.sh --upstream-dir ~/src/ui-ux-pro-max-skill
#
# 真正的导出/改写/校验在 scripts/build_mirror.py，本脚本只负责取上游、解析 ref 与
# 版本号，最后汇总成「CI 是否需要提交」的形式。
#
# 为什么默认跟 tag 而不是 main：上游用 semantic-release，main 上长期存在已合并但
# 未发版的提交（例如 main 在 2026-09-19，而最新稳定 tag 只到 v2.15.0 @ 2026-08-14）。
# 跟 tag 意味着镜像只包含正式发布过的内容，行为更可预期。
set -Eeuo pipefail

REPO_URL="${UPSTREAM_REPO_URL:-https://github.com/nextlevelbuilder/ui-ux-pro-max-skill.git}"
REF=""
VERSION=""
REF_KIND=""
UPSTREAM_DIR=""
CHECK=0

usage() {
  cat <<'EOF'
把 skills/ 与上游 ui-ux-pro-max-skill 对齐。

选项：
  --ref <branch|tag>     指定上游 ref；不传则自动取最新 release tag
  --upstream-dir <path>  复用本地已有的上游 checkout，不做网络克隆
  --check                只报告漂移，有差异时退出码为 1（不改工作区）
  -h, --help             显示本帮助

环境变量：
  UPSTREAM_REPO_URL      覆盖上游克隆地址
  PYTHON                 指定 python 解释器（默认 python3）
EOF
}

while (($#)); do
  case "$1" in
    --ref)          REF="${2:?--ref 需要一个值}"; shift 2 ;;
    --upstream-dir) UPSTREAM_DIR="${2:?--upstream-dir 需要一个值}"; shift 2 ;;
    --check)        CHECK=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    *)              printf '错误：未知参数 %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
TMP_DIR=""

cleanup() {
  if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

# 取上游最新的稳定 release tag：优先 vX.Y.Z，跳过 dev 分支发的 beta 预发布。
# 用 git 自带的版本排序（--sort=-v:refname），不依赖 GNU sort -V，macOS 也能跑。
resolve_latest_tag() {
  local tags stable
  tags="$(git ls-remote --tags --refs --sort=-v:refname "$REPO_URL" 'v*' 2>/dev/null \
    | awk '{print $2}' | sed 's#^refs/tags/##')" || true
  stable="$(printf '%s\n' "$tags" | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1)" || true
  if [[ -n "$stable" ]]; then
    printf '%s\n' "$stable"
  else
    # 没有稳定版就退回任意 v* tag（比如上游只发过预发布）
    printf '%s\n' "$tags" | head -1
  fi
}

if [[ -n "$UPSTREAM_DIR" ]]; then
  UPSTREAM_DIR="$(cd "$UPSTREAM_DIR" && pwd)"
  echo "==> 使用本地上游 checkout：${UPSTREAM_DIR}"
  REF_KIND="local"
  if [[ -z "$REF" ]]; then
    REF="$(git -C "$UPSTREAM_DIR" describe --tags --abbrev=0 2>/dev/null || echo local)"
  fi
else
  command -v git >/dev/null || { echo "错误：需要 git" >&2; exit 2; }

  if [[ -z "$REF" ]]; then
    echo "==> 解析上游最新 release tag"
    REF="$(resolve_latest_tag)"
    if [[ -z "$REF" ]]; then
      echo "错误：无法从 ${REPO_URL} 解析最新 tag（网络问题？）" >&2
      echo "提示：可以用 --ref <branch|tag> 显式指定" >&2
      exit 1
    fi
    REF_KIND="tag"
    VERSION="$REF"
  else
    # 用户显式给了 ref：是 tag 还是分支，问一下远端就知道
    if git ls-remote --exit-code --tags "$REPO_URL" "refs/tags/${REF}" >/dev/null 2>&1; then
      REF_KIND="tag"
      VERSION="$REF"
    else
      REF_KIND="branch"
    fi
    echo "==> 使用指定 ref：${REF}（${REF_KIND}）"
  fi

  TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/uupm-upstream.XXXXXX")"
  echo "==> 克隆 ${REPO_URL}（${REF}）"
  if ! git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$REF" "$REPO_URL" "${TMP_DIR}/upstream"; then
    echo "错误：无法克隆 ${REPO_URL} 的 ref ${REF}" >&2
    echo "提示：--ref 接受分支或 tag 名，不接受 commit sha" >&2
    exit 1
  fi
  UPSTREAM_DIR="${TMP_DIR}/upstream"
fi

COMMIT="$(git -C "$UPSTREAM_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"

build_args=(build
  --upstream-dir "$UPSTREAM_DIR"
  --ref "$REF"
  --ref-kind "$REF_KIND"
  --commit "$COMMIT"
)
if [[ -n "$VERSION" ]]; then
  build_args+=(--version "$VERSION")
fi
if ((CHECK)); then
  build_args+=(--check)
fi

if "$PYTHON" "${ROOT}/scripts/build_mirror.py" "${build_args[@]}"; then
  status=0
else
  status=$?
fi

# 构建/校验失败必须传出非 0（否则 CI 会把一个坏镜像当成好镜像提交）；
# --check 模式下非 0 就是「有漂移」的信号，同样直接返回。
if ((status != 0)); then
  exit "$status"
fi
if ((CHECK)); then
  exit 0
fi

if [[ -d "${ROOT}/.git" ]]; then
  changed="$(git -C "$ROOT" status --porcelain -- skills upstream.lock.json | wc -l | tr -d ' ')"
  echo
  if [[ "$changed" == "0" ]]; then
    echo "==> 与上次提交相比没有变化（无需提交）"
  else
    echo "==> skills/ 或 upstream.lock.json 有 ${changed} 处变更："
    git -C "$ROOT" status --porcelain -- skills upstream.lock.json | sed 's/^/    /'
    echo "    提交并推送后，CC Switch 侧才会看到更新"
  fi
fi
