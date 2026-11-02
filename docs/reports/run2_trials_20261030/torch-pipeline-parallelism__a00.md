# torch-pipeline-parallelism / attempt 0（失败）

- trial：`traj/tb:torch-pipeline-parallelism/strong/00`；步数 8；读取的技能：`traj/tb:cancel-async-tasks/strong/00/seg00/skill`
- 主因：B_skill_content

## 1. 为什么失败 / 卡在哪里

- 卡点：判分阶段 `test_pipeline_parallel[1]` 与 `[2]`（1 进程与 2 进程两组）都在前向时崩溃：判分环境的新版 transformers 在 Llama 解码层里执行 `cos, sin = position_embeddings`，实现没有传 `position_embeddings`，得到 `TypeError: cannot unpack non-iterable NoneType object`。`test_pipeline_parallel_exists` 与 `test_no_hooks_in_pipeline_parallel` 通过。
- 过程：
  - 第 0 步工作区为空；第 1 步 `import torch` 报 `ModuleNotFoundError`，随即读了 cancel-async 的"从零写独立 Python 模块"技能。
  - 第 2 步写出约 170 行的 `pipeline_parallel.py`（含 `train_step_pipeline_afab`），只有 `py_compile` 通过。
  - 第 3 步用 AST 断言函数名与参数表（`model, inputs, targets, device, dtype`）。
  - 第 5 步重写文件（6545 字节），仍只做 `py_compile` + AST，输出 `COMPILE_OK AST_OK`；第 6 步直接声明完成。
- 直接原因：实现按旧版 HF Llama 层的调用方式逐层前向，没有计算/传递 rotary 位置嵌入（`position_embeddings`），与判分环境的 transformers 版本不兼容；1 进程与 2 进程两组都复现同一 TypeError，说明不是分布式同步问题，而是模型层接口用错。

## 2. 技能库里有没有能解决它的技能

- `bank/merge/0006`（相关性 中）——"所需产物缺失（含库）时如何获取/安装并做功能验证"，正好对应"torch 不在沙箱里"这一步；本 trial 未读。
- `bank/merge/0004`（相关性 低到中）——提交前门禁明确要求对 built/runnable artifact 做端到端行为验证，会否决"只编译"的交付；本 trial 未读。
- `traj/tb:cancel-async-tasks/strong/00/seg00/skill`（相关性 中）——确实是"空工作区、写一个模块"的通用模板，本 trial 读了；但它的第 4 步把"第三方包缺失"引向"不要安装、`py_compile` 即可"，是本题的直接陷阱。
- `bank/merge/0001`（相关性 低）——面向有评测器的候选改进循环，本题没有可用评测器。
- 缺的能力：torch.distributed 流水线并行（AFAB 调度、send/recv 顺序、microbatch 梯度累积）与 HF transformers Llama 层的当前接口（`position_embeddings` / `rotary_emb`、loss 计算）。

## 3. 能解决问题的技能被触发了吗？为什么没解决

- 触发：读了 `traj/tb:cancel-async-tasks/.../seg00`，并严格照做。
- 原因：模型完全执行了技能的步骤（1 环境检查、2 写文件、4 "ModuleNotFoundError 时不要安装、只 `py_compile`"、7 重复同一检查），问题在技能内容本身：
  - 触发条件写的是 "self-contained Python module"，而本题模块依赖 torch/transformers，并非 self-contained；
  - 第 4 步把 `py_compile` 当作缺依赖时的合格验证——对"判分要真正运行"的交付物，这等于没验证；
  - 第 5 步的 runtime smoke test 以 "possible and safe" 为前提，对"依赖完全不可用"没有替代方案。
  结果是三次尝试都从未获得一次真实运行反馈。

## 4. 结论与依据

- 主因：B_skill_content
- 一句话依据：照技能第 4 步"第三方包缺失不要安装、`py_compile` 即可"，模块从未运行；判分环境用新版 transformers，Llama 层要求 `position_embeddings`，代码未传，`[1]`/`[2]` 两组都在 `modeling_llama` 的 `cos, sin = position_embeddings` 处 TypeError。
- 次要因素：A_model_capability（按旧版 API 写代码）；D_skill_missing（库中无 torch/分布式技能）；E_environment（沙箱无 torch/transformers/pip，判分却在预装 torch 的独立 Python 环境里跑，开发环境与判分环境不一致，任务在沙箱内无法自测）。
