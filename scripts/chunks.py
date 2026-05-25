#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import sys
import urllib.parse
from typing import Any

from common import (
    ConfigError,
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

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 100


def _build_global_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")
    add_runtime_config_arguments(parser)
    return parser


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    global_parser = _build_global_parser()
    parser = argparse.ArgumentParser(description="只读查看 RAGFlow 文档 chunk。", parents=[global_parser])
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出文档 chunk", parents=[global_parser])
    list_parser.add_argument("dataset_id", help="知识库 ID")
    list_parser.add_argument("document_id", help="文档 ID")
    list_parser.add_argument("--page", type=int, default=DEFAULT_PAGE, help=f"页码，默认 {DEFAULT_PAGE}")
    list_parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help=f"每页数量，默认 {DEFAULT_PAGE_SIZE}")

    get_parser = subparsers.add_parser("get", help="读取单个 chunk", parents=[global_parser])
    get_parser.add_argument("dataset_id", help="知识库 ID")
    get_parser.add_argument("document_id", help="文档 ID")
    get_parser.add_argument("chunk_id", help="chunk ID")

    expand_parser = subparsers.add_parser("expand", help="读取目标 chunk 前后相邻上下文", parents=[global_parser])
    expand_parser.add_argument("dataset_id", help="知识库 ID")
    expand_parser.add_argument("document_id", help="文档 ID")
    expand_parser.add_argument("chunk_id", help="chunk ID")
    expand_parser.add_argument("--before", type=int, default=2, help="向前展开 chunk 数，默认 2")
    expand_parser.add_argument("--after", type=int, default=2, help="向后展开 chunk 数，默认 2")
    expand_parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help=f"扫描页大小，默认 {DEFAULT_PAGE_SIZE}")
    return parser.parse_args(argv)


def _validate_ids(dataset_id: str, document_id: str) -> tuple[str, str]:
    normalized_dataset_id = dataset_id.strip()
    normalized_document_id = document_id.strip()
    if not normalized_dataset_id:
        raise ConfigError("知识库 ID 不能为空。")
    if not normalized_document_id:
        raise ConfigError("文档 ID 不能为空。")
    return normalized_dataset_id, normalized_document_id


def _chunks_url(base_url: str, dataset_id: str, document_id: str, query: dict[str, Any]) -> str:
    encoded_dataset_id = urllib.parse.quote(dataset_id, safe="")
    encoded_document_id = urllib.parse.quote(document_id, safe="")
    encoded_query = urllib.parse.urlencode(query)
    return f"{base_url}/api/v1/datasets/{encoded_dataset_id}/documents/{encoded_document_id}/chunks?{encoded_query}"


def _normalize_chunk(chunk: dict[str, Any], index: int | None = None) -> dict[str, Any]:
    return {
        "index": index,
        "chunk_id": chunk.get("id") or chunk.get("chunk_id"),
        "content": chunk.get("content") or chunk.get("content_with_weight") or "",
        "important_keywords": chunk.get("important_keywords"),
        "questions": chunk.get("questions"),
        "available": chunk.get("available"),
        "positions": chunk.get("positions"),
        "metadata": chunk.get("metadata") or chunk.get("meta_fields"),
    }


def _extract_chunk_page(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    payload = ensure_success(payload)
    data = payload.get("data")
    if isinstance(data, dict):
        chunks = data.get("chunks")
        total = data.get("total")
        if chunks is None:
            chunks = data.get("docs") or data.get("items")
        if total is None:
            total = len(chunks) if isinstance(chunks, list) else 0
    elif isinstance(data, list):
        chunks = data
        total = len(data)
    else:
        raise DataError("chunk 列表响应 data 应为对象或数组。")

    if not isinstance(chunks, list):
        raise DataError("chunk 列表响应缺少 chunks 数组。")
    if not isinstance(total, int):
        total = len(chunks)
    return [chunk for chunk in chunks if isinstance(chunk, dict)], total


def list_chunks(
    dataset_id: str,
    document_id: str,
    *,
    page: int,
    page_size: int,
    base_url: str,
    api_key: str,
) -> dict[str, Any]:
    dataset_id, document_id = _validate_ids(dataset_id, document_id)
    if page <= 0:
        raise ConfigError("--page 必须大于 0。")
    if page_size <= 0:
        raise ConfigError("--page-size 必须大于 0。")

    chunks, total = _extract_chunk_page(
        request_json(
            _chunks_url(base_url, dataset_id, document_id, {"page": page, "page_size": page_size}),
            api_key,
        )
    )
    start_index = (page - 1) * page_size
    return success_payload(
        {
            "checked_at": current_timestamp(),
            "dataset_id": dataset_id,
            "document_id": document_id,
            "page": page,
            "page_size": page_size,
            "total": total,
            "count": len(chunks),
            "chunks": [_normalize_chunk(chunk, start_index + index) for index, chunk in enumerate(chunks)],
        }
    )


def _fetch_all_chunks(dataset_id: str, document_id: str, *, page_size: int, base_url: str, api_key: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    page = 1
    total: int | None = None
    while True:
        page_chunks, page_total = _extract_chunk_page(
            request_json(
                _chunks_url(base_url, dataset_id, document_id, {"page": page, "page_size": page_size}),
                api_key,
            )
        )
        total = page_total if total is None else total
        chunks.extend(page_chunks)
        if len(chunks) >= total or not page_chunks:
            return chunks[:total]
        page += 1


def _find_chunk_index(chunks: list[dict[str, Any]], chunk_id: str) -> int:
    for index, chunk in enumerate(chunks):
        if str(chunk.get("id") or chunk.get("chunk_id") or "") == chunk_id:
            return index
    raise DataError(f"文档中未找到 chunk：{chunk_id}")


def get_chunk(dataset_id: str, document_id: str, chunk_id: str, *, base_url: str, api_key: str) -> dict[str, Any]:
    dataset_id, document_id = _validate_ids(dataset_id, document_id)
    normalized_chunk_id = chunk_id.strip()
    if not normalized_chunk_id:
        raise ConfigError("chunk ID 不能为空。")

    chunks = _fetch_all_chunks(dataset_id, document_id, page_size=DEFAULT_PAGE_SIZE, base_url=base_url, api_key=api_key)
    index = _find_chunk_index(chunks, normalized_chunk_id)
    return success_payload(
        {
            "checked_at": current_timestamp(),
            "dataset_id": dataset_id,
            "document_id": document_id,
            "chunk_id": normalized_chunk_id,
            "chunk": _normalize_chunk(chunks[index], index),
        }
    )


def expand_chunk(
    dataset_id: str,
    document_id: str,
    chunk_id: str,
    *,
    before: int,
    after: int,
    page_size: int,
    base_url: str,
    api_key: str,
) -> dict[str, Any]:
    dataset_id, document_id = _validate_ids(dataset_id, document_id)
    normalized_chunk_id = chunk_id.strip()
    if not normalized_chunk_id:
        raise ConfigError("chunk ID 不能为空。")
    if before < 0 or after < 0:
        raise ConfigError("--before 和 --after 不能小于 0。")
    if page_size <= 0:
        raise ConfigError("--page-size 必须大于 0。")

    chunks = _fetch_all_chunks(dataset_id, document_id, page_size=page_size, base_url=base_url, api_key=api_key)
    target_index = _find_chunk_index(chunks, normalized_chunk_id)
    start = max(0, target_index - before)
    end = min(len(chunks), target_index + after + 1)
    return success_payload(
        {
            "checked_at": current_timestamp(),
            "dataset_id": dataset_id,
            "document_id": document_id,
            "chunk_id": normalized_chunk_id,
            "target_index": target_index,
            "range": {"start": start, "end": end - 1, "before": before, "after": after},
            "count": end - start,
            "chunks": [_normalize_chunk(chunk, start + index) for index, chunk in enumerate(chunks[start:end])],
            "note": "相邻 chunk 基于 RAGFlow 列表顺序展开；如需严谨页序，请同时参考 positions。",
        }
    )


def _format_text(payload: dict[str, Any]) -> str:
    lines = [
        f"知识库 ID：{payload['dataset_id']}",
        f"文档 ID：{payload['document_id']}",
    ]
    if "chunk_id" in payload:
        lines.append(f"chunk ID：{payload['chunk_id']}")
    if "total" in payload:
        lines.append(f"chunk 数：{payload['count']} / 总数：{payload['total']}")
    else:
        lines.append(f"chunk 数：{payload.get('count', 1)}")

    chunks = payload.get("chunks")
    if not chunks and payload.get("chunk"):
        chunks = [payload["chunk"]]
    for chunk in chunks or []:
        lines.extend(
            [
                "",
                f"[{chunk.get('index')}] {chunk.get('chunk_id') or '未知'}",
                str(chunk.get("content") or "")[:500],
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _parse_args(argv)

    try:
        base_url, api_key = resolve_runtime_config(args)
        if args.command == "list":
            payload = list_chunks(
                args.dataset_id,
                args.document_id,
                page=args.page,
                page_size=args.page_size,
                base_url=base_url,
                api_key=api_key,
            )
        elif args.command == "get":
            payload = get_chunk(args.dataset_id, args.document_id, args.chunk_id, base_url=base_url, api_key=api_key)
        elif args.command == "expand":
            payload = expand_chunk(
                args.dataset_id,
                args.document_id,
                args.chunk_id,
                before=args.before,
                after=args.after,
                page_size=args.page_size,
                base_url=base_url,
                api_key=api_key,
            )
        else:
            raise ConfigError(f"不支持的命令：{args.command}")

        print(format_json(payload) if args.json_output else _format_text(payload))
        return 0
    except ScriptError as exc:
        payload = error_payload(exc)
        if getattr(args, "json_output", False):
            print(format_json(payload))
        else:
            print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
