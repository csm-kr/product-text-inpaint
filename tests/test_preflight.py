import base64
import json

from image_lab.preflight import WARN_SECONDS, check_auth, decode_jwt_exp, default_codex_home


def test_default_codex_home_honors_environment(monkeypatch, tmp_path):
    configured = tmp_path / "local-codex"
    monkeypatch.setenv("CODEX_HOME", str(configured))
    assert default_codex_home() == configured


def make_jwt(exp: int | None) -> str:
    """테스트용 JWT — 서명은 검증하지 않으므로 페이로드만 유효하면 된다."""
    payload = {} if exp is None else {"exp": exp}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{raw}.signature"


def write_auth(tmp_path, *, exp, auth_mode="chatgpt", access=True, account=True):
    tokens = {}
    if access:
        tokens["access_token"] = make_jwt(exp)
    if account:
        tokens["account_id"] = "acct_test"
    p = tmp_path / "auth.json"
    p.write_text(json.dumps({"auth_mode": auth_mode, "tokens": tokens}), encoding="utf-8")
    return p


def test_decode_jwt_exp_reads_expiry():
    assert decode_jwt_exp(make_jwt(1234567890)) == 1234567890


def test_decode_jwt_exp_returns_none_on_garbage():
    assert decode_jwt_exp("not-a-jwt") is None
    assert decode_jwt_exp(make_jwt(None)) is None


def test_valid_token_is_ok(tmp_path):
    now = 1_000_000
    auth = write_auth(tmp_path, exp=now + 10 * 86400)
    inst = tmp_path / "installation_id"
    inst.write_text("abc", encoding="utf-8")

    status = check_auth(auth, inst, now)

    assert status.ok is True
    assert status.auth_mode == "chatgpt"
    assert status.seconds_left == 10 * 86400
    assert status.has_installation_id is True
    assert status.problems == ()


def test_expired_token_is_not_ok(tmp_path):
    now = 1_000_000
    auth = write_auth(tmp_path, exp=now - 1)
    inst = tmp_path / "installation_id"

    status = check_auth(auth, inst, now)

    assert status.ok is False
    assert any("만료" in p for p in status.problems)


def test_near_expiry_warns_but_stays_ok(tmp_path):
    now = 1_000_000
    auth = write_auth(tmp_path, exp=now + WARN_SECONDS - 60)
    inst = tmp_path / "installation_id"
    inst.write_text("abc", encoding="utf-8")

    status = check_auth(auth, inst, now)

    assert status.ok is True
    assert any("곧 만료" in w for w in status.warnings)


def test_missing_auth_file_is_not_ok(tmp_path):
    status = check_auth(tmp_path / "nope.json", tmp_path / "installation_id", 1_000_000)
    assert status.ok is False
    assert any("찾을 수 없" in p for p in status.problems)


def test_missing_access_token_is_not_ok(tmp_path):
    auth = write_auth(tmp_path, exp=2_000_000, access=False)
    status = check_auth(auth, tmp_path / "installation_id", 1_000_000)
    assert status.ok is False
    assert any("access_token" in p for p in status.problems)


def test_missing_installation_id_warns_only(tmp_path):
    now = 1_000_000
    auth = write_auth(tmp_path, exp=now + 86400 * 5)
    status = check_auth(auth, tmp_path / "installation_id", now)
    assert status.ok is True
    assert status.has_installation_id is False
    assert any("installation_id" in w for w in status.warnings)


def test_non_chatgpt_auth_mode_warns(tmp_path):
    now = 1_000_000
    auth = write_auth(tmp_path, exp=now + 86400 * 5, auth_mode="apikey")
    inst = tmp_path / "installation_id"
    inst.write_text("abc", encoding="utf-8")
    status = check_auth(auth, inst, now)
    assert any("auth_mode" in w for w in status.warnings)


def test_undecodable_access_token_warns(tmp_path):
    """access_token 은 있지만 JWT 로 디코딩할 수 없는 경우 — 조용히 통과시키면 안 된다."""
    now = 1_000_000
    p = tmp_path / "auth.json"
    p.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {"access_token": "sk-not-a-jwt", "account_id": "acct_test"},
            }
        ),
        encoding="utf-8",
    )
    inst = tmp_path / "installation_id"
    inst.write_text("abc", encoding="utf-8")

    status = check_auth(p, inst, now)

    # 만료 시각을 읽을 수 없을 뿐, 토큰 자체가 잘못됐다고 단정할 수는 없으므로 problem 이 아니라 warning.
    assert status.ok is True
    assert status.expires_at is None
    assert status.seconds_left is None
    assert any("만료 시각을 읽을 수 없습니다" in w for w in status.warnings)


def test_malformed_auth_json_is_not_ok(tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text("{not json", encoding="utf-8")

    status = check_auth(auth, tmp_path / "installation_id", 1_000_000)

    assert status.ok is False
    assert any("해석할 수 없" in p for p in status.problems)
