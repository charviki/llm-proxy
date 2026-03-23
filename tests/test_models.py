import pytest
import os
import json
from pathlib import Path
from proxy.models import ModelsManager, Backend
from config.models import BackendsConfig, GroupConfig, APIConfig
import logging

@pytest.fixture
def mock_logger():
    return logging.getLogger("test_logger")

@pytest.fixture
def backends_config():
    return BackendsConfig(
        groups=[
            GroupConfig(
                name="Test Group",
                model_prefix="tg/",
                endpoint="https://group.test.com",
                stream=True,
                models_endpoint="/v1/models",
                models_file="models/test_group.json",
                api_key_env="TEST_GROUP_KEY"
            )
        ],
        apis=[
            APIConfig(
                name="Test API",
                endpoint="https://api.test.com",
                stream=False,
                custom_model_id="my-custom-model",
                target_model_id="real-model",
                api_key_env="TEST_API_KEY"
            )
        ]
    )

def test_find_backend_api(backends_config, mock_logger, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "api_secret_123")
    
    manager = ModelsManager(backends_config, mock_logger)
    backend = manager.find_backend("my-custom-model")
    
    assert backend is not None
    assert backend.endpoint == "https://api.test.com"
    assert backend.target_model_id == "real-model"
    assert backend.api_key == "api_secret_123"
    assert backend.stream is False

def test_find_backend_group(backends_config, mock_logger, monkeypatch):
    monkeypatch.setenv("TEST_GROUP_KEY", "group_secret_456")
    
    manager = ModelsManager(backends_config, mock_logger)
    backend = manager.find_backend("tg/gpt-4")
    
    assert backend is not None
    assert backend.endpoint == "https://group.test.com"
    assert backend.target_model_id == "gpt-4"
    assert backend.api_key == "group_secret_456"
    assert backend.stream is True

def test_find_backend_not_found(backends_config, mock_logger):
    manager = ModelsManager(backends_config, mock_logger)
    backend = manager.find_backend("unknown-model")
    assert backend is None

@pytest.mark.asyncio
async def test_load_models_success(backends_config, mock_logger, tmp_path, monkeypatch):
    # Change models_file path to tmp_path
    backends_config.groups[0].models_file = str(tmp_path / "test_group.json")
    
    class MockResponse:
        status_code = 200
        def json(self):
            return {"data": [{"id": "gpt-4"}, {"id": "gpt-3.5"}]}
            
    class MockClient:
        async def get(self, url, headers=None, timeout=None):
            return MockResponse()

    manager = ModelsManager(backends_config, mock_logger)
    await manager.load_models(MockClient())
    
    # Check if file was created
    assert Path(backends_config.groups[0].models_file).exists()
    
    # Check loaded models
    assert manager.group_models["Test Group"] == ["tg/gpt-4", "tg/gpt-3.5"]
    
    # Check get_all_models
    all_models = manager.get_all_models()
    assert all_models["object"] == "list"
    
    model_ids = {m["id"] for m in all_models["data"]}
    assert "my-custom-model" in model_ids
    assert "tg/gpt-4" in model_ids
    assert "tg/gpt-3.5" in model_ids

@pytest.mark.asyncio
async def test_load_models_from_file_fallback(backends_config, mock_logger, tmp_path):
    # Prepare fallback file
    fallback_file = tmp_path / "fallback.json"
    fallback_file.write_text(json.dumps({"models": ["tg/fallback-model"]}), encoding="utf-8")
    backends_config.groups[0].models_file = str(fallback_file)
    
    class MockErrorClient:
        async def get(self, url, headers=None, timeout=None):
            raise Exception("Network Error")
            
    manager = ModelsManager(backends_config, mock_logger)
    await manager.load_models(MockErrorClient())
    
    # Check if loaded from file
    assert manager.group_models["Test Group"] == ["tg/fallback-model"]
