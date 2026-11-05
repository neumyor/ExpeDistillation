日期：2026-11-04
一句话：在 folder / exam_folder 交付里加一份始终可见的全局 playbook（任务配方 + 搜索循环恢复），作为 P0 与 SkillOpt 对齐；默认关闭。
对应 log.md：2026-11-04「全局 playbook（P0）」条目

## 背景

对照 SkillOpt 的官方 ALFWorld skill
（`benchmark/alfworld-eval/src/alfworld_eval/skills_docs/skillopt_alfworld.md`，
13,179 字符）后确认三个系统差距：

1. SkillOpt 把整篇策略常驻 prompt；我们是 catalog + 每局最多 3 张卡，关键
   规则可能从头到尾没被打开；
2. 我们缺全局任务类型表：`pick_two`、`desklamp/look_at`、`diningtable`
   没有专门配方；
3. 我们缺搜索循环恢复规则（exact-instance lockout、broadening、
   finite-class exhaustion、destination-as-source、pick-two phase memory）。

coverage K=5 的失败结构：128/128 个失败 trial 全部耗尽 50 步，动作去重率
0.294（成功的 trial 是 0.751、中位 13 步）。失败不是"不知道做什么"，而是
搜索循环把预算耗完。

## 方案

- 新增 `configs/playbooks/alfworld_global_20261104.md`（约 4.8k 字符），
  从官方 skill 蒸馏：6 类任务配方、Pick Two 记账、通用原则、搜索循环恢复、
  strict search ledger、destination-as-source、clean 任务的两个具体兜底。
- 交付层开关：`folder` / `exam_folder` 的 delivery 映射新增
  `playbook_file` + `playbook_sha256`。phase resolver 读文件、核对哈希，
  把正文作为 `playbook` 传给 params；folder manifest 增加
  `playbook_text`（空时省略，旧 manifest 逐字节不变）。
- 渲染：ALFWorld 的 folder block 在 `Available skills:` 之前加
  `Global playbook:` 段落，每一步都可见；exam 的 catalog 文本前同样加上。
- 控制：不传 `playbook_file` 的臂行为逐字节不变；外部裸 Q 对照没有
  folder，完全不受影响。

## 实验

参考臂的 P0 变体：`e6_playbook_k{5,3,1}`（ALFWorld only），规则与
`e1_ours` / `e3_k3` / `e3_k1` 完全相同，只多 playbook。对照是已有的
参考臂结果（0.6990 / 0.6866 / 0.6617）和同一份冻结裸 Q（0.6269）。
生成器矩阵 89 → 92 个配置，`--check` 0 problems。

## 不做什么

- 不改执行协议、任务门槛、覆盖规则、exam 的 `bare_control`；
- 不改官方 skill 的原文到仓库外：只把蒸馏版放进 `configs/playbooks/`，
  并在配置里钉 sha256；
- 不把 playbook 写进 system prompt（交付层更便于按臂开关，且控制臂不受影响）。
