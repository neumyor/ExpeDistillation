日期：2026-10-20
说明：为整理模块的 G9 分组新增独立的 `grouping_input` 输入开关。
对应 log.md：2026-10-20 的“curation 新增 grouping_input 开关”条目。

# 整理模块新增 grouping_input 开关

## 背景

整理模块有两个模型调用会看到整库技能：

- G7 `SIMILARITY`：找相似组、冲突对和低价值技能；
- G9 `GROUP`：给每条技能分配能力组。

2026-10-19 新增的 `similarity_input` 只控制 G7，G9 一直固定看正文。库变大后，G9 的输入也会随正文长度膨胀；G9 的判断目标是“按用途分组”，一句话摘要通常已经足够。用户要求给 G9 增加独立开关，默认保持现状。

## 候选方案

1. **复用 `similarity_input`**：一个开关同时控制 G7 和 G9。改动最小，但两个调用的输入规模和失败模式不同，合并后无法单独做消融，也改变已有配置的语义。
2. **新增 `grouping_input`**：G7 与 G9 各自独立，取值都为 `full` / `summary`，默认 `full`。
3. **不新增开关，直接让 G9 看摘要**：省参数，但改变所有已有配置的行为，且旧库缺摘要会直接失败。

## 选择

采用方案 2。

- `AgentBrewParams` 新增 `grouping_input: str = "full"`；
- 取值 `full` 或 `summary`；
- `curate_bank` 把它传给 `assign_groups`；
- `assign_groups` 用 `_listing(skills, grouping_input)` 渲染 G9 输入；
- 默认 `full`，现有配置和已有 run 的行为不变。

## 理由

- G7 和 G9 的输入成本、判断目标不同，独立开关允许分别做消融；
- 默认 `full` 不改变已有行为，也不需要重生成 golden 基线；
- `summary` 与 G7 的摘要模式共用同一套前提：每条已接受技能必须有非空摘要，缺摘要时在模型调用前报 `ConfigError`；
- G9 的 `GROUP` 提示词文本不变，prompt digest 不变；变化只是送给它的 `skills_listing` 内容。

## 影响

- 新配置可以写：
  ```yaml
  curate:
    name: agentbrew
    grouping_input: summary
  ```
- 不写时默认 `full`。
- 该参数随 `phases.learn.curate` 进入 manifest，属于冻结的方法参数；改动需要新 run。
- 旧库缺摘要时，`grouping_input: summary` 会在 G9 前报错；需要先补齐摘要。

## 涉及文件

- `src/expedistill/methods/curation/__init__.py`
- `src/expedistill/methods/curation/agentbrew.py`
- `src/expedistill/composites/curation.py`
- `src/expedistill/blocks/curate.py`
- `src/expedistill/methods/curation/README.md`
- `README.md`
- `tests/unit/methods/test_params.py`
- `tests/unit/blocks/test_curate.py`
- `tests/composites/test_curation.py`
- `log.md`
