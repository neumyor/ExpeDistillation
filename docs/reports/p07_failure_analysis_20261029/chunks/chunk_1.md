# chunk 1 — chess-best-move / mteb-retrieve / torch-pipeline-parallelism / kv-store-grpc

日期：2026-10-29
一句话：chunk 1 的 11 条失败 trial 的逐条归因汇总（明细见 `trials/` 下同名 JSON）。
对应 log.md：2026-10-29 的“P07 失败 trial 归因分析”条目。

| trial | blocker_step | verdict | confidence |
|---|---|---|---|
| traj/tb:chess-best-move/weak/00 | 48 | mixed | medium |
| traj/tb:chess-best-move/weak/01 | 14 | mixed | medium |
| traj/tb:chess-best-move/weak/02 | 59 | mixed | medium |
| traj/tb:mteb-retrieve/weak/00 | 116 | model_capability | medium |
| traj/tb:mteb-retrieve/weak/01 | 69 | model_capability | medium |
| traj/tb:mteb-retrieve/weak/02 | 34 | model_capability | medium |
| traj/tb:torch-pipeline-parallelism/weak/00 | 32 | model_capability | high |
| traj/tb:torch-pipeline-parallelism/weak/01 | 21 | model_capability | medium |
| traj/tb:torch-pipeline-parallelism/weak/02 | 157 | model_capability | high |
| traj/tb:kv-store-grpc/weak/01 | 2 | model_capability | high |
| traj/tb:kv-store-grpc/weak/02 | 2 | model_capability | high |

## 归纳

1. 11 条失败全部发生在“模型自己的产物”与“外部严格判分器”的接缝上，而模型从未构造过独立参照：chess 用错误局面推理，torch 只断言“不抛异常”，mteb 用自己的排序口径，kv 用自己写的客户端。自洽验证在 11/11 里都等于没有验证。
2. 两条纯规范遵循失败：任务原文逐字给出“must use the installed mteb package to load and encode”和字段名 `value`（int），模型分别换成了 `SentenceTransformer` 和 `val`；kv 用同一个错 proto 派生的客户端自测通过，mteb 在读过 `mteb/models/bge_models.py`（含中文 query 指令）后仍未改。
3. 交付的技能卡是被“表面词”命中的，而不是被失败模式命中的：选中的是“OCR/图像”“构建/编译”“下载远程资产”这类字面相近的卡；真正对口的“命令挂起/无提示符不得提交/用测试证明模块正确”三类卡 0 次打开。
4. 终端与进程管理是独立的一条失败轴（chess/00）：一次批量 keystrokes 把 shell 卡成 `python3`，之后约 200 步 Ctrl-C、Ctrl-D、tmux 转义都没恢复，最后在看不到提示符时确认完成；对应恢复卡存在但未打开，pending 的 `req/e20a68bdd559` 正是这个主题。
5. 存在真实能力缺口：交付的 26 张卡里没有任何一张讲“从渲染图恢复棋盘局面”。三次 chess 的棋子占用检测其实都对，错在种类（e2 分别被判成 N / Q / Q），且从不做“每色一王、子力数、局面合法性”这类一致性校验。
6. torch 三次各写了一版手写并行/反向，都没有按任务给出的判分口径（逐层 hook 比对参考激活、梯度逐元素比对）自查；错误分别落在 hook 计数（重放前向使每层多调用一次）、损失口径（移位 + 每 microbatch 平均）、`-100` padding（手写 one-hot CE 越界）。
7. “把答案写全”的检查普遍缺失：chess/01、02 各只写一条走法，而 prompt 要求列出所有获胜走法；对应的完备性卡（`traj/tb:bn-fit-modify/strong/00/seg03/skill`）仍是 pending，未交付。
8. 同一 task 的多次尝试不收敛：chess 三次对同一格的棋子类型各不相同，kv 的 00 号通过、01/02 重复同一个字段名错误——更像采样方差，而不是“试错后学习”。
