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
    error_payload,
    request_json,
    resolve_runtime_config,
    success_payload,
)

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 10
DEFAULT_ORDERBY = "create_time"
SCAN_PAGE_SIZE = 100


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读列出一个 RAGFlow 知识库中的文档。")
    parser.add_argument("dataset_id", help="知识库 ID")
    parser.add_argument("--page", type=int, default=DEFAULT_PAGE, help=f"页码，默认 {DEFAULT_PAGE}")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help=f"每页数量，默认 {DEFAULT_PAGE_SIZE}")
    parser.add_argument("--orderby", default=DEFAULT_ORDERBY, help=f"排序字段，默认 {DEFAULT_ORDERBY}")
    parser.add_argument("--asc", action="store_true", help="升序排列，默认倒序")
    parser.add_argument("--keywords", help="按关键词过滤")
    parser.add_argument("--id", dest="document_id", help="按文档 ID 过滤")
    parser.add_argument("--name", help="按文档名称跨页过滤")
    parser.add_argument("--suffix", help="按文件后缀过滤，例如 pdf")
    parser.add_argument("--run", help="按解析状态过滤，例如 DONE")
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")
    add_runtime_config_arguments(parser)
    return parser.parse_args(argv)


def _validate_positive(name: str, value: int) -> None:
    if value <= 0:
        raise DataError(f"{name} 必须大于 0。")


def _documents_url(base_url: str, dataset_id: str, query: dict[str, Any]) -> str:
    encoded_dataset_id = urllib.parse.quote(dataset_id, safe="")
    encoded_query = urllib.parse.urlencode(query)
    return f"{base_url}/api/v1/datasets/{encoded_dataset_id}/documents?{encoded_query}"


def _fetch_page(
    *,
    base_url: str,
    api_key: str,
    dataset_id: str,
    page: int,
    page_size: int,
    orderby: str,
    desc: bool,
    keywords: str | None = None,
    document_id: str | None = None,
    suffix: str | None = None,
    run: str | None = None,
) -> dict[str, Any]:
    normalized_dataset_id = dataset_id.strip()
    if not normalized_dataset_id:
        raise ConfigError("知识库 ID 不能为空。")

    query: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
        "orderby": orderby,
        "desc": str(desc).lower(),
    }
    if keywords:
        query["keywords"] = keywords
    if document_id:
        query["id"] = document_id
    if suffix:
        query["suffix"] = suffix
    if run:
        query["run"] = run

    payload = ensure_success(request_json(_documents_url(base_url, normalized_dataset_id, query), api_key))
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DataError("文档列表响应缺少 data 对象。")

    docs = data.get("docs")
    total = data.get("total")
    if not isinstance(docs, list):
        raise DataError("文档列表响应缺少 data.docs 数组。")
    if not isinstance(total, int):
        raise DataError("文档列表响应缺少 data.total。")
    return {"docs": docs, "total": total}


def _normalize_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": document.get("id"),
        "name": document.get("name"),
        "location": document.get("location"),
        "dataset_id": document.get("dataset_id"),
        "run": document.get("run"),
        "type": document.get("type"),
        "chunk_count": document.get("chunk_count"),
        "token_count": document.get("token_count"),
        "size": document.get("size"),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
        "metadata": document.get("metadata") or document.get("meta_fields"),
    }


def list_documents(args: argparse.Namespace, *, base_url: str, api_key: str) -> dict[str, Any]:
    _validate_positive("--page", args.page)
    _validate_positive("--page-size", args.page_size)
    dataset_id = args.dataset_id.strip()
    if not dataset_id:
        raise ConfigError("知识库 ID 不能为空。")

    if args.name:
        keyword = args.name.strip().lower()
        if not keyword:
            raise ConfigError("--name 不能为空。")
        matched: list[dict[str, Any]] = []
        page = 1
        total: int | None = None
        while True:
            page_payload = _fetch_page(
                base_url=base_url,
                api_key=api_key,
                dataset_id=dataset_id,
                page=page,
                page_size=SCAN_PAGE_SIZE,
                orderby=args.orderby,
                desc=not args.asc,
                keywords=args.keywords,
                document_id=args.document_id,
                suffix=args.suffix,
                run=args.run,
            )
            docs = page_payload["docs"]
            total = page_payload["total"] if total is None else total
            matched.extend(
                doc
                for doc in docs
                if isinstance(doc, dict)
                and (
                    keyword in str(doc.get("name") or "").lower()
                    or keyword in str(doc.get("location") or "").lower()
                )
            )
            if page * SCAN_PAGE_SIZE >= total or not docs:
                break
            page += 1

        start = (args.page - 1) * args.page_size
        end = start + args.page_size
        docs = matched[start:end]
        return success_payload(
            {
                "dataset_id": dataset_id,
                "page": args.page,
                "page_size": args.page_size,
                "total": len(matched),
                "count": len(docs),
                "name_filter": args.name,
                "documents": [_normalize_document(doc) for doc in docs],
            }
        )

    page_payload = _fetch_page(
        base_url=base_url,
        api_key=api_key,
        dataset_id=dataset_id,
        page=args.page,
        page_size=args.page_size,
        orderby=args.orderby,
        desc=not args.asc,
        keywords=args.keywords,
        document_id=args.document_id,
        suffix=args.suffix,
        run=args.run,
    )
    docs = page_payload["docs"]
    return success_payload(
        {
            "dataset_id": dataset_id,
            "page": args.page,
            "page_size": args.page_size,
            "total": page_payload["total"],
            "count": len(docs),
            "documents": [_normalize_document(doc) for doc in docs if isinstance(doc, dict)],
        }
    )


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


def _format_text(payload: dict[str, Any]) -> str:
    lines = [
        f"知识库 ID：{payload['dataset_id']}",
        f"本页文档数：{payload['count']} / 总数：{payload['total']}",
    ]
    if payload.get("name_filter"):
        lines.append(f"名称过滤：{payload['name_filter']}")
    if not payload["documents"]:
        lines.append("当前条件下没有匹配文档。")
        return "\n".join(lines)

    for document in payload["documents"]:
        lines.append("")
        lines.append(_format_document_line(document))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _parse_args(argv)

    try:
        base_url, api_key = resolve_runtime_config(args)
        payload = list_documents(args, base_url=base_url, api_key=api_key)
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_format_text(payload))
        return 0
    except ScriptError as exc:
        payload = error_payload(exc, dataset_id=getattr(args, "dataset_id", None))
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
