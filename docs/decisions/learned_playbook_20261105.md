日期：2026-11-05
一句话：让全局 playbook 由 P02 之前的 baseline 轨迹学出来，机制按 benchmark 可插拔、交付层开关控制、默认关闭。
对应 log.md：2026-11-05「可学习的全局 playbook」条目

## 背景

手写 playbook（`global_playbook_p0_20261104.md`）让三条参考臂的 avg@3 提升
13–16 个点。要把它变成方法的一部分，playbook 必须能由流水线自己产出。

用 `exp3_p0_k3_20261104_2042` 的中间产物核对过证据在哪里：

- P01 摸底 234 条轨迹里，78 条失败全部跑满 50 步；失败 trial 的中位数是
  33 次 `go to`、只有 8 个不同地点、同一个 `go to` 最多重复 8 次、推进动作
  2 个；成功 trial 中位 9–10 步、动作去重率 0.80–0.84，失败是 0.31–0.37。
  按任务类型：弱模型 cool 18/18 失败，heat 12/21，clean 9/27。
- P02 把这些失败切成段、聚成簇，写出 22 张局部技能卡；这些卡里没有一张含
  "revisit / searched / ledger / visited" 这类循环相关的词，终库 6 张卡约
  26.5k 字符，按需打开，不常驻。
- P04/P06 的试验级审阅只看 P03/P05 考试的失败。考试是文本场景考试，跑不出
  50 步环境循环，所以结论讲的是"没核对候选实例、没检查器具状态"。

循环行为的证据只在 baseline 轨迹里，现有通路没有任何一条把它汇总成全局规则。

## 方案

一条新通路，复用已有的 `playbook` 交付通道，只改文本的来源。

1. **事实提取（程序，不调模型）。** 插件方法 `playbook_facts(sheets)` 把
   baseline 的 trial 级答卷变成写作者读的文本。ALFWorld 的实现按任务类型
   统计两个臂的成败、失败 trial 的 `go to` 次数、不同地点数、单地点最大
   重复数、是否从未取放物体，并给出最短成功和最长失败各一条动作骨架。
2. **写作（一次模型调用）。** 通用块 `blocks/playbook.write_playbook` 用
   插件声明的 prompt 名称（`playbook_prompt_name`）调用 author 模型，
   读者画像走 `PROFILE_ROLES["playbook_reader"]`；回复必须是非空且不超过
   20000 字符的 `playbook_text`。
3. **落盘。** `stages/learn_playbook.ensure_playbook` 在 baseline 之后的
   第一个 learn 相位里写 `playbook.md`（`composites/playbook_store`），
   文件已存在则不再调用模型。
4. **交付。** `folder` / `exam_folder` 交付新增参数 `playbook_source:
   learned`，与 `playbook_file` 互斥；resolver 读最近一个早于当前相位的
   `playbook.md`，作为 `playbook` 传给 params，之后的渲染路径与手写版相同。

## 可插拔点

| 位置 | 内容 | 谁提供 |
|---|---|---|
| `BenchmarkPlugin.playbook_prompt_name` | 写作 prompt 的注册名；None 表示该 benchmark 不支持 | 插件 |
| `BenchmarkPlugin.playbook_facts(sheets)` | 从 baseline 答卷提取事实 | 插件 |
| 插件 runner 渲染 manifest 的 `playbook_text` | 执行时把文本放到 prompt 里 | 插件 |
| `blocks/playbook`、`stages/learn_playbook`、`composites/playbook_store`、resolver | 通用流程 | 框架，不含 benchmark 名字 |

ALFWorld 的两项在 `benchmarks/alfworld/playbook.py`。TB21 要接入时：实现
自己的 `playbook_facts`（例如按任务类别统计命令失败、超时和重复命令）、
注册自己的 prompt、声明 `playbook_prompt_name`，并让 Harbor 侧的 folder
prompt 渲染 `playbook_text`；通用代码一行不改。

## 开关与默认行为

- 开关只有一个：delivery 里的 `playbook_source: learned`。不写这个键，
  `requested(config)` 为假，P02 不多一次模型调用，stage 0 不冻结写作
  prompt，旧 run 的 manifest、prompt digest 和 golden 快照逐字节不变。
- 要求 learned 但插件没有声明能力、或声明的 prompt 没注册，stage 0 直接
  报 ConfigError，不会跑到一半才失败。
- 没有新增 config 顶层字段，不升 schema 版本。

## 不做什么

- 不在 P02 之后加 live 相位做接受门：序列和成本都会变，本次只验证"学出来
  的文本能否达到手写版的效果"；
- 不用测试集信息：只读训练集 baseline；
- 不改 P02/P04/P06 的技能写作与审阅逻辑。

## 实验

`e7_learnedpb_k{5,3,1}`（ALFWorld only）：与 `e1_ours` / `e3_k3` / `e3_k1`
相比只多两行 `playbook_source: learned`。baseline 相位可由
`assemble-reuse --level baseline` 复用，主指标沿用 avg@3 与 pass@3，对照
是参考臂、手写 playbook 臂（e6）和冻结裸 Q。
