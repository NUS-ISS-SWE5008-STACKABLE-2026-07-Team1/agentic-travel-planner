from flaskapp.config import get_llm_settings


def test_azure_llm_configuration_is_normalized():
    settings, error = get_llm_settings({
        "LLM_PROVIDER": "azure_openai", "LLM_MODEL": "travel-gpt",
        "AZURE_OPENAI_API_KEY": "secret", "AZURE_OPENAI_ENDPOINT": "https://example.test",
        "AZURE_OPENAI_API_VERSION": "2024-12-01-preview",
    })
    assert error is None
    assert settings["provider"] == "azure_openai"
    assert settings["model"] == "travel-gpt"


def test_openai_llm_configuration_is_normalized():
    settings, error = get_llm_settings({
        "LLM_PROVIDER": "openai", "LLM_MODEL": "gpt-5", "OPENAI_API_KEY": "secret",
    })
    assert error is None
    assert settings["provider"] == "openai"
    assert settings["model"] == "gpt-5"


def test_llm_configuration_error_never_contains_secret():
    settings, error = get_llm_settings({"LLM_PROVIDER": "openai", "LLM_MODEL": "gpt-5"})
    assert settings is None
    assert error == "OpenAI configuration is incomplete"


def test_auto_detects_deepseek_and_supplies_provider_default():
    settings, error = get_llm_settings({
        "LLM_PROVIDER": "auto", "DEEPSEEK_API_KEY": "secret",
    })
    assert error is None
    assert settings["provider"] == "deepseek"
    assert settings["model"] == "deepseek-v4-flash"
    assert settings["base_url"] == "https://api.deepseek.com"


def test_auto_rejects_ambiguous_credentials():
    settings, error = get_llm_settings({
        "LLM_PROVIDER": "auto", "OPENAI_API_KEY": "one", "XAI_API_KEY": "two",
    })
    assert settings is None
    assert "exactly one" in error


def test_hosted_meta_requires_endpoint_and_model():
    settings, error = get_llm_settings({
        "LLM_PROVIDER": "meta", "META_API_KEY": "secret",
        "META_BASE_URL": "https://host.example/v1", "LLM_MODEL": "hosted-llama",
    })
    assert error is None
    assert settings["provider"] == "meta"
