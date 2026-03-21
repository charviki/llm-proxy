"""配置加载器 - 负责加载和验证配置文件"""
from pathlib import Path
import yaml
from .models import AppConfig


class ConfigLoader:
    """配置加载器"""

    def __init__(self, config_path: str = "config.yml"):
        self.config_path = Path(config_path)

    def load(self) -> AppConfig:
        """加载并验证配置"""
        self._check_file_exists()
        config_dict = self._load_yaml()
        config = self._validate_config(config_dict)
        self._validate_business_rules(config)
        return config

    def _check_file_exists(self) -> None:
        """检查配置文件是否存在"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")

    def _load_yaml(self) -> dict:
        """加载 YAML 配置"""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            config_dict = yaml.safe_load(f)

        if not config_dict:
            raise ValueError("配置文件为空")

        return config_dict

    def _validate_config(self, config_dict: dict) -> AppConfig:
        """使用 Pydantic 验证配置"""
        return AppConfig.model_validate(config_dict)

    def _validate_business_rules(self, config: AppConfig) -> None:
        """验证业务规则"""
        self._validate_cert_files(config)

    def _validate_cert_files(self, config: AppConfig) -> None:
        """验证证书文件是否存在"""
        if not config.server.cert_file or not config.server.key_file:
            return
        cert_path = Path(config.server.cert_file)
        key_path = Path(config.server.key_file)

        if not cert_path.exists():
            raise FileNotFoundError(f"证书文件不存在: {cert_path}")
        if not key_path.exists():
            raise FileNotFoundError(f"私钥文件不存在: {key_path}")
