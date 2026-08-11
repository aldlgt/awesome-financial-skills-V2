# Awesome Financial Skills

聚合、整理并定期更新「精而专」的金融 skill（研究方法、数据检索、知识库、指标查询、交易/定价模型等），按研究类与数据类组织，面向投研、信用与量化研究者。

## 目标

- 按**专题**发现金融 skill（产业链、生物医药、信用、量化交易、金融产品、数据聚合等）
- 每个专题在评分筛选后保留约 **10 个**高质量结果（先多抓、再精选）
- 通过 CI 产出候选清单与代码包，经人工审核后合入分类文档

## 目录

- 研究类
  - 产业研究 — `categories/research-industrial.md`
  - 投资交易 — `categories/research-trading.md`
  - 信用研究 — `categories/research-credit.md`
  - 金融产品研究 — `categories/research-products.md`
- 数据类
  - 聚合搜索 — `categories/data-aggregator.md`
  - 指标查询 — `categories/data-indicators.md`
  - 知识库 — `categories/data-knowledge.md`

## 自动发现流水线

配置文件：`search_params.json`（按 `topics` 专题检索 + 黑名单 + 金融/技能信号词）

```text
search_params.json
        │
        ▼
tools/fetch_candidates.py   # 专题搜索 → 评分 → 每专题保留 ~10 个
        │
        ├─ candidates/by_topic/<topic_id>.json
        └─ candidates/all_candidates.json
        │
        ▼
tools/package_skills.py     # 按专题下载并解压到 artifacts/skills/<topic_id>/
        │
        ▼
tools/create_candidate_prs.py / tools/apply_candidates.py
```

评分要求同时满足：

1. 命中金融信号词（`finance_signals`）
2. 命中该专题信号词（`topic_signals`）
3. 综合分 ≥ `global.min_score`
4. 未命中黑名单

### 本地运行

```bash
pip install -r requirements.txt
set GITHUB_TOKEN=ghp_xxx          # PowerShell: $env:GITHUB_TOKEN="ghp_xxx"

# 跑全部专题（每专题精选约 10 个）
python tools/fetch_candidates.py

# 只跑信用 + 生物医药
set TOPIC_IDS=credit,biopharma
python tools/fetch_candidates.py

# 下载精选仓库
python tools/package_skills.py
```

常用环境变量：

| 变量 | 含义 | 默认 |
|------|------|------|
| `TOPIC_IDS` | 逗号分隔专题 id | 全部 |
| `TARGET_PER_TOPIC` | 每专题保留数量 | 10 |
| `MAX_RESULTS` | 每专题搜索扫描上限 | 80 |
| `DRY_RUN` | `1` 只预览不改库 | 1 |

专题 id 见 `search_params.json` → `topics[].id`，例如：`industry_chain`、`biopharma`、`credit`、`trading_quant`。

### GitHub Actions

工作流：`.github/workflows/main.yml`

- 可手动触发，并指定 `topic_ids` / `target_per_topic` / `dry_run`
- 产物 artifact：`skills_archive`（按专题分目录）

## 条目格式

```markdown
- [skill-name](链接) — 一行简述。来源：xxx。最后更新时间：YYYY-MM-DD。
```

贡献与质量门槛见 [CONTRIBUTING.md](CONTRIBUTING.md)。
