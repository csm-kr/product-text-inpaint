"""Codex 인증 사전 점검.

god-tibo 는 `~/.codex/auth.json` 파일만 읽는다 — codex 프로세스가 떠 있을 필요는 없다.
다만 이 라이브러리에는 **토큰 자동 갱신이 없다**: `refresh_token` 을 읽기만 하고 쓰지
않는다. 그래서 만료를 여기서 잡아 사용자에게 갱신을 안내한다.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# 이 시간 안에 만료되면 경고한다 (24시간).
WARN_SECONDS = 24 * 3600


def default_codex_home() -> Path:
    """Codex 홈 경로. 명시적 CODEX_HOME 이 있으면 사용자 홈보다 우선한다."""
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


@dataclass(frozen=True)
class AuthStatus:
    """인증 점검 결과 — ok 가 False 면 생성을 시도하지 않는다."""

    ok: bool
    auth_mode: str | None
    expires_at: int | None
    seconds_left: int | None
    has_installation_id: bool
    problems: tuple[str, ...] = field(default=())
    warnings: tuple[str, ...] = field(default=())


def decode_jwt_exp(token: str) -> int | None:
    """JWT 페이로드의 `exp` 를 읽는다. 서명은 검증하지 않는다 (만료 확인만 목적)."""
    if not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1].replace("-", "+").replace("_", "/")
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        data = json.loads(base64.b64decode(payload).decode("utf-8"))
    except Exception:
        return None
    exp = data.get("exp")
    return int(exp) if isinstance(exp, (int, float)) else None


def check_auth(auth_path: Path, installation_id_path: Path, now_epoch: int) -> AuthStatus:
    """auth.json 을 읽어 생성 가능한 상태인지 판정한다."""
    problems: list[str] = []
    warnings: list[str] = []

    try:
        raw = Path(auth_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return AuthStatus(
            ok=False, auth_mode=None, expires_at=None, seconds_left=None,
            has_installation_id=False,
            problems=(f"Codex 인증 파일을 찾을 수 없습니다: {auth_path}",),
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return AuthStatus(
            ok=False, auth_mode=None, expires_at=None, seconds_left=None,
            has_installation_id=False,
            problems=(f"인증 파일을 해석할 수 없습니다: {exc}",),
        )

    auth_mode = data.get("auth_mode")
    tokens = data.get("tokens") or {}
    access = tokens.get("access_token")

    if auth_mode and auth_mode != "chatgpt":
        warnings.append(f"auth_mode 가 {auth_mode} 입니다 — private backend 경로는 chatgpt 를 기대합니다.")
    if not access:
        problems.append("tokens.access_token 이 없습니다.")
    if not tokens.get("account_id"):
        problems.append("tokens.account_id 가 없습니다.")

    expires_at = decode_jwt_exp(access) if access else None
    seconds_left = None
    if access and expires_at is None:
        # 토큰이 손상됐거나 JWT 형식이 아니면 만료 여부를 알 수 없다 — 조용히 넘어가지 않고 경고한다.
        # (토큰 자체가 잘못됐다고 단정할 수는 없으므로 problem 이 아니라 warning.)
        warnings.append(
            "access token 의 만료 시각을 읽을 수 없습니다 — 토큰이 손상됐거나 JWT 형식이 아닙니다."
        )
    if expires_at is not None:
        seconds_left = expires_at - int(now_epoch)
        if seconds_left <= 0:
            problems.append(
                "access token 이 만료되었습니다. codex 를 한 번 실행해 로그인을 갱신하세요."
            )
        elif seconds_left < WARN_SECONDS:
            warnings.append(
                f"access token 이 곧 만료됩니다 ({seconds_left // 3600}시간 남음). "
                "codex 를 한 번 실행해 갱신하세요."
            )

    has_inst = Path(installation_id_path).is_file()
    if not has_inst:
        warnings.append(
            "~/.codex/installation_id 가 없습니다 — 요청에서 client metadata 가 빠집니다."
        )

    return AuthStatus(
        ok=not problems,
        auth_mode=auth_mode,
        expires_at=expires_at,
        seconds_left=seconds_left,
        has_installation_id=has_inst,
        problems=tuple(problems),
        warnings=tuple(warnings),
    )
