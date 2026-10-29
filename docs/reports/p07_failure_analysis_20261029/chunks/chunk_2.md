# Chunk 2 — P07 失败 trial 归因

日期：2026-10-29
一句话：本 chunk 覆盖 11 个失败 trial（feal-differential-cryptanalysis ×3、overfull-hbox ×3、video-processing ×3、crack-7z-hash/01、password-recovery/01）的逐条归因表。
对应 log.md：2026-10-29 的“P07 失败 trial 归因分析”条目。

| trial | blocker_step | verdict | confidence |
|---|---|---|---|
| traj/tb:feal-differential-cryptanalysis/weak/00 | 12 | model_capability | medium |
| traj/tb:feal-differential-cryptanalysis/weak/01 | 35 | model_capability | high |
| traj/tb:feal-differential-cryptanalysis/weak/02 | 69 | model_capability | high |
| traj/tb:overfull-hbox/weak/00 | 17 | model_capability | high |
| traj/tb:overfull-hbox/weak/01 | 93 | model_capability | high |
| traj/tb:overfull-hbox/weak/02 | 9 | model_capability | high |
| traj/tb:video-processing/weak/00 | 42 | model_capability | high |
| traj/tb:video-processing/weak/01 | 14 | model_capability | high |
| traj/tb:video-processing/weak/02 | 40 | model_capability | high |
| traj/tb:crack-7z-hash/weak/01 | 18 | model_capability | high |
| traj/tb:password-recovery/weak/01 | 59 | model_capability | high |

计数：`model_capability` 11（feal/00 为 medium，其余 high）。

**本组反复出现的模式**

1. 11 条都是模型自身的推理/执行错误，判分与环境没有可疑之处；唯一
   需要保留的例外是 feal/00——它以 7200 s `AgentTimeoutError` 收尾，
   但 14 个回合里单次请求最长约 600 s、累计约 41 万输出 token，只执行
   了 13 条命令且没写出 `attack.py`，所以仍归为模型自身的产出问题。
2. 交付的 26 条技能没有一条覆盖这五类任务（差分密码分析、白名单逐词
   替换、视频运动估计、慢速爆破、磁盘字符串取证）。各 trial 打开的
   几乎都是列表第一条 `bank/merge/0000`（远程资产恢复）或名字相近但
   不适用的卡片（`extract-moves-from-video` 是 OCR、`req-29e0efe45bee`
   自述“不是 SQLite 就停止”），没有一次是因技能内容错误而失败。
3. 但 bank 里已有两条 pending 卡片正好对应 4 条失败，属于“写了但没
   交付”：`req/a20befa537b9`（改唯一参考工件前先冻结副本）对应
   overfull/01、/02 凭记忆重写 `input.tex`；`req/e20a68bdd559`（长任务
   先等 30–60 s、看进程再决定是否打断）对应 crack/01 提前 Ctrl-C 和
   password/01 的全盘 grep 卡死。
4. 交付集中唯一涉及长命令的 `filter-js-from-html` seg01 反而教“命令
   挂起就 Ctrl-C”，对慢速爆破是反向指导（本次未被打开），说明现有
   技能对“合法长任务”和“真挂起”没有区分。
5. 验证 oracle 错位是 feal/01、/02 的共同根因：两份 `attack.py` 都靠
   改写自己 import 的 `feal.key[5]` 来判定候选，本地 10/10 PASS，判分器
   传入独立的 C 实现 `feal_in_c.encrypt` 后恒返回第一个候选 0。
6. overfull/01、/02 是典型的破坏性编辑：`git` 不可用后没有先复制原件，
   直接凭记忆 heredoc 重写 `input.tex`（可见混入的粘贴行、`'''`、以及
   一直对不上的 md5 `229b6…`），之后所有“恢复”都只是模型自己的重建。
7. overfull/00 是另一种约束错误：它按语义近似替换了不在白名单里的词
   （`unusually→atypical`、`consequence→outcomes`），从未做
   `w in synonyms` 的程序化检查；只要有一次逐 token 对照就不会失败。
8. 视频三例都是检测概念/阈值错误：video/01 把“运动员入镜”当起跳
   （两个视频都报 33），video/00 的质心启发式把落地点判早 4 帧，
   video/02 判晚 8 帧、在测试视频上把起跳判到第 2 帧；三者都只做了
   print 与结构自检，没有任何可对照的真值。
9. 两条超时 trial 都是“预算被自己耗光”：crack/01 在 john 约 120 s 时
   打断唯一能命中的词表，之后用 60 秒一截的猜测跑到 2 小时；password/01
   自己启动 `grep -r /` 卡死 shell，之后约 120 步按键只回显不执行。
10. 反证失败不是任务不可解：同题的 crack-7z-hash/00 与 password-recovery/00
    分别用 21、25 步通过，用的正是本次被放弃的“跑完 john 词表”和
    “全文扫描大写串拼出 `8XDP5Q2RT9Z` + `K7VB3BV4WW54`”两条路径。
