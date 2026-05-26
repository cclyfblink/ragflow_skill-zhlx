#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import ctypes
import os
import sys
import urllib.parse
from typing import Any

from common import (
    ConfigError,
    ScriptError,
    current_timestamp,
    error_payload,
    format_json,
    success_payload,
)
from datasets import list_datasets

RAGFLOW_API_URL_ENV = "RAGFLOW_API_URL"
RAGFLOW_API_KEY_ENV = "RAGFLOW_API_KEY"
WINDOWS_ENV_BROADCAST = 0x1A
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查或配置 RAGFlow 知识库连接。")
    subparsers = parser.add_subparsers(dest="command")

    check_parser = subparsers.add_parser("check", help="检查当前配置和连接状态")
    check_parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")

    config_parser = subparsers.add_parser("configure", help="写入用户级 RAGFlow 环境变量")
    config_parser.add_argument("--api-url", required=True, help="RAGFlow API 地址")
    config_parser.add_argument("--api-key", required=True, help="RAGFlow API Key")
    config_parser.add_argument("--force", action="store_true", help="覆盖已有用户级配置")
    config_parser.add_argument("--skip-test", action="store_true", help="只保存配置，不测试连接")
    config_parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")

    args = parser.parse_args(argv)
    if not args.command:
        args.command = "check"
        args.json_output = False
    return args


def _mask_secret(value: str) -> str:
    if not value:
        return "未配置"
    if len(value) <= 6:
        return "已配置（已隐藏）"
    return f"已配置（尾号 {value[-4:]}）"


def _validate_url(value: str) -> str:
    api_url = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(api_url)
    if not parsed.scheme or not parsed.netloc:
        raise ConfigError("RAGFlow API 地址无效，请使用完整地址，例如 http://127.0.0.1:9380。")
    return api_url


def _read_windows_user_env(name: str) -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _value_type = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return ""
    except OSError:
        return ""
    return str(value).strip()


def _write_windows_user_env(name: str, value: str) -> None:
    if sys.platform != "win32":
        raise ConfigError("自动写入用户级环境变量目前仅支持 Windows。")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
    os.environ[name] = value


def _broadcast_windows_env_change() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WINDOWS_ENV_BROADCAST,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            None,
        )
    except Exception:
        return


def _current_config() -> dict[str, str]:
    process_url = os.environ.get(RAGFLOW_API_URL_ENV, "").strip()
    process_key = os.environ.get(RAGFLOW_API_KEY_ENV, "").strip()
    user_url = _read_windows_user_env(RAGFLOW_API_URL_ENV)
    user_key = _read_windows_user_env(RAGFLOW_API_KEY_ENV)
    return {
        "api_url": process_url or user_url,
        "api_key": process_key or user_key,
        "process_api_url": process_url,
        "process_api_key": process_key,
        "user_api_url": user_url,
        "user_api_key": user_key,
    }


def _connection_payload(api_url: str, api_key: str) -> dict[str, Any]:
    datasets = list_datasets(base_url=api_url, api_key=api_key)
    return {
        "status": "成功",
        "dataset_count": datasets["count"],
        "datasets": [{"id": item.get("id"), "name": item.get("name")} for item in datasets["datasets"]],
    }


def _check() -> dict[str, Any]:
    config = _current_config()
    payload: dict[str, Any] = {
        "checked_at": current_timestamp(),
        "api_url": "已配置" if config["api_url"] else "未配置",
        "api_key": _mask_secret(config["api_key"]),
        "process_env": {
            "api_url": "已配置" if config["process_api_url"] else "未配置",
            "api_key": "已配置" if config["process_api_key"] else "未配置",
        },
        "user_env": {
            "api_url": "已配置" if config["user_api_url"] else "未配置",
            "api_key": "已配置" if config["user_api_key"] else "未配置",
        },
    }

    if not config["api_url"] or not config["api_key"]:
        payload["connection"] = {"status": "未测试", "message": "缺少 API 地址或 API Key。"}
        return success_payload(payload)

    try:
        payload["connection"] = _connection_payload(_validate_url(config["api_url"]), config["api_key"])
    except ScriptError as exc:
        payload["connection"] = {"status": "失败", "message": str(exc)}
    return success_payload(payload)


def _configure(args: argparse.Namespace) -> dict[str, Any]:
    api_url = _validate_url(args.api_url)
    api_key = args.api_key.strip()
    if not api_key:
        raise ConfigError("API Key 不能为空。")

    existing_url = _read_windows_user_env(RAGFLOW_API_URL_ENV)
    existing_key = _read_windows_user_env(RAGFLOW_API_KEY_ENV)
    if (existing_url or existing_key) and not args.force:
        raise ConfigError("用户级环境变量已存在。如需覆盖，请明确使用 --force。")

    _write_windows_user_env(RAGFLOW_API_URL_ENV, api_url)
    _write_windows_user_env(RAGFLOW_API_KEY_ENV, api_key)
    _broadcast_windows_env_change()

    payload: dict[str, Any] = {
        "configured_at": current_timestamp(),
        "api_url": "已写入用户级环境变量",
        "api_key": _mask_secret(api_key),
        "message": "配置已保存。新开的 Codex/终端会自动读取；当前脚本也已用新配置完成连接检查。",
    }

    if args.skip_test:
        payload["connection"] = {"status": "未测试", "message": "用户选择跳过连接测试。"}
        return success_payload(payload)

    try:
        payload["connection"] = _connection_payload(api_url, api_key)
    except ScriptError as exc:
        payload["connection"] = {"status": "失败", "message": str(exc)}
    return success_payload(payload)


def _format_check(payload: dict[str, Any]) -> str:
    connection = payload["connection"]
    lines = [
        f"检查时间：{payload['checked_at']}",
        f"{RAGFLOW_API_URL_ENV}：{payload['api_url']}",
        f"{RAGFLOW_API_KEY_ENV}：{payload['api_key']}",
        f"连接状态：{connection.get('status')}",
    ]
    if connection.get("message"):
        lines.append(f"说明：{connection['message']}")
    if connection.get("dataset_count") is not None:
        lines.append(f"可访问知识库：{connection['dataset_count']} 个")
    return "\n".join(lines)


def _format_configure(payload: dict[str, Any]) -> str:
    connection = payload["connection"]
    lines = [
        f"配置时间：{payload['configured_at']}",
        f"{RAGFLOW_API_URL_ENV}：{payload['api_url']}",
        f"{RAGFLOW_API_KEY_ENV}：{payload['api_key']}",
        payload["message"],
        f"连接状态：{connection.get('status')}",
    ]
    if connection.get("message"):
        lines.append(f"说明：{connection['message']}")
    if connection.get("dataset_count") is not None:
        lines.append(f"可访问知识库：{connection['dataset_count']} 个")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "configure":
            payload = _configure(args)
            print(format_json(payload) if args.json_output else _format_configure(payload))
            return 0

        payload = _check()
        print(format_json(payload) if args.json_output else _format_check(payload))
        return 0
    except ScriptError as exc:
        payload = error_payload(exc)
        if getattr(args, "json_output", False):
            print(format_json(payload))
        else:
            print(f"错误：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
