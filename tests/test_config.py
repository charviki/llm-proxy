import pytest
from pathlib import Path
from config.loader import ConfigLoader
from config.models import AppConfig

def test_load_valid_config(tmp_path):
    # Create a temporary valid config file
    config_content = """
    domains:
      - api.example.com
    chunk_parsers:
      reasoning:
        - gemini
        - google
      think_tag: minimax
      default: reasoning_content
    backends:
      groups: []
      apis:
        - name: "Test API"
          endpoint: "https://api.test.com"
          stream: true
          custom_model_id: "test-model"
          target_model_id: "real-test-model"
    server:
      domains:
        - api.example.com
      port: 8443
      debug: true
    sse_coalescing:
      enabled: true
      window_ms: 50
      max_buffer_length: 512
      processing_delay_ms: 1000
    """
    config_file = tmp_path / "config.yml"
    config_file.write_text(config_content, encoding="utf-8")

    loader = ConfigLoader(str(config_file))
    config = loader.load()

    assert isinstance(config, AppConfig)
    assert config.server.port == 8443
    assert config.server.debug is True
    assert len(config.backends.apis) == 1
    assert config.backends.apis[0].name == "Test API"
    assert config.chunk_parsers == {
        "reasoning": ["gemini", "google"],
        "think_tag": ["minimax"],
        "default": "reasoning_content",
    }
    assert config.sse_coalescing.enabled is True
    assert config.sse_coalescing.window_ms == 50
    assert config.sse_coalescing.max_buffer_length == 512
    assert config.sse_coalescing.processing_delay_ms == 1000


def test_load_legacy_chunk_parsers_config_is_rejected(tmp_path):
    config_content = """
    chunk_parsers:
      gemini: reasoning
      google: reasoning
      minimax: think_tag
      default: reasoning_content
    server:
      domains:
        - api.example.com
      port: 443
      debug: false
    """
    config_file = tmp_path / "config_legacy_chunk_parsers.yml"
    config_file.write_text(config_content, encoding="utf-8")

    loader = ConfigLoader(str(config_file))
    with pytest.raises(ValueError, match="parser -> keywords"):
        loader.load()


def test_load_config_uses_default_sse_coalescing(tmp_path):
    config_content = """
    server:
      domains:
        - api.example.com
      port: 443
      debug: false
    """
    config_file = tmp_path / "config_default_sse.yml"
    config_file.write_text(config_content, encoding="utf-8")

    loader = ConfigLoader(str(config_file))
    config = loader.load()

    assert config.sse_coalescing.enabled is False
    assert config.sse_coalescing.window_ms == 20
    assert config.sse_coalescing.max_buffer_length == 256
    assert config.sse_coalescing.processing_delay_ms is None

def test_load_missing_file():
    loader = ConfigLoader("non_existent_config.yml")
    with pytest.raises(FileNotFoundError):
        loader.load()

def test_load_empty_file(tmp_path):
    config_file = tmp_path / "empty_config.yml"
    config_file.write_text("", encoding="utf-8")
    
    loader = ConfigLoader(str(config_file))
    with pytest.raises(ValueError, match="配置文件为空"):
        loader.load()

def test_load_invalid_cert_files(tmp_path):
    config_content = """
    server:
      domains: ["api.example.com"]
      port: 443
      debug: false
      cert_file: "/non/existent/cert.crt"
      key_file: "/non/existent/key.key"
    """
    config_file = tmp_path / "config_cert.yml"
    config_file.write_text(config_content, encoding="utf-8")
    
    loader = ConfigLoader(str(config_file))
    with pytest.raises(FileNotFoundError, match="证书文件不存在"):
        loader.load()
