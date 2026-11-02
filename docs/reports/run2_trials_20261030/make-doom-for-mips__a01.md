# make-doom-for-mips / attempt 1（失败）

- trial：`traj/tb:make-doom-for-mips/strong/01`；步数 160；读取的技能：`bank/merge/0006`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：第 136–155 步——WAD lump 名查找与绘图补丁大面积失效，最终渲染出的帧与参考只有 0.7368 的相似度；第 156–157 步仍以"文件存在且 BMP 头合法"提交。
- 过程：
  - 第 2 步读 `bank-merge-0006` 技能正文，第 16 步装好 `mipsel-linux-gnu` 交叉工具链；
  - 第 52–76 步自写 `vm_runtime.c` 补齐 `my_stdlib` 缺失符号；第 76 步首次运行 node 直接崩溃、无帧；
  - 第 112–124 步查 `lumphash/lumpinfo/numlumps` 与 `W_CheckNumForName` 反汇编，怀疑 lump 查找；
  - 第 136 步把 `patch->patch == -1` 的 `I_Error` 改成赋 0，运行到 `P_Init`/`R_InitSprites`，报 `Sprite TROO : A : 1 has two lumps mapped to it`，终止于 PC=0x40cf8c，无帧；
  - 第 152–155 步日志出现成片 `W_GetNumForName: STCFN71 not found; using 0`、`R_GenerateLookup: column without a patch (REDWALL1)`、`Bad V_DrawPatch x=-2839 y=-3863 patch.width=0 patch.height=7936 topoffset=3863 leftoffset=2839`；第 155 步终于写出 1001 KB 的帧；
  - 第 156–157 步只检查 BMP 头（640×400、32bpp）就提交，全程没有把帧与任何参考比对。
- 直接原因：`test_frame_bmp_similar_to_reference` 报 `Images not similar enough. Score: 0.7368, req: 0.95`。交付的帧内容大体是错的：`Bad V_DrawPatch` 那几行显示补丁宽为 0、高为 7936、横纵偏移是离谱负数，大量 lump 查不到被兜成 0，精灵还有重复 lump 选错。两条失败断言分开看：`test_vm_execution`（期望文本缺失）受早杀机制影响——测试 1.013 s 内被遗留 `frame.bmp` 提前放行、sleep 1 秒后杀 VM，标准输出只覆盖启动段，这一条与产出无关；`test_frame_bmp_similar_to_reference`（`Score: 0.7368, req: 0.95`）不受该机制影响——它比较的就是第 157 步最终产物写出的帧（测试 1 秒内杀进程来不及覆盖该文件），帧内容确实错误。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0006`（相关性 高，限构建）：被读了，帮上了工具链与交叉编译；不覆盖"自定义 libc 与 WAD 语义/渲染正确性"。
  - `bank/merge/0001`（相关性 中）——基线-单因素变体-评分-验证 exactness 的迭代框架。本任务没有可见判分器与参考帧，但它的"每次改动都记录可复核指标、不凭感觉"的要求，正对应本 trial 后段"改一处跑一次、只看有没有帧"的调试方式。
  - `bank/merge/0004`（相关性 中）——完成前门禁（独立参考、端到端行为）。若执行，至少会要求"用一个独立方式确认交付帧的内容"，而不是只看 BMP 头。
  - `bank/merge/0005`（相关性 低）——hang/清理类异步调试，与渲染错误无关。
- 缺的能力：在自定义 syscall/libc 运行时下让 DOOM 正确加载 WAD（lump 名匹配、补丁索引）并渲染出与参考一致的帧；以及"画面正确性"的自查方法（没有参考图时怎样判断渲染是否合理）。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：读了 `bank/merge/0006`（第 2 步 `cat`，meta 记为已读）。
- 原因：读了但内容不足。它是"从源码获取/构建/安装"的技能，判据是 `make` 退出 0、二进制存在、基本冒烟通过；模型在构建成功后自然认为过关，而失败发生在构建之后的运行时语义（lump 查找/补丁绘制）。技能里没有任何相关步骤，模型随后用"把错误改成 0/跳过"消音（第 136 步 `patch->patch = 0`；第 152 步起缺失 lump 兜成 0），没有回到根因（WAD 查找在模拟环境下的行为）去验证。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：两条失败断言中，`test_frame_bmp_similar_to_reference`（`Score: 0.7368, req: 0.95`）独立于早杀机制地失败——日志里成片的 `Bad V_DrawPatch ... patch.width=0 patch.height=7936` 与 `column without a patch (REDWALL1)` 说明渲染管线在 lump 查找/补丁索引处就错了；`test_vm_execution` 的失败才由残留帧提前放行（1.013 s）造成。
- 次要因素：E——只影响 `test_vm_execution` 一条，不改变交付帧错误的判定；技能库缺少运行时/渲染正确性类技能（D）。
