"""Public connection suggestions. Nothing is selected for a fresh installation."""
from urllib.parse import urlsplit
import ipaddress

PROVIDERS = [
    {"id": "custom", "name": "自定义 OpenAI 兼容接口", "endpoint": "", "models": [], "requires_key": True, "note": "填写你信任的服务地址、模型名称和个人密钥。"},
    {"id": "deepseek", "name": "DeepSeek", "endpoint": "https://api.deepseek.com/chat/completions", "models": ["deepseek-flash", "deepseek-v4-pro"], "requires_key": True, "note": "模型建议参考官方文档；请选择账号可用的模型。"},
    {"id": "openai", "name": "OpenAI", "endpoint": "https://api.openai.com/v1/chat/completions", "models": [], "requires_key": True, "note": "手动填写支持 Chat Completions 的模型名称。"},
    {"id": "dashscope", "name": "阿里云百炼 · 千问", "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "models": ["qwen-plus", "qwen3.8-max"], "requires_key": True, "note": "请按所在地域与业务空间调整地址，确保 API Key 与地域一致。"},
    {"id": "tju", "name": "天津大学大模型平台", "endpoint": "https://ai.tju.edu.cn/api/v3/chat/completions", "models": ["tju-llm"], "requires_key": True, "note": "仅限有该平台使用权限的用户自行配置。"},
    {"id": "ollama", "name": "Ollama · 本机模型", "endpoint": "http://localhost:11434/v1/chat/completions", "models": [], "requires_key": False, "note": "先在本机安装并拉取模型，再填写已安装的模型名。回环地址无需密钥。"},
]


def requires_key(settings: dict) -> bool:
    if settings.get("provider") != "ollama":
        return True
    host = urlsplit(settings.get("endpoint", "")).hostname or ""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower().rstrip(".") == "localhost"
    return not loopback
