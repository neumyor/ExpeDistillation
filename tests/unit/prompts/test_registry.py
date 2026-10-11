"""Unit tests for the B2 prompt registry (register / get / digest / digests)."""

import hashlib

import pytest

from expedistill.prompts import (
    PROMPTS,
    DuplicatePromptError,
    PromptNotFoundError,
    PromptSpec,
    digest,
    digests,
    get,
    register,
)


def _temp_spec(name, template):
    return PromptSpec(name=name, template=template, description="test: temporary spec")


def test_register_and_get():
    spec = _temp_spec("TEST_REGISTER", "hello {who}")
    try:
        assert register(spec) is spec
        assert PROMPTS["TEST_REGISTER"] is spec
        assert get("TEST_REGISTER") == "hello {who}"
    finally:
        PROMPTS.pop("TEST_REGISTER", None)


def test_register_duplicate_raises():
    name = next(iter(PROMPTS))
    with pytest.raises(DuplicatePromptError, match=name):
        register(_temp_spec(name, "other text"))


def test_get_unknown_lists_known_names():
    with pytest.raises(PromptNotFoundError) as excinfo:
        get("NO_SUCH_PROMPT")
    message = str(excinfo.value)
    assert "NO_SUCH_PROMPT" in message
    for name in PROMPTS:
        assert name in message


def test_digest_is_sha256_of_template_and_stable():
    for name in PROMPTS:
        expected = hashlib.sha256(PROMPTS[name].template.encode("utf-8")).hexdigest()
        assert digest(name) == expected
        assert digest(name) == digest(name)  # stable across calls


def test_digest_sensitive_to_text_change():
    spec = _temp_spec("TEST_DIGEST_A", "same prefix")
    other = _temp_spec("TEST_DIGEST_B", "same prefixX")
    try:
        register(spec)
        register(other)
        assert digest("TEST_DIGEST_A") != digest("TEST_DIGEST_B")
    finally:
        PROMPTS.pop("TEST_DIGEST_A", None)
        PROMPTS.pop("TEST_DIGEST_B", None)


def test_digest_unknown_raises():
    with pytest.raises(PromptNotFoundError):
        digest("NO_SUCH_PROMPT")


def test_digests_covers_every_registered_prompt():
    result = digests()
    assert set(result) == set(PROMPTS)
    for name, value in result.items():
        assert value == digest(name)
