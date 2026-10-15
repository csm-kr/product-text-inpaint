# 로컬 런타임 환경 구성 — text-clean 과 test-local 이 공유한다.
#
#   . "$PROJECT_ROOT/toolchain/env.sh"
#   configure_local_env "$PROJECT_ROOT"
#
# 자격증명은 이 저장소로 복사하지도 링크하지도 않는다. 머신마다 codex 로 직접
# 로그인하고, 여기서는 그 머신의 Codex 홈을 가리키기만 한다. 그래서 같은
# 체크아웃을 다른 컴퓨터로 옮겨도 인증만 그 자리에서 받으면 그대로 쓸 수 있다.
#
# 셸이 직접 쓰는 경로(VENV_PYTHON, PATH 등)는 POSIX 형식으로 두고, 네이티브
# 프로세스에 환경변수로 넘기는 경로만 to_native_path 로 바꾼다. Windows 의
# Python 은 /c/... 를 해석하지 못하고, PATH 는 MSYS 가 알아서 변환한다.

configure_local_env() {
  PROJECT_ROOT=$1
  . "$PROJECT_ROOT/toolchain/versions.env"
  . "$PROJECT_ROOT/toolchain/platform.sh"
  resolve_platform "$(uname -s)" "$(uname -m)" || {
    echo "[error] 지원하지 않는 플랫폼입니다: $(uname -s) $(uname -m)" >&2
    echo "[error] toolchain/versions.env 와 toolchain/platform.sh 에 항목을 추가하세요." >&2
    return 1
  }

  LOCAL_ROOT="$PROJECT_ROOT/.local"
  CACHE_ROOT="$PROJECT_ROOT/.cache"
  LOCAL_BIN="$LOCAL_ROOT/bin"

  NODE_BIN_DIR="$LOCAL_ROOT/node${NODE_BIN_SUBDIR:+/$NODE_BIN_SUBDIR}"
  NODE_BIN="$NODE_BIN_DIR/node$EXE_SUFFIX"
  VENV_BIN_DIR="$PROJECT_ROOT/.venv/$VENV_BIN_SUBDIR"
  VENV_PYTHON="$VENV_BIN_DIR/python$EXE_SUFFIX"

  # ffmpeg·ffprobe 는 링크나 심이 아니라 PATH 로 노출한다. Windows 에서는
  # symlink 에 관리자 권한이 필요하고, .cmd 심을 두면 의존성 스킬의
  # spawn("ffmpeg") 가 그것을 실행하지 못한다 (shell 없이는 .exe 만 찾는다).
  MEDIA_TOOL_DIR="$PROJECT_ROOT/toolchain/media"
  FFMPEG_DIR="$MEDIA_TOOL_DIR/node_modules/ffmpeg-static"
  FFPROBE_DIR="$MEDIA_TOOL_DIR/node_modules/@derhuerst/ffprobe-static"
  FFMPEG_BIN="$FFMPEG_DIR/ffmpeg$EXE_SUFFIX"
  FFPROBE_BIN="$FFPROBE_DIR/ffprobe$EXE_SUFFIX"

  TIBO_PACKAGE_DIR="$PROJECT_ROOT/$TIBO_RUNTIME_SUBPATH"

  # 이 머신의 Codex 홈. 명시적 CODEX_HOME 이 있으면 그것이 이긴다.
  CODEX_HOME_POSIX=${CODEX_HOME:-"$HOME/.codex"}

  PATH="$NODE_BIN_DIR:$LOCAL_BIN:$FFMPEG_DIR:$FFPROBE_DIR:$VENV_BIN_DIR:$PATH"
  CODEX_HOME=$(to_native_path "$CODEX_HOME_POSIX")
  CODEX_IMAGEGEN_AUTH_FILE=$(to_native_path "$CODEX_HOME_POSIX/auth.json")
  CODEX_IMAGEGEN_INSTALLATION_ID_FILE=$(to_native_path "$CODEX_HOME_POSIX/installation_id")
  # 의존성 스킬은 링크가 아닌 실제 경로로 넘긴다 — 링크 경로로 넘기면 Node 의
  # ESM 진입점 판정이 어긋나 exit 0 인데 아무 일도 하지 않는다
  # 링크 대신 실제 경로를 사용해 Node의 ESM 진입점 판정을 유지한다.
  GOD_TIBO_SKILL_ROOT=$(to_native_path "$TIBO_PACKAGE_DIR")
  GOD_TIBO_NODE=$(to_native_path "$NODE_BIN")
  PYTHONUTF8=1
  PYTHONPYCACHEPREFIX=$(to_native_path "$CACHE_ROOT/pycache")
  XDG_CACHE_HOME=$(to_native_path "$CACHE_ROOT/xdg")
  npm_config_cache=$(to_native_path "$CACHE_ROOT/npm")

  export PATH CODEX_HOME CODEX_IMAGEGEN_AUTH_FILE CODEX_IMAGEGEN_INSTALLATION_ID_FILE \
    GOD_TIBO_SKILL_ROOT GOD_TIBO_NODE PYTHONUTF8 PYTHONPYCACHEPREFIX XDG_CACHE_HOME \
    npm_config_cache
}

# 로컬 툴체인이 설치돼 있는지 확인한다. configure_local_env 뒤에 부른다.
require_local_toolchain() {
  for _rlt_path in "$VENV_PYTHON" "$NODE_BIN" "$FFMPEG_BIN" "$FFPROBE_BIN"; do
    if [ ! -x "$_rlt_path" ]; then
      echo "[error] 로컬 툴체인이 없습니다: $_rlt_path" >&2
      echo "[error] 먼저 ./setup-local.sh 를 실행하세요." >&2
      return 1
    fi
  done
}
