"""Compatibility endpoints now expose upstream service status, not a second game backend."""
from furina.services.gscore import snapshot, ensure_running


def save_settings(body: dict) -> dict:
    raise ValueError("旧版游戏设置已停用，请在游戏管理中的 GsCore 官方控制台配置。")


def save_account_settings(qq: str, body: dict) -> dict:
    raise ValueError("账号由 GsCore 统一管理，此接口不再修改账号或接收凭证。")
