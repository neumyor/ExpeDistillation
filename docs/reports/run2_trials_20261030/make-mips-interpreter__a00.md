# make-mips-interpreter / attempt 0（失败）

- trial：`traj/tb:make-mips-interpreter/strong/00`；步数 168；读取的技能：无
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：第 162 步——自写解释器终于"跑完"并写出 `frame.bmp`，但帧是 320×200；第 166–167 步提交。
- 过程：
  - 第 0–3 步：工作目录只有预编译的 `doomgeneric_mips`（1.5 MB ELF32 小端）、DOOM 源码树与 `doom.wad`，没有 `vm.js`——任务是从零写 MIPS 解释器；
  - 第 30 步用一条 here-doc 写出完整 `vm.js`（ELF 加载、通用寄存器、指令解码、syscall 层、写 `frame.bmp`）；
  - 第 43–82 步按单步日志修指令语义（`ins`/`ext`、延迟槽、分支目标）；
  - 第 108 步的 35 秒运行只到 `W_Init` 后就打印 `Z_Malloc: failed on allocation of %i bytes`（`%i` 未被替换，说明 printf 语义也没模拟全）并超时（EXIT 124）；
  - 第 147–162 步修掉一处自造语法错误后，第 162 步 `node vm.js` 以 EXIT 0 结束并写出 256054 字节的帧；
  - 第 165 步模型在自写的 `vm.js` 里查找 `fallbackFrame`/"Write fallback" 等字样（只命中 `process.exit`，未找到兜底实现），说明它在排查自己的写帧路径。
- 直接原因：两条失败断言分开看。`test_frame_bmp_similar_to_reference`（`Image sizes do not match: (320, 200) vs (640, 400)`）不受早杀机制影响：同一个预编译程序在另一 trial 的解释器里写出的是 640×400（DOOM 内部 320×200、日志里 `Auto-scaling factor: 2`），本 trial 的 `vm.js` 自己在代码里维护 `FRAME` 常量与 `frameDone` 判定（第 30 步的代码片段可见），交付帧来自解释器侧的写帧路径而非被模拟程序的 BMP 写出路径——这是真实产出缺陷。`test_vm_execution` 的失败则受早杀影响：判分只捕获到 `... Trying IWAD file:doom.wad / W_Init: Init WADfiles. adding doom.wad` 就结束，测试耗时 1.025 s ≈ 被残留 `frame.bmp` 提前放行后 sleep(1) 杀掉，没有等到 `I_InitGraphics`。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `traj/tb:path-tracing/strong/00/seg01`（相关性 中-高）——"图像特征化、可执行文件反汇编、逐像素验证复现输出"：第 5 步教反汇编写出器、跟踪逐像素算术，第 7 步教"与参考输出逐像素比较、最大绝对差为 0 才算成功"。本任务正是"写解释器复现一个不透明二进制产生的图像"，工作方法直接对应。
  - `traj/tb:path-tracing/strong/00/seg00`（相关性 中）——"工作目录有陌生文件、旁边有伴随可执行文件时，先运行它并比较其生成的输出"。任务里的 `doomgeneric_mips` 就是那个伴随可执行文件。
  - `bank/merge/0001`（相关性 低-中）——用 evaluator/基线-变体循环验证 exactness；本任务没有现成 evaluator。
  - `bank/merge/0005`（相关性 低）——卡住循环的定位（超时重跑、加标记）；对第 108 步的 `Z_Malloc` 卡死有一点方法价值。
- 缺的能力：MIPS32 解释器（含 ELF 加载、Linux syscall 语义、FPU 指令）以及"帧必须由被模拟程序自己写出、解释器不得替换/兜底输出"的约束意识。库中没有 MIPS/模拟器类技能。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：没读。`meta.json` 的 `skills_read` 为空、`skills_read.md` 无内容；轨迹里没有打开技能正文的动作，也没有出现 `<available_skills>` 清单。
- 原因：`path-tracing` 两张卡的触发条件（"工作目录有陌生文件/伴随可执行文件""图像 + 不透明参考可执行文件"）与本题高度吻合，但模型没有查技能文件夹，直接开始逆向；这两张卡也不会直接给出解释器指令语义，所以主要仍是模型能力问题。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：`test_frame_bmp_similar_to_reference` 独立于早杀机制地失败（320×200 vs 640×400，同一程序在另一 trial 的解释器下写出 640×400），说明模型没有跑通程序的真实写帧路径，只做到"有帧文件"就提交；早杀只解释 `test_vm_execution` 一条。
- 次要因素：E——仅影响 `test_vm_execution`（1.025 s 提前放行，未等到 `I_InitGraphics`）；技能库缺 MIPS/模拟器类技能（D）。
