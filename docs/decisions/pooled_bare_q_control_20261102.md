日期：2026-11-02
一句话：实验四剩余四个 ALFWorld 臂改用一份冻结的“合并裸 Q 对照”，不再各跑一份新的裸 Q 对照；这是预先定死、对所有臂一致的规则，不是按结果挑对照。
对应 log.md：2026-11-02 「实验四剩余四臂改用合并对照」条目

## 背景

用户要求不再为新的实验臂跑裸 Q 对照，以节省算力。裸 Q 对照在已完成 run 之间本来就有波动：

| run | 对照 avg@3 |
|---|---|
| e1_ours | 0.8918 |
| e2_d2q | 0.8719 |
| e2_q2q | 0.8731 |
| e3_k1 | 0.8731 |

最高与最低相差约 2 个百分点，和处理效应本身同量级。**按“哪一份更能体现我们的结果”去挑对照属于看结果后选对照，会系统性高估效应，违反 `AGENTS.md` 第二节“先注册，再看结果”的规则，不能做。**

## 决定

四个尚未启动的实验四 ALFWorld 臂——`e4_a2_trigger`、`e4_a3_joint`、`e4_joint9`、`e4_staged9`——改用一份冻结的合并对照：

- 来源：`e1_ours`、`e2_d2q`、`e2_q2q`、`e3_k1` 四个已完成 run 的**对照臂**；
- 合并方式：按题拼接所有有判分的 trial（每题 10–12 次，四个 run 各 3 次，个别 invalid 剔除），每题的对照分数取这些 trial 的均值；
- 冻结：`configs/seed/alfworld_bare_q_pooled_20261102.json`，sha256 写入四个配置；合并后的总平均为 0.8775；
- 生成脚本：`scripts/run/build_pooled_control.py`。

这个规则在四个臂的 with-bank 结果产生之前就已经定死，并且对四个臂一视同仁；它不会因为某个臂的结果好看而改变。

## 不同时改的部分

- `e4_a0`、`e4_a1_body` 继续用各自 run 内的新跑对照（它们的中止续跑发生在决定之前，对照已在跑）。
- 主表 `e1_ours`、实验二四臂、实验三 `e3_k1` 继续用各自 run 内的对照，数字不变。
- 因此论文里会同时存在两种对照来源：前七个 run 是同 run 对照，后四个是合并对照。报告和表格必须逐臂注明 `control_source`；主表与实验二/三的结论不受影响。

## 实现

- `composites/external_control.py`：score 文件新增可选 `"aligned_attempts": "all"`，读取该文件的每题**全部** trial（而不是截到 run 的 3 次采样）；metrics 的 `attempts/valid` 记实际消费的 trial 数（10–12），每题的率是合并均值。
- `scripts/run/build_paper_configs.py`：`POOLED_CONTROL` 常量（path/sha256/model_key）与 `POOLED_CONTROL_ARMS` 集合；只有这四个 ALFWorld 配置的 `phases.test.compare` 变成 `external_campaign`，其余 21 个 ALFWorld 配置仍是 `fresh_bare`。
- 测试：external control 的 `"all"` 模式与非法模式；配置生成器的“只差预期键”断言把 `phases.test.compare.against` / `.external` 计入这四个臂的预期差异。全量 3119 通过。

## 读法上的限制

- 四个臂的对照不是与带库臂同一时间跑的，限流、网关路由、负载都可能不同；这正是 `paper_statistics_20261030.md` 说“噪声底只覆盖同一份库重采样”的边界之外的部分。
- 合并对照每题 10–12 个 trial，其自身对半重抽的噪声可以算；但它与带库臂不是同 run 配对，报告里必须写明 `control_source: external_campaign` 和对照文件 sha256。
- 如果审稿要求所有臂统一口径，可以按同一份合并对照把主表与实验二/三重算一遍作为附录敏感性分析；主表仍以同 run 对照为准。
