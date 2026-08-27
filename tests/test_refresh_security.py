"""PARTE H — 13 tests de seguridad adicionales (grace idempotente D1, reuse antiguo D2, bootstrap)."""
import os
import inspect
from datetime import datetime, timezone, timedelta

import pytest

from rental.refresh_tokens import (generate_refresh_token, hash_refresh_token,
                                   next_refresh_token, classify_refresh_attempt,
                                   rotation_update, bootstrap_update,
                                   ROTATE, GRACE_ROTATE, REUSE_DETECTED, BOOTSTRAP, DENY,
                                   GRACE_SECONDS, ROTATED_HASHES_CAP, CONSUMED_COLLECTION)

os.environ.setdefault("TENANT_JWT_SECRET", "test-secret-for-hmac-chain")
NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)


def session(**over):
    base = {"sid": "a" * 32, "user_id": "u1", "role": "admin",
            "expires_at": NOW + timedelta(days=20), "revoked_at": None,
            "refresh_token_hash": None, "refresh_prev_hash": None,
            "rotated_hashes": [], "refresh_rotated_at": None,
            "refresh_expires_at": None, "refresh_generation": 0}
    base.update(over)
    return base


def rotate_chain(s, presented_raw, at):
    """Simula la rotación del endpoint: R_{n+1} = HMAC(K, R_n)."""
    new_raw = next_refresh_token(presented_raw)
    return {**s, **rotation_update(s, hash_refresh_token(new_raw), at)}, new_raw


# 1. Refresh concurrente A/B con el mismo R1
def test_h01_concurrent_refresh_same_r1():
    r1 = generate_refresh_token()
    s = {**session(), **bootstrap_update(session(), hash_refresh_token(r1), NOW)}
    # Request A rota
    s2, r2_a = rotate_chain(s, r1, NOW)
    # Request B llega 5s después con el MISMO R1 → GRACE_ROTATE (no revoca)
    at_b = NOW + timedelta(seconds=5)
    assert classify_refresh_attempt(s2, hash_refresh_token(r1), at_b) == GRACE_ROTATE
    # y la re-derivación de B produce EXACTAMENTE el mismo R2 que recibió A
    r2_b = next_refresh_token(r1)
    assert r2_b == r2_a
    assert hash_refresh_token(r2_b) == s2["refresh_token_hash"]


# 2. Semántica exacta de la grace: idempotente, sin rotación extra, R2 sigue vigente
def test_h02_grace_response_idempotent_no_extra_rotation():
    r1 = generate_refresh_token()
    s = {**session(), **bootstrap_update(session(), hash_refresh_token(r1), NOW)}
    s2, r2 = rotate_chain(s, r1, NOW)
    gen_after_a = s2["refresh_generation"]
    # el endpoint en GRACE_ROTATE NO escribe rotación (verificación por código):
    import rental.refresh_router as rr
    src = inspect.getsource(rr.refresh)
    grace_block = src.split("if action == GRACE_ROTATE:")[1].split("if action == BOOTSTRAP:")[0]
    assert "update_one" not in grace_block          # cero writes de rotación en grace
    assert "rotation_update" not in grace_block
    assert "_next_refresh_or_503" in grace_block    # re-deriva mediante wrapper fail-closed
    wrapper_src = inspect.getsource(rr._next_refresh_or_503)
    assert "next_refresh_token" in wrapper_src      # wrapper conserva derivación D1
    # R2 sigue siendo el token vigente tras el replay de R1
    assert classify_refresh_attempt(s2, hash_refresh_token(r2), NOW + timedelta(seconds=10)) == ROTATE
    assert s2["refresh_generation"] == gen_after_a


# 3. Replay repetido de R1 durante la grace NO acuña tokens ilimitados
def test_h03_replay_during_grace_mints_nothing_new():
    r1 = generate_refresh_token()
    outputs = {next_refresh_token(r1) for _ in range(50)}
    assert len(outputs) == 1  # siempre el MISMO R2 — cero acuñación nueva


# 4. El retry NO requiere almacenar R2 raw en el servidor
def test_h04_no_raw_r2_stored():
    r1 = generate_refresh_token()
    s = {**session(), **bootstrap_update(session(), hash_refresh_token(r1), NOW)}
    s2, r2 = rotate_chain(s, r1, NOW)
    assert r2 not in str(s2)                        # el doc solo contiene hashes
    assert hash_refresh_token(r2) in str(s2)
    # y la cadena permite recuperarlo solo con R1 + clave del servidor
    assert next_refresh_token(r1) == r2


# 5-6. Token con >10 rotaciones de antigüedad reutilizado ⇒ REUSE + revocación de familia
def test_h05_h06_old_token_beyond_cap_revokes():
    r = generate_refresh_token()
    s = {**session(), **bootstrap_update(session(), hash_refresh_token(r), NOW)}
    first_raw = r
    consumed = []                                    # espejo de consumed_refresh_hashes (D2)
    for i in range(1, 15):                           # 14 rotaciones > cap 10
        consumed.append(hash_refresh_token(r))
        s, r = rotate_chain(s, r, NOW + timedelta(seconds=i))
    old_hash = hash_refresh_token(first_raw)
    assert old_hash not in (s.get("rotated_hashes") or [])   # ya salió del cap del doc
    assert old_hash in consumed                               # pero está en consumed (TTL)
    # el endpoint: sesión no encontrada por hash → consulta consumed → revoca familia
    import rental.refresh_router as rr
    src = inspect.getsource(rr.refresh)
    assert "CONSUMED_COLLECTION" in src and "find_one({\"hash\": presented_hash})" in src
    assert "_revoke_family_for_reuse(consumed[" in src


# 7. Storage acotado/escalable
def test_h07_bounded_storage():
    r = generate_refresh_token()
    s = {**session(), **bootstrap_update(session(), hash_refresh_token(r), NOW)}
    for i in range(1, 100):
        s, r = rotate_chain(s, r, NOW + timedelta(seconds=i))
    assert len(s["rotated_hashes"]) <= ROTATED_HASHES_CAP     # doc de sesión acotado
    import rental.refresh_router as rr
    src = inspect.getsource(rr)
    assert "expireAfterSeconds=0" in src                       # consumed con TTL
    assert '"expires_at": exp + timedelta(days=1)' in src      # vida = expiry absoluto + margen


# 8-9. Carrera de bootstrap: solo UNO establece la familia (guard atómico)
def test_h08_h09_bootstrap_race_single_winner():
    s = session()
    assert classify_refresh_attempt(s, None, NOW) == BOOTSTRAP
    import rental.refresh_router as rr
    src = inspect.getsource(rr.refresh)
    boot_block = src.split("if action == BOOTSTRAP:")[1].split("else:")[0]
    assert '"refresh_token_hash": None' in boot_block          # guard exige hash None
    assert "modified_count != 1" in src                        # el perdedor recibe 401
    # tras el bootstrap ganador, el segundo intento clasifica DENY:
    s_after = {**s, **bootstrap_update(s, hash_refresh_token("x"), NOW)}
    assert classify_refresh_attempt(s_after, None, NOW) == DENY


# 10. Bootstrap tras revocación falla
def test_h10_bootstrap_after_revocation_fails():
    s = session(revoked_at=NOW)
    assert classify_refresh_attempt(s, None, NOW + timedelta(seconds=1)) == DENY


# 11. Bootstrap no extiende la expiración absoluta ni cambia el sid
def test_h11_bootstrap_no_expiry_extension():
    s = session()
    b = bootstrap_update(s, hash_refresh_token("x"), NOW)
    assert b["refresh_expires_at"] == s["expires_at"]
    assert "expires_at" not in b                                # jamás toca la sesión
    assert "sid" not in b
    assert b["refresh_family_id"] == s["sid"]


# 12-13. Legacy sin sid y user_sessions raw NO pueden bootstrap
def test_h12_h13_legacy_cannot_bootstrap():
    import rental.refresh_router as rr
    src = inspect.getsource(rr._session_from_bearer)
    assert "if not sid" in src and "return None" in src        # JWT sin sid → rechazo
    code_only = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "user_sessions" not in code_only                    # raw legacy → jwt.decode falla → None
    # y el rate limit aplica también al bootstrap (antes de clasificar):
    full = inspect.getsource(rr.refresh)
    assert full.index("check_rate_limit_persistent") < full.index("classify_refresh_attempt")
