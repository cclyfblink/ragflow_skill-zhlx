#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from common import (
    ApiError,
    ConfigError,
    DataError,
    ScriptError,
    add_runtime_config_arguments,
    configure_stdio_utf8,
    current_timestamp,
    decode_json_body,
    decode_json_response,
    decode_response_text,
    ensure_success,
    error_payload,
    extract_error_message,
    format_json,
    request_json,
    resolve_runtime_config,
    success_payload,
)

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xlsx",
    ".txt",
}
DEFAULT_BATCH_SIZE = 5
DEFAULT_PAGE_SIZE = 100
DEFAULT_UPLOAD_TIMEOUT = 300
DEFAULT_MAX_NAME_LENGTH = 180


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按目录或文件批量上传文档到 RAGFlow 知识库。"
    )
    parser.add_argument("dataset", help="知识库 ID 或名称关键词")
    parser.add_argument("paths", nargs="*", help="要上传的文件或目录。目录会递归扫描。")
    parser.add_argument("--root", help="相对命名根目录，例如统计年鉴根目录")
    parser.add_argument("--source", help="在 --root 下要上传的子目录或文件，也可以是绝对路径")
    parser.add_argument(
        "--extensions",
        default=",".join(sorted(SUPPORTED_EXTENSIONS)),
        help="允许上传的后缀，逗号分隔，默认支持 pdf/doc/docx/xlsx/txt",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="每批上传文件数")
    parser.add_argument("--max-mb", type=float, help="跳过超过该大小的文件")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不上传")
    parser.add_argument("--start-parse", action="store_true", help="上传成功后立即请求解析")
    parser.add_argument("--chunk-method", help="上传后写入文档切片方式，例如 naive")
    parser.add_argument("--parser-config", help="上传后写入解析配置 JSON 对象，或 @path/to/file.json")
    parser.add_argument("--meta-fields", help="上传后写入元数据 JSON 对象，或 @path/to/file.json")
    parser.add_argument("--name-separator", default="-", help="相对路径片段连接符，默认 -")
    parser.add_argument("--exclude-name", action="append", default=[], help="按文件名关键词跳过文件，可重复使用")
    parser.add_argument("--allow-duplicates", action="store_true", help="允许上传同名文档")
    parser.add_argument(
        "--max-name-length",
        type=int,
        default=DEFAULT_MAX_NAME_LENGTH,
        help="上传到 RAGFlow 的文档名最大长度",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")
    add_runtime_config_arguments(parser)
    return parser.parse_args(argv)


def _normalize_extensions(raw_value: str) -> set[str]:
    extensions: set[str] = set()
    for item in raw_value.split(","):
        value = item.strip().lower()
        if not value:
            continue
        if not value.startswith("."):
            value = "." + value
        extensions.add(value)
    if not extensions:
        raise ConfigError("--extensions 至少需要包含一个文件后缀。")
    return extensions


def _resolve_input_paths(args: argparse.Namespace) -> tuple[Path | None, list[Path]]:
    root = Path(args.root).expanduser() if args.root else None
    inputs: list[Path] = []

    if args.source:
        source = Path(args.source).expanduser()
        if not source.is_absolute():
            if root is None:
                raise ConfigError("使用相对 --source 时必须同时提供 --root。")
            source = root / source
        inputs.append(source)

    inputs.extend(Path(path).expanduser() for path in args.paths)

    if not inputs:
        raise ConfigError("请提供要上传的文件或目录，例如 --root ROOT --source 子目录。")
    return root, inputs


def _iter_files(inputs: list[Path], extensions: set[str]) -> list[Path]:
    files: list[Path] = []
    for input_path in inputs:
        if not input_path.exists():
            raise ConfigError(f"路径不存在：{input_path}")
        if input_path.is_file():
            candidates = [input_path]
        else:
            candidates = [path for path in input_path.rglob("*") if path.is_file()]

        for path in candidates:
            name = path.name
            if name.startswith("~$"):
                continue
            if path.suffix.lower() not in extensions:
                continue
            files.append(path)
    return sorted(set(files), key=lambda item: str(item).lower())


def _try_relative(path: Path, root: Path | None) -> Path:
    if root is None:
        return Path(path.name)
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return Path(path.name)


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _truncate_name(name: str, max_length: int) -> str:
    if len(name) <= max_length:
        return name
    path = Path(name)
    suffix = path.suffix
    stem = name[: max(1, max_length - len(suffix) - 13)]
    return f"{stem}__{_short_hash(name)}{suffix}"


def _build_upload_name(path: Path, root: Path | None, max_length: int, name_separator: str) -> str:
    relative_path = _try_relative(path, root)
    separator = name_separator or "-"
    name = separator.join(part for part in relative_path.parts if part not in ("", "."))
    return _truncate_name(name, max_length)


def _build_records(
    files: list[Path],
    root: Path | None,
    *,
    max_mb: float | None,
    max_name_length: int,
    name_separator: str,
    exclude_names: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    used_names: dict[str, int] = {}
    exclude_keywords = [item.strip().lower() for item in exclude_names if item.strip()]

    for path in files:
        size = path.stat().st_size
        size_mb = size / 1024 / 1024
        upload_name = _build_upload_name(path, root, max_name_length, name_separator)
        matched_exclude = next((keyword for keyword in exclude_keywords if keyword in path.name.lower() or keyword in upload_name.lower()), None)
        if matched_exclude:
            skipped.append(
                {
                    "path": str(path),
                    "upload_name": upload_name,
                    "size_mb": round(size_mb, 2),
                    "reason": f"匹配 --exclude-name={matched_exclude}",
                }
            )
            continue
        if max_mb is not None and size_mb > max_mb:
            skipped.append(
                {
                    "path": str(path),
                    "upload_name": upload_name,
                    "size_mb": round(size_mb, 2),
                    "reason": f"超过 --max-mb={max_mb}",
                }
            )
            continue

        if upload_name in used_names:
            used_names[upload_name] += 1
            original = upload_name
            stem = upload_name[: -len(path.suffix)] if path.suffix and upload_name.endswith(path.suffix) else upload_name
            upload_name = _truncate_name(
                f"{stem}__{used_names[original]}__{_short_hash(str(path))}{path.suffix}",
                max_name_length,
            )
        else:
            used_names[upload_name] = 1

        records.append(
            {
                "path": str(path),
                "upload_name": upload_name,
                "extension": path.suffix.lower(),
                "size": size,
                "size_mb": round(size_mb, 2),
            }
        )
    return records, skipped


def _resolve_dataset_id(dataset_query: str, *, base_url: str, api_key: str) -> dict[str, Any]:
    payload = ensure_success(request_json(f"{base_url}/api/v1/datasets", api_key))
    datasets = payload.get("data")
    if not isinstance(datasets, list):
        raise DataError("知识库列表响应缺少 data 数组。")

    query = dataset_query.strip().lower()
    if not query:
        raise ConfigError("知识库 ID 或名称不能为空。")

    exact = [
        dataset
        for dataset in datasets
        if isinstance(dataset, dict)
        and (
            str(dataset.get("id") or "").lower() == query
            or str(dataset.get("name") or "").lower() == query
        )
    ]
    if len(exact) == 1:
        return exact[0]

    fuzzy = [
        dataset
        for dataset in datasets
        if isinstance(dataset, dict)
        and query in str(dataset.get("name") or "").lower()
    ]
    if len(fuzzy) == 1:
        return fuzzy[0]
    if len(fuzzy) > 1:
        names = ", ".join(str(dataset.get("name") or dataset.get("id")) for dataset in fuzzy)
        raise DataError(f"匹配到多个知识库，请提供更精确的名称或 ID：{names}")

    raise DataError(f"当前账号可访问知识库中未找到：{dataset_query}")


def _fetch_existing_document_names(dataset_id: str, *, base_url: str, api_key: str) -> set[str]:
    names: set[str] = set()
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
        docs = data.get("docs")
        total = data.get("total") if total is None else total
        if not isinstance(docs, list) or not isinstance(total, int):
            raise DataError("文档列表响应缺少 data.docs 或 data.total。")

        for doc in docs:
            if isinstance(doc, dict):
                name = str(doc.get("name") or "").strip()
                if name:
                    names.add(name)

        if len(names) >= total or not docs:
            return names
        page += 1


def _build_multipart(records: list[dict[str, Any]]) -> tuple[str, bytes]:
    boundary = "----RagflowSkillBoundary" + uuid.uuid4().hex
    body = bytearray()

    for record in records:
        file_path = record["path"]
        upload_name = record["upload_name"]
        mime = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
        with open(file_path, "rb") as file_obj:
            content = file_obj.read()

        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        disposition = f'Content-Disposition: form-data; name="file"; filename="{upload_name}"\r\n'
        body.extend(disposition.encode("utf-8"))
        body.extend(f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
        body.extend(content)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return boundary, bytes(body)


def _normalize_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": document.get("id"),
        "name": document.get("name"),
        "dataset_id": document.get("dataset_id"),
        "run": document.get("run"),
        "chunk_method": document.get("chunk_method"),
        "chunk_count": document.get("chunk_count"),
        "token_count": document.get("token_count"),
        "created_at": document.get("created_at"),
    }


def _upload_batch(
    dataset_id: str,
    records: list[dict[str, Any]],
    *,
    base_url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    boundary, body = _build_multipart(records)
    encoded_dataset_id = urllib.parse.quote(dataset_id, safe="")
    request_obj = urllib.request.Request(
        f"{base_url}/api/v1/datasets/{encoded_dataset_id}/documents",
        data=body,
        method="POST",
    )
    request_obj.add_header("Authorization", f"Bearer {api_key}")
    request_obj.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    try:
        with urllib.request.urlopen(request_obj, timeout=DEFAULT_UPLOAD_TIMEOUT) as response:
            payload = decode_json_response(response.read())
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()
        response_payload = decode_json_body(body_bytes)
        response_text = decode_response_text(body_bytes)
        message = extract_error_message(body_bytes)
        raise ApiError(
            message or f"上传失败，HTTP 状态码：{exc.code}。",
            http_status=exc.code,
            api_code=response_payload.get("code") if isinstance(response_payload, dict) else None,
            response_payload=response_payload,
            response_body=response_text,
        ) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ApiError(f"上传失败：{reason}") from None

    ensure_success(payload)
    raw_documents = payload.get("data")
    if not isinstance(raw_documents, list):
        raise DataError("上传响应缺少 data 数组。")
    return [_normalize_document(document) for document in raw_documents if isinstance(document, dict)]


def _load_json_object(raw_value: str, option_name: str) -> dict[str, Any]:
    value = raw_value
    if raw_value.startswith("@"):
        path = Path(raw_value[1:]).expanduser()
        try:
            value = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"读取 {option_name} 文件失败：{path}，{exc}") from exc

    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{option_name} 必须是合法 JSON：{exc.msg}") from exc

    if not isinstance(payload, dict):
        raise ConfigError(f"{option_name} 必须是 JSON 对象。")
    return payload


def _build_document_update_payload(plan: dict[str, Any]) -> dict[str, Any]:
    update_options = plan.get("document_update") or {}
    payload: dict[str, Any] = {}
    if update_options.get("chunk_method"):
        payload["chunk_method"] = update_options["chunk_method"]
    if update_options.get("parser_config") is not None:
        payload["parser_config"] = update_options["parser_config"]
    if update_options.get("meta_fields") is not None:
        payload["meta_fields"] = update_options["meta_fields"]
    return payload


def _update_uploaded_document(
    dataset_id: str,
    document_id: str,
    payload: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
) -> dict[str, Any]:
    encoded_dataset_id = urllib.parse.quote(dataset_id, safe="")
    encoded_document_id = urllib.parse.quote(document_id, safe="")
    response = ensure_success(
        request_json(
            f"{base_url}/api/v1/datasets/{encoded_dataset_id}/documents/{encoded_document_id}",
            api_key,
            method="PUT",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )
    )
    data = response.get("data")
    if not isinstance(data, dict):
        raise DataError("更新文档解析配置响应缺少 data 对象。")
    return _normalize_document(data)


def _start_parse(dataset_id: str, document_ids: list[str], *, base_url: str, api_key: str) -> dict[str, Any]:
    if not document_ids:
        return {"requested": False, "document_ids": []}
    encoded_dataset_id = urllib.parse.quote(dataset_id, safe="")
    payload = ensure_success(
        request_json(
            f"{base_url}/api/v1/datasets/{encoded_dataset_id}/chunks",
            api_key,
            method="POST",
            body=format_json({"document_ids": document_ids}).encode("utf-8"),
            content_type="application/json",
        )
    )
    return {"requested": True, "document_ids": document_ids, "api_response": payload}


def _chunks(records: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    return [records[index : index + batch_size] for index in range(0, len(records), batch_size)]


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_extension: dict[str, int] = {}
    total_size = 0
    for record in records:
        by_extension[record["extension"]] = by_extension.get(record["extension"], 0) + 1
        total_size += int(record["size"])
    return {
        "count": len(records),
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "by_extension": dict(sorted(by_extension.items())),
        "sample": records[:20],
    }


def build_plan(args: argparse.Namespace, *, base_url: str, api_key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if args.batch_size <= 0:
        raise ConfigError("--batch-size 必须大于 0。")
    if args.max_name_length < 60:
        raise ConfigError("--max-name-length 不能小于 60。")

    root, inputs = _resolve_input_paths(args)
    extensions = _normalize_extensions(args.extensions)
    files = _iter_files(inputs, extensions)
    records, skipped = _build_records(
        files,
        root,
        max_mb=args.max_mb,
        max_name_length=args.max_name_length,
        name_separator=args.name_separator,
        exclude_names=args.exclude_name,
    )

    dataset = _resolve_dataset_id(args.dataset, base_url=base_url, api_key=api_key)
    dataset_id = str(dataset.get("id") or "").strip()
    if not dataset_id:
        raise DataError("匹配到的知识库缺少 ID。")

    duplicate_skipped: list[dict[str, Any]] = []
    if not args.allow_duplicates and records:
        existing_names = _fetch_existing_document_names(dataset_id, base_url=base_url, api_key=api_key)
        retained = []
        for record in records:
            if record["upload_name"] in existing_names:
                duplicate_skipped.append({**record, "reason": "知识库中已存在同名文档"})
            else:
                retained.append(record)
        records = retained

    plan = success_payload({
        "planned_at": current_timestamp(),
        "dataset": {"id": dataset_id, "name": dataset.get("name")},
        "root": str(root) if root else None,
        "inputs": [str(path) for path in inputs],
        "dry_run": bool(args.dry_run),
        "start_parse": bool(args.start_parse),
        "batch_size": args.batch_size,
        "name_separator": args.name_separator,
        "exclude_name": args.exclude_name,
        "document_update": {
            "chunk_method": args.chunk_method,
            "parser_config": _load_json_object(args.parser_config, "--parser-config") if args.parser_config else None,
            "meta_fields": _load_json_object(args.meta_fields, "--meta-fields") if args.meta_fields else None,
        },
        "upload": _summarize(records),
        "skipped": skipped + duplicate_skipped,
    })
    return plan, records


def execute_upload(
    plan: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    base_url: str,
    api_key: str,
) -> dict[str, Any]:
    dataset_id = plan["dataset"]["id"]
    uploaded_documents: list[dict[str, Any]] = []
    batches = _chunks(records, int(plan["batch_size"]))
    for batch_index, batch in enumerate(batches, start=1):
        documents = _upload_batch(dataset_id, batch, base_url=base_url, api_key=api_key)
        uploaded_documents.extend(documents)

    document_ids = [
        str(document.get("id"))
        for document in uploaded_documents
        if isinstance(document.get("id"), str) and str(document.get("id")).strip()
    ]
    update_payload = _build_document_update_payload(plan)
    updated_documents: list[dict[str, Any]] = []
    if update_payload:
        for document_id in document_ids:
            updated_documents.append(
                _update_uploaded_document(
                    dataset_id,
                    document_id,
                    update_payload,
                    base_url=base_url,
                    api_key=api_key,
                )
            )
    parse_result = None
    if plan.get("start_parse"):
        parse_result = _start_parse(dataset_id, document_ids, base_url=base_url, api_key=api_key)

    return success_payload({
        "uploaded_at": current_timestamp(),
        "dataset": plan["dataset"],
        "uploaded_count": len(uploaded_documents),
        "documents": uploaded_documents,
        "updated_documents": updated_documents,
        "document_update": plan.get("document_update"),
        "document_ids": document_ids,
        "parse": parse_result,
        "skipped": plan["skipped"],
    })


def _format_plan_text(plan: dict[str, Any]) -> str:
    upload = plan["upload"]
    lines = [
        f"知识库：{plan['dataset'].get('name') or plan['dataset']['id']}",
        f"待上传：{upload['count']} 个文件，合计 {upload['total_size_mb']} MB",
        f"跳过：{len(plan['skipped'])} 个文件",
        f"批大小：{plan['batch_size']}",
        f"是否只预览：{'是' if plan['dry_run'] else '否'}",
        f"上传后解析：{'是' if plan['start_parse'] else '否'}",
        f"文档名连接符：{plan.get('name_separator') or '-'}",
    ]
    document_update = plan.get("document_update") or {}
    configured_updates = [key for key, value in document_update.items() if value not in (None, "")]
    if configured_updates:
        lines.append("上传后写入配置：" + "，".join(configured_updates))
    if upload["by_extension"]:
        lines.append("类型：" + "，".join(f"{key}={value}" for key, value in upload["by_extension"].items()))
    if upload["sample"]:
        lines.append("")
        lines.append("样例：")
        for record in upload["sample"][:10]:
            lines.append(f"- {record['upload_name']}（{record['size_mb']} MB）")
    if plan["skipped"]:
        lines.append("")
        lines.append("跳过样例：")
        for record in plan["skipped"][:10]:
            lines.append(f"- {record.get('upload_name') or record.get('path')}：{record.get('reason')}")
    return "\n".join(lines)


def _format_upload_text(payload: dict[str, Any]) -> str:
    lines = [
        f"知识库：{payload['dataset'].get('name') or payload['dataset']['id']}",
        f"上传完成时间：{payload['uploaded_at']}",
        f"上传成功：{payload['uploaded_count']} 个文档",
        f"跳过：{len(payload['skipped'])} 个文件",
    ]
    parse_result = payload.get("parse")
    if payload.get("updated_documents"):
        lines.append(f"已写入解析配置：{len(payload['updated_documents'])} 个文档")
    if isinstance(parse_result, dict):
        lines.append(f"已请求解析：{'是' if parse_result.get('requested') else '否'}")
    if payload["documents"]:
        lines.append("")
        for document in payload["documents"][:20]:
            lines.append(f"- {document.get('name') or '未命名'}（{document.get('id') or '无ID'}）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _parse_args(argv)

    try:
        base_url, api_key = resolve_runtime_config(args)
        plan, records = build_plan(args, base_url=base_url, api_key=api_key)
        if args.dry_run:
            print(format_json(plan) if args.json_output else _format_plan_text(plan))
            return 0

        result = execute_upload(plan, records, base_url=base_url, api_key=api_key)
        print(format_json(result) if args.json_output else _format_upload_text(result))
        return 0
    except ScriptError as exc:
        payload = error_payload(exc)
        if args.json_output:
            print(format_json(payload))
        else:
            print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
