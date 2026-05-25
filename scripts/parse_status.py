#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
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
    format_json,
    request_json,
    resolve_runtime_config,
    serialize_script_error,
)

DEFAULT_PAGE_SIZE = 100
STATUS_ORDER = ("UNSTART", "RUNNING", "DONE", "FAIL", "CANCEL")
RUN_STATUS_MAP = {
    "0": "UNSTART",
    "1": "RUNNING",
    "2": "CANCEL",
    "3": "DONE",
    "4": "FAIL",
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="查看 RAGFlow 文档解析状态。")
    parser.add_argument("dataset_id", help="知识库 ID")
    parser.add_argument("--doc-ids", help="逗号分隔的文档 ID，只查看指定文档")
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")
    add_runtime_config_arguments(parser)
    return parser.parse_args(argv)


def _parse_doc_ids(raw_value: str | None) -> list[str] | None:
    if raw_value is None:
        return None
    doc_ids = []
    seen = set()
    for item in raw_value.split(","):
        value = item.strip()
        if value and value not in seen:
            seen.add(value)
            doc_ids.append(value)
    if not doc_ids:
        raise ConfigError("--doc-ids 至少需要包含一个文档 ID。")
    return doc_ids


def _normalize_run(value: Any) -> str:
    if isinstance(value, int):
        return RUN_STATUS_MAP.get(str(value), str(value))
    if isinstance(value, str):
        raw = value.strip()
        return RUN_STATUS_MAP.get(raw, raw.upper())
    return str(value)


def _fetch_all_documents(dataset_id: str, *, base_url: str, api_key: str) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    page = 1
    total: int | None = None
    encoded_dataset_id = urllib.parse.quote(dataset_id, safe="")

    while True:
        query = urllib.parse.urlencode({"page": page, "page_size": DEFAULT_PAGE_SIZE})
        payload = ensure_success(
            request_json(
                f"{base_url}/api/v1/datasets/{encoded_dataset_id}/documents?{query}",
                api_key,
            )
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise DataError("文档列表响应缺少 data 对象。")
        page_docs = data.get("docs")
        total = data.get("total") if total is None else total
        if not isinstance(page_docs, list) or not isinstance(total, int):
            raise DataError("文档列表响应缺少 data.docs 或 data.total。")
        docs.extend(doc for doc in page_docs if isinstance(doc, dict))
        if len(docs) >= total or not page_docs:
            return docs[:total]
        page += 1


def collect_status(dataset_id: str, target_ids: list[str] | None, *, base_url: str, api_key: str) -> dict[str, Any]:
    normalized_dataset_id = dataset_id.strip()
    if not normalized_dataset_id:
        raise ConfigError("知识库 ID 不能为空。")

    docs = _fetch_all_documents(normalized_dataset_id, base_url=base_url, api_key=api_key)
    if target_ids:
        wanted = set(target_ids)
        docs = [doc for doc in docs if str(doc.get("id") or "") in wanted]

    normalized_docs = []
    summary: dict[str, int] = {"total": len(docs)}
    for status in STATUS_ORDER:
        summary[status] = 0

    for doc in docs:
        run = _normalize_run(doc.get("run"))
        if run in summary:
            summary[run] += 1
        normalized_docs.append(
            {
                "id": doc.get("id"),
                "name": doc.get("name"),
                "run": run,
                "chunk_count": doc.get("chunk_count"),
                "token_count": doc.get("token_count"),
                "progress_msg": doc.get("progress_msg"),
            }
        )

    return {
        "dataset_id": normalized_dataset_id,
        "checked_at": current_timestamp(),
        "summary": summary,
        "documents": normalized_docs,
    }


def _format_text(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        f"知识库 ID：{payload['dataset_id']}",
        f"检查时间：{payload['checked_at']}",
        f"文档数：{summary['total']}",
        "",
    ]
    for status in STATUS_ORDER:
        lines.append(f"{status}：{summary[status]}")
    for doc in payload["documents"][:50]:
        lines.extend(
            [
                "",
                f"[{doc['run']}] {doc.get('name') or '未命名'}",
                f"ID：{doc.get('id') or '未知'}",
                f"切片数：{doc.get('chunk_count') if doc.get('chunk_count') is not None else '未知'}",
                f"Token数：{doc.get('token_count') if doc.get('token_count') is not None else '未知'}",
            ]
        )
        if doc.get("progress_msg"):
            lines.append(f"消息：{doc['progress_msg']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _parse_args(argv)

    try:
        base_url, api_key = resolve_runtime_config(args)
        payload = collect_status(
            args.dataset_id,
            _parse_doc_ids(args.doc_ids),
            base_url=base_url,
            api_key=api_key,
        )
        print(format_json(payload) if args.json_output else _format_text(payload))
        return 0
    except ScriptError as exc:
        if args.json_output:
            print(
                format_json(
                    {
                        "dataset_id": args.dataset_id,
                        "checked_at": current_timestamp(),
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
