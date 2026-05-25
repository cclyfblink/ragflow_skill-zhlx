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
    error_payload,
    format_json,
    request_json,
    resolve_runtime_config,
    success_payload,
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

    metadata_parser = subparsers.add_parser("metadata", help="抽样查看知识库可见元数据字段", parents=[global_parser])
    metadata_parser.add_argument("dataset", help="知识库 ID 或名称关键词")
    metadata_parser.add_argument("--sample-size", type=int, default=200, help="抽样文档数，默认 200")
    metadata_parser.set_defaults(command="metadata")

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


def _documents_url(base_url: str, dataset_id: str, page: int, page_size: int) -> str:
    import urllib.parse

    encoded_dataset_id = urllib.parse.quote(dataset_id, safe="")
    query = urllib.parse.urlencode({"page": page, "page_size": page_size})
    return f"{base_url}/api/v1/datasets/{encoded_dataset_id}/documents?{query}"


def _compact_example(value: Any) -> Any:
    if isinstance(value, str):
        compact = " ".join(value.split())
        return compact if len(compact) <= 160 else compact[:157] + "..."
    if isinstance(value, dict):
        return {
            key: _compact_example(item)
            for key, item in list(value.items())[:8]
        }
    if isinstance(value, list):
        return [_compact_example(item) for item in value[:5]]
    return value


def metadata_summary(dataset_query: str, *, sample_size: int, base_url: str, api_key: str) -> dict[str, Any]:
    if sample_size <= 0:
        raise DataError("--sample-size 必须大于 0。")

    datasets_payload = list_datasets(base_url=base_url, api_key=api_key)
    dataset = find_dataset(datasets_payload["datasets"], dataset_query)
    dataset_id = str(dataset.get("id") or "").strip()
    if not dataset_id:
        raise DataError("匹配到的知识库缺少 ID。")

    docs: list[dict[str, Any]] = []
    page = 1
    page_size = min(100, sample_size)
    total: int | None = None
    while len(docs) < sample_size:
        payload = ensure_success(request_json(_documents_url(base_url, dataset_id, page, page_size), api_key))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise DataError("文档列表响应缺少 data 对象。")
        page_docs = data.get("docs")
        total = data.get("total") if total is None else total
        if not isinstance(page_docs, list):
            raise DataError("文档列表响应缺少 data.docs 数组。")
        if not isinstance(total, int):
            raise DataError("文档列表响应缺少 data.total。")
        docs.extend(doc for doc in page_docs if isinstance(doc, dict))
        if len(docs) >= total or not page_docs:
            break
        page += 1

    field_counts: dict[str, int] = {}
    metadata_field_counts: dict[str, int] = {}
    examples: dict[str, list[Any]] = {}
    metadata_examples: dict[str, list[Any]] = {}

    for doc in docs[:sample_size]:
        for key, value in doc.items():
            field_counts[key] = field_counts.get(key, 0) + 1
            if value not in (None, ""):
                examples.setdefault(key, [])
                if len(examples[key]) < 3:
                    examples[key].append(_compact_example(value))

        metadata = doc.get("metadata") or doc.get("meta_fields")
        if isinstance(metadata, dict):
            for key, value in metadata.items():
                metadata_field_counts[key] = metadata_field_counts.get(key, 0) + 1
                if value not in (None, ""):
                    metadata_examples.setdefault(key, [])
                    if len(metadata_examples[key]) < 3:
                        metadata_examples[key].append(_compact_example(value))

    return success_payload(
        {
            "checked_at": current_timestamp(),
            "dataset": dataset,
            "sampled_documents": min(len(docs), sample_size),
            "total_documents": total,
            "document_fields": {
                key: {"count": count, "examples": examples.get(key, [])}
                for key, count in sorted(field_counts.items())
            },
            "metadata_fields": {
                key: {"count": count, "examples": metadata_examples.get(key, [])}
                for key, count in sorted(metadata_field_counts.items())
            },
            "note": "metadata_summary 基于当前可见文档字段抽样推断，不代表 RAGFlow 服务端 schema。",
        }
    )


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


def _format_metadata(payload: dict[str, Any]) -> str:
    dataset = payload["dataset"]
    lines = [
        f"检查时间：{payload['checked_at']}",
        f"知识库：{dataset.get('name') or '未命名知识库'}",
        f"抽样文档数：{payload['sampled_documents']} / 总数：{payload.get('total_documents')}",
        "文档字段：" + "，".join(payload["document_fields"].keys()),
    ]
    if payload["metadata_fields"]:
        lines.append("元数据字段：" + "，".join(payload["metadata_fields"].keys()))
    else:
        lines.append("元数据字段：当前抽样文档未发现 metadata/meta_fields。")
    lines.append(payload["note"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _parse_args(argv)

    try:
        base_url, api_key = resolve_runtime_config(args)

        if args.command == "list":
            payload = success_payload(list_datasets(base_url=base_url, api_key=api_key))
            print(format_json(payload) if args.json_output else _format_list(payload))
            return 0

        if args.command == "info":
            payload = success_payload(dataset_info(args.dataset, base_url=base_url, api_key=api_key))
            print(format_json(payload) if args.json_output else _format_info(payload))
            return 0

        if args.command == "metadata":
            payload = metadata_summary(args.dataset, sample_size=args.sample_size, base_url=base_url, api_key=api_key)
            print(format_json(payload) if args.json_output else _format_metadata(payload))
            return 0

        raise DataError(f"不支持的命令：{args.command}")
    except ScriptError as exc:
        if args.json_output:
            print(format_json(error_payload(exc)))
        else:
            print(f"错误：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
