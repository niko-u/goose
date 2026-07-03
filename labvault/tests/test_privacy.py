import os

import pytest

from labvault.config import PrivacyError, Settings


def _make(**env):
    for k, v in env.items():
        os.environ[k] = v
    return Settings()


def teardown_function():
    for k in ("LABVAULT_LLM_BACKEND", "LABVAULT_LLM_BASE_URL", "LABVAULT_ALLOW_REMOTE_LLM"):
        os.environ.pop(k, None)


def test_localhost_llm_allowed():
    s = _make(LABVAULT_LLM_BACKEND="ollama", LABVAULT_LLM_BASE_URL="http://localhost:11434")
    s.check_llm_privacy()  # no raise


def test_private_ip_allowed():
    s = _make(LABVAULT_LLM_BACKEND="ollama", LABVAULT_LLM_BASE_URL="http://192.168.1.50:11434")
    s.check_llm_privacy()  # no raise


def test_public_host_refused():
    s = _make(LABVAULT_LLM_BACKEND="openai", LABVAULT_LLM_BASE_URL="https://api.openai.com/v1")
    with pytest.raises(PrivacyError):
        s.check_llm_privacy()


def test_public_host_allowed_with_override():
    s = _make(
        LABVAULT_LLM_BACKEND="openai",
        LABVAULT_LLM_BASE_URL="https://api.openai.com/v1",
        LABVAULT_ALLOW_REMOTE_LLM="1",
    )
    s.check_llm_privacy()  # no raise when explicitly allowed
