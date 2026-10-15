#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LOCAL_ROOT="$PROJECT_ROOT/.local"
CACHE_ROOT="$PROJECT_ROOT/.cache"
LOCAL_BIN="$LOCAL_ROOT/bin"
LOCAL_STATE="$LOCAL_ROOT/state"
VERSIONS_FILE="$PROJECT_ROOT/toolchain/versions.env"

if [ ! -f "$VERSIONS_FILE" ]; then
  echo "[error] Compatibility manifest is missing: $VERSIONS_FILE" >&2
  exit 1
fi
# shellcheck source=toolchain/versions.env
. "$VERSIONS_FILE"
# shellcheck source=toolchain/platform.sh
. "$PROJECT_ROOT/toolchain/platform.sh"

if ! resolve_platform "$(uname -s)" "$(uname -m)"; then
  echo "[error] 이 로컬 툴체인이 지원하지 않는 플랫폼입니다: $(uname -s) $(uname -m)" >&2
  echo "[error] toolchain/versions.env 에 체크섬을, toolchain/platform.sh 에 분기를 추가하세요." >&2
  exit 1
fi
echo "[info] 플랫폼: $PLATFORM_ID"

TIBO_PACKAGE_DIR="$PROJECT_ROOT/$TIBO_RUNTIME_SUBPATH"
MEDIA_TOOL_DIR="$PROJECT_ROOT/toolchain/media"
NODE_VERSION_DIR="$LOCAL_ROOT/$NODE_DIR_NAME"
NODE_BIN_DIR="$LOCAL_ROOT/node${NODE_BIN_SUBDIR:+/$NODE_BIN_SUBDIR}"

bootstrap_commands="curl git"
case "$ARCHIVE_FORMAT" in
  zip) bootstrap_commands="$bootstrap_commands unzip" ;;
  *) bootstrap_commands="$bootstrap_commands tar" ;;
esac
for command_name in $bootstrap_commands; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "[error] Bootstrap command is missing: $command_name" >&2
    exit 1
  fi
done
if ! command -v shasum >/dev/null 2>&1 && ! command -v sha256sum >/dev/null 2>&1; then
  echo "[error] Bootstrap command is missing: shasum 또는 sha256sum" >&2
  exit 1
fi

mkdir -p "$LOCAL_BIN" "$LOCAL_ROOT/src" "$CACHE_ROOT/downloads" \
  "$CACHE_ROOT/npm" "$CACHE_ROOT/uv" "$LOCAL_STATE" \
  "$PROJECT_ROOT/.agents/skills" "$PROJECT_ROOT/.claude/skills"

# --- uv ---------------------------------------------------------------------
uv_needs_install=1
if [ -x "$LOCAL_BIN/uv$EXE_SUFFIX" ]; then
  case "$("$LOCAL_BIN/uv$EXE_SUFFIX" --version 2>/dev/null)" in
    "uv $UV_VERSION "*) uv_needs_install=0 ;;
  esac
fi
if [ "$uv_needs_install" -eq 1 ]; then
  uv_archive_path="$CACHE_ROOT/downloads/$UV_ARCHIVE"
  curl -fL "$UV_URL" -o "$uv_archive_path"
  verify_sha256 "$UV_SHA256" "$uv_archive_path"
  uv_extract_dir="$CACHE_ROOT/downloads/uv-extract"
  rm -rf "$uv_extract_dir"
  mkdir -p "$uv_extract_dir"
  extract_archive "$uv_archive_path" "$uv_extract_dir"
  # tar.gz 는 아카이브를 감싸는 디렉터리가 있고, Windows zip 은 없다.
  uv_payload_dir="$uv_extract_dir${UV_DIR_NAME:+/$UV_DIR_NAME}"
  mv -f "$uv_payload_dir/uv$EXE_SUFFIX" "$LOCAL_BIN/uv$EXE_SUFFIX"
  mv -f "$uv_payload_dir/uvx$EXE_SUFFIX" "$LOCAL_BIN/uvx$EXE_SUFFIX"
  rm -rf "$uv_extract_dir"
fi
UV="$LOCAL_BIN/uv$EXE_SUFFIX"

# --- Node -------------------------------------------------------------------
if [ ! -x "$NODE_BIN_DIR/node$EXE_SUFFIX" ] || [ ! -d "$NODE_VERSION_DIR" ]; then
  node_archive_path="$CACHE_ROOT/downloads/$NODE_ARCHIVE"
  curl -fL "$NODE_URL" -o "$node_archive_path"
  verify_sha256 "$NODE_SHA256" "$node_archive_path"
  rm -rf "$NODE_VERSION_DIR"
  extract_archive "$node_archive_path" "$LOCAL_ROOT"
fi
link_dir "$NODE_VERSION_DIR" "$LOCAL_ROOT/node"

export PATH="$NODE_BIN_DIR:$LOCAL_BIN:$PATH"
export npm_config_cache=$(to_native_path "$CACHE_ROOT/npm")
export UV_CACHE_DIR=$(to_native_path "$CACHE_ROOT/uv")
export UV_PYTHON_INSTALL_DIR=$(to_native_path "$LOCAL_ROOT/python")
export UV_MANAGED_PYTHON=1
export UV_PROJECT_ENVIRONMENT=$(to_native_path "$PROJECT_ROOT/.venv")

# --- 저장소에 포함된 MIT 라이선스 런타임 --------------------------------------
# 비공개 GitHub 저장소에 접근하지 않는다. npm 의 공개 패키지만 설치한다.
if [ ! -f "$TIBO_PACKAGE_DIR/package-lock.json" ]; then
  echo "[error] Dependency skill package is missing its lockfile: $TIBO_PACKAGE_DIR" >&2
  exit 1
fi

tibo_lock_hash=$(sha256_of "$TIBO_PACKAGE_DIR/package-lock.json")
tibo_stamp="$LOCAL_STATE/tibo-package-lock.sha256"
recorded_tibo_hash=
if [ -f "$tibo_stamp" ]; then
  IFS= read -r recorded_tibo_hash < "$tibo_stamp" || true
fi
if [ ! -d "$TIBO_PACKAGE_DIR/node_modules/god-tibo-imagen" ] || \
   [ "$recorded_tibo_hash" != "$tibo_lock_hash" ]; then
  npm ci --omit=dev --no-audit --no-fund --prefix "$(to_native_path "$TIBO_PACKAGE_DIR")"
  printf '%s\n' "$tibo_lock_hash" > "$tibo_stamp"
fi

# --- ffmpeg / ffprobe -------------------------------------------------------
# 링크를 만들지 않는다 — 런처가 node_modules 디렉터리를 PATH 에 직접 올린다.
# Windows 에서 symlink 는 관리자 권한이 필요하고, .cmd 심은 의존성 스킬의
# spawn("ffmpeg") 가 실행하지 못한다.
FFMPEG_BIN="$MEDIA_TOOL_DIR/node_modules/ffmpeg-static/ffmpeg$EXE_SUFFIX"
FFPROBE_BIN="$MEDIA_TOOL_DIR/node_modules/@derhuerst/ffprobe-static/ffprobe$EXE_SUFFIX"
media_lock_hash=$(sha256_of "$MEDIA_TOOL_DIR/package-lock.json")
media_stamp="$LOCAL_STATE/media-package-lock.sha256"
recorded_media_hash=
if [ -f "$media_stamp" ]; then
  IFS= read -r recorded_media_hash < "$media_stamp" || true
fi
if [ ! -x "$FFMPEG_BIN" ] || [ ! -x "$FFPROBE_BIN" ] || \
   [ "$recorded_media_hash" != "$media_lock_hash" ]; then
  npm ci --no-audit --no-fund --prefix "$(to_native_path "$MEDIA_TOOL_DIR")"
  printf '%s\n' "$media_lock_hash" > "$media_stamp"
fi
if [ ! -x "$FFMPEG_BIN" ] || [ ! -x "$FFPROBE_BIN" ]; then
  echo "[error] Local ffmpeg or ffprobe installer did not produce an executable." >&2
  exit 1
fi

# --- Python -----------------------------------------------------------------
"$UV" python install --no-bin --no-registry "$PYTHON_VERSION"
python_path=$("$UV" python find --managed-python "$PYTHON_VERSION")
"$UV" sync --project "$(to_native_path "$PROJECT_ROOT/runtime/python")" \
  --locked --extra dev --python "$python_path"

# --- 프로젝트 로컬 스킬 등록 ---------------------------------------------------
# 등록은 커밋하지 않고 여기서 만든다 — 커밋된 symlink 는 core.symlinks=false 인
# 체크아웃에서 살아남지 못한다 (docs/SETUP-ISSUES.md IL-017).
for registration in .agents/skills/product-text-inpaint .claude/skills/product-text-inpaint; do
  link_dir "$PROJECT_ROOT/skills/product-text-inpaint" "$PROJECT_ROOT/$registration"
done

echo "[ok] Local toolchain installed ($PLATFORM_ID)."

# --- 이 머신의 Codex 인증 -----------------------------------------------------
# 자격증명은 이 저장소로 복사하지도 링크하지도 않는다. 머신마다 codex 에서 직접
# 로그인하고, 런처는 그 머신의 Codex 홈을 가리키기만 한다.
CODEX_HOME_POSIX=${CODEX_HOME:-"$HOME/.codex"}
if [ -f "$CODEX_HOME_POSIX/auth.json" ]; then
  echo "[ok] Codex 인증 확인: $CODEX_HOME_POSIX/auth.json"
else
  echo "[안내] 이 머신에는 Codex 로그인이 없습니다: $CODEX_HOME_POSIX/auth.json"
  echo "[안내] codex 를 한 번 실행해 로그인한 뒤 ./text-clean doctor 로 확인하세요."
  echo "[안내] Codex 홈이 다른 곳이면 CODEX_HOME 을 지정해 실행하세요."
fi

echo "[ok] Ready: ./text-clean doctor"
echo "[ok] Optional diagnostics: ./text-clean doctor"
echo "[ok] Optional tests: ./test-local"
