"""Unit tests: model provisioning logs must never carry provider secrets.

Regression guard for the DEBUG line in provision_langchain_model: provider
clients embed the decrypted API key in their repr, so the log must use the
secrets-free description instead.
"""

from open_notebook.ai.provision import _describe_model

SECRET = "sk-or-v1-fake-secret-key"


class _FakeConfig:
    model_name = "fake/model"


class _FakeModel:
    """Mimics a provider client: secret in attributes AND repr."""

    _config = _FakeConfig()
    api_key = SECRET
    base_url = "https://example.invalid/v1"

    def __repr__(self) -> str:
        return f"_FakeModel(api_key='{SECRET}', base_url='{self.base_url}')"


class _BareModel:
    """No model_name anywhere — description falls back to the class name."""

    def __repr__(self) -> str:
        return f"_BareModel(api_key='{SECRET}')"


def test_describe_model_excludes_api_key():
    description = _describe_model(_FakeModel())
    assert SECRET not in description
    assert "example.invalid" not in description


def test_describe_model_includes_class_and_model_name():
    description = _describe_model(_FakeModel())
    assert "_FakeModel" in description
    assert "fake/model" in description


def test_describe_model_without_model_name_falls_back_to_class():
    description = _describe_model(_BareModel())
    assert description == "_BareModel"
    assert SECRET not in description
