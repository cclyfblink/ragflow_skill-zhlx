# ragflow-dataset-ingest

Codex skill for RAGFlow knowledge base retrieval and controlled dataset ingest.

这个仓库提供一个 RAGFlow 知识库查询与入库 skill，适合让 agent 通过 CLI 工具查询远端知识库、展开 chunk 上下文、读取已解析文档文本，并在明确要求时执行管理员入库操作。

## 主要能力

- 查询 RAGFlow 知识库，支持多知识库检索、按知识库名或文档名过滤。
- 展开命中 chunk 前后文，读取已解析文档的完整文本。
- 批量上传目录或文件到 RAGFlow dataset。
- 上传时支持路径拼接命名、解析配置、元数据描述和自动解析。
- 默认只做只读查询，入库操作需要用户明确要求。

## 配置

首次使用需要配置环境变量：

```bash
RAGFLOW_API_URL
RAGFLOW_API_KEY
```

也可以使用 skill 内置脚本写入用户级环境变量：

```bash
python3 scripts/check_config.py configure --api-url "https://your-ragflow.example" --api-key "ragflow-xxx"
```

API Key 不应写入仓库文件、日志或公开说明。

## 常用命令

```bash
python3 scripts/check_config.py check --json
python3 scripts/retrieve.py "查询问题" --json
python3 scripts/search.py "查询问题" --mode hybrid --json
python3 scripts/chunks.py expand DATASET_ID DOCUMENT_ID CHUNK_ID --before 2 --after 2 --json
python3 scripts/read_document.py DATASET_ID DOCUMENT_ID --format markdown --json
```

管理员入库示例：

```bash
python3 scripts/upload.py "知识库名称或ID" --root "D:\资料库根目录" --source "待上传子目录" --dry-run --json
python3 scripts/upload.py "知识库名称或ID" --root "D:\资料库根目录" --source "待上传子目录" --name-separator "-" --meta-fields "{\"content_description\":\"资料内容说明\",\"topic\":\"主题\"}" --start-parse --json
```

上传资料时建议让远端文档名包含目录、年份、版本和文件名，并通过 `--meta-fields` 写入 `source_path`、`content_description`、`topic`、`year`、`document_type` 等字段。

## 安装

可以从 GitHub Release 下载 zip 包，解压到 Codex skills 目录后使用。也可以直接把仓库内容放入本地 skill 目录。

## License

MIT
