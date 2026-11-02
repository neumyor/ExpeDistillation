# make-doom-for-mips / attempt 0（失败）

- trial：`traj/tb:make-doom-for-mips/strong/00`；步数 148；读取的技能：无
- 主因：E_environment

## 1. 为什么失败 / 卡在哪里
- 卡点：第 145 步——最后一条自测命令删除旧帧后重跑 `timeout 15s node vm.js`，以超时（RUN_EXIT=124）结束，但把新生成的 `frame.bmp` 留在容器临时目录里；第 146–147 步直接提交。
- 过程：
  - 第 0–4 步勘查：工作目录有 `doom.wad`、`doomgeneric` 源码树和题目给定的 `vm.js`（MIPS 模拟器），没有交叉编译器；
  - 第 26–70 步自建 `myrt/my_stdlib.c` 与构建脚本，用 `mipsel-linux-gnu-gcc` 交叉编译 DOOM，首次运行报 `Unaligned word access at 0x2d25`，无帧；第 103 步改 `-O0` 后又报 `undefined reference to atof`；
  - 第 140 步运行到 `W_GetNumForName: STCFN33 not found!` 失败、无帧；第 143–144 步修 printf 的补零/宽度实现后，首次写出 1024054 字节、640×400 的 `frame.bmp`；
  - 第 145 步删帧重跑，15 秒超时被杀，但 `frame.bmp` 已生成（时间戳 05:30）；随后提交。
- 直接原因：判分器三个测试中 `test_frame_bmp_exists`（0.0005 s）与 `test_frame_bmp_similar_to_reference`（0.238 s）都通过——交付的帧与参考帧相似度过线；唯一失败的是 `test_vm_execution`，它捕获的标准输出只到 `W_Init: Init WADfiles. adding doom.wad ... Open!`。该测试耗时 1.012 s ≈「立即找到 `frame.bmp` + sleep(1) + terminate」，且失败类型是文本断言而不是 `TimeoutError`，说明它没有等 VM 自己出帧，而是把第 145 步留下的旧 `frame.bmp` 当成"已出帧"信号，在 VM 启动约 1 秒时把它杀掉，因此看不到后面由图形初始化打印的 `I_InitGraphics: DOOM screen size: w x h: 320 x 200`（模型的构建确实走完了图形初始化，否则不可能渲染出通过相似度检查的帧）。若没有这个残留文件，测试会按设计等待 VM 出帧，再读标准输出。按断言逐条归因：`test_frame_bmp_exists` 与 `test_frame_bmp_similar_to_reference` 均通过、不受该机制影响；唯一失败的 `test_vm_execution` 完全由"旧帧提前放行 + 1 秒杀进程"造成，本 trial 没有独立于该机制的真实产出缺陷。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0006`（相关性 高，针对构建阶段）——"probe-first 地获取、配置、编译、安装并用 ldd/冒烟测试验证软件工件"。本 trial 正是"缺交叉编译器 + 有 C 源码树"，与它的触发条件（构件缺失、声明依赖未证实）完全吻合；但它只覆盖构建，不覆盖"最终产物如何满足隐藏的验收输出"。
  - `bank/merge/0004`（相关性 中）——提交前门禁：核对精确路径、端到端行为、从干净状态复核，并要求在发出完成标记前重跑判据。若照做，会暴露"最后一次自测是超时 run、临时目录里还留着上次的帧"这个交付状态。
  - `bank/merge/0001`（相关性 低-中）——用 evaluator 记录基线/单因素变体并验证 exactness；本任务没有可见的 evaluator 与参考帧，价值有限。
- 缺的能力：MIPS 交叉编译 + 自定义 syscall ABI 运行时（与给定 `vm.js` 的 syscall 号/结构对齐）这一整类技能；以及"交付前复位运行环境（临时帧文件、残留进程）"的检查项。前者本库没有。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：没读。`meta.json` 的 `skills_read` 为空、`skills_read.md` 无内容，轨迹里也没有打开技能正文的动作。
- 原因：技能文件夹确实存在（同任务的 attempt 1/2 都从里面读过 `bank-merge-0006`），但本 trial 一次都没看。按 0006 的"When to use"（缺少编译器、从源码构建），它本应在第 2–4 步被选中；没读没有直接造成失败（模型自己走完了构建），但也没有采用 0004 的"完成前从干净状态复核"，最终交付状态带着残留帧文件。

## 4. 结论与依据
- 主因：E_environment
- 一句话依据：三条断言中两条通过（帧存在、帧相似度），唯一失败的 `test_vm_execution`（1.012 s、标准输出只到 `W_Init`）完全由第 145 步留下的旧 `frame.bmp` 让测试提前放行、1 秒后杀 VM 造成；该 trial 没有任何独立于这一机制的真实产出缺陷。
- 次要因素：A 侧——第 145 步以超时（RUN_EXIT=124）结束仍提交，且没有清理临时帧文件；如果把"交付前复位环境、从干净状态跑一遍"当作必做检查（可选技能 `bank/merge/0004` 正是这个主题），本 trial 很可能是 3/3。
