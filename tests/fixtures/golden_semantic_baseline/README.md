# 语义等价基线束（M4.1 仪器）

日期：2026-10-16
一句话说明：从提交 69904f7 的金矩阵提取的 E1–E6 语义视图基线——M4.3 用 v4 代码
重生成产物后，"行为没变"的唯一判据（方案 docs/decisions/
execute_learn_implementation_20261015.md §8）。
对应 log.md 条目：2026-10-16 M4.1 语义等价仪器先行落地。

## 内容与体积

- 8 份 `<acceptance>-<delivery>-<mode>.json`：每个组合的 E1–E6 预注册视图
  （技能库行多重集、逐技能/逐组终态、C 相位测试 summary + retrieval_audit、
  模型调用多重集、live 账本行）。每份文件内注明读取的字段（views 由
  `tests/golden/semantics.py` 的 `semantic_views` 投影，投影即归一化：
  v2/v4 对象新字段 source_subtasks / skill_id / schema_version 不进视图）。
- `provenance.json`：来源 commit、每组合模型调用数与崩溃点（E5 变体机器的输入）。
- 总体积约 416 KiB。

## 提取命令（可复现）

```bash
.venv/bin/python tests/golden/extract_baseline.py
```

默认源提交 `69904f7`（golden 的最后一个 v1 对象、stage 目录布局版本），
用 `git show` 读取，不 checkout。输出字节确定（排序键、固定缩进、无时间戳）：
重跑必须逐字节复现已提交的束——这是常驻自检
`test_extraction_reproduces_the_committed_bundle`。

## 谁消费它

- `tests/golden/semantics.py`：`compare_combo(combo, v4_root, baseline_root)`
  单组合全准则比较 + live 白名单①机判；`run_e5` 崩溃续跑变体机器。
- `tests/golden/test_semantic_instrument.py`：identity（重提取复现、当前金
  矩阵等价、现跑等价、合成 v4 布局等价）+ tamper 反证（9 种定向篡改各自转红）。
- M4.3 验收：用 v4 代码重生成产物，跑 `compare_combo`/`run_e5`，要求
  E1–E4/E6 全等 + 白名单①恰等于删光出题调用 + E5 续跑等价。

基线束是**预注册**的：视图口径在见到任何 v4 产物之前固定（本目录 +
semantics.py 同提交落地）；v4 布局与预测不一致时只改 semantics.py 的
`SEQUENCES` / `v4_layout` 数据表，断言函数不硬编码路径。
