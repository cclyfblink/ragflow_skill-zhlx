---
name: ragflow-dataset-ingest
description: "仅当用户明确要求查询 RAGFlow、知识库、内部知识库、项目资料库、企业知识库或文档库时使用；支持检索、chunk 上下文展开、按文档读取已解析文本、列知识库/文档，以及显式管理员入库操作。不要用于代码任务、Git 操作、通用问答、网页搜索，或未明确要求查询/维护 RAGFlow 知识库的领域问题。"
metadata:
  requires:
    env:
      - RAGFLOW_API_URL
      - RAGFLOW_API_KEY
    bins:
      - python3
  primaryEnv: RAGFLOW_API_KEY
---

# RAGFlow 知识库查询与入库

这个 skill 用于查询 RAGFlow 知识库中的资料，也提供受控的管理员入库脚本。组织名、项目名、品牌名只表示知识库入口或资料来源标签，不表示政策、标准、通知的发布主体。

优先加 `--json`，便于准确读取字段。对外回答遵循 `reference.md`。

## 触发规则

只有用户明确要求查询或维护 RAGFlow 知识库时才使用本 skill。

可以触发的表达包括：

- 查 RAGFlow 知识库
- 查知识库
- 查内部知识库
- 查企业知识库
- 查项目资料库
- 查文档库
- 查资料库里关于某事项的规定、通知、标准
- 查过往项目资料
- 查内部资料
- 用 RAGFlow 查
- 用知识库查
- 配置 RAGFlow 知识库
- 检查知识库连接
- 上传资料到 RAGFlow
- 上传文件到知识库
- 解析 RAGFlow 文档
- 更新 RAGFlow 文档名称或解析配置
- 直接点名 `$ragflow-dataset-ingest`

不要因为用户只提到普通主题词就自动触发，例如：城市名、客户名、AQI、政策、标准、项目、污染源、报告。

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
- 配置完成后提示用户新开的 agent 会话或终端会自动读取。当前脚本会立即使用新配置测试连接。

检查配置和连接：

```bash
python3 scripts/check_config.py check --json
```

## 使用边界

- 日常问答只做只读查询。
- `read_document.py` 读取 RAGFlow 已解析 chunk 的合并文本，不下载原始 PDF/Word 文件。
- 用户要求下载原始文件时，先说明当前脚本只支持读取已解析文本。
- 上传、解析、更新文档属于管理员入库能力，只有用户明确要求时才使用。
- 不创建、不删除知识库，不删除文档，不停止解析。
- 权限分桶由 RAGFlow 用户和 API key 管理，本 skill 不维护本地 dataset 白名单。
- 除首次配置脚本外，其他脚本只读取环境变量，不持久化任何配置。
- 用户没指定知识库时，默认查询当前 API key 可访问的全部知识库。
- 用户指定知识库名称、主题或文件名时，先在当前可访问知识库中匹配；匹配不明确时说明候选项。
- 政府文件、标准、通知必须按原始来源表述，不能把知识库名称或组织名称写成发布主体。
- 项目、方案、内部材料可以表述为“根据项目资料/内部资料”。
- 没检索到时明确说“当前可访问知识库未检索到相关资料”，不要编造。

## Agent 召回工作流

- 可以把这些脚本理解成本地文件命令的远端版本：
  - `retrieve.py` / `search.py` 类似远端语义版 `rg`，用于先找相关证据。
  - `chunks.py expand` 类似 `rg -C`，用于读取命中片段前后的上下文。
  - `read_document.py` 类似远端 `cat`，用于在确认某个文档很关键后读取该文档的全部 chunk。
  - `read_by_name.py` 类似先远端查文件名再 `cat`，用于用户给出明确文件名但没有给 ID 的场景。
  - `list_documents.py --name` 类似远端文件名查找，用于先定位具体文档 ID。
- 默认知识库查询问题，优先调用 `scripts/retrieve.py "问题" --json`，它会先 hybrid 检索，必要时自动 broad/keyword 重试，并展开关键 chunk 上下文。
- 需要精细控制时，调用 `scripts/search.py "问题" --mode hybrid --json`，低置信再用 `--mode broad`。
- 用户提到文件名、表名、编号、政策名、客户名时，用 `--document-name` 或 `--mode keyword`。
- 用户指定年份、部门、项目、文档类型时，先用 `scripts/datasets.py metadata DATASET_ID --json` 查看可见字段，再带 `--metadata-condition` 检索。
- 最终回答引用关键证据前，可对主要 chunk 调用 `scripts/chunks.py expand`，避免只看孤立片段。
- 如果检索结果显示某个文档是核心来源，且需要通读上下文、提取整份报告结构或核对多个章节，调用 `scripts/read_document.py DATASET_ID DOCUMENT_ID --format markdown --json`。
- 用户给出明确文件名但没有给 ID 时，调用 `scripts/read_by_name.py "文件名关键词" --dataset "知识库名称" --json`；匹配到多个文档时先让用户或上下文进一步限定。
- `read_document.py` 默认返回合并内容和轻量 chunk 目录；只有需要逐 chunk 核对时才加 `--include-chunks`。
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
python3 scripts/read_document.py DATASET_ID DOCUMENT_ID --format markdown --max-chars 80000 --json
python3 scripts/read_document.py DATASET_ID DOCUMENT_ID --format markdown --include-chunks --json
python3 scripts/read_by_name.py "文件名关键词" --dataset "知识库名称关键词" --json
python3 scripts/read_document.py DATASET_ID DOCUMENT_ID --format text --output "C:\Users\用户名\AppData\Local\Temp\ragflow-document.txt" --json
python3 scripts/datasets.py list --json
python3 scripts/datasets.py info "知识库名称或ID" --json
python3 scripts/datasets.py metadata DATASET_ID --json
python3 scripts/list_documents.py DATASET_ID --json
python3 scripts/list_documents.py DATASET_ID --name "文件名关键词" --json
```

管理员入库：

```bash
python3 scripts/upload.py "知识库名称或ID" --root "D:\资料库根目录" --source "待上传子目录" --dry-run --json
python3 scripts/upload.py "知识库名称或ID" --root "D:\资料库根目录" --source "待上传子目录" --batch-size 5 --name-separator "-" --chunk-method naive --parser-config "{\"chunk_token_num\":512,\"delimiter\":\"\n\",\"layout_recognize\":\"DeepDOC\"}" --start-parse --json
python3 scripts/upload.py "知识库名称或ID" --root "D:\资料库根目录" --source "待上传子目录" --meta-fields "{\"source_path\":\"资料分类/年份/文件名.pdf\",\"content_description\":\"这批文件的完整内容说明\",\"topic\":\"主题\",\"year\":\"年份\",\"document_type\":\"资料类型\"}" --json
python3 scripts/parse_status.py DATASET_ID --json
python3 scripts/parse.py DATASET_ID DOC_ID1 DOC_ID2 --json
python3 scripts/update_document.py DATASET_ID DOC_ID --name "新文档名.pdf" --json
```

入库规则：

- 上传前先运行 `scripts/upload.py ... --dry-run --json`，确认待上传数量、样例文件名和跳过项。
- 上传目录时按批次执行，优先一个资料文件夹一批；解析完成并测试召回后再传下一个文件夹。
- 文档名使用相对路径拼接，默认用 `-` 连接路径片段，例如 `资料分类-年份-文件名.pdf`；需要其他连接符时使用 `--name-separator`。
- 入库时尽量让远端文档名自己带有来源信息，优先包含资料分类、子目录、年份、版本、文件名；不要只用 `报告.pdf`、`扫描件.pdf` 这类无法判断来源的名称。
- 批量上传时优先使用 `--meta-fields` 写入资料说明。常用字段包括 `source_path`、`content_description`、`topic`、`year`、`document_type`、`publisher`。`content_description` 应说明这批文件主要包含什么内容、覆盖年份或地区、适合回答什么问题。
- 如果每个文件需要不同描述，按文件或小批次分别上传；不要为了省事给完全不同主题的文件写同一段笼统描述。
- 默认只上传当前 RAGFlow 稳定可解析格式：`pdf/doc/docx/xlsx/txt`。旧版 `xls` 建议先另存或转换为 `xlsx` 后再入库。
- 需要指定切片或解析方式时，使用 `--chunk-method` 和 `--parser-config`；上传后立即解析时再加 `--start-parse`。
- 默认跳过知识库中已存在的同名文档；只有用户明确要求重复上传时才加 `--allow-duplicates`。
- 不上传压缩包、CAJ/NH、图片、网页资源、程序文件、数据库文件。
- 大文件建议先用 `--max-mb` 控制范围，解析稳定后再放宽。

## 回答要求

- 先给结论，再列来源。
- 说明命中的知识库名、文档名和来源类型。
- `source_type_inferred` 只是脚本推断，不能替代原文发布主体或文件来源。
- 对政策、标准、通知，使用“根据某某文件/某某部门发布的文件”。
- 对项目、方案、内部材料，使用“根据项目资料/内部资料”。
- 保留 API 返回的关键错误信息，不猜测不存在的字段。
