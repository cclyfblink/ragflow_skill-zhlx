#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import urllib.parse

from common import (
    ConfigError,
    ScriptError,
    add_runtime_config_arguments,
    configure_stdio_utf8,
    current_timestamp,
    ensure_success,
    format_json,
    request_json,
    resolve_runtime_config,
    serialize_script_error,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="请求 RAGFlow 解析已上传文档。")
    parser.add_argument("dataset_id", help="知识库 ID")
    parser.add_argument("document_ids", nargs="+", help="要解析的文档 ID")
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")
    add_runtime_config_arguments(parser)
    return parser.parse_args(argv)


def start_parse(dataset_id: str, document_ids: list[str], *, base_url: str, api_key: str) -> dict[str, object]:
    normalized_dataset_id = dataset_id.strip()
    normalized_doc_ids = [doc_id.strip() for doc_id in document_ids if doc_id.strip()]
    if not normalized_dataset_id:
        raise ConfigError("知识库 ID 不能为空。")
    if not normalized_doc_ids:
        raise ConfigError("至少需要一个文档 ID。")

    encoded_dataset_id = urllib.parse.quote(normalized_dataset_id, safe="")
    response = ensure_success(
        request_json(
            f"{base_url}/api/v1/datasets/{encoded_dataset_id}/chunks",
            api_key,
            method="POST",
            body=json.dumps({"document_ids": normalized_doc_ids}).encode("utf-8"),
            content_type="application/json",
        )
    )
    return {
        "dataset_id": normalized_dataset_id,
        "document_ids": normalized_doc_ids,
        "parse_requested_at": current_timestamp(),
        "api_response": response,
    }


def _format_text(payload: dict[str, object]) -> str:
    lines = [
        f"知识库 ID：{payload['dataset_id']}",
        f"请求解析时间：{payload['parse_requested_at']}",
        f"文档数：{len(payload['document_ids'])}",
    ]
    api_response = payload.get("api_response")
    if isinstance(api_response, dict):
        message = api_response.get("message")
        if isinstance(message, str) and message.strip():
            lines.append(f"API 消息：{message.strip()}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _parse_args(argv)

    try:
        base_url, api_key = resolve_runtime_config(args)
        payload = start_parse(args.dataset_id, args.document_ids, base_url=base_url, api_key=api_key)
        print(format_json(payload) if args.json_output else _format_text(payload))
        return 0
    except ScriptError as exc:
        if args.json_output:
            print(
                format_json(
                    {
                        "dataset_id": args.dataset_id,
                        "document_ids": args.document_ids,
                        "parse_requested_at": current_timestamp(),
                        "error": str(exc),
                        "error_detail": serialize_script_error(exc),
                    }
                )
            )
        else:
            print(f"错误：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
