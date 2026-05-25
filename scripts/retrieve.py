#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import re
from typing import Any

from chunks import expand_chunk
from common import (
    ConfigError,
    ScriptError,
    add_runtime_config_arguments,
    configure_stdio_utf8,
    error_payload,
    format_json,
    resolve_runtime_config,
    success_payload,
)
from search import search

LOW_CONFIDENCE_THRESHOLD = 0.28
MIN_HYBRID_RESULTS = 2
DEFAULT_EXPAND_TOP = 2
FILE_HINT_PATTERN = re.compile(
    r"(\.pdf|\.docx?|\.xlsx?|\.xls|《[^》]+》|[A-Za-z]{1,8}[-_/]?\d{2,}|表\s*\d+|[0-9]+[-_][0-9]+)"
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="面向 agent 的 RAGFlow 证据召回封装。")
    parser.add_argument("query", help="查询问题")
    parser.add_argument("dataset", nargs="?", help="可选：知识库 ID、完整名称或名称关键词")
    parser.add_argument("--dataset-ids", help="可选：逗号分隔的知识库 ID")
    parser.add_argument("--dataset-name", help="可选：知识库名称或描述关键词")
    parser.add_argument("--doc-ids", help="可选：逗号分隔的文档 ID")
    parser.add_argument("--document-name", "--file-name", dest="document_name", help="可选：文档名称关键词")
    parser.add_argument("--metadata-condition", help="RAGFlow metadata_condition JSON 对象")
    parser.add_argument("--cross-languages", help="跨语言检索设置，例如 Chinese,English")
    parser.add_argument("--rerank-id", help="可选：重排模型 ID")
    parser.add_argument("--search-id", help="可选：检索会话 ID")
    parser.add_argument("--expand-top", type=int, default=DEFAULT_EXPAND_TOP, help="展开前 N 条关键 chunk，默认 2")
    parser.add_argument("--expand-before", type=int, default=1, help="向前展开 chunk 数，默认 1")
    parser.add_argument("--expand-after", type=int, default=1, help="向后展开 chunk 数，默认 1")
    parser.add_argument("--no-expand", action="store_true", help="不自动展开上下文")
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")
    add_runtime_config_arguments(parser)
    return parser.parse_args(argv)


def _search_args(args: argparse.Namespace, mode: str, *, document_name: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        query=args.query,
        dataset=args.dataset,
        mode=mode,
        dataset_ids=args.dataset_ids,
        dataset_name=args.dataset_name,
        doc_ids=args.doc_ids,
        document_name=document_name if document_name is not None else args.document_name,
        candidate_top_k=None,
        limit=None,
        page=1,
        page_size=None,
        threshold=None,
        vector_weight=None,
        keyword=False,
        no_keyword=False,
        highlight=False,
        no_highlight=False,
        metadata_condition=args.metadata_condition,
        cross_languages=args.cross_languages,
        rerank_id=args.rerank_id,
        search_id=args.search_id,
        json_output=True,
    )


def _highest_similarity(payload: dict[str, Any]) -> float | None:
    values = [
        chunk.get("similarity")
        for chunk in payload.get("chunks", [])
        if isinstance(chunk.get("similarity"), (int, float))
    ]
    if not values:
        return None
    return float(max(values))


def _is_low_confidence(payload: dict[str, Any]) -> bool:
    if int(payload.get("count") or 0) < MIN_HYBRID_RESULTS:
        return True
    highest = _highest_similarity(payload)
    return highest is None or highest < LOW_CONFIDENCE_THRESHOLD


def _looks_like_precise_query(query: str, document_name: str | None) -> bool:
    return bool(document_name or FILE_HINT_PATTERN.search(query))


def _merge_chunks(primary: dict[str, Any], extra_payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for payload in [primary, *extra_payloads]:
        for chunk in payload.get("chunks", []):
            key = (
                str(chunk.get("dataset_id") or ""),
                str(chunk.get("document_id") or ""),
                str(chunk.get("chunk_id") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            chunks.append(chunk)
    return chunks


def _step_summary(name: str, reason: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "reason": reason,
        "ok": payload.get("ok"),
        "count": payload.get("count"),
        "request": payload.get("request"),
        "highest_similarity": _highest_similarity(payload),
    }


def _expand_key_chunks(
    chunks: list[dict[str, Any]],
    *,
    args: argparse.Namespace,
    base_url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    if args.no_expand:
        return []
    if args.expand_top < 0:
        raise ConfigError("--expand-top 不能小于 0。")
    if args.expand_before < 0 or args.expand_after < 0:
        raise ConfigError("--expand-before 和 --expand-after 不能小于 0。")

    expansions: list[dict[str, Any]] = []
    for chunk in chunks[: args.expand_top]:
        dataset_id = chunk.get("dataset_id")
        document_id = chunk.get("document_id")
        chunk_id = chunk.get("chunk_id")
        if not dataset_id or not document_id or not chunk_id:
            expansions.append({"ok": False, "reason": "缺少 dataset_id/document_id/chunk_id", "chunk": chunk})
            continue
        try:
            expansions.append(
                expand_chunk(
                    str(dataset_id),
                    str(document_id),
                    str(chunk_id),
                    before=args.expand_before,
                    after=args.expand_after,
                    page_size=100,
                    base_url=base_url,
                    api_key=api_key,
                )
            )
        except ScriptError as exc:
            expansions.append(error_payload(exc, dataset_id=dataset_id, document_id=document_id, chunk_id=chunk_id))
    return expansions


def retrieve(args: argparse.Namespace, *, base_url: str, api_key: str) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    hybrid = search(_search_args(args, "hybrid"), base_url=base_url, api_key=api_key)
    steps.append(_step_summary("hybrid", "默认检索", hybrid))

    extra_payloads: list[dict[str, Any]] = []
    fallback_used = False
    if _is_low_confidence(hybrid):
        broad = search(_search_args(args, "broad"), base_url=base_url, api_key=api_key)
        extra_payloads.append(broad)
        fallback_used = True
        steps.append(_step_summary("broad", "hybrid 命中少或最高分偏低", broad))

    if _looks_like_precise_query(args.query, args.document_name):
        keyword = search(_search_args(args, "keyword"), base_url=base_url, api_key=api_key)
        extra_payloads.append(keyword)
        fallback_used = True
        steps.append(_step_summary("keyword", "问题包含文件名、编号或表名特征", keyword))

    chunks = _merge_chunks(hybrid, extra_payloads)
    expansions = _expand_key_chunks(chunks, args=args, base_url=base_url, api_key=api_key)
    datasets = hybrid.get("datasets", [])
    doc_ids = hybrid.get("doc_ids", [])
    matched_documents = hybrid.get("matched_documents", [])
    return success_payload(
        {
            "query": args.query,
            "checked_at": hybrid.get("checked_at"),
            "fallback_used": fallback_used,
            "steps": steps,
            "datasets": datasets,
            "doc_ids": doc_ids,
            "matched_documents": matched_documents,
            "count": len(chunks),
            "chunks": chunks,
            "expansions": expansions,
            "workflow_note": "retrieve.py 是高层封装；需要精细控制时可直接调用 search.py 和 chunks.py。",
        }
    )


def _format_text(payload: dict[str, Any]) -> str:
    lines = [
        f"查询：{payload['query']}",
        f"命中数量：{payload['count']}",
        f"发生 fallback：{'是' if payload['fallback_used'] else '否'}",
        f"展开上下文：{len(payload['expansions'])} 组",
    ]
    for chunk in payload["chunks"][:8]:
        lines.extend(
            [
                "",
                f"- {chunk.get('document_name') or '未知文档'}",
                f"  知识库：{chunk.get('dataset_name') or '未知知识库'}",
                f"  相似度：{chunk.get('similarity')}",
                f"  chunk：{chunk.get('chunk_id')}",
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _parse_args(argv)

    try:
        base_url, api_key = resolve_runtime_config(args)
        payload = retrieve(args, base_url=base_url, api_key=api_key)
        print(format_json(payload) if args.json_output else _format_text(payload))
        return 0
    except ScriptError as exc:
        payload = error_payload(exc)
        if args.json_output:
            print(format_json(payload))
        else:
            print(f"错误：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
