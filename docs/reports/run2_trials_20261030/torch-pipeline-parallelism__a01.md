# torch-pipeline-parallelism / attempt 1（失败）

- trial：`traj/tb:torch-pipeline-parallelism/strong/01`；步数 13；读取的技能：`traj/tb:cancel-async-tasks/strong/00/seg00/skill`
- 主因：B_skill_content

## 1. 为什么失败 / 卡在哪里

- 卡点：判分阶段 `test_pipeline_parallel[1]` 与 `[2]` 都在 Llama 解码层前向时崩溃：`cos, sin = position_embeddings` 收到 None，`TypeError: cannot unpack non-iterable NoneType object`。`test_pipeline_parallel_exists` 与 `test_no_hooks_in_pipeline_parallel` 通过。
- 过程：
  - 第 0 步工作区为空；第 1 步确认 torch 与 transformers 都不可导入（python 3.12）。
  - 第 2 步读了 cancel-async 的通用写模块技能。
  - 第 3 步写出 `pipeline_parallel.py`（约 6900 字节），`py_compile` 通过。
  - 第 4 步列出技能文件夹，看到库成员齐全；第 5 步只读了文件夹说明。
  - 第 6 步做字符串级补丁（给非末层加 `dist.send(hidden, dst=rank+1)`），第 10 步再加 `dist.barrier()` 保证 all-forward-all-backward 顺序，两次都只 `py_compile` 通过；第 8 步用 grep 核对 send/recv 行号即收工。
- 直接原因：与 a00 相同——逐层调用没有传 `position_embeddings`，与判分环境的 transformers 版本不兼容；两组进程都在同一行崩溃，说明改动（send 条件、barrier）没有触及真正的失败点。

## 2. 技能库里有没有能解决它的技能

- `bank/merge/0006`（相关性 中）——获取/安装缺失库并做功能验证；本 trial 未读，尽管第 4 步已经看到技能夹里包含它。
- `bank/merge/0004`（相关性 低到中）——提交前门禁要求可运行交付物做端到端行为验证；本 trial 未读。
- `traj/tb:cancel-async-tasks/strong/00/seg00/skill`（相关性 中）——通用模块模板，本 trial 读了；第 4 步"缺第三方包不要安装、`py_compile` 即可"直接导致零运行验证。
- `bank/merge/0001`（相关性 低）——评测器驱动的改进循环；本题无可用评测器。
- 缺的能力：torch.distributed 流水线并行的正确实现与 HF Llama 层当前接口（`position_embeddings`、`rotary_emb`）。

## 3. 能解决问题的技能被触发了吗？为什么没解决

- 触发：读了 cancel-async seg00，并按照它做完了"写文件—编译—重复编译"的整套流程。
- 原因：技能内容不足。模型在缺依赖时严格按第 4 步只做 `py_compile`，从未尝试安装或构造最小运行环境；第 5 步的 smoke test 是"possible and safe"才做，模型判断不可行后直接跳到第 7 步的"重复检查"。它甚至在第 6、10 步两次修改分布式通信逻辑，但全是盲改——技能没有任何一步要求在交付前取得一次真实执行证据。第 4 步的 "do not try to install it" 在这里是错误指令。

## 4. 结论与依据

- 主因：B_skill_content
- 一句话依据：第 6、10 步连续盲改通信逻辑，却始终只跑 `py_compile`；判分时新版 transformers 的 Llama 层需要 `position_embeddings`，代码未传，`[1]`/`[2]` 两组同点 TypeError。
- 次要因素：A_model_capability（API 版本知识错误）；D_skill_missing（无 torch/分布式技能）；E_environment（沙箱缺 torch/transformers，判分环境却预装，无法自测）。
