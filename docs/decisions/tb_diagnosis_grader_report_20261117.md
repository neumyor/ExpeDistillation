日期：2026-11-17
说明：TB 学习相位的失败诊断读判分器报告；TB 用自己的诊断提示词，ALFWorld、SearchQA 不变。
对应 log.md：2026-11-17 "TB 失败诊断读判分器报告（K1）"

# TB 失败诊断读判分器报告

## 背景

学习相位的失败诊断（`TRIAL_FAILURE_ANALYSIS`）只看轨迹，提示词里只写 "The trial failed."。
TB 的插件取回判分日志后，只从中算出 `verifier_ran` 和奖励，日志内容不往下传。

11-16 run 训练集练习相位有 46 个失败。离线实验让强模型对照判分器报告，判断现有诊断说的原因
对不对：27 个一致，10 个只说对一部分，9 个归错原因。例如：

- caffe-cifar-10：二进制编到了 `._release`，判分器找的是 `.build_release`，原诊断归因于准确率
  的报告方式；
- raman-fitting：认错了峰的位置，原诊断说是峰形用错；
- torch-tensor-parallelism：第一项权重切分检查就失败了，原诊断说是梯度和 bias 错。

把报告补进提示词重新诊断后，这 19 个都判为一致。判断者和诊断者是同一个模型，新诊断又看过
报告，所以这个数字偏乐观。

这批练习失败的判分日志里还有 8 个字面格式错误、13 个找不到文件或路径，这类错误在轨迹里
看不出来。

## 候选方案

1. 改共用的 `TRIAL_FAILURE_ANALYSIS`，加一个槽，ALFWorld 与 SearchQA 填空。渲染文本不变，
   但这条提示词的 digest 变了，ALFWorld 已固定的方法也要换 digest。
2. TB 用插件已有的替换机制（`PromptPack.overrides`）登记自己的诊断提示词。替换提示词的
   占位符必须与原提示词相同，所以报告不走新槽，而是接在任务说明之后；接在前面的标题段也要
   登记成提示词，按"benchmark 专用提示词登记在 `benchmarks/<名字>/`"的规则，放在 TB 侧，
   由提示词包新增的 `grader_report_prompt` 声明。

## 决定

用户选方案 2，并定下原则：只属于某个 benchmark 的提示词都登记在各自的 `benchmarks/<名字>/`
下。共用的方法提示词留在 `prompts/`，benchmark 之间的差异只经词汇槽与 `protocol` 进入。

## 实现

- `benchmarks/tb/plugin.py`：`grader_report(verifier_log)` 摘取最后一次 pytest 会话的失败断言
  （`E ` 行，最多 40 行）、short test summary（最多 30 行）和汇总行，每行最多 300 字符，合计
  最多 3,000 字符；会话前的安装输出丢掉。取回结果时写进 `meta["grader_report"]`（没有日志时为
  空串）。它是结果，不进 run id 和条件指纹。
- `benchmarks/tb/diagnose.py`：`TB_TRIAL_FAILURE_ANALYSIS` 是方法提示词加两处：有判分报告时
  从报告出发找偏离的那一步；两条笔记不得写报告里的值、文件名或期望输出。
  `test_tb_diagnose.py` 钉住两者只差这两处，方法提示词一改，这个测试就提醒同步 TB 副本。
  `TB_DIAGNOSIS_GRADER_REPORT` 是报告段（一个 `{report}` 槽）。
- `benchmarks/base.py`：`PromptPack.grader_report_prompt`（缺省 None）；插件接口
  `backfill_meta(meta)`（缺省不补）。
- `composites/trace.execution_grader_report`、`methods/diagnose` 的 `report_prompt` 参数、
  `stages/phase_learn` 传入提示词包的段名：有段名又有报告时，接在任务说明之后；否则提示词与
  原来逐字节相同。
- `stages/stage0_freeze`：声明了的段名必须已注册、占位符恰好是 `{report}`，digest 冻结；
  manifest 的 `prompt_pack` 只在声明时多写这个名字。
- `stages/import_baseline`：CLI 传入 benchmark 插件，插件的 `backfill_meta` 给源轨迹补上没有
  记录的执行事实，只补新键，已有键报配置错误。TB 从 `meta.remote.verifier_log` 或绝对路径的
  本地工作目录读判分日志，读得到才补；`BASELINE_ORIGIN.md` 写明补了多少条。源文件摘要按读到的
  源文件计算，不受补充影响。

## 影响

- ALFWorld、SearchQA：冻结的 prompt digest 集合与数值、manifest 的 `prompt_pack` 与改动前
  逐项相同（对照 `5f030ef` 计算），golden 快照不变。
- TB：manifest 多 `TB_TRIAL_FAILURE_ANALYSIS` 与 `TB_DIAGNOSIS_GRADER_REPORT` 两个 digest，
  `overrides` 多一项，`prompt_pack` 多 `grader_report_prompt`。这是新实验，11-16 的 run 不能在
  新代码上续跑。
- 10-28 摸底源 run 的 120 个 trial 判分日志都还在基准主机的 Harbor jobs 目录下，导入时能补上。

## 未验证

- 新诊断的效果还没有在真实运行里看过：用户要求这次只改代码、不启动运行。
- 泄漏检查（笔记里是否出现报告中的期望值）要在第一次学习相位跑完后做。
