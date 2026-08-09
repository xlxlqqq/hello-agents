import json
import os
from datetime import datetime

from tools.base import BaseTool

# 用户配置持久化路径：data/config.json
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_CONFIG_FILE = os.path.join(_DATA_DIR, "config.json")

# 默认城市：首次运行未配置时使用
DEFAULT_CITY = "北京"
# 默认发送时间（24 小时制）和默认收件人
DEFAULT_SEND_TIME = "08:00"
DEFAULT_RECIPIENTS = ["xlxlqqq@163.com"]


def _default_config() -> dict:
    """生成一份默认配置（首次运行或配置损坏时使用）"""
    return {
        "city": DEFAULT_CITY,
        "updated_at": None,
        "send_time": DEFAULT_SEND_TIME,
        "recipients": list(DEFAULT_RECIPIENTS),
    }


def _ensure_data_dir():
    """确保 data 目录存在（首次运行时自动创建）"""
    os.makedirs(_DATA_DIR, exist_ok=True)


def _load_config() -> dict:
    """读取配置，文件不存在或损坏时返回默认配置"""
    _ensure_data_dir()
    if not os.path.exists(_CONFIG_FILE):
        return _default_config()
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        # 兼容旧配置：补齐缺失的邮件相关字段
        defaults = _default_config()
        for key, val in defaults.items():
            config.setdefault(key, val)
        return config
    except (json.JSONDecodeError, OSError):
        return _default_config()


def _save_config(config: dict):
    """写入配置"""
    _ensure_data_dir()
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


class GetLocationTool(BaseTool):
    name = "get_location"
    description = "查询当前配置的城市地址"

    def parameters(self):
        return {}

    def required(self):
        return []

    def run(self) -> str:
        try:
            config = _load_config()
            return config.get("city", DEFAULT_CITY)
        except Exception as e:
            return f"查询地址失败：{type(e).__name__}: {e}"


class SetLocationTool(BaseTool):
    name = "set_location"
    description = "修改当前城市地址，修改后立即生效，下次天气按新地址查询"

    def parameters(self):
        return {
            "city": {"type": "string", "description": "城市名称，如 上海、广州"}
        }

    def required(self):
        return ["city"]

    def run(self, city: str) -> str:
        try:
            config = _load_config()
            config["city"] = city
            config["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _save_config(config)
            return f"已将地址修改为：{city}，下次查询天气将使用此地址"
        except Exception as e:
            return f"修改地址失败：{type(e).__name__}: {e}"
