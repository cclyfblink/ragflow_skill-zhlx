#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
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
from datasets import find_dataset, list_datasets

DEFAULT_CANDIDATE_TOP_K = 1024
DEFAULT_PAGE = 1
DEFAULT_LIMIT = 8
PREVIEW_LIMIT = 240

MODE_DEFAULTS: dict[str, dict[str, Any]] = {
    "hybrid": {
        "keyword": True,
        "highlight": True,
        "vector_similarity_weight": 0.3,
        "similarity_threshold": 0.2,
        "candidate_top_k": 1024,
        "limit": 8,
    },
    "semantic": {
        "keyword": False,
        "highlight": True,
        "vector_similarity_weight": 1.0,
        "similarity_threshold": 0.2,
        "candidate_top_k": 1024,
        "limit": 8,
    },
    "keyword": {
        "keyword": True,
        "highlight": True,
        "vector_similarity_weight": 0.0,
        "similarity_threshold": 0.1,
        "candidate_top_k": 1024,
        "limit": 8,
    },
    "strict": {
        "keyword": True,
        "highlight": True,
        "vector_similarity_weight": 0.3,
        "similarity_threshold": 0.45,
        "candidate_top_k": 1024,
        "limit": 8,
    },
    "broad": {
        "keyword": True,
        "highlight": True,
        "vector_similarity_weight": 0.3,
        "similarity_threshold": 0.05,
        "candidate_top_k": 2048,
        "limit": 15,
    },
}

GOVERNMENT_SOURCE_HINTS = (
    "政府",
    "委员会",
    "发展改革",
    "生态环境",
    "工信",
    "能源局",
    "国家",
    "标准",
    "gb ",
    "gb-",
    "gb_",
    "gb/",
    "gb",
    "hj ",
    "hj-",
    "hj_",
    "hj/",
    "hj",
    "通知",
    "办法",
    "指南",
    "规范",
    "规程",
    "公告",
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="查询 RAGFlow 知识库中的相关资料。")
    parser.add_argument("query", help="查询问题")
    parser.add_argument(
        "dataset",
        nargs="?",
        help="可选：知识库 ID、完整名称或名称关键词。不填时查询当前账号可访问的全部知识库。",
    )
    parser.add_argument("--mode", choices=sorted(MODE_DEFAULTS), default="hybrid", help="检索模式")
    parser.add_argument("--dataset-ids", help="可选：逗号分隔的知识库 ID")
    parser.add_argument("--dataset-name", help="可选：知识库名称或描述关键词")
    parser.add_argument("--doc-ids", help="可选：逗号分隔的文档 ID")
    parser.add_argument("--document-name", "--file-name", dest="document_name", help="可选：文档名称关键词")
    parser.add_argument(
        "--top-k",
        dest="candidate_top_k",
        type=int,
        help="候选召回数量，传给 RAGFlow top_k；默认由 mode 决定",
    )
    parser.add_argument(
        "--candidate-top-k",
        type=int,
        help="候选召回数量，等同于 --top-k",
    )
    parser.add_argument("--limit", type=int, help="最终返回 chunk 数量；默认由 mode 决定")
    parser.add_argument("--page", type=int, default=DEFAULT_PAGE, help=f"页码，默认 {DEFAULT_PAGE}")
    parser.add_argument(
        "--page-size",
        "--size",
        dest="page_size",
        type=int,
        help="RAGFlow 每页返回数量；默认等于 --limit",
    )
    parser.add_argument("--threshold", type=float, help="相似度阈值，范围 0 到 1；默认由 mode 决定")
    parser.add_argument("--vector-weight", type=float, help="向量相似度权重，范围 0 到 1；默认由 mode 决定")
    parser.add_argument("--keyword", action="store_true", help="显式启用关键词能力")
    parser.add_argument("--no-keyword", action="store_true", help="显式关闭关键词能力")
    parser.add_argument("--highlight", action="store_true", help="显式启用高亮")
    parser.add_argument("--no-highlight", action="store_true", help="显式关闭高亮")
    parser.add_argument("--metadata-condition", help="RAGFlow metadata_condition JSON 对象")
    parser.add_argument("--cross-languages", help="跨语言检索设置，例如 Chinese,English")
    parser.add_argument("--rerank-id", help="可选：重排模型 ID")
    parser.add_argument("--search-id", help="可选：检索会话 ID")
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON")
    add_runtime_config_arguments(parser)
    return parser.parse_args(argv)


def _validate_range(name: str, value: float, *, min_value: float = 0.0, max_value: float = 1.0) -> None:
    if value < min_value or value > max_value:
        raise ConfigError(f"{name} 必须在 {min_value} 到 {max_value} 之间。")


def parse_ids(raw_value: str, *, label: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for item in raw_value.split(","):
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)

    if not values:
        raise ConfigError(f"{label} 至少需要包含一个 ID。")
    return values


def _load_json_object(raw_value: str, option_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{option_name} 必须是合法 JSON：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{option_name} 必须是 JSON 对象。")
    return payload


def _split_languages(raw_value: str | None) -> list[str] | None:
    if not raw_value:
        return None
    values = [item.strip() for item in raw_value.split(",") if item.strip()]
    return values or None


def resolve_dataset_ids(args: argparse.Namespace, *, base_url: str, api_key: str) -> tuple[list[str], dict[str, str]]:
    datasets_payload = list_datasets(base_url=base_url, api_key=api_key)
    datasets = datasets_payload["datasets"]
    dataset_names = {
        str(dataset.get("id")): str(dataset.get("name") or "未命名知识库")
        for dataset in datasets
        if dataset.get("id")
    }

    if args.dataset_ids:
        return parse_ids(args.dataset_ids, label="--dataset-ids"), dataset_names

    dataset_query = args.dataset_name or args.dataset
    if dataset_query:
        dataset = find_dataset(datasets, dataset_query)
        dataset_id = str(dataset.get("id") or "").strip()
        if not dataset_id:
            raise DataError(f"匹配到的知识库缺少 ID：{dataset.get('name') or dataset_query}")
        return [dataset_id], dataset_names

    dataset_ids = [str(dataset.get("id")) for dataset in datasets if dataset.get("id")]
    if not dataset_ids:
        raise DataError("当前账号没有可访问的知识库，无法检索。")
    return dataset_ids, dataset_names


def _list_documents_by_name(
    *,
    base_url: str,
    api_key: str,
    dataset_id: str,
    document_name: str,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    import urllib.parse

    matched: list[dict[str, Any]] = []
    page = 1
    document_keyword = document_name.strip().lower()
    while True:
        query = urllib.parse.urlencode(
            {
                "page": page,
                "page_size": page_size,
                "orderby": "create_time",
                "desc": "true",
            }
        )
        payload = ensure_success(
            request_json(
                f"{base_url}/api/v1/datasets/{urllib.parse.quote(dataset_id, safe='')}/documents?{query}",
                api_key,
            )
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise DataError("文档列表响应缺少 data 对象。")
        docs = data.get("docs")
        total = data.get("total")
        if not isinstance(docs, list):
            raise DataError("文档列表响应缺少 data.docs 数组。")

        matched.extend(
            {**doc, "dataset_id": dataset_id}
            for doc in docs
            if isinstance(doc, dict)
            and (
                document_keyword in str(doc.get("name") or "").lower()
                or document_keyword in str(doc.get("location") or "").lower()
            )
        )
        if not isinstance(total, int) or page * page_size >= total or not docs:
            break
        page += 1
    return matched


def resolve_document_ids(
    args: argparse.Namespace,
    *,
    base_url: str,
    api_key: str,
    dataset_ids: list[str],
    dataset_names: dict[str, str],
) -> tuple[list[str], list[dict[str, Any]]]:
    doc_ids = parse_ids(args.doc_ids, label="--doc-ids") if args.doc_ids else []
    if not args.document_name:
        return doc_ids, []

    document_name = args.document_name.strip()
    if not document_name:
        raise ConfigError("--document-name 不能为空。")

    matches: list[dict[str, Any]] = []
    for dataset_id in dataset_ids:
        matches.extend(
            _list_documents_by_name(
                base_url=base_url,
                api_key=api_key,
                dataset_id=dataset_id,
                document_name=document_name,
            )
        )

    if not matches:
        raise DataError(f"当前可访问知识库未找到名称包含“{document_name}”的文档。")

    resolved_ids = [
        str(document.get("id"))
        for document in matches
        if isinstance(document.get("id"), str) and document.get("id")
    ]
    if not resolved_ids:
        raise DataError(f"匹配到的文档缺少 ID，无法限定检索：{document_name}")

    seen = set(doc_ids)
    for document_id in resolved_ids:
        if document_id not in seen:
            doc_ids.append(document_id)
            seen.add(document_id)

    normalized_matches = [
        {
            "id": document.get("id"),
            "name": document.get("name"),
            "dataset_id": document.get("dataset_id"),
            "dataset_name": dataset_names.get(str(document.get("dataset_id")), "未知知识库"),
            "location": document.get("location"),
        }
        for document in matches
    ]
    return doc_ids, normalized_matches


def _normalize_content(chunk: dict[str, Any]) -> str:
    for key in ("content_with_weight", "content", "answer", "chunk"):
        value = chunk.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(str(item) for item in value)
    return ""


def _normalize_highlight(chunk: dict[str, Any]) -> Any:
    for key in ("highlight", "highlights", "content_ltks"):
        if key in chunk:
            return chunk.get(key)
    return None


def infer_source_type(document_name: str, dataset_name: str) -> str:
    lowered = document_name.lower()
    if any(hint in lowered for hint in GOVERNMENT_SOURCE_HINTS):
        return "政府文件/标准资料"
    if "政策" in dataset_name or "研究报告" in document_name or "报告" in document_name:
        return "外部公开资料/政策研究资料"
    return "项目资料/内部资料"


def normalize_chunk(chunk: dict[str, Any], dataset_names: dict[str, str]) -> dict[str, Any]:
    dataset_id = chunk.get("dataset_id") or chunk.get("kb_id")
    dataset_name = dataset_names.get(str(dataset_id), "未知知识库")
    document_name = chunk.get("document_keyword") or chunk.get("docnm_kwd") or chunk.get("document_name") or ""
    document_id = chunk.get("document_id") or chunk.get("doc_id")
    chunk_id = chunk.get("chunk_id") or chunk.get("id")
    location = chunk.get("location") or chunk.get("docnm_kwd") or chunk.get("image_id")
    return {
        "content": _normalize_content(chunk),
        "highlight": _normalize_highlight(chunk),
        "dataset_name": dataset_name,
        "document_name": document_name or None,
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "positions": chunk.get("positions"),
        "metadata": chunk.get("metadata") or chunk.get("meta_fields"),
        "similarity": chunk.get("similarity"),
        "term_similarity": chunk.get("term_similarity"),
        "vector_similarity": chunk.get("vector_similarity"),
        "location": location,
        "source_type_inferred": infer_source_type(str(document_name), dataset_name),
    }


def extract_chunks(payload: dict[str, Any], dataset_names: dict[str, str]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if data is None:
        return []
    if isinstance(data, dict):
        chunks = data.get("chunks")
        if chunks is None:
            return []
        if not isinstance(chunks, list):
            raise DataError("检索响应 data.chunks 应为数组。")
        return [normalize_chunk(chunk, dataset_names) for chunk in chunks if isinstance(chunk, dict)]
    if isinstance(data, list):
        return [normalize_chunk(chunk, dataset_names) for chunk in data if isinstance(chunk, dict)]
    raise DataError("检索响应 data 应为对象或数组。")


def _mode_config(args: argparse.Namespace) -> dict[str, Any]:
    config = dict(MODE_DEFAULTS[args.mode])
    if args.candidate_top_k is not None:
        config["candidate_top_k"] = args.candidate_top_k
    if args.limit is not None:
        config["limit"] = args.limit
    if args.page_size is not None:
        config["page_size"] = args.page_size
    else:
        config["page_size"] = config["limit"]
    if args.threshold is not None:
        config["similarity_threshold"] = args.threshold
    if args.vector_weight is not None:
        config["vector_similarity_weight"] = args.vector_weight
    if args.keyword:
        config["keyword"] = True
    if args.no_keyword:
        config["keyword"] = False
    if args.highlight:
        config["highlight"] = True
    if args.no_highlight:
        config["highlight"] = False
    return config


def _validate_config(args: argparse.Namespace, config: dict[str, Any]) -> None:
    if int(config["candidate_top_k"]) <= 0:
        raise ConfigError("--top-k/--candidate-top-k 必须大于 0。")
    if int(config["limit"]) <= 0:
        raise ConfigError("--limit 必须大于 0。")
    if int(config["page_size"]) <= 0:
        raise ConfigError("--page-size 必须大于 0。")
    if args.page <= 0:
        raise ConfigError("--page 必须大于 0。")
    _validate_range("--threshold", float(config["similarity_threshold"]))
    _validate_range("--vector-weight", float(config["vector_similarity_weight"]))


def build_retrieval_body(args: argparse.Namespace, dataset_ids: list[str], doc_ids: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    config = _mode_config(args)
    _validate_config(args, config)

    body: dict[str, Any] = {
        "question": args.query,
        "dataset_ids": dataset_ids,
        "top_k": int(config["candidate_top_k"]),
        "similarity_threshold": float(config["similarity_threshold"]),
        "page": args.page,
        "size": int(config["page_size"]),
        "keyword": bool(config["keyword"]),
        "highlight": bool(config["highlight"]),
        "vector_similarity_weight": float(config["vector_similarity_weight"]),
    }
    if doc_ids:
        body["document_ids"] = doc_ids
    if args.metadata_condition:
        body["metadata_condition"] = _load_json_object(args.metadata_condition, "--metadata-condition")
    cross_languages = _split_languages(args.cross_languages)
    if cross_languages:
        body["cross_languages"] = cross_languages
    if args.rerank_id:
        body["rerank_id"] = args.rerank_id
    if args.search_id:
        body["search_id"] = args.search_id

    request_config = {
        "mode": args.mode,
        "candidate_top_k": body["top_k"],
        "limit": int(config["limit"]),
        "page_size": body["size"],
        "similarity_threshold": body["similarity_threshold"],
        "vector_similarity_weight": body["vector_similarity_weight"],
        "keyword": body["keyword"],
        "highlight": body["highlight"],
    }
    return body, request_config


def search(args: argparse.Namespace, *, base_url: str, api_key: str) -> dict[str, Any]:
    dataset_ids, dataset_names = resolve_dataset_ids(args, base_url=base_url, api_key=api_key)
    doc_ids, matched_documents = resolve_document_ids(
        args,
        base_url=base_url,
        api_key=api_key,
        dataset_ids=dataset_ids,
        dataset_names=dataset_names,
    )
    body, request_config = build_retrieval_body(args, dataset_ids, doc_ids)

    payload = ensure_success(
        request_json(
            f"{base_url}/api/v1/retrieval",
            api_key,
            method="POST",
            body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )
    )
    chunks = extract_chunks(payload, dataset_names)[: request_config["limit"]]

    return success_payload(
        {
            "checked_at": current_timestamp(),
            "query": args.query,
            "api": "retrieval",
            "request": request_config,
            "dataset_ids": dataset_ids,
            "datasets": [
                {"id": dataset_id, "name": dataset_names.get(dataset_id, "未知知识库")}
                for dataset_id in dataset_ids
            ],
            "doc_ids": doc_ids,
            "matched_documents": matched_documents,
            "count": len(chunks),
            "chunks": chunks,
        }
    )


def _format_similarity(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.2%}"
    return "未知"


def _format_preview(content: str) -> str:
    compact = " ".join(content.split())
    if not compact:
        return "无摘录"
    if len(compact) <= PREVIEW_LIMIT:
        return compact
    return f"{compact[: PREVIEW_LIMIT - 3]}..."


def _format_text(payload: dict[str, Any]) -> str:
    lines = [
        f"检查时间：{payload['checked_at']}",
        f"查询：{payload['query']}",
        f"模式：{payload['request']['mode']}",
        f"检索知识库：{', '.join(dataset['name'] for dataset in payload['datasets'])}",
        f"命中数量：{payload['count']}",
    ]
    if payload["doc_ids"]:
        lines.append(f"限定文档 ID：{', '.join(payload['doc_ids'])}")
    if payload.get("matched_documents"):
        lines.append(
            "匹配文档："
            + "；".join(
                f"{doc.get('dataset_name') or '未知知识库'} / {doc.get('name') or '未命名文档'}"
                for doc in payload["matched_documents"]
            )
        )

    if payload["count"] == 0:
        lines.append("当前可访问知识库未检索到相关资料。")
        return "\n".join(lines)

    for index, chunk in enumerate(payload["chunks"], start=1):
        lines.extend(
            [
                "",
                f"[{index}] {chunk.get('document_name') or '未知文档'}",
                f"  知识库：{chunk.get('dataset_name') or '未知知识库'}",
                f"  来源类型推断：{chunk.get('source_type_inferred') or '未知'}",
                f"  相似度：{_format_similarity(chunk.get('similarity'))}",
                f"  文档 ID：{chunk.get('document_id') or '未知'}",
                f"  切片 ID：{chunk.get('chunk_id') or '未知'}",
                f"  摘录：{_format_preview(chunk.get('content') or '')}",
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _parse_args(argv)

    try:
        base_url, api_key = resolve_runtime_config(args)
        payload = search(args, base_url=base_url, api_key=api_key)
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
