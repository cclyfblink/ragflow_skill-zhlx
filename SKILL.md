---
name: ragflow-dataset-ingest
description: "仅当用户明确要求查询智慧绿行、绿行、公司内部资料、公司知识库、RAGFlow 知识库或 zhlx 知识库时使用；也可在用户明确要求上传、解析或更新 RAGFlow 文档时作为管理员入库工具使用。不要用于代码任务、Git 操作、通用问答、网页搜索，或未明确要求查询/维护内部知识库的领域问题。"
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

# 智慧绿行内部知识库查询与入库

这个 skill 默认用于查询智慧绿行内部知识库中的资料，也提供受控的管理员入库脚本。智慧绿行/绿行只表示知识库入口或内部资料来源，不表示政策、标准、通知的发布主体。

优先加 `--json`，便于准确读取字段。对外回答遵循 `reference.md`。

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
- 配置智慧绿行知识库
- 配置绿行知识库
- 检查知识库连接
- 上传资料到 RAGFlow
- 上传文件到公司知识库
- 解析 RAGFlow 文档
- 更新 RAGFlow 文档名称或解析配置
- 直接点名 `$ragflow-dataset-ingest`

不要因为用户只提到普通主题词就自动触发，例如：通州、AQI、零碳园区、政策、标准、项目、污染源。

## 首次配置

如果用户明确要求配置本 skill，并提供 RAGFlow API 地址和 API Key，可以使用配置脚本写入 Windows 用户级环境变量：

```bash
python3 scripts/check_config.py configure --api-url "RAGFLOW_API_URL" --api-key "RAGFLOW_API_KEY"
```

配置规则：

- 只允许写入用户级环境变量 `RAGFLOW_API_URL` 和 `RAGFLOW_API_KEY`。
- 不要把 API Key 写入仓库文件、配置 JSON、日志、回答正文或临时文件。
- 不要回显完整 API Key；只能说已配置，最多显示尾号。
- 如果用户级环境变量已存在，不要覆盖，除非用户明确要求覆盖；覆盖时使用 `--force`。
- 配置完成后提示用户新开的 Codex/终端会自动读取。当前脚本会立即使用新配置测试连接。

检查配置和连接：

```bash
python3 scripts/check_config.py check --json
```

## 使用边界

- 日常问答只做只读查询。
- 上传、解析、更新文档属于管理员入库能力，只有用户明确要求时才使用。
- 不创建、不删除知识库，不删除文档，不停止解析。
- 权限分桶由 RAGFlow 用户和 API key 管理，本 skill 不维护本地 dataset 白名单。
- 除首次配置脚本外，其他脚本只读取环境变量，不持久化任何配置。
- 用户没指定知识库时，默认查询当前 API key 可访问的全部知识库。
- 用户指定知识库名称、主题或文件名时，先在当前可访问知识库中匹配；匹配不明确时说明候选项。
- 政府文件、标准、通知必须按原始来源表述，不能写成“绿行规定”。
- 公司项目资料可以表述为“根据智慧绿行项目资料/内部资料”。
- 没检索到时明确说“当前可访问知识库未检索到相关资料”，不要编造。

## Agent 召回工作流

- 默认内部资料问题，优先调用 `scripts/retrieve.py "问题" --json`，它会先 hybrid 检索，必要时自动 broad/keyword 重试，并展开关键 chunk 上下文。
- 需要精细控制时，调用 `scripts/search.py "问题" --mode hybrid --json`，低置信再用 `--mode broad`。
- 用户提到文件名、表名、编号、政策名、客户名时，用 `--document-name` 或 `--mode keyword`。
- 用户指定年份、部门、项目、文档类型时，先用 `scripts/datasets.py metadata DATASET_ID --json` 查看可见字段，再带 `--metadata-condition` 检索。
- 最终回答引用关键证据前，可对主要 chunk 调用 `scripts/chunks.py expand`，避免只看孤立片段。
- RAGFlow 检索结果和本地代码/文件冲突时，本地文件代表当前实现事实，RAGFlow 作为背景资料来源。

## 常用命令

查询：

```bash
python3 scripts/check_config.py check --json
python3 scripts/retrieve.py "查询问题" --json
python3 scripts/retrieve.py "查询问题" --dataset-name "统计年鉴" --json
python3 scripts/search.py "查询问题" --mode hybrid --json
python3 scripts/search.py "查询问题" --mode broad --dataset-name "知识库名称关键词" --json
python3 scripts/search.py "查询问题" --mode keyword --document-name "文件名关键词" --json
python3 scripts/search.py "查询问题" --metadata-condition "{\"year\":\"2024\"}" --cross-languages "Chinese,English" --json
python3 scripts/chunks.py expand DATASET_ID DOCUMENT_ID CHUNK_ID --before 2 --after 2 --json
python3 scripts/datasets.py list --json
python3 scripts/datasets.py info "知识库名称或ID" --json
python3 scripts/datasets.py metadata DATASET_ID --json
python3 scripts/list_documents.py DATASET_ID --json
python3 scripts/list_documents.py DATASET_ID --name "文件名关键词" --json
```

管理员入库：

```bash
python3 scripts/upload.py "知识库名称或ID" --root "\\192.168.23.238\Share\共享数据\统计年鉴" --source "电力工业统计资料汇编" --dry-run --json
python3 scripts/upload.py "知识库名称或ID" --root "\\192.168.23.238\Share\共享数据\统计年鉴" --source "电力工业统计资料汇编" --batch-size 5 --start-parse --json
python3 scripts/parse_status.py DATASET_ID --json
python3 scripts/parse.py DATASET_ID DOC_ID1 DOC_ID2 --json
python3 scripts/update_document.py DATASET_ID DOC_ID --name "新文档名.pdf" --json
```

入库规则：

- 上传前先运行 `scripts/upload.py ... --dry-run --json`，确认待上传数量、样例文件名和跳过项。
- 上传目录时按批次执行，优先一个资料文件夹一批，例如先传 `电力工业统计资料汇编`，解析完成并测试召回后再传下一个文件夹。
- 文档名使用相对路径拼接：`统计年鉴` 下的路径片段用 `__` 连接，例如 `电力工业统计资料汇编__电力工业统计资料汇编2021__电力工业统计资料汇编2021 可复制数据.pdf`。
- 默认只上传可解析格式：`pdf/doc/docx/xls/xlsx/txt/md`。
- 默认跳过知识库中已存在的同名文档；只有用户明确要求重复上传时才加 `--allow-duplicates`。
- 不上传压缩包、CAJ/NH、图片、网页资源、程序文件、数据库文件。
- 大文件建议先用 `--max-mb` 控制范围，解析稳定后再放宽。

## 回答要求

- 先给结论，再列来源。
- 说明命中的知识库名、文档名和来源类型。
- `source_type_inferred` 只是脚本推断，不能替代原文发布主体或文件来源。
- 对政策、标准、通知，使用“根据某某文件/某某部门发布的文件”。
- 对项目、方案、内部材料，使用“根据智慧绿行项目资料/内部资料”。
- 保留 API 返回的关键错误信息，不猜测不存在的字段。
