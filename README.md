# AeroNav Papers

面向具身导航研究者的每日论文情报站。项目使用 GitHub Actions 定时抓取公开论文元数据，生成静态 JSON，并由 GitHub Pages 展示。

## 功能

- 覆盖三条板块十二条技术线：空域自主（UAV 目标搜索、空域 VLN）、地面导航（ObjectNav、地面 VLN）、基础能力（空间智能、世界模型、多模态大模型与智能体、规划智能体、具身基准与评测、真机与 Sim2Real、主动感知与重建、状态估计与控制——SLAM/里程计/轨迹规划等落地核心，高影响力工作收录、常规增量拒绝）
- 三层质量漏斗：规则粗筛（主题词+动作词根）→ LLM 评审门（相关度/严谨度 0-10 打分，不达标不入库）→ 元数据加分（顶会白名单核验、开源代码）
- arXiv 与 Semantic Scholar 元数据聚合、按 DOI/arXiv ID/标题去重
- 中文精炼研判：核心变化、证据质量、与研究方向的关联
- 主题、来源、证据等级、质量档、关键词筛选
- 每日自动更新，并保留最近 180 天的数据
- 经典必读库：近五年高被引论文（门槛 = 40 × max(1, 距今年数)，随年份自动滚动），按引用数排序、单独 Tab 展示、中文研判一次写成永久复用
- 隐形访问统计（不蒜子）：访客计数不展示在前台，访问量在 `/stats.html` 查看（站内无链接、noindex）
- 简报推送：每次有新论文入库时，GLM 额外生成一段「今日速览」写入 `data/*.json` 并展示在页面顶部，同时经 Server酱 推送到微信（Secret `SERVERCHAN_SENDKEY`，未配置则静默跳过；空跑不推送，免费额度 5 条/天足够）

## 部署到 GitHub Pages

1. 新建 GitHub 仓库，将本目录全部文件推送到默认分支。
2. 在仓库 `Settings → Pages` 中，将 Source 设为 **GitHub Actions**。
3. 在 `Settings → Secrets and variables → Actions` 添加：
   - `OPENAI_API_KEY`：可选但推荐，用于生成中文研判。
   - `S2_API_KEY`：可选，可提高 Semantic Scholar API 限额。
   - `SERVERCHAN_SENDKEY`：可选，[Server酱](https://sct.ftqq.com) 微信扫码获取，用于把每次更新的一段式中文简报推送到微信。
   - 使用智谱 GLM 等兼容 OpenAI 的服务时：`OPENAI_API_KEY` 填服务方的 key，并额外添加 `OPENAI_BASE_URL` 和 `OPENAI_MODEL`（如 `glm-5.2`）。注意：智谱 **Coding Plan 订阅 key** 只在专属端点 `https://open.bigmodel.cn/api/coding/paas/v4/` 可用，通用端点 `api/paas/v4` 会报"余额不足"；按量付费 key 则用通用端点。
4. 打开 `Actions`，手动运行一次 **Update papers and deploy**。

工作流默认每天北京时间凌晨 01:00 更新，03:00 补跑一次兜底（增量幂等，主跑成功则补跑空转）。GitHub Actions 的 cron 使用 UTC，因此配置为 `17:00` 与 `19:00 UTC`。

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

如果不提供 `OPENAI_API_KEY`，脚本仍会更新论文信息，但中文简介使用基于标题与摘要的保守模板，不会生成未经来源支持的判断。换用其他 OpenAI 兼容服务时，设置环境变量 `OPENAI_BASE_URL` 与 `OPENAI_MODEL` 即可，例如智谱 GLM：

```bash
export OPENAI_API_KEY=你的GLM密钥
# Coding Plan 订阅 key 用下面这个端点；按量付费 key 改为 https://open.bigmodel.cn/api/paas/v4/
export OPENAI_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4/
export OPENAI_MODEL=glm-5.2
python scripts/update_papers.py
```

## 调整关注范围

编辑 `config/topics.json`：

- `queries`：检索式
- `topics_catalog`：技术线目录（板块分组），GLM 归类与前端筛选都以此为准
- `scope_note`：收录范围定义（正负面清单），是 GLM 评审门的判据
- `keywords`：相关性评分词（粗筛与无 API 时的兜底分类）
- `venue_allowlist`：重点会议与期刊
- `max_new_per_run`：每日最多新增数量

## 数据说明

`data/papers.json` 是网站的唯一数据源。自动研判仅基于公开元数据与摘要，不等同于阅读全文；证据等级会区分预印本、已发表论文、是否有代码，以及实验描述是否充分。

### 质量筛选机制

新论文经过三层漏斗才会入库：

1. **规则粗筛**（零成本）：关键词相关分需达到 `min_relevance_score`，且标题或摘要必须命中 `action_stems` 中的动作词根（navigat/search/explor 等），排除仅靠术语擦边的论文；关键词相关分达到 `bypass_stems_score` 的强信号论文（如多模态大模型/智能体旗舰工作）可免此限制。
2. **LLM 评审门**：GLM 对照 `scope_note` 定义的研究范围输出 `relevance`（相关度 0-10）与 `rigor`（严谨度 0-10），低于 `gate.min_relevance`/`gate.min_rigor` 的论文**不入库**，连同拒绝理由存档到 `data/rejected.json`，误杀可人工复查（删除对应条目后下次运行会重新评审）。
3. **元数据加分**：发表来源命中 `venue_allowlist` 加 2 分，摘要含开源代码信号加 1 分；综合分 = relevance×2 + rigor + 加分，决定列表排序与"精选（相关≥8）/常规"分档。

未配置 `OPENAI_API_KEY` 时评审门自动跳过，仅保留规则粗筛。调整松紧改 `config/topics.json` 中的 `gate` 与 `min_relevance_score`。

### 经典必读库（`scripts/update_classics.py`）

独立于每日更新，每周运行一次（周三凌晨 04:00，可手动触发），规则：

- **候选发现与筛选零 LLM 成本**：OpenAlex 检索各技术线近五年论文（免 key、限额宽松），纯按引用数门槛过滤（当年与去年 ≥40，更早的每年递增 40，即 40 × max(1, 距今年数)）；
- **每篇经典只评审一次**：结果写入 `data/classics.json` 永久复用；已在每日库中的论文升级为经典时直接复用现有中文研判（零新增调用）；
- **每次运行最多评审 `classic.max_new_per_run` 篇**（默认 30，按引用数从高到低入场），首轮积压会分摊到后续运行；想加快建设速度，手动多触发几次该 workflow 或临时调大该值；
- 超龄论文（超过 `classic.max_age` 年）随年份滚动自动移出；范围外的著名论文记入 `excluded` 不再重复评审。
