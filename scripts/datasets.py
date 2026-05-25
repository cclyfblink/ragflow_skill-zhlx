#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from typing import Any

from common import (
    DataError,
    ScriptError,
    add_runtime_config_arguments,
    configure_stdio_utf8,
    current_timestamp,
    ensure_success,
    format_json,
    request_json,
    resolve_runtime_config,
)


def _build_global_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")
    add_runtime_config_arguments(parser)
    return parser


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    global_parser = _build_global_parser()
    parser = argparse.ArgumentParser(
        description="只读查看当前账号可访问的 RAGFlow 知识库。",
        parents=[global_parser],
    )
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="列出可访问知识库", parents=[global_parser])
    list_parser.set_defaults(command="list")

    info_parser = subparsers.add_parser("info", help="查看一个知识库", parents=[global_parser])
    info_parser.add_argument("dataset", help="知识库 ID 或名称关键词")
    info_parser.set_defaults(command="info")

    args = parser.parse_args(argv)
    if not args.command:
        args.command = "list"
    return args


def normalize_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": dataset.get("id"),
        "name": dataset.get("name"),
        "avatar": dataset.get("avatar"),
        "description": dataset.get("description"),
        "chunk_count": dataset.get("chunk_count"),
        "created_at": dataset.get("created_at"),
        "permission": dataset.get("permission"),
        "embedding_model": dataset.get("embedding_model") or dataset.get("embd_id"),
        "chunk_method": dataset.get("chunk_method") or dataset.get("parser_id"),
        "language": dataset.get("language"),
    }


def list_datasets(*, base_url: str, api_key: str) -> dict[str, Any]:
    payload = ensure_success(request_json(f"{base_url}/api/v1/datasets", api_key))
    datasets = payload.get("data")
    if not isinstance(datasets, list):
        raise DataError("知识库列表响应缺少 data 数组。")
    normalized = [normalize_dataset(dataset) for dataset in datasets if isinstance(dataset, dict)]
    return {
        "checked_at": current_timestamp(),
        "count": len(normalized),
        "datasets": normalized,
    }


def find_dataset(datasets: list[dict[str, Any]], query: str) -> dict[str, Any]:
    normalized_query = query.strip().lower()
    if not normalized_query:
        raise DataError("知识库 ID 或名称不能为空。")

    exact_matches = [
        dataset
        for dataset in datasets
        if str(dataset.get("id") or "").lower() == normalized_query
        or str(dataset.get("name") or "").lower() == normalized_query
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]

    fuzzy_matches = [
        dataset
        for dataset in datasets
        if normalized_query in str(dataset.get("name") or "").lower()
        or normalized_query in str(dataset.get("description") or "").lower()
    ]
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0]
    if len(fuzzy_matches) > 1:
        names = ", ".join(str(dataset.get("name") or dataset.get("id") or "未命名") for dataset in fuzzy_matches)
        raise DataError(f"匹配到多个知识库，请提供更精确的名称或 ID：{names}")

    raise DataError(f"当前账号可访问知识库中未找到：{query}")


def dataset_info(dataset_query: str, *, base_url: str, api_key: str) -> dict[str, Any]:
    payload = list_datasets(base_url=base_url, api_key=api_key)
    return {
        "checked_at": current_timestamp(),
        "dataset": find_dataset(payload["datasets"], dataset_query),
    }


def _format_list(payload: dict[str, Any]) -> str:
    lines = [
        f"检查时间：{payload['checked_at']}",
        f"可访问知识库数量：{payload['count']}",
    ]
    if not payload["datasets"]:
        lines.append("当前账号没有可访问的知识库。")
        return "\n".join(lines)

    for dataset in payload["datasets"]:
        lines.extend(
            [
                "",
                f"- {dataset.get('name') or '未命名知识库'}",
                f"  ID：{dataset.get('id') or '未知'}",
                f"  切片数：{dataset.get('chunk_count') if dataset.get('chunk_count') is not None else '未知'}",
                f"  权限：{dataset.get('permission') or '未知'}",
                f"  描述：{dataset.get('description') or '无'}",
            ]
        )
    return "\n".join(lines)


def _format_info(payload: dict[str, Any]) -> str:
    dataset = payload["dataset"]
    return "\n".join(
        [
            f"检查时间：{payload['checked_at']}",
            f"名称：{dataset.get('name') or '未命名知识库'}",
            f"ID：{dataset.get('id') or '未知'}",
            f"描述：{dataset.get('description') or '无'}",
            f"切片数：{dataset.get('chunk_count') if dataset.get('chunk_count') is not None else '未知'}",
            f"权限：{dataset.get('permission') or '未知'}",
            f"嵌入模型：{dataset.get('embedding_model') or '未知'}",
            f"切片方式：{dataset.get('chunk_method') or '未知'}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _parse_args(argv)

    try:
        base_url, api_key = resolve_runtime_config(args)

        if args.command == "list":
            payload = list_datasets(base_url=base_url, api_key=api_key)
            print(format_json(payload) if args.json_output else _format_list(payload))
            return 0

        if args.command == "info":
            payload = dataset_info(args.dataset, base_url=base_url, api_key=api_key)
            print(format_json(payload) if args.json_output else _format_info(payload))
            return 0

        raise DataError(f"不支持的命令：{args.command}")
    except ScriptError as exc:
        if args.json_output:
            print(format_json({"checked_at": current_timestamp(), "error": str(exc)}))
        else:
            print(f"错误：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
