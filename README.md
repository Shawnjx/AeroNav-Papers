# AeroNav Papers

面向 UAV 主动目标搜索与 ObjectNav 的每日论文情报站。项目使用 GitHub Actions 定时抓取公开论文元数据，生成静态 JSON，并由 GitHub Pages 展示。

## 功能

- 聚焦 UAV active target search、ObjectNav、belief/semantic mapping、active search planning、VLM planning、multi-robot exploration
- arXiv 与 Semantic Scholar 元数据聚合、按 DOI/arXiv ID/标题去重
- 中文精炼研判：核心变化、证据质量、与你研究的关系
- 主题、来源、证据等级、关键词筛选
- 每日自动更新，并保留最近 180 天的数据

## 部署到 GitHub Pages

1. 新建 GitHub 仓库，将本目录全部文件推送到默认分支。
2. 在仓库 `Settings → Pages` 中，将 Source 设为 **GitHub Actions**。
3. 在 `Settings → Secrets and variables → Actions` 添加：
   - `OPENAI_API_KEY`：可选但推荐，用于生成中文研判。
   - `S2_API_KEY`：可选，可提高 Semantic Scholar API 限额。
4. 打开 `Actions`，手动运行一次 **Update papers and deploy**。

工作流默认每天北京时间 07:30 更新。GitHub Actions 的 cron 使用 UTC，因此配置为 `23:30 UTC`。

## 本地预览

```bash
python -m http.server 8000
```

然后访问 `http://localhost:8000`。

## 手动更新数据

```bash
python -m pip install -r requirements.txt
python scripts/update_papers.py
```

如果不提供 `OPENAI_API_KEY`，脚本仍会更新论文信息，但中文简介使用基于标题与摘要的保守模板，不会生成未经来源支持的判断。

## 调整关注范围

编辑 `config/topics.json`：

- `queries`：检索式
- `keywords`：相关性评分词
- `venue_allowlist`：重点会议与期刊
- `max_new_per_run`：每日最多新增数量

## 数据说明

`data/papers.json` 是网站的唯一数据源。自动研判仅基于公开元数据与摘要，不等同于阅读全文；证据等级会区分预印本、已发表论文、是否有代码，以及实验描述是否充分。
