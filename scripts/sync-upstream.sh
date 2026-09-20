#!/usr/bin/env bash
# Sync skills/ with the upstream nextlevelbuilder/ui-ux-pro-max-skill repository.
#
#   bash scripts/sync-upstream.sh                 # clone upstream main, rebuild, verify
#   bash scripts/sync-upstream.sh --ref v2.13.0   # pin a tag instead of the default branch
#   bash scripts/sync-upstream.sh --check         # dry run: report drift, change nothing
#   bash scripts/sync-upstream.sh --upstream-dir ~/src/ui-ux-pro-max-skill
#
# The heavy lifting lives in scripts/build_mirror.py; this wrapper only fetches
# the upstream checkout and reports what changed so CI can decide to commit.
set -Eeuo pipefail

REPO_URL="${UPSTREAM_REPO_URL:-https://github.com/nextlevelbuilder/ui-ux-pro-max-skill.git}"
REF="main"
UPSTREAM_DIR=""
CHECK=0

usage() {
  cat <<'EOF'
Sync skills/ with upstream ui-ux-pro-max-skill.

Options:
  --ref <branch|tag>     upstream ref to mirror (default: main)
  --upstream-dir <path>  use an existing upstream checkout instead of cloning
  --check                dry run: report drift and exit 1 if skills/ differs
  -h, --help             show this help

Environment:
  UPSTREAM_REPO_URL      override the upstream clone URL
EOF
}

while (($#)); do
  case "$1" in
    --ref)          REF="${2:?--ref needs a value}"; shift 2 ;;
    --upstream-dir) UPSTREAM_DIR="${2:?--upstream-dir needs a value}"; shift 2 ;;
    --check)        CHECK=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    *)              printf 'error: unknown argument: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
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

if [[ -z "$UPSTREAM_DIR" ]]; then
  command -v git >/dev/null || { echo "error: git is required" >&2; exit 2; }
  TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/uupm-upstream.XXXXXX")"
  echo "==> cloning ${REPO_URL} (${REF})"
  if ! git clone --quiet --depth 1 --branch "$REF" "$REPO_URL" "${TMP_DIR}/upstream"; then
    echo "error: could not clone ${REPO_URL} at ref ${REF}" >&2
    echo "hint: --ref accepts a branch or tag name, not a commit sha" >&2
    exit 1
  fi
  UPSTREAM_DIR="${TMP_DIR}/upstream"
else
  UPSTREAM_DIR="$(cd "$UPSTREAM_DIR" && pwd)"
  echo "==> using existing upstream checkout: ${UPSTREAM_DIR}"
fi

COMMIT="$(git -C "$UPSTREAM_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"

build_args=(build --upstream-dir "$UPSTREAM_DIR" --ref "$REF" --commit "$COMMIT")
if ((CHECK)); then
  build_args+=(--check)
fi

if "$PYTHON" "${ROOT}/scripts/build_mirror.py" "${build_args[@]}"; then
  status=0
else
  status=$?
fi

if ((CHECK)); then
  exit "$status"
fi

if [[ -d "${ROOT}/.git" ]]; then
  changed="$(git -C "$ROOT" status --porcelain -- skills upstream.lock.json | wc -l | tr -d ' ')"
  echo
  if [[ "$changed" == "0" ]]; then
    echo "==> mirror unchanged since last commit (nothing to commit)"
  else
    echo "==> ${changed} changed path(s) under skills/ or upstream.lock.json:"
    git -C "$ROOT" status --porcelain -- skills upstream.lock.json | sed 's/^/    /'
    echo "    commit and push to publish the update to CC Switch"
  fi
fi
