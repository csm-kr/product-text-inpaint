# 로컬 툴체인 플랫폼 해석 — setup-local.sh 와 toolchain/env.sh 가 공유한다.
#
#   . "$PROJECT_ROOT/toolchain/versions.env"
#   . "$PROJECT_ROOT/toolchain/platform.sh"
#   resolve_platform "$(uname -s)" "$(uname -m)"
#
# versions.env 를 먼저 로드해야 한다 — 버전과 체크섬의 단일 출처다.

# uname 결과에서 플랫폼을 정하고 아래 변수를 설정한다. 미지원이면 1을 반환한다.
#   PLATFORM_ID ARCHIVE_FORMAT
#   NODE_ARCHIVE NODE_URL NODE_SHA256 NODE_DIR_NAME NODE_BIN_SUBDIR
#   UV_ARCHIVE UV_URL UV_SHA256 UV_DIR_NAME
#   VENV_BIN_SUBDIR EXE_SUFFIX
resolve_platform() {
  _rp_system=$1
  _rp_machine=$2

  case "$_rp_machine" in
    arm64 | aarch64) _rp_arch=arm64 ;;
    x86_64 | amd64) _rp_arch=x64 ;;
    *) return 1 ;;
  esac
  case "$_rp_system" in
    Darwin) _rp_os=darwin ;;
    Linux) _rp_os=linux ;;
    # Git Bash·MSYS2·Cygwin 은 모두 같은 Windows 아티팩트를 쓴다.
    MINGW* | MSYS* | CYGWIN*) _rp_os=win ;;
    *) return 1 ;;
  esac

  PLATFORM_ID="$_rp_os-$_rp_arch"
  case "$PLATFORM_ID" in
    darwin-arm64)
      ARCHIVE_FORMAT=tar.gz
      NODE_DIR_NAME="node-v${NODE_VERSION}-darwin-arm64"
      NODE_ARCHIVE="${NODE_DIR_NAME}.tar.gz"
      NODE_SHA256=$NODE_SHA256_DARWIN_ARM64
      NODE_BIN_SUBDIR=bin
      UV_DIR_NAME=uv-aarch64-apple-darwin
      UV_ARCHIVE="${UV_DIR_NAME}.tar.gz"
      UV_SHA256=$UV_SHA256_DARWIN_ARM64
      VENV_BIN_SUBDIR=bin
      EXE_SUFFIX=
      ;;
    linux-x64)
      ARCHIVE_FORMAT=tar.gz
      NODE_DIR_NAME="node-v${NODE_VERSION}-linux-x64"
      NODE_ARCHIVE="${NODE_DIR_NAME}.tar.gz"
      NODE_SHA256=$NODE_SHA256_LINUX_X64
      NODE_BIN_SUBDIR=bin
      UV_DIR_NAME=uv-x86_64-unknown-linux-gnu
      UV_ARCHIVE="${UV_DIR_NAME}.tar.gz"
      UV_SHA256=$UV_SHA256_LINUX_X64
      VENV_BIN_SUBDIR=bin
      EXE_SUFFIX=
      ;;
    win-x64)
      ARCHIVE_FORMAT=zip
      NODE_DIR_NAME="node-v${NODE_VERSION}-win-x64"
      NODE_ARCHIVE="${NODE_DIR_NAME}.zip"
      NODE_SHA256=$NODE_SHA256_WIN_X64
      # win-x64 Node 는 node.exe 가 아카이브 최상위에 있다 — bin 하위가 없다.
      NODE_BIN_SUBDIR=
      # uv 의 Windows zip 은 감싸는 디렉터리 없이 uv.exe 를 최상위에 담는다.
      UV_DIR_NAME=
      UV_ARCHIVE=uv-x86_64-pc-windows-msvc.zip
      UV_SHA256=$UV_SHA256_WIN_X64
      VENV_BIN_SUBDIR=Scripts
      EXE_SUFFIX=.exe
      ;;
    *) return 1 ;;
  esac

  NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/${NODE_ARCHIVE}"
  UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_ARCHIVE}"
}

# 네이티브 프로세스(Python·Node·npm)에 넘길 형태로 경로를 바꾼다.
# Windows 의 Python 은 MSYS 경로(/c/...)를 해석하지 못하므로 반드시 필요하다.
# PATH 는 예외다 — MSYS 가 네이티브 프로세스를 띄울 때 알아서 변환한다.
to_native_path() {
  case "$PLATFORM_ID" in
    win-*) cygpath -w "$1" ;;
    *) printf '%s' "$1" ;;
  esac
}

# 디렉터리 링크를 만든다. Windows 는 관리자 권한 없이 symlink 를 만들 수 없어
# junction 을 쓴다. 기존 링크는 대상 내용을 건드리지 않고 교체하고, 실제
# 디렉터리가 그 자리에 있으면 지우지 않고 실패한다.
link_dir() {
  _ld_target=$1
  _ld_link=$2
  case "$PLATFORM_ID" in
    win-*)
      if [ -f "$_ld_link" ]; then
        # core.symlinks=false 인 체크아웃에서 git 은 커밋된 symlink 를 경로
        # 문자열이 든 일반 파일로 푼다. 링크 자리의 일반 파일은 그 자리표시자뿐이다.
        rm -f "$_ld_link"
      elif [ -e "$_ld_link" ]; then
        # rmdir 은 junction 을 대상 손실 없이 지우고, 비어있지 않은 실제
        # 디렉터리는 거부한다 — 그 거부가 곧 안전장치다.
        cmd //c rmdir "$(cygpath -w "$_ld_link")" >/dev/null 2>&1 || {
          echo "[error] 링크 자리에 실제 디렉터리가 있습니다: $_ld_link" >&2
          return 1
        }
      fi
      cmd //c mklink //J "$(cygpath -w "$_ld_link")" "$(cygpath -w "$_ld_target")" >/dev/null
      ;;
    *)
      if [ -e "$_ld_link" ] && [ ! -L "$_ld_link" ]; then
        echo "[error] 링크 자리에 실제 디렉터리가 있습니다: $_ld_link" >&2
        return 1
      fi
      ln -sfn "$_ld_target" "$_ld_link"
      ;;
  esac
}

# sha256 검증. macOS 는 shasum, 배포판에 따라 sha256sum 만 있다.
verify_sha256() {
  _vs_expected=$1
  _vs_path=$2
  if command -v shasum >/dev/null 2>&1; then
    printf '%s  %s\n' "$_vs_expected" "$_vs_path" | shasum -a 256 -c - >/dev/null
  else
    printf '%s  %s\n' "$_vs_expected" "$_vs_path" | sha256sum -c - >/dev/null
  fi
}

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  else
    sha256sum "$1" | cut -d' ' -f1
  fi
}

# 아카이브를 디렉터리에 푼다. tar 는 zip 을 다루지 못하므로 형식으로 나눈다.
extract_archive() {
  case "$ARCHIVE_FORMAT" in
    zip) unzip -q -o "$1" -d "$2" ;;
    *) tar -xzf "$1" -C "$2" ;;
  esac
}
