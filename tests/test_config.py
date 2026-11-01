"""配置读取、优先级、边界和安全提示的行为测试。"""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from nova.config import ConfigurationError, Settings, load_settings


def test_defaults_are_typed_and_do_not_create_directories(tmp_path):
    settings = Settings()

    assert settings.environment == "development"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.log_level == "INFO"
    assert settings.db_timeout == 5.0
    assert settings.data_dir == tmp_path / "data"
    assert settings.database_path == tmp_path / "data" / "nova.sqlite3"
    assert settings.secret_values() == ()
    assert not (tmp_path / "data").exists()


def test_settings_does_not_implicitly_read_local_env(tmp_path):
    (tmp_path / ".env").write_text("NOVA_PORT=9001\n", encoding="utf-8")

    assert Settings().port == 8000
    assert load_settings().port == 9001


def test_loads_utf8_dotenv_and_resolves_paths(tmp_path):
    env_file = tmp_path / "local.env"
    env_file.write_text(
        "NOVA_ENV=test\nNOVA_HOST=localhost\nNOVA_PORT=9002\n"
        "NOVA_LOG_LEVEL=DEBUG\nNOVA_DATA_DIR=本地数据\n"
        "NOVA_DATABASE_PATH=单独目录/nova.sqlite3\nNOVA_DB_TIMEOUT=1.5\n"
        "NOVA_LLM_PROVIDER=deepseek\nDEEPSEEK_MODEL=保留占位\nDEEPSEEK_API_KEY=\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file)

    assert settings.environment == "test"
    assert settings.host == "localhost"
    assert settings.port == 9002
    assert settings.log_level == "DEBUG"
    assert settings.data_dir == tmp_path / "本地数据"
    assert settings.database_path == tmp_path / "单独目录" / "nova.sqlite3"
    assert settings.db_timeout == 1.5
    assert settings.model == "保留占位"
    assert settings.secret_values() == ()


def test_environment_overrides_dotenv(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("NOVA_PORT=9001\nNOVA_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    monkeypatch.setenv("NOVA_PORT", "9003")

    settings = load_settings(env_file)

    assert settings.port == 9003
    assert settings.log_level == "DEBUG"


def test_no_env_file_mode_ignores_dotenv(tmp_path):
    (tmp_path / ".env").write_text("NOVA_PORT=9004\n", encoding="utf-8")

    assert load_settings(None).port == 8000


def test_loading_has_no_global_cache(monkeypatch):
    monkeypatch.setenv("NOVA_PORT", "9005")
    assert load_settings(None).port == 9005
    monkeypatch.setenv("NOVA_PORT", "9006")
    assert load_settings(None).port == 9006


def test_constructor_accepts_field_names_and_path_objects(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "custom.sqlite3", port=9007)

    assert settings.port == 9007
    assert settings.database_path == tmp_path / "custom.sqlite3"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NOVA_PORT", "0"),
        ("NOVA_PORT", "65536"),
        ("NOVA_PORT", "not-a-port"),
        ("NOVA_PORT", "1.5"),
        ("NOVA_LOG_LEVEL", "TRACE"),
        ("NOVA_LOG_LEVEL", ""),
        ("NOVA_ENV", "unknown"),
        ("NOVA_HOST", ""),
        ("NOVA_HOST", "https://localhost"),
        ("NOVA_DATA_DIR", ""),
        ("NOVA_DATABASE_PATH", "   "),
        ("NOVA_DB_TIMEOUT", "0"),
        ("NOVA_DB_TIMEOUT", "31"),
        ("NOVA_DB_TIMEOUT", "NaN"),
        ("NOVA_DB_TIMEOUT", "inf"),
        ("NOVA_LLM_PROVIDER", "unsupported"),
    ],
)
def test_invalid_environment_reports_safe_field_hint(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError) as caught:
        load_settings(None)

    assert name in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "0.0.0.0", "localhost"])
def test_supported_listening_hosts(host):
    assert Settings(host=host).host == host


@pytest.mark.parametrize("port", [1, 65535])
def test_port_boundaries(port):
    assert Settings(port=port).port == port


@pytest.mark.parametrize("timeout", [0.1, 30.0])
def test_database_timeout_boundaries(timeout):
    assert Settings(db_timeout=timeout).db_timeout == timeout


@pytest.mark.parametrize("field", ["port", "db_timeout"])
def test_booleans_are_not_accepted_as_numeric_configuration(field):
    with pytest.raises(ValidationError):
        Settings(**{field: True})


def test_invalid_secret_like_value_is_not_in_safe_error(monkeypatch):
    synthetic_value = "SYNTHETIC_CONFIG_VALUE_FOR_REDACTION"
    monkeypatch.setenv("NOVA_PORT", synthetic_value)

    with pytest.raises(ConfigurationError) as caught:
        load_settings(None)

    assert "NOVA_PORT" in str(caught.value)
    assert synthetic_value not in str(caught.value)
    assert synthetic_value not in repr(caught.value)


def test_unknown_dotenv_setting_reports_neither_name_nor_value(tmp_path):
    synthetic_value = "SYNTHETIC_UNKNOWN_CONFIG_VALUE"
    env_file = tmp_path / ".env"
    env_file.write_text(f"UNRECOGNIZED_PRIVATE_FIELD={synthetic_value}\n", encoding="utf-8")

    with pytest.raises(ConfigurationError) as caught:
        load_settings(env_file)

    assert "未声明" in str(caught.value)
    assert synthetic_value not in str(caught.value)
    assert "UNRECOGNIZED_PRIVATE_FIELD" not in str(caught.value)


def test_secret_is_available_only_explicitly_and_not_dumped(monkeypatch):
    synthetic_value = "SYNTHETIC_CREDENTIAL_FOR_CONFIG_TESTS"
    monkeypatch.setenv("DEEPSEEK_API_KEY", synthetic_value)

    settings = load_settings(None)

    assert isinstance(settings.api_key, SecretStr)
    assert settings.secret_values() == (synthetic_value,)
    assert synthetic_value not in repr(settings)
    assert synthetic_value not in str(settings.model_dump())
    assert synthetic_value not in settings.model_dump_json()
    assert "api_key" not in settings.model_dump()


def test_missing_dotenv_uses_defaults(tmp_path):
    assert load_settings(tmp_path / "missing.env").port == 8000


def test_directory_is_not_a_valid_dotenv_file(tmp_path):
    with pytest.raises(ConfigurationError, match="必须为文件"):
        load_settings(tmp_path)


def test_invalid_utf8_dotenv_has_safe_message(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"NOVA_PORT=\xff\n")

    with pytest.raises(ConfigurationError, match="UTF-8"):
        load_settings(env_file)


def test_empty_path_is_rejected_without_resolving_to_cwd():
    with pytest.raises(ValidationError):
        Settings(database_path="")


def test_path_with_null_character_is_rejected():
    with pytest.raises(ValidationError):
        Settings(database_path="invalid\x00path")


def test_settings_are_immutable():
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.port = 9100


def test_path_type_is_preserved():
    assert isinstance(Settings().database_path, Path)
