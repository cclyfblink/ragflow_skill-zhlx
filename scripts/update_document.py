#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import urllib.parse
from pathlib import Path
from typing import Any

from common import (
    ConfigError,
    DataError,
    ScriptError,
    add_runtime_config_arguments,
    configure_stdio_utf8,
    error_payload,
    ensure_success,
    format_json,
    request_json,
    resolve_runtime_config,
    success_payload,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="更新 RAGFlow 中单个文档的名称或解析配置。")
    parser.add_argument("dataset_id", help="知识库 ID")
    parser.add_argument("document_id", help="文档 ID")
    parser.add_argument("--name", help="新的文档名称")
    parser.add_argument("--chunk-method", help="新的切片方式")
    parser.add_argument("--parser-config", help="解析配置 JSON 对象，或 @path/to/file.json")
    parser.add_argument("--meta-fields", help="元数据 JSON 对象，或 @path/to/file.json")
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")
    add_runtime_config_arguments(parser)
    return parser.parse_args(argv)


def _load_json_object(raw_value: str, option_name: str) -> dict[str, Any]:
    value = raw_value
    if raw_value.startswith("@"):
        path = Path(raw_value[1:]).expanduser()
        try:
            value = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"读取 {option_name} 文件失败：{path}，{exc}") from exc

    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{option_name} 必须是合法 JSON：{exc.msg}") from exc

    if not isinstance(payload, dict):
        raise ConfigError(f"{option_name} 必须是 JSON 对象。")
    return payload


def _build_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if args.name is not None:
        payload["name"] = args.name
    if args.chunk_method is not None:
        payload["chunk_method"] = args.chunk_method
    if args.parser_config is not None:
        payload["parser_config"] = _load_json_object(args.parser_config, "--parser-config")
    if args.meta_fields is not None:
        payload["meta_fields"] = _load_json_object(args.meta_fields, "--meta-fields")

    if not payload:
        raise ConfigError("没有提供更新字段。请使用 --name、--chunk-method、--parser-config 或 --meta-fields。")
    return payload


def update_document(args: argparse.Namespace, *, base_url: str, api_key: str) -> dict[str, Any]:
    dataset_id = args.dataset_id.strip()
    document_id = args.document_id.strip()
    if not dataset_id:
        raise ConfigError("知识库 ID 不能为空。")
    if not document_id:
        raise ConfigError("文档 ID 不能为空。")

    payload = _build_payload(args)
    encoded_dataset_id = urllib.parse.quote(dataset_id, safe="")
    encoded_document_id = urllib.parse.quote(document_id, safe="")
    response = ensure_success(
        request_json(
            f"{base_url}/api/v1/datasets/{encoded_dataset_id}/documents/{encoded_document_id}",
            api_key,
            method="PUT",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )
    )
    data = response.get("data")
    if not isinstance(data, dict):
        raise DataError("更新响应缺少 data 对象。")
    return success_payload(
        {
            "updated_at": response.get("data", {}).get("update_time") or response.get("data", {}).get("updated_at"),
            "dataset_id": dataset_id,
            "document_id": document_id,
            "data": data,
            "api_response": response,
        }
    )


def _format_text(payload: dict[str, Any]) -> str:
    data = payload["data"]
    lines = [
        f"已更新文档：{data.get('name') or '未命名'}",
        f"文档 ID：{data.get('id') or '未知'}",
        f"知识库 ID：{data.get('dataset_id') or '未知'}",
    ]
    if data.get("run") is not None:
        lines.append(f"解析状态：{data.get('run')}")
    if data.get("chunk_method"):
        lines.append(f"切片方式：{data.get('chunk_method')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _parse_args(argv)

    try:
        base_url, api_key = resolve_runtime_config(args)
        payload = update_document(args, base_url=base_url, api_key=api_key)
        print(format_json(payload) if args.json_output else _format_text(payload))
        return 0
    except ScriptError as exc:
        payload = error_payload(exc, dataset_id=args.dataset_id, document_id=args.document_id)
        if args.json_output:
            print(format_json(payload))
        else:
            print(f"错误：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
