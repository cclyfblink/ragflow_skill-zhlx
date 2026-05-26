#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from typing import Any

from common import (
    ConfigError,
    DataError,
    ScriptError,
    add_runtime_config_arguments,
    configure_stdio_utf8,
    error_payload,
    format_json,
    resolve_runtime_config,
    success_payload,
)
from datasets import find_dataset, list_datasets
from list_documents import _fetch_page, _normalize_document
from read_document import read_document

SCAN_PAGE_SIZE = 100


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按知识库名称和文档名称读取 RAGFlow 已解析文本。")
    parser.add_argument("document_name", help="文档名称关键词")
    parser.add_argument("--dataset", "--dataset-name", dest="dataset", help="知识库 ID、完整名称或名称关键词")
    parser.add_argument("--format", choices=("text", "markdown", "json"), default="markdown", help="合并内容格式，默认 markdown")
    parser.add_argument("--max-chars", type=int, default=80000, help="最大返回字符数，默认 80000")
    parser.add_argument("--page-size", type=int, default=100, help="读取 chunk 时的页大小，默认 100")
    parser.add_argument("--include-chunks", action="store_true", help="在 JSON 中返回每个 chunk 的完整内容")
    parser.add_argument("--output", help="可选：把合并后的内容写入指定文件路径")
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")
    add_runtime_config_arguments(parser)
    return parser.parse_args(argv)


def _match_documents(
    *,
    base_url: str,
    api_key: str,
    dataset_id: str,
    dataset_name: str,
    document_name: str,
) -> list[dict[str, Any]]:
    keyword = document_name.strip().lower()
    if not keyword:
        raise ConfigError("文档名称关键词不能为空。")

    matches: list[dict[str, Any]] = []
    page = 1
    total: int | None = None
    while True:
        page_payload = _fetch_page(
            base_url=base_url,
            api_key=api_key,
            dataset_id=dataset_id,
            page=page,
            page_size=SCAN_PAGE_SIZE,
            orderby="create_time",
            desc=True,
        )
        docs = page_payload["docs"]
        total = page_payload["total"] if total is None else total
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if keyword in str(doc.get("name") or "").lower() or keyword in str(doc.get("location") or "").lower():
                matches.append({**_normalize_document(doc), "dataset_name": dataset_name})
        if page * SCAN_PAGE_SIZE >= total or not docs:
            break
        page += 1
    return matches


def _resolve_datasets(dataset_query: str | None, *, base_url: str, api_key: str) -> list[dict[str, Any]]:
    payload = list_datasets(base_url=base_url, api_key=api_key)
    datasets = payload["datasets"]
    if dataset_query:
        return [find_dataset(datasets, dataset_query)]
    return datasets


def read_by_name(args: argparse.Namespace, *, base_url: str, api_key: str) -> dict[str, Any]:
    datasets = _resolve_datasets(args.dataset, base_url=base_url, api_key=api_key)
    matches: list[dict[str, Any]] = []
    for dataset in datasets:
        dataset_id = str(dataset.get("id") or "").strip()
        if not dataset_id:
            continue
        matches.extend(
            _match_documents(
                base_url=base_url,
                api_key=api_key,
                dataset_id=dataset_id,
                dataset_name=str(dataset.get("name") or "未命名知识库"),
                document_name=args.document_name,
            )
        )

    if not matches:
        raise DataError(f"当前可访问知识库未找到名称包含“{args.document_name}”的文档。")
    if len(matches) > 1:
        return success_payload(
            {
                "document_name_query": args.document_name,
                "matched_count": len(matches),
                "matches": matches,
                "read": None,
                "note": "匹配到多个文档。请用更精确的文档名称，或改用 read_document.py 指定 dataset_id 和 document_id。",
            }
        )

    document = matches[0]
    dataset_id = str(document.get("dataset_id") or "").strip()
    document_id = str(document.get("id") or "").strip()
    if not dataset_id or not document_id:
        raise DataError("匹配文档缺少 dataset_id 或 document_id，无法读取。")

    payload = read_document(
        dataset_id,
        document_id,
        content_format=args.format,
        max_chars=args.max_chars,
        page_size=args.page_size,
        include_chunks=args.include_chunks,
        output=args.output,
        base_url=base_url,
        api_key=api_key,
    )
    return success_payload(
        {
            "document_name_query": args.document_name,
            "matched_count": 1,
            "matches": matches,
            "read": payload,
        }
    )


def _format_text(payload: dict[str, Any]) -> str:
    if payload.get("read"):
        read_payload = payload["read"]
        return str(read_payload.get("content") or "")
    lines = [f"匹配到 {payload.get('matched_count')} 个文档："]
    for item in payload.get("matches", []):
        lines.append(f"- {item.get('dataset_name')} / {item.get('name')}（{item.get('id')}）")
    if payload.get("note"):
        lines.append(str(payload["note"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _parse_args(argv)

    try:
        base_url, api_key = resolve_runtime_config(args)
        payload = read_by_name(args, base_url=base_url, api_key=api_key)
        print(format_json(payload) if args.json_output else _format_text(payload))
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
