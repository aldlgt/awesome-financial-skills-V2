贡献指南（精要）

1. 提交格式
   - 在对应分类文件末尾新增条目，格式：
     - `- [名称](链接) — 1 行描述。来源：xxx。最后更新时间：YYYY‑MM‑DD`
   - 每个条目务必说明：该 skill 的目标、主要数据/方法来源、是否有可运行代码或 notebook。

2. 质量要求
   - 聚焦“精而专”：优先收录可复用的模板、脚本、方法论、或高质量数据汇编。
   - 非 AI 生成（人工或社区编写）。若利用 AI 辅助，须注明并经人工校验。
   - 自动发现结果需同时满足：金融相关 + 专题相关 + 评分达标；勿直接合入未审核候选。

3. 自动发现与 candidate PR
   - CI 按专题发现候选（每专题约 10 个优质结果），创建 candidate PR 或写入 `proposals/`。
   - 本地调试：`TOPIC_IDS=credit python tools/fetch_candidates.py`
   - 若不同意自动发 PR，可在仓库设置中禁用该工作流，或保持 `DRY_RUN=1`。

4. 调参建议（准确率）
   - 提高准确率：增大 `global.min_score`，收紧专题 `topic_signals` / `search_queries`
   - 提高召回：增大 `max_fetch_per_topic`，略降 `min_score`，补充专题关键词
   - 查看 `candidates/rejected_sample.json` 排查误杀/漏检

5. 维护与清理
   - 每条目请保留 metadata（source、first_seen、last_checked、maintainer）。
   - 建议每季度人工审查一次，移除不再维护或重复项。
