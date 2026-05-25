---
name: ragflow-dataset-ingest
description: "仅当用户明确要求查询智慧绿行、绿行、公司内部资料、公司知识库、RAGFlow 知识库或 zhlx 知识库时使用。用于只读检索当前 RAGFlow 账号可访问的知识库资料；不要用于代码任务、Git 操作、通用问答、网页搜索，或未明确要求查询内部知识库的领域问题。"
metadata:
  openclaw:
    requires:
      env:
        - RAGFLOW_API_URL
        - RAGFLOW_API_KEY
      bins:
        - python3
    primaryEnv: RAGFLOW_API_KEY
---

# 智慧绿行内部知识库查询

这个 skill 只用于查询智慧绿行内部知识库中的资料。智慧绿行/绿行只表示知识库入口或内部资料来源，不表示政策、标准、通知的发布主体。

只能使用 `scripts/` 里的只读脚本。优先加 `--json`，便于准确读取字段。对外回答遵循 `reference.md`。

## 触发规则

只有用户明确要求查询内部知识库时才使用本 skill。

可以触发的表达包括：

- 查智慧绿行知识库
- 查绿行知识库
- 查绿行资料里关于某事项的规定、通知、标准
- 查智慧绿行过往项目
- 查绿行项目资料
- 查公司知识库
- 查内部资料
- 用 RAGFlow 查
- 用 zhlx 知识库查
- 直接点名 `$ragflow-dataset-ingest`

不要因为用户只提到普通主题词就自动触发，例如：通州、AQI、零碳园区、政策、标准、项目、污染源。

## 使用边界

- 只读查询，不创建、不上传、不更新、不删除、不启动解析、不停止解析。
- 权限分桶由 RAGFlow 用户和 API key 管理，本 skill 不维护本地 dataset 白名单。
- 用户没指定知识库时，默认查询当前 API key 可访问的全部知识库。
- 用户指定知识库名称、主题或文件名时，先在当前可访问知识库中匹配；匹配不明确时说明候选项。
- 政府文件、标准、通知必须按原始来源表述，不能写成“绿行规定”。
- 公司项目资料可以表述为“根据智慧绿行项目资料/内部资料”。
- 没检索到时明确说“当前可访问知识库未检索到相关资料”，不要编造。

## 常用命令

```bash
python3 scripts/search.py "查询问题" --json
python3 scripts/search.py "查询问题" "知识库名称或ID" --json
python3 scripts/search.py "查询问题" --dataset-name "知识库名称关键词" --json
python3 scripts/search.py "查询问题" --document-name "文件名关键词" --json
python3 scripts/search.py "查询问题" --dataset-ids DATASET_ID1,DATASET_ID2 --json
python3 scripts/search.py "查询问题" --doc-ids DOC_ID1,DOC_ID2 --json
python3 scripts/datasets.py list --json
python3 scripts/datasets.py info "知识库名称或ID" --json
python3 scripts/list_documents.py DATASET_ID --json
python3 scripts/list_documents.py DATASET_ID --name "文件名关键词" --json
```

## 回答要求

- 先给结论，再列来源。
- 说明命中的知识库名、文档名和来源类型。
- 对政策、标准、通知，使用“根据某某文件/某某部门发布的文件”。
- 对项目、方案、内部材料，使用“根据智慧绿行项目资料/内部资料”。
- 保留 API 返回的关键错误信息，不猜测不存在的字段。
