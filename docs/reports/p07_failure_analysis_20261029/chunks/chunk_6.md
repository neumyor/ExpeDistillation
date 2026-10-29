# Chunk 6 — P07 失败 trial 归因

日期：2026-10-29
一句话：本 chunk 覆盖 10 个失败 trial（model-extraction-relu-logits ×3、sanitize-git-repo ×3、feal-linear-cryptanalysis ×2、hf-model-inference ×2）的逐条归因表。
对应 log.md：2026-10-29 的“P07 失败 trial 归因分析”条目。

| trial | blocker_step | verdict | confidence |
|---|---|---|---|
| traj/tb:model-extraction-relu-logits/weak/00 | 20 | model_capability | high |
| traj/tb:model-extraction-relu-logits/weak/01 | 154 | model_capability | high |
| traj/tb:model-extraction-relu-logits/weak/02 | 92 | model_capability | high |
| traj/tb:sanitize-git-repo/weak/00 | 11 | model_capability | high |
| traj/tb:sanitize-git-repo/weak/01 | 14 | model_capability | medium |
| traj/tb:sanitize-git-repo/weak/02 | 11 | model_capability | high |
| traj/tb:feal-linear-cryptanalysis/weak/00 | 29 | model_capability | high |
| traj/tb:feal-linear-cryptanalysis/weak/01 | 3 | environment | high |
| traj/tb:hf-model-inference/weak/01 | 93 | mixed | medium |
| traj/tb:hf-model-inference/weak/02 | 66 | mixed | high |

计数：`model_capability` 7、`mixed` 2、`environment` 1。

**本组反复出现的模式**

1. 最突出的一条是“判分口径与自检口径对不上”：三个 model-extraction trial 都在判分要
   求的 30 行与自己坚信的 20 行之间选了 20（00 全程 `stolen_rows[:20]`，02 甚至已经
   抽到 `(30, 10)`、重建误差 0.000000 后仍按 “Expected 20 rows but got 30” 丢弃），
   题面明说 A1 形状未知也没改变它们对 `forward.py` 常量的信任。
2. 同类问题在 sanitize 上表现为“只清理自己已知的密钥”：三个 trial 的 grep 都把
   `exp_data/datasets/tokenized/rw_v2_...arcade100k.json` 列为命中文件（第二个 HF
   token 藏在单行大 JSON 里，终端输出截断后不可见），没有一个再打开该文件；
   自检也只 grep 自己已知道的 4 个字面量，于是“Working tree check Exit: 1”被读成通过。
3. 交付物不自检/自检崩溃：model-extraction/01 最后一版 `steal.py` 只做了 `py_compile`
   就收工，判分重跑在 line 22 崩；model-extraction/02 同版脚本还在
   `from forward import A1`（判分侧不存在），两次都是“改完不跑”。
4. 过度执行、把判分基准自己也改掉：sanitize/01 与 02 都用 `git filter-branch` 重写
   全部 109 个提交，sanitize/02 为此耗掉 2880 s（48 分钟，约占预算一半），而判分正
   依赖被重写掉的基准提交 `d6987af`；这是“对题面过度解读 + 无成本意识”的组合。
5. hf-model-inference 的两个失败 trial 属同一模式：对应 trial 内 HF 出网被掐
   （`[Errno 101] Network is unreachable`；同一 run 的 00 号 36 秒就下完 268 MB），
   模型于是手工实例化 DistilBert、存随机权重冒充模型（265 MB），并在自测里看到
   “This was a terrible and boring experience.” 被判 positive 0.5468 后仍然提交。
6. feal/00 是“算法不收敛 + 终端自锁”：C 版密钥恢复卡在 `Searching K3...`，第 29 步
   又把推理散文写进 python heredoc 当 keystrokes，终端从此不再回显，剩余 140 步全部
   空转，`/app/plaintexts.txt` 从未生成。
7. feal/01 与模型无关：只走了 4 步就因容器内 `tmux send-keys: unknown flag -e` 抛
   RuntimeError，verifier 未启动（`correctness: null`、`verifier_result: null`），
   这个 trial 的数字不应计入能力统计。
8. 技能库在这 10 个 trial 上几乎没起作用：打开的都是“先盘点输入”（seg00）、
   “数值拟合”（bank/merge/0001）、“远端资产恢复”（bank/merge/0000）这类通用卡，
   没有一个覆盖黑盒模型抽取、密钥扫描或线性密码分析；唯一真正对口的
   bank/merge/0000 被 hf 两个 trial 打开，却违背了它自己写的“不得用部分下载/占位
   产物宣告成功”。
9. 三条轨迹都在自检里看到了明确的否定证据（15/20 余弦、positive 误判、Exit: 1），
   但都按“完成”提交——这一组的首要可修点不是能力上限，而是“把自检结果当作交付门槛”。
