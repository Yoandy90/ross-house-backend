"""
Unit tests for the _clean_env() helper introduced in iter_11 to defend
against malformed Railway environment variable pastes (multi-line, leading/
trailing whitespace, Windows \r\n line endings).

Real-world bug being prevented:
  XCEL_REDIRECT_URI was pasted in Railway as
      https://rosshouserentals.com/tenant/utilities?callback=greenbutton\n
      XCEL_ADMIN_SCOPE=FB=34_35
  -> Xcel SAML rejected the malformed redirect_uri.

_clean_env must:
  - Trim leading/trailing whitespace
  - Truncate at the first newline (\n or \r\n)
  - Return '' on empty/unset (no crash)
  - Honor default when var unset
"""
import os
import importlib
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rental import xcel_energy_router as xer  # noqa: E402


def test_clean_env_strips_leading_trailing_whitespace():
    os.environ["TEST_CLEAN_ENV_VAR"] = "   value   "
    try:
        assert xer._clean_env("TEST_CLEAN_ENV_VAR") == "value"
    finally:
        del os.environ["TEST_CLEAN_ENV_VAR"]


def test_clean_env_truncates_at_middle_newline():
    """The real Railway bug: multiline paste concatenated two env vars."""
    os.environ["TEST_CLEAN_ENV_VAR"] = (
        "https://rosshouserentals.com/tenant/utilities?callback=greenbutton\n"
        "XCEL_ADMIN_SCOPE=FB=34_35"
    )
    try:
        result = xer._clean_env("TEST_CLEAN_ENV_VAR")
        assert result == "https://rosshouserentals.com/tenant/utilities?callback=greenbutton"
        assert "\n" not in result
        assert "XCEL_ADMIN_SCOPE" not in result
        assert "FB=34_35" not in result
    finally:
        del os.environ["TEST_CLEAN_ENV_VAR"]


def test_clean_env_handles_windows_crlf():
    os.environ["TEST_CLEAN_ENV_VAR"] = "https://example.com/cb\r\nXCEL_OTHER=foo"
    try:
        result = xer._clean_env("TEST_CLEAN_ENV_VAR")
        assert result == "https://example.com/cb"
        assert "\r" not in result
        assert "\n" not in result
    finally:
        del os.environ["TEST_CLEAN_ENV_VAR"]


def test_clean_env_handles_leading_newline():
    os.environ["TEST_CLEAN_ENV_VAR"] = "\nactual_value"
    try:
        # First logical line is empty -> returns ''
        assert xer._clean_env("TEST_CLEAN_ENV_VAR") == ""
    finally:
        del os.environ["TEST_CLEAN_ENV_VAR"]


def test_clean_env_handles_trailing_newline():
    os.environ["TEST_CLEAN_ENV_VAR"] = "value\n"
    try:
        assert xer._clean_env("TEST_CLEAN_ENV_VAR") == "value"
    finally:
        del os.environ["TEST_CLEAN_ENV_VAR"]


def test_clean_env_empty_string_returns_empty():
    os.environ["TEST_CLEAN_ENV_VAR"] = ""
    try:
        assert xer._clean_env("TEST_CLEAN_ENV_VAR") == ""
    finally:
        del os.environ["TEST_CLEAN_ENV_VAR"]


def test_clean_env_unset_returns_empty_no_crash():
    os.environ.pop("DEFINITELY_UNSET_VAR_XYZ", None)
    assert xer._clean_env("DEFINITELY_UNSET_VAR_XYZ") == ""


def test_clean_env_unset_uses_default():
    os.environ.pop("DEFINITELY_UNSET_VAR_XYZ", None)
    assert xer._clean_env("DEFINITELY_UNSET_VAR_XYZ", "fallback") == "fallback"


def test_clean_env_only_whitespace_returns_empty():
    os.environ["TEST_CLEAN_ENV_VAR"] = "   \t  "
    try:
        assert xer._clean_env("TEST_CLEAN_ENV_VAR") == ""
    finally:
        del os.environ["TEST_CLEAN_ENV_VAR"]


def test_clean_env_strips_after_truncating_newline():
    """Combined case: leading spaces + trailing newline + junk after."""
    os.environ["TEST_CLEAN_ENV_VAR"] = "  https://x.com/cb  \nNEXT_VAR=bad"
    try:
        assert xer._clean_env("TEST_CLEAN_ENV_VAR") == "https://x.com/cb"
    finally:
        del os.environ["TEST_CLEAN_ENV_VAR"]


# ────────────── Integration: verify the reload picks up cleaned values ──────────────

def test_module_constants_reload_with_malformed_redirect_uri():
    """Simulate the actual Railway bug: set a multiline XCEL_REDIRECT_URI,
    reload the module, confirm the module-level constant is clean."""
    bad_value = (
        "https://rosshouserentals.com/tenant/utilities?callback=greenbutton\n"
        "XCEL_ADMIN_SCOPE=FB=34_35"
    )
    original = os.environ.get("XCEL_REDIRECT_URI")
    os.environ["XCEL_REDIRECT_URI"] = bad_value
    try:
        reloaded = importlib.reload(xer)
        assert reloaded.XCEL_REDIRECT_URI == (
            "https://rosshouserentals.com/tenant/utilities?callback=greenbutton"
        )
        assert "\n" not in reloaded.XCEL_REDIRECT_URI
        assert "XCEL_ADMIN_SCOPE" not in reloaded.XCEL_REDIRECT_URI
    finally:
        if original is None:
            os.environ.pop("XCEL_REDIRECT_URI", None)
        else:
            os.environ["XCEL_REDIRECT_URI"] = original
        importlib.reload(xer)


def test_all_10_xcel_constants_use_clean_env():
    """grep-style check: ensure all XCEL_* module-level constants invoke _clean_env."""
    src_path = os.path.join(os.path.dirname(__file__), "..", "rental", "xcel_energy_router.py")
    with open(src_path) as f:
        src = f.read()

    expected = [
        "XCEL_CLIENT_ID",
        "XCEL_CLIENT_SECRET",
        "XCEL_AUTH_URL",
        "XCEL_TOKEN_URL",
        "XCEL_API_BASE",
        "XCEL_REDIRECT_URI",
        "XCEL_SCOPE",
        "XCEL_ADMIN_SCOPE",
        "XCEL_APPLICATION_ID",
        "XCEL_REGISTRATION_TOKEN",
    ]
    for name in expected:
        # Match assignment "NAME = _clean_env(" (allowing arbitrary whitespace)
        pattern_a = f"{name} = _clean_env("
        pattern_b = f"{name}    = _clean_env("  # safety, multi-space
        assert pattern_a in src or pattern_b in src, (
            f"{name} does not use _clean_env() at module level"
        )
