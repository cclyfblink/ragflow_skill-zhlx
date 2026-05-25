#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from typing import Any
from unittest import mock

import chunks
import list_documents
import search


def _args(**overrides: Any) -> argparse.Namespace:
    values = {
        "query": "问题",
        "dataset": None,
        "mode": "hybrid",
        "dataset_ids": "ds1",
        "dataset_name": None,
        "doc_ids": None,
        "document_name": None,
        "candidate_top_k": None,
        "limit": None,
        "page": 1,
        "page_size": None,
        "threshold": None,
        "vector_weight": None,
        "keyword": False,
        "no_keyword": False,
        "highlight": False,
        "no_highlight": False,
        "metadata_condition": None,
        "cross_languages": None,
        "rerank_id": None,
        "search_id": None,
        "json_output": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_search_mode_mapping() -> None:
    captured: dict[str, Any] = {}

    def fake_request_json(url: str, api_key: str, **kwargs: Any) -> dict[str, Any]:
        captured["url"] = url
        captured["body"] = kwargs["body"]
        return {
            "code": 0,
            "data": {
                "chunks": [
                    {
                        "id": "chunk1",
                        "dataset_id": "ds1",
                        "document_id": "doc1",
                        "document_keyword": "doc.pdf",
                        "content": "正文",
                        "highlight": "<em>正文</em>",
                        "similarity": 0.5,
                    }
                ]
            },
        }

    with mock.patch.object(search, "list_datasets", return_value={"datasets": [{"id": "ds1", "name": "库"}]}):
        with mock.patch.object(search, "request_json", side_effect=fake_request_json):
            payload = search.search(_args(metadata_condition='{"year":"2024"}', cross_languages="Chinese,English"), base_url="http://x", api_key="k")

    body_text = captured["body"].decode("utf-8")
    assert '"top_k": 1024' in body_text
    assert '"size": 8' in body_text
    assert '"keyword": true' in body_text
    assert '"highlight": true' in body_text
    assert '"metadata_condition": {"year": "2024"}' in body_text
    assert '"cross_languages": ["Chinese", "English"]' in body_text
    assert payload["ok"] is True
    assert payload["chunks"][0]["chunk_id"] == "chunk1"
    assert "raw" not in payload["chunks"][0]


def test_list_documents_name_scans_pages() -> None:
    calls = []

    def fake_request_json(url: str, api_key: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(url)
        docs = [{"id": "a", "name": "第一页.pdf"}] if len(calls) == 1 else [{"id": "b", "name": "目标文件.pdf"}]
        return {"code": 0, "data": {"docs": docs, "total": 101}}

    args = argparse.Namespace(
        dataset_id="ds1",
        page=1,
        page_size=10,
        orderby="create_time",
        asc=False,
        keywords=None,
        document_id=None,
        name="目标",
        suffix=None,
        run=None,
        json_output=True,
    )
    with mock.patch.object(list_documents, "request_json", side_effect=fake_request_json):
        payload = list_documents.list_documents(args, base_url="http://x", api_key="k")

    assert len(calls) == 2
    assert payload["total"] == 1
    assert payload["documents"][0]["id"] == "b"


def test_chunks_expand() -> None:
    def fake_request_json(url: str, api_key: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "code": 0,
            "data": {
                "chunks": [
                    {"id": "c1", "content": "前"},
                    {"id": "c2", "content": "中"},
                    {"id": "c3", "content": "后"},
                ],
                "total": 3,
            },
        }

    with mock.patch.object(chunks, "request_json", side_effect=fake_request_json):
        payload = chunks.expand_chunk("ds1", "doc1", "c2", before=1, after=1, page_size=100, base_url="http://x", api_key="k")

    assert payload["ok"] is True
    assert payload["target_index"] == 1
    assert [item["chunk_id"] for item in payload["chunks"]] == ["c1", "c2", "c3"]


def main() -> int:
    test_search_mode_mapping()
    test_list_documents_name_scans_pages()
    test_chunks_expand()
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
