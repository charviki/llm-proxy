"""后端配置和模型管理器"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional
import time
import logging
import httpx

from config.models import BackendsConfig, GroupConfig, APIConfig


@dataclass
class Backend:
    """后端配置"""
    endpoint: str
    target_model_id: str
    api_key: Optional[str] = None
    stream: bool = True


class ModelsManager:
    """模型管理器 - 管理各后端的模型列表"""

    def __init__(self, backends: BackendsConfig, logger: logging.Logger):
        self.backends = backends
        self.logger = logger
        self.group_models: dict[str, list[str]] = {}

    async def load_models(self, client: httpx.AsyncClient) -> None:
        """加载所有后端的模型列表"""
        for group in self.backends.groups:
            if group.models_endpoint and group.models_file:
                await self._load_group_models(client, group)

    async def _load_group_models(self, client: httpx.AsyncClient, group: GroupConfig) -> None:
        """加载后端的模型列表"""
        models_file = Path(group.models_file)
        models_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            response = await client.get(
                f"{group.endpoint}{group.models_endpoint}",
                timeout=30.0
            )
            if response.status_code == 200:
                data = response.json()
                model_list = data.get("data", [])

                # 保存时加上 group 前缀
                model_ids = [group.model_prefix + m.get("id") for m in model_list if m.get("id")]

                save_data = {
                    "models": model_ids,
                    "updated_at": datetime.now().isoformat()
                }

                with open(models_file, 'w', encoding='utf-8') as f:
                    json.dump(save_data, f, ensure_ascii=False, indent=2)

                self.group_models[group.name] = model_ids
                self.logger.info(f"已更新 {group.name} 模型列表，共 {len(model_ids)} 个模型")
            else:
                self._load_from_file(group)
                self.logger.warning(f"获取 {group.name} 模型列表失败 (HTTP {response.status_code})，使用缓存")
        except Exception as e:
            self._load_from_file(group)
            self.logger.warning(f"获取 {group.name} 模型列表异常: {e}，使用缓存")

    def _load_from_file(self, group: GroupConfig) -> None:
        """从文件加载模型列表"""
        if not group.models_file:
            return

        models_file = Path(group.models_file)
        if models_file.exists():
            try:
                with open(models_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    model_ids = data.get("models", [])
                    self.group_models[group.name] = model_ids
                    self.logger.info(f"已从文件加载 {group.name} 模型列表，共 {len(model_ids)} 个模型")
            except Exception as e:
                self.logger.warning(f"读取 {group.name} 模型文件失败: {e}")
                self.group_models[group.name] = []
        else:
            self.group_models[group.name] = []

    def find_backend(self, model_id: str) -> Optional[Backend]:
        """查找后端配置"""
        # 先精确匹配 APIs
        for api in self.backends.apis:
            if api.custom_model_id == model_id:
                api_key = os.environ.get(api.api_key_env) if api.api_key_env else None
                return Backend(
                    endpoint=api.endpoint,
                    target_model_id=api.target_model_id,
                    api_key=api_key,
                    stream=api.stream
                )

        # 再前缀匹配 Groups
        for group in self.backends.groups:
            if model_id.startswith(group.model_prefix):
                prefix_len = len(group.model_prefix)
                target_model_id = model_id[prefix_len:] if prefix_len > 0 else model_id
                api_key = os.environ.get(group.api_key_env) if group.api_key_env else None
                return Backend(
                    endpoint=group.endpoint,
                    target_model_id=target_model_id,
                    api_key=api_key,
                    stream=group.stream
                )

        return None

    def get_all_models(self) -> dict:
        """获取所有模型（标准 OpenAI 格式）"""
        all_models = []

        # 添加精确匹配的 APIs
        for api in self.backends.apis:
            all_models.append({
                "id": api.custom_model_id,
                "object": "model",
                "created": int(time.time()),
                "owned_by": api.name
            })

        seen_ids = {m.get("id") for m in all_models}

        # 添加组中的模型（已带前缀）
        for group_name, model_ids in self.group_models.items():
            for model_id in model_ids:
                if model_id not in seen_ids:
                    all_models.append({
                        "id": model_id,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": group_name
                    })
                    seen_ids.add(model_id)

        return {
            "object": "list",
            "data": all_models
        }
