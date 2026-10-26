# 决定：技能结构从四段改为三段，彻底删除 `[Verify]` 段

日期：2026-10-26
一句话：删除技能正文里的 `[Verify]` 段及其写作字段、校验、测试和文档；技能只保留 `[When to use]` / `[Steps]` / `[Do not use when]` 三段，不保留旧版本兼容。
对应 log.md：2026-10-26 的 summary 上限去除与 `[Verify]` 段删除条目。

## 用户裁决

> 删除 skill 中的 `[Verify]` 段。我说的删除是完全删除，包含生成 `[Verify]` 的相关写作 prompt、相关的校验、相关的单元测试、相关的文档等，且无需保留和上一个版本的兼容。

## 变更

- `core/models.py`：`SKILL_MARKERS` 从四元组改为三元组，删除 `[Verify]`。
- `blocks/author.py`：`SKILL_SECTION_FIELDS` 删除 `verify`；`render_skill_content` 只渲染三段；`_validate_summary` 之外的四段逻辑、docstring、rewrite 守卫全部改为三段；`has_skill_sections` 只要求三个 marker。
- `prompts/author.py`：G2 两个模式的输出 JSON 删除 `verify` 字段；Pass 5 删除“verify field”检查项并重排编号；G6 REWRITE 只要求保留三个 bracket section，删除 verify 检查项。
- `prompts/attribution.py`：G11 修订 prompt 只要求保留三个 bracket section。
- `prompts/curate.py`：G8 合并 prompt 只要求三个 bracket section。
- `composites/curation.py`、`composites/skill_folder.py`、`methods/author/revise.py` 等消费方随 `has_skill_sections` / `SKILL_MARKERS` 自动改为三段；错误文案改为“three bracket sections”。
- 测试：所有 skill 结构 fixture、fake model 响应、渲染/校验/改写/合并断言删除 `[Verify]`；`test_models` 的 marker 钉死改为三元组。
- golden：prompt 与 fake 响应改变，四个组合重新生成，调用数 34/18/13/24，崩溃续跑等价门全过。
- 文档：README、`methods/author/README.md`、`methods/delivery/README.md` 改为三段；新增本文件。

## 不保留兼容

- 不提供四段到三段的迁移、转换或切换开关；新技能直接按三段生成。
- 旧 run/store 里的四段正文仍能按字符串读取（`[When to use]` / `[Steps]` / `[Do not use when]` 三个 marker 都在，`has_skill_sections` 为 True），但没有为旧 `[Verify]` 段保留任何专门解析、校验或渲染代码。
- 历史 log 和旧 decision/review 文档保留原文，作为当时的记录；当前设计以本文件与 README 为准。

## 影响

- 技能正文变短；原来集中在 `[Verify]` 段的完成判据，现在只作为决策节点里的 `verify <pass signal>` 语义存在，不再单独成段。
- 依赖四段结构的旧断言、旧 golden、旧文档必须同步改；本轮已全部更新并通过全量测试。
