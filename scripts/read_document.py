#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from pathlib import Path
from typing import Any

from chunks import _fetch_all_chunks, _normalize_chunk
from common import (
    ConfigError,
    ScriptError,
    add_runtime_config_arguments,
    configure_stdio_utf8,
    current_timestamp,
    error_payload,
    format_json,
    resolve_runtime_config,
    success_payload,
)

DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_CHARS = 80000


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读读取一个 RAGFlow 文档的全部 chunk。")
    parser.add_argument("dataset_id", help="知识库 ID")
    parser.add_argument("document_id", help="文档 ID")
    parser.add_argument(
        "--format",
        choices=("text", "markdown", "json"),
        default="text",
        help="合并内容格式，默认 text；json 会把 chunk 列表渲染为 JSON 字符串",
    )
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help=f"扫描页大小，默认 {DEFAULT_PAGE_SIZE}")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help=f"最大返回字符数，默认 {DEFAULT_MAX_CHARS}")
    parser.add_argument("--include-chunks", action="store_true", help="在 JSON 中返回每个 chunk 的完整内容")
    parser.add_argument("--output", help="可选：把合并后的内容写入指定文件路径")
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")
    add_runtime_config_arguments(parser)
    return parser.parse_args(argv)


def _validate_ids(dataset_id: str, document_id: str) -> tuple[str, str]:
    normalized_dataset_id = dataset_id.strip()
    normalized_document_id = document_id.strip()
    if not normalized_dataset_id:
        raise ConfigError("知识库 ID 不能为空。")
    if not normalized_document_id:
        raise ConfigError("文档 ID 不能为空。")
    return normalized_dataset_id, normalized_document_id


def _first_value(chunks: list[dict[str, Any]], keys: tuple[str, ...]) -> Any | None:
    for chunk in chunks:
        for key in keys:
            value = chunk.get(key)
            if value not in (None, ""):
                return value
    return None


def _infer_document_name(raw_chunks: list[dict[str, Any]], normalized_chunks: list[dict[str, Any]]) -> str | None:
    value = _first_value(
        raw_chunks,
        (
            "document_name",
            "document_keyword",
            "docnm_kwd",
            "doc_name",
            "name",
        ),
    )
    if value:
        return str(value)

    for chunk in normalized_chunks:
        metadata = chunk.get("metadata")
        if not isinstance(metadata, dict):
            continue
        for key in ("document_name", "doc_name", "filename", "file_name", "name"):
            value = metadata.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def _chunk_text(chunk: dict[str, Any]) -> str:
    return str(chunk.get("content") or "").strip()


def _render_text(chunks: list[dict[str, Any]]) -> str:
    return "\n\n".join(text for text in (_chunk_text(chunk) for chunk in chunks) if text)


def _render_markdown(dataset_id: str, document_id: str, document_name: str | None, chunks: list[dict[str, Any]]) -> str:
    title = document_name or document_id
    lines = [
        f"# {title}",
        "",
        f"- 知识库 ID：`{dataset_id}`",
        f"- 文档 ID：`{document_id}`",
        f"- chunk 数：{len(chunks)}",
    ]
    for chunk in chunks:
        content = _chunk_text(chunk)
        if not content:
            continue
        lines.extend(
            [
                "",
                f"## Chunk {chunk.get('index')}",
                "",
                f"- chunk ID：`{chunk.get('chunk_id') or '未知'}`",
                "",
                content,
            ]
        )
    return "\n".join(lines)


def _render_content(
    *,
    content_format: str,
    dataset_id: str,
    document_id: str,
    document_name: str | None,
    chunks: list[dict[str, Any]],
) -> str:
    if content_format == "markdown":
        return _render_markdown(dataset_id, document_id, document_name, chunks)
    if content_format == "json":
        return format_json({"chunks": chunks})
    return _render_text(chunks)


def _truncate_content(content: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0:
        raise ConfigError("--max-chars 必须大于 0。")
    if len(content) <= max_chars:
        return content, False
    return content[:max_chars], True


def _chunk_catalog(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for chunk in chunks:
        content = str(chunk.get("content") or "")
        catalog.append(
            {
                "index": chunk.get("index"),
                "chunk_id": chunk.get("chunk_id"),
                "content_chars": len(content),
                "positions": chunk.get("positions"),
                "metadata": chunk.get("metadata"),
                "preview": content[:160],
            }
        )
    return catalog


def _write_output(path_value: str, content: str) -> str:
    path = Path(path_value).expanduser()
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path.resolve())


def read_document(
    dataset_id: str,
    document_id: str,
    *,
    content_format: str,
    max_chars: int,
    page_size: int,
    include_chunks: bool,
    output: str | None,
    base_url: str,
    api_key: str,
) -> dict[str, Any]:
    dataset_id, document_id = _validate_ids(dataset_id, document_id)
    if page_size <= 0:
        raise ConfigError("--page-size 必须大于 0。")
    if content_format not in {"text", "markdown", "json"}:
        raise ConfigError("--format 只能是 text、markdown 或 json。")

    raw_chunks = _fetch_all_chunks(dataset_id, document_id, page_size=page_size, base_url=base_url, api_key=api_key)
    normalized_chunks = [_normalize_chunk(chunk, index) for index, chunk in enumerate(raw_chunks)]
    document_name = _infer_document_name(raw_chunks, normalized_chunks)
    rendered = _render_content(
        content_format=content_format,
        dataset_id=dataset_id,
        document_id=document_id,
        document_name=document_name,
        chunks=normalized_chunks,
    )
    full_chars = len(rendered)
    content, truncated = _truncate_content(rendered, max_chars)
    output_path = _write_output(output, content) if output else None

    return success_payload(
        {
            "checked_at": current_timestamp(),
            "dataset_id": dataset_id,
            "document_id": document_id,
            "document_name": document_name,
            "content_format": content_format,
            "chunk_count": len(raw_chunks),
            "returned_chunks": len(normalized_chunks),
            "content_chars": len(content),
            "full_content_chars": full_chars,
            "truncated": truncated,
            "output_path": output_path,
            "content": content,
            "chunk_catalog": _chunk_catalog(normalized_chunks),
            "chunks": normalized_chunks if include_chunks else [],
            "chunks_included": include_chunks,
            "note": "整文内容基于 RAGFlow chunk 列表顺序合并；如需严谨页序，请同时参考 positions。此脚本只返回已解析文本，不返回原始文件。",
        }
    )


def _format_text(payload: dict[str, Any]) -> str:
    header = [
        f"文档：{payload.get('document_name') or payload['document_id']}",
        f"知识库 ID：{payload['dataset_id']}",
        f"chunk 数：{payload['chunk_count']}",
    ]
    if payload.get("truncated"):
        header.append(f"内容已截断：{payload['content_chars']} / {payload['full_content_chars']} 字符")
    if payload.get("output_path"):
        header.append(f"已写入：{payload['output_path']}")
    return "\n".join(header + ["", payload.get("content") or ""])


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _parse_args(argv)

    try:
        base_url, api_key = resolve_runtime_config(args)
        payload = read_document(
            args.dataset_id,
            args.document_id,
            content_format=args.format,
            max_chars=args.max_chars,
            page_size=args.page_size,
            include_chunks=args.include_chunks,
            output=args.output,
            base_url=base_url,
            api_key=api_key,
        )
        print(format_json(payload) if args.json_output else _format_text(payload))
        return 0
    except ScriptError as exc:
        payload = error_payload(exc, dataset_id=getattr(args, "dataset_id", None), document_id=getattr(args, "document_id", None))
        if getattr(args, "json_output", False):
            print(format_json(payload))
        else:
            print(f"错误：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
