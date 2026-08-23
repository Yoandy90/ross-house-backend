"""Refresh Tokens Fase A — 18 tests obligatorios."""
import os
import inspect
from datetime import datetime, timezone, timedelta

import pytest

from rental.refresh_tokens import (generate_refresh_token, hash_refresh_token,
                                   classify_refresh_attempt, rotation_update,
                                   bootstrap_update, reuse_revocation_update,
                                   refresh_enabled, legacy_user_sessions_allowed,
                                   ROTATE, GRACE_ROTATE, REUSE_DETECTED, BOOTSTRAP, DENY,
                                   GRACE_SECONDS, ROTATED_HASHES_CAP)

NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)


def session(**over):
    base = {
        "sid": "a" * 32, "user_id": "u1", "role": "admin",
        "expires_at": NOW + timedelta(days=20), "revoked_at": None,
        "refresh_token_hash": None, "refresh_prev_hash": None,
        "rotated_hashes": [], "refresh_rotated_at": None,
        "refresh_expires_at": None, "refresh_generation": 0,
    }
    base.update(over)
    return base


def with_refresh(raw="tok-gen1", **over):
    s = session(refresh_token_hash=hash_refresh_token(raw),
                refresh_expires_at=NOW + timedelta(days=20),
                refresh_generation=1, **over)
    return s, raw


# 1. refresh success (hash actual → ROTATE)
def test_01_refresh_success():
    s, raw = with_refresh()
    assert classify_refresh_attempt(s, hash_refresh_token(raw), NOW) == ROTATE


# 2. la rotación cambia el hash y sube la generación
def test_02_refresh_rotates_token():
    s, raw = with_refresh()
    new_hash = hash_refresh_token(generate_refresh_token())
    upd = rotation_update(s, new_hash, NOW)
    assert upd["refresh_token_hash"] == new_hash != s["refresh_token_hash"]
    assert upd["refresh_prev_hash"] == s["refresh_token_hash"]
    assert upd["refresh_generation"] == 2
    assert upd["refresh_rotated_at"] == NOW


# 3. el token viejo (prev) queda inválido tras rotación (fuera de grace)
def test_03_old_token_invalid_after_rotation():
    s, raw = with_refresh()
    upd = rotation_update(s, hash_refresh_token("tok-gen2-raw"), NOW)
    s2 = {**s, **upd}
    late = NOW + timedelta(seconds=GRACE_SECONDS + 1)
    assert classify_refresh_attempt(s2, hash_refresh_token(raw), late) == REUSE_DETECTED


# 4. retry legítimo dentro de grace → GRACE_ROTATE (no revoca)
def test_04_legit_retry_within_grace():
    s, raw = with_refresh()
    upd = rotation_update(s, hash_refresh_token("tok-gen2-raw"), NOW)
    s2 = {**s, **upd}
    soon = NOW + timedelta(seconds=GRACE_SECONDS - 5)
    assert classify_refresh_attempt(s2, hash_refresh_token(raw), soon) == GRACE_ROTATE


# 5. reuse fuera de grace / generación vieja revoca familia
def test_05_reuse_outside_grace_revokes():
    s, raw1 = with_refresh("gen1")
    s = {**s, **rotation_update(s, hash_refresh_token("gen2"), NOW)}
    s = {**s, **rotation_update(s, hash_refresh_token("gen3"), NOW + timedelta(seconds=1))}
    # gen1 quedó en rotated_hashes → robo aunque estemos dentro del minuto
    assert classify_refresh_attempt(s, hash_refresh_token("gen1"),
                                    NOW + timedelta(seconds=2)) == REUSE_DETECTED
    upd = reuse_revocation_update(NOW)
    assert upd["revoked_reason"] == "refresh_reuse"
    assert upd["revoked_at"] == upd["refresh_reuse_detected_at"] == NOW


# 6. sesión revocada no puede refrescar
def test_06_revoked_cannot_refresh():
    s, raw = with_refresh(revoked_at=NOW - timedelta(hours=1))
    assert classify_refresh_attempt(s, hash_refresh_token(raw), NOW) == DENY


# 7. refresh expirado (expiración absoluta del refresh) no refresca
def test_07_expired_refresh_denied():
    s, raw = with_refresh()
    s["refresh_expires_at"] = NOW - timedelta(seconds=1)
    assert classify_refresh_attempt(s, hash_refresh_token(raw), NOW) == DENY


# 8. sesión inexistente / hash desconocido → DENY genérico
def test_08_wrong_session_denied():
    assert classify_refresh_attempt(None, hash_refresh_token("x"), NOW) == DENY
    s, _ = with_refresh()
    assert classify_refresh_attempt(s, hash_refresh_token("hash-desconocido"), NOW) == DENY


# 9. logout mata el refresh (revoked_at ⇒ DENY; endpoint usa la misma sesión)
def test_09_logout_kills_refresh():
    s, raw = with_refresh()
    s["revoked_at"] = NOW  # lo que hace POST /auth/logout sobre auth_sessions
    assert classify_refresh_attempt(s, hash_refresh_token(raw), NOW + timedelta(seconds=1)) == DENY


# 10. logout-all revoca todas las familias (misma primitiva revoked_at por sesión)
def test_10_logout_all_kills_families():
    sessions = [with_refresh(f"tok{i}")[0] for i in range(3)]
    for s in sessions:
        s["revoked_at"] = NOW  # sessions_router.logout_all hace update_many revoked_at
        assert classify_refresh_attempt(s, s["refresh_token_hash"], NOW) == DENY


# 11. revocación remota de UNA sesión (DELETE /auth/sessions/{sid}) mata su refresh
def test_11_remote_revoke_kills_refresh():
    s, raw = with_refresh()
    s["revoked_at"] = NOW
    s["revoked_reason"] = "revoked_by_user"
    assert classify_refresh_attempt(s, hash_refresh_token(raw), NOW) == DENY


# 12. 2FA: refresh nunca crea sesión nueva — solo renueva la sesión sid existente
def test_12_admin_2fa_preserved():
    import rental.refresh_router as rr
    src = inspect.getsource(rr)
    assert "create_session" not in src.replace("_session_from_bearer", "")
    assert "auth_sessions.insert_one" not in src
    # el access emitido conserva el mismo sid de la sesión 2FA
    assert '"sid": session["sid"]' in src


# 13. tokens legacy user_sessions NO pueden usar /auth/refresh
def test_13_legacy_user_sessions_rejected():
    import rental.refresh_router as rr
    src = inspect.getsource(rr._session_from_bearer)
    code_only = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "user_sessions" not in code_only      # jamás consulta la colección legacy
    assert "return None" in src                   # JWT inválido o sin sid ⇒ rechazo
    # y la lógica exige sid:
    assert "if not sid" in src


# 14. el refresh raw nunca se persiste (solo hashes en updates)
def test_14_raw_never_stored():
    s, _ = with_refresh()
    raw = generate_refresh_token()
    for upd in (rotation_update(s, hash_refresh_token(raw), NOW),
                bootstrap_update(s, hash_refresh_token(raw), NOW)):
        assert raw not in str(upd)
    import rental.refresh_router as rr
    src = inspect.getsource(rr)
    assert "insert_one" not in src or "new_raw" not in src.split("insert_one")[1][:200]


# 15. el refresh raw nunca se loggea ni entra al audit
def test_15_raw_never_logged():
    import rental.refresh_router as rr
    src = inspect.getsource(rr)
    for line in src.splitlines():
        if "logger." in line or "audit_log(" in line:
            assert "new_raw" not in line and "refresh_token" not in line.replace(
                "refresh_reuse", "").replace('action="refresh', '')
    # audit meta jamás lleva el token (no se pasa meta con token)
    assert "meta=" not in src


# 16. flag apagado preserva comportamiento actual (endpoint deshabilitado)
def test_16_flag_off_preserves_behavior(monkeypatch):
    monkeypatch.delenv("REFRESH_TOKENS_ENABLED", raising=False)
    assert refresh_enabled() is False
    monkeypatch.setenv("REFRESH_TOKENS_ENABLED", "true")
    assert refresh_enabled() is True
    # legacy user_sessions: default permitido (producción intacta), Fase B lo apaga
    monkeypatch.delenv("ALLOW_LEGACY_USER_SESSIONS", raising=False)
    assert legacy_user_sessions_allowed() is True
    monkeypatch.setenv("ALLOW_LEGACY_USER_SESSIONS", "false")
    assert legacy_user_sessions_allowed() is False


# 17. idempotencia/carrera: guard atómico en el endpoint + rotated_hashes con cap
def test_17_race_behavior():
    import rental.refresh_router as rr
    src = inspect.getsource(rr.refresh)
    assert '"revoked_at": None' in src and "modified_count != 1" in src  # guard atómico
    s, _ = with_refresh("g0")
    for i in range(1, 20):
        s = {**s, **rotation_update(s, hash_refresh_token(f"g{i}"), NOW + timedelta(seconds=i))}
    assert len(s["rotated_hashes"]) <= ROTATED_HASHES_CAP


# 18. expiración absoluta: bootstrap fija refresh_expires_at = expires_at de la sesión
#     y la rotación NUNCA la extiende
def test_18_absolute_expiry_enforced():
    s = session()
    b = bootstrap_update(s, hash_refresh_token("g1"), NOW)
    assert b["refresh_expires_at"] == s["expires_at"]
    upd = rotation_update({**s, **b}, hash_refresh_token("g2"), NOW)
    assert "refresh_expires_at" not in upd  # rotación no toca la expiración absoluta
    late = s["expires_at"] + timedelta(seconds=1)
    assert classify_refresh_attempt({**s, **b}, hash_refresh_token("g1"), late) == DENY


# extra: bootstrap solo una vez por sesión
def test_19_bootstrap_once():
    s = session()
    assert classify_refresh_attempt(s, None, NOW) == BOOTSTRAP
    s2, _ = with_refresh()
    assert classify_refresh_attempt(s2, None, NOW) == DENY
