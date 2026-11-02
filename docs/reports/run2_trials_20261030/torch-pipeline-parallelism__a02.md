# torch-pipeline-parallelism / attempt 2（失败）

- trial：`traj/tb:torch-pipeline-parallelism/strong/02`；步数 11；读取的技能：`traj/tb:cancel-async-tasks/strong/00/seg00/skill`
- 主因：B_skill_content

## 1. 为什么失败 / 卡在哪里

- 卡点：判分阶段 `test_pipeline_parallel[1]` 与 `[2]` 都在 Llama 解码层前向时崩溃：`cos, sin = position_embeddings` 收到 None，`TypeError: cannot unpack non-iterable NoneType object`。
- 过程：
  - 第 1 步明确探测：python 3.12，`no torch`、`no transformers`。
  - 第 2 步进一步搜索：系统里没有 torch 目录、没有测试文件、`pip list` 为空，确认沙箱内无法运行。
  - 第 3 步读了 cancel-async 的通用写模块技能；第 4 步读了技能目录的 `catalog.md`（已看到 `bank/merge/0006` 等成员）和文件夹说明。
  - 第 5 步写出 5722 字节的 `pipeline_parallel.py`，第 7 步重写为 6092 字节，两次都只有 `py_compile` 通过。
  - 第 8 步用 grep 核对 `dist.send`/`dist.recv` 行号后收工。
- 直接原因：与 a00/a01 相同——逐层前向没有传 `position_embeddings`，与判分环境的 transformers 版本不兼容；两组进程组在同一行失败。

## 2. 技能库里有没有能解决它的技能

- `bank/merge/0006`（相关性 中）——"所需库缺失时先探测、安装、再做功能验证"，是本题唯一可能打开"本地真实运行"的技能；第 4 步读 catalog 时已经看到它，但没读。
- `bank/merge/0004`（相关性 低到中）——提交前门禁要求可运行产物做行为验证；未读。
- `traj/tb:cancel-async-tasks/strong/00/seg00/skill`（相关性 中）——通用模块模板，本 trial 读了；第 4 步"缺第三方包不要安装、`py_compile` 即可"是直接原因之一。
- `bank/merge/0001`（相关性 低）——评测器驱动的改进循环；无可用评测器。
- 缺的能力：torch.distributed 流水线并行实现 + HF transformers Llama 层当前 API。

## 3. 能解决问题的技能被触发了吗？为什么没解决

- 触发：读了 cancel-async seg00；更接近问题（缺库、需安装验证）的 `bank/merge/0006` 在 catalog 里出现过但没读。
- 原因：两层原因叠加。其一，读到的技能内容不足：第 4 步"ModuleNotFoundError 时不要安装、只 `py_compile`"让模型在确认环境缺 torch 后直接放弃运行验证，第 5 步的 smoke test 又要求 "possible and safe"，缺依赖时没有替代路径。其二，模型自己没有把"依赖缺失"当成需要解决的子问题去读 `bank/merge/0006`，而是一路按模板编译到收工。结果是它清楚地知道环境跑不了，却仍然交付了从未执行过的代码。

## 4. 结论与依据

- 主因：B_skill_content
- 一句话依据：第 1–2 步已确认沙箱无 torch/transformers 且无测试文件，仍按技能第 4 步只做 `py_compile`；判分时新版 transformers 的 Llama 层需要 `position_embeddings`，代码未传，`[1]`/`[2]` 同点 TypeError。
- 次要因素：A_model_capability（API 版本知识错误）；D_skill_missing（无 torch/分布式技能）；E_environment（开发环境无 torch、判分环境预装，二者不一致）。
