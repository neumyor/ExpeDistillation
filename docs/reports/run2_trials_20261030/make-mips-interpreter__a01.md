# make-mips-interpreter / attempt 1（失败）

- trial：`traj/tb:make-mips-interpreter/strong/01`；步数 145；读取的技能：无
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：第 137–142 步——解释器把"第一次关闭 `frame.bmp`"当作结束条件，抓到的首帧全黑（第 139 步量到 `distinct_pixels 1`、第 142 步 `distinct_all 1`）；第 143–144 步提交。
- 过程：
  - 第 0–4 步勘查：预编译 `doomgeneric_mips` + 源码树（含 `my_stdlib.c`、`fake_fs.c`），没有 `vm.js`，要从零写解释器；
  - 第 26–59 步按反汇编修 `seh` 等指令，跑过 `Z_Init`，随后卡在 `Unable to allocate %i MiB of RAM for zone`（`%i` 未替换），第 81 步显示 1.24 亿条指令仍停在同一个 PC；
  - 第 114 步的 10 秒运行写出 5120270 字节的畸形帧并超时（EXIT 124）；
  - 第 125–137 步加"关闭 `frame.bmp` 即停 VM"的判定，第 137 步得到 1024054 字节、640×400 的帧，EXIT 0，标准输出到 `R_Init`/`P_Init`/`S_Init`；
  - 第 138–139 步检查发现像素全 0；第 141 步曾把两帧拼成一个 2048108 字节文件；第 142 步修 open 标志后最终帧仍是全 0。
- 直接原因：两条失败断言分开看。`test_frame_bmp_similar_to_reference`（`Score: 0.7339, req: 0.95`）不受早杀机制影响：它比较的就是第 142 步最终产物写出的帧，而该帧全黑（`distinct_all 1`），全黑帧的得分就是 0.7339——模型在第一次 `frame.bmp` 关闭时立刻停 VM，此时 DOOM 还没渲染出标题画面，这是真实产出缺陷。`test_vm_execution` 的失败则受早杀影响：测试只跑了 1.009 s，被残留 `frame.bmp` 提前放行（与 attempt 0/2 同一机制）。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `traj/tb:path-tracing/strong/00/seg01`（相关性 中-高）——逐像素验证与反汇编写出器；最关键的是它要求"与参考输出逐像素一致"，而不是"文件存在"。
  - `traj/tb:path-tracing/strong/00/seg00`（相关性 中）——伴随可执行文件先运行、用 `cmp` 比对输出。
  - `bank/merge/0001`（相关性 中）——基线-变体-评测循环、可复现冠军；按它执行会先定义"什么算一张合格的帧"再优化。
  - `traj/tb:llm-inference-batching-scheduler/strong/00/seg03`（相关性 低-中）——"卡住的循环：逐点打印、隔离违反的约束、全目标通过才停"；第 81 步停在固定 PC 的调试方式与它相近，但它面向参数搜索问题。
- 缺的能力：MIPS32 解释器（ELF/syscall/FPU）；以及"何时算渲染完成"——需要让程序多跑若干帧、跑到标题画面再取帧，而不是第一帧。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：没读。`meta.json` 的 `skills_read` 为空；轨迹里没有打开技能正文的动作，也没有出现 `<available_skills>` 清单。
- 原因：`path-tracing` 两张卡正是为"图像 + 不透明可执行文件、要复现输出"写的，但模型没有查技能文件夹；即使读了，卡里也没有 MIPS 指令语义，只能在验收方式上纠正它"帧文件存在即成功"的标准。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：相似度断言独立于早杀机制地失败（`Score: 0.7339`，与全黑帧同分），模型自己在第 139/142 步已量到 `distinct_pixels 1`、`nonzero_bytes 0` 仍以"BMP 头合法、尺寸 640×400"提交；早杀只解释 `test_vm_execution` 一条。
- 次要因素：E——仅影响 `test_vm_execution`（1.009 s 提前放行）；D——库中没有"取帧时机/渲染验收"类技能。
