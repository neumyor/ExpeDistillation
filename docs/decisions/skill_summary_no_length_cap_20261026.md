# 决定：取消 Skill.summary 的 500 字符硬上限

日期：2026-10-26
一句话：删除 `SKILL_SUMMARY_MAX_CHARS = 500` 及三处写入校验里的长度检查；summary 仍要求非空、单行，但不再有长度上限。
对应 log.md：2026-10-26 的 summary 上限去除与 `[Verify]` 段删除条目。

## 用户裁决

> 去除 summary 500 字符硬限制。

## 变更

- `core/models.py`：删除 `SKILL_SUMMARY_MAX_CHARS` 常量；`Skill.summary` 的注释改为“单行、无换行”，不再提长度。
- `blocks/author.py`、`blocks/attribution.py`、`blocks/curate.py`：`_validate_summary` / `_validate_merge_summary` 删除 `len(summary) > 500` 的拒绝分支；保留非空、单行（`\r`/`\r\n` 归一后不许出现换行）两条格式约束。
- 测试：删除 501 字符被拒的参数化用例；新增 5,000 字符 summary 在 G2、G6、G8 和三处合并路径都能通过并被原样保存的用例。
- 文档：README 的 Skill 字段表写明 summary“非空、单行、无长度上限”。

## 影响

- 模型仍被 prompt 要求写一句话摘要，但不再因为超长被当成 `ModelOutputError` 终止单元；超长摘要会原样进入目录行。
- 目录渲染仍只取单行；如果摘要过长，展示层自行处理，不在写入侧截断或拒绝。
- 不保留旧行为兼容；旧 run 中已写入的摘要不受影响。
