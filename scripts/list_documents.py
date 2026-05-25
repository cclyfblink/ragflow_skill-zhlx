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
    ensure_success,
    request_json,
    resolve_runtime_config,
)

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 10
DEFAULT_ORDERBY = "create_time"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读列出一个 RAGFlow 知识库中的文档。")
    parser.add_argument("dataset_id", help="知识库 ID")
    parser.add_argument("--page", type=int, default=DEFAULT_PAGE, help=f"页码，默认 {DEFAULT_PAGE}")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help=f"每页数量，默认 {DEFAULT_PAGE_SIZE}")
    parser.add_argument("--orderby", default=DEFAULT_ORDERBY, help=f"排序字段，默认 {DEFAULT_ORDERBY}")
    parser.add_argument("--asc", action="store_true", help="升序排列，默认倒序")
    parser.add_argument("--keywords", help="按关键词过滤")
    parser.add_argument("--id", dest="document_id", help="按文档 ID 过滤")
    parser.add_argument("--name", help="按文档名称过滤")
    parser.add_argument("--suffix", help="按文件后缀过滤，例如 pdf")
    parser.add_argument("--run", help="按解析状态过滤，例如 DONE")
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")
    add_runtime_config_arguments(parser)
    return parser.parse_args(argv)


def _validate_positive(name: str, value: int) -> None:
    if value <= 0:
        raise DataError(f"{name} 必须大于 0。")


def _build_documents_url(base_url: str, args: argparse.Namespace) -> str:
    dataset_id = args.dataset_id.strip()
    if not dataset_id:
        raise ConfigError("知识库 ID 不能为空。")

    query: dict[str, Any] = {
        "page": args.page,
        "page_size": args.page_size,
        "orderby": args.orderby,
        "desc": str(not args.asc).lower(),
    }
    if args.keywords:
        query["keywords"] = args.keywords
    if args.document_id:
        query["id"] = args.document_id
    if args.suffix:
        query["suffix"] = args.suffix
    if args.run:
        query["run"] = args.run

    encoded_dataset_id = urllib.parse.quote(dataset_id, safe="")
    encoded_query = urllib.parse.urlencode(query)
    return f"{base_url}/api/v1/datasets/{encoded_dataset_id}/documents?{encoded_query}"


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = ensure_success(payload)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DataError("文档列表响应缺少 data 对象。")

    docs = data.get("docs")
    total = data.get("total")
    if not isinstance(docs, list):
        raise DataError("文档列表响应缺少 data.docs 数组。")
    if not isinstance(total, int):
        raise DataError("文档列表响应缺少 data.total。")

    return {
        "code": payload.get("code"),
        "message": payload.get("message", ""),
        "data": data,
    }


def _filter_by_name(payload: dict[str, Any], name: str | None) -> dict[str, Any]:
    if not name:
        return payload

    keyword = name.strip().lower()
    if not keyword:
        return payload

    data = dict(payload["data"])
    docs = [
        doc
        for doc in data["docs"]
        if keyword in str(doc.get("name") or "").lower()
        or keyword in str(doc.get("location") or "").lower()
    ]
    data["docs"] = docs
    data["total"] = len(docs)
    return {**payload, "data": data}


def _format_document_line(document: dict[str, Any]) -> str:
    document_id = str(document.get("id", "")).strip() or "缺少ID"
    name = str(document.get("name", "")).strip() or "未命名文档"
    lines = [f"{name}（{document_id}）"]

    details = []
    for key, label in (
        ("run", "状态"),
        ("type", "类型"),
        ("chunk_count", "切片数"),
        ("token_count", "Token数"),
        ("size", "大小"),
    ):
        value = document.get(key)
        if value not in (None, ""):
            details.append(f"{label}={value}")
    if details:
        lines.append("  " + "，".join(details))

    return "\n".join(lines)


def _format_text(payload: dict[str, Any], dataset_id: str) -> str:
    data = payload["data"]
    docs = data["docs"]
    total = data["total"]

    lines = [f"知识库 ID：{dataset_id}", f"本页文档数：{len(docs)} / 总数：{total}"]
    if not docs:
        lines.append("当前条件下没有匹配文档。")
        return "\n".join(lines)

    for document in docs:
        lines.append("")
        lines.append(_format_document_line(document))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _parse_args(argv)

    try:
        _validate_positive("--page", args.page)
        _validate_positive("--page-size", args.page_size)
        base_url, api_key = resolve_runtime_config(args)
        payload = request_json(_build_documents_url(base_url, args), api_key)
        normalized = _filter_by_name(_normalize_payload(payload), args.name)

        if args.json_output:
            print(json.dumps(normalized, ensure_ascii=False, indent=2))
        else:
            print(_format_text(normalized, args.dataset_id.strip()))
        return 0
    except ScriptError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
