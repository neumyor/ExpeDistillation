日期：2026-11-16
说明：仓库代码整理的需求、约束与分阶段方案，重点是精简单元测试。
对应 log.md：2026-11-16 "仓库代码整理：精简单元测试与清理源码"

# 仓库代码整理

## 一、现状

- 源码 `src/` 28,565 行（132 个文件），测试 `tests/` 33,889 行（163 个文件、2,388 个用例），
  `scripts/` 5,071 行；全量测试约 47 秒，其中一个 ALFWorld 用例单独 30 秒；
- 四份只读普查的逐条清单在 `tmp/cleanup/`（不进 git）：
  `survey_tests_core_infra_llm_prompts.md`、`survey_tests_benchmarks_blocks_methods_composites.md`、
  `survey_tests_stages_cli_scripts_golden_helpers.md`、`survey_src_scripts.md`。

## 二、需求

1. **精简单元测试**。删除这几类测试：
   - 与 golden 或组合测试重复的；
   - 断言字面量表、长文本或提示词措辞的（golden 快照已逐字节固定这些内容）；
   - 检查已删除功能"确实不在了"的；
   - 只测构造函数、取值函数、数据类缺省值的。

   只是输入不同的一组测试合并成一个参数化测试。按"一个积木一个测试文件"合并散落的小文件。被多处复制的测试脚手架移进 `tests/helpers/`，测试文件之间不再互相 import。完成标准：测试行数降到 28,000 行以下，全量时间降到 20 秒以下。
2. **整理源码**：
   - 删除没有调用方的代码和已删机制留下的文档字符串；
   - 合并重复实现；
   - 删除没有调用方传入的可选参数，以及在多层重复的检查；
   - 修复 ALFWorld 子进程的关闭顺序缺陷：读超时之后先关管道、后终止子进程，会让真正卡死的子进程永远等不到读超时。
3. **整理脚本**：删除一次性的、或与命令行功能重复的脚本，以及只被它们使用的数据文件。

## 三、约束

1. 除第二节第 2 条的缺陷修复外，不改变任何运行行为：
   - golden 快照逐字节不变，不重新生成；
   - 提示词 digest 不变；
   - 44 份配置 `build_paper_configs.py --check` 通过；
   - manifest 的字段不变，已有 run 目录续跑不受影响；
   - 存储目录名（`stage1_baseline/` 等）不改。
2. 保留每一个独有契约：
   - 异常分类与重试；
   - 逐单元落盘与续跑；
   - 与外层统一 runner 逐字节一致的执行协议；
   - schema 版本拒绝；
   - 并行与串行的产物逐字节相同；
   - `import-baseline` 与 `fork-run` 的核对与拒绝；
   - 用录制数据重建轨迹。
3. 删除一个测试之前，确认它的契约在别处有覆盖（普查清单已注明）。保留下来的测试不放宽断言。
4. 分层检查、ruff（E9、F）、私有信息扫描全部通过；设计正本、AGENTS.md、README 与代码一致。
5. 不提交，等用户同意。

## 四、判断后的取舍

- 删除 `strong2_weak2` 规则：44 份配置都没有选用它。规则机制本身与其余三条规则保留；
- 删除 `skills_injected` 的列表形态分支：当前三种交付都不产生列表；
- 删除 `EXPEDISTILL_ALFWORLD_MAX_OUTPUT_TOKENS`：ALFWorld 方法已固定，这个诊断用的覆盖开关不再需要；
- 删除 `run_parallel` 的进程池分支：两个调用方都传 `thread=True`；
- `batch_execute` 只删除死掉的单值交付路径和没有调用方传入的参数，不改为用 `run_units` 实现：两者对代码缺陷的包装方式不同，改动会改变行为；
- `PhaseReport` 删除没有读取方的字段。`trace.resolve` 换成只做边界检查的 `check_ref`，同时删除 `TraceContext`；
- `llm/fake.py` 是测试替身，移到 `tests/helpers/fake_llm.py`。各包 `__init__` 的再导出缩减为文档字符串，测试改为 import 子模块；
- 删除的脚本：
  - `scripts/analysis/paper_tables.py` 及其测试，与 `expedistill paper-tables` 重复；
  - `scripts/analysis/tb21_f3_baseline.py`、`tb_archive_sample.py`、`tb_archive_validate.py`，以及只被它们使用的 `configs/seed/manifest_qwen.tsv`、`manifest_dpsk.tsv`、`audit_20261011.json`；
  - `scripts/run/build_pooled_control.py`，它的冻结产物保留；
  - `scripts/smoke/tb_g1_segment_smoke.py`。

## 五、阶段与分工

阶段之间顺序执行，每个阶段结束后由主会话做静态评审并跑全量测试。

**阶段 1：源码与脚本（2 个子智能体并行）**

- A：`core`、`infra`、`llm`、`prompts`、`inject`、`blocks`、`benchmarks` 下的源码，以及 ALFWorld 子进程缺陷的修复；
- B：`composites`、`methods`、`stages`、`cli.py`、`scripts/`、`configs/seed/`。

两人都只修改因自己的改动而失败的测试，并且只做最小修改；测试精简留到阶段 3。同一个测试文件需要两人同时修改时，后到的一方报告给主会话，由主会话合并。

完成标准：
- 全量测试通过；
- golden 快照不变；
- 提示词 digest 不变；
- 配置检查通过。

**阶段 2：测试脚手架（1 个子智能体）**

- 新建 `tests/helpers/fake_plugin.py`，作为插件安装器的唯一实现，替换 13 处复制；
- 移入 `tests/helpers/`：`fake_llm.py`、`tb_fake_remote.py`、ALFWorld 假适配器，以及被其他测试文件 import 的辅助函数；
- `PinnedClient` 与 `GoldenClient` 合并为一个，`CrashBefore` 与 `CrashAfter` 合并为一个。

完成标准：
- 全量测试通过；
- golden 快照不变；
- 没有测试文件 import 另一个测试文件。

**阶段 3：精简测试（3 个子智能体并行，目录互不重叠）**

| 子智能体 | 负责目录 |
|---|---|
| T1 | `tests/unit/{core,infra,llm,prompts,inject}`、`tests/test_layering.py` |
| T2 | `tests/unit/{benchmarks,blocks,methods,composites}` |
| T3 | `tests/unit/stages`、`tests/unit/test_cli*.py`、`tests/unit/scripts`、`tests/composites` |

各自按对应的普查清单执行，再查一遍清单之外同类的问题。需要把测试移到别人目录的，只在自己目录里删除，同时报告给主会话，由主会话移入。

完成标准：
- 全量测试通过；
- 行数与时间达到第二节的目标。

**阶段 4：文档与收尾（主会话）**

- 把 AGENTS.md 第四、五、八节，设计正本中提到的函数与积木，以及 README 同步到当前代码；
- 做整体静态评审，全量验证；
- 写 log.md。

## 六、结果（2026-11-16）

| 项目 | 整理前 | 整理后 |
|---|---|---|
| 测试代码 | 33,889 行 | 28,164 行 |
| 测试用例 | 2,388 个 | 2,051 个 |
| 全量测试时间 | 47 秒 | 16 秒 |
| `src/` | 28,565 行 | 27,661 行 |

- golden 三个文件的哈希、31 个提示词 digest、44 份配置检查、ruff、私有信息扫描都与整理前一致或通过；
- 测试行数目标（28,000 行以下）差 164 行，没有再删：剩余文件各自守着独有契约；全量时间目标（20 秒以下）达到；
- 缺陷修复：`_shutdown_child` 先终止子进程再关管道，对应测试从 30 秒降到约 2.6 秒；
- 独立静态评审没有发现行为变化。留下三点：
  1. 关管道没有超时，子进程连 SIGKILL 都等不到时才会挂住；
  2. 终测相位用 `none` 交付且配置 `playbook: evolved` 时，playbook 被静默丢掉（整理前就是如此，已原样保留，没有配置走到这里）；
  3. `ALFWorldEnv.step` 和 `extract_action` 没有测试也没有生产调用，要删需要改 `BaseEnv` 接口，没有动。
- 第四节写的"保留三条规则"实际是两个注册表里共 5 条规则（切段 3 条、轨迹挑选 2 条），只删除了 `strong2_weak2`。
