日期：2026-10-11
一句话：TB（TerminalBench）远程链路适配的九项决定与已接受边界——环境接线、判卷契约对齐、时限、重试分工、用量记账口径、容量指引与强模型输出预算。
对应 log.md 条目：2026-10-11 15:07（用 TB2.1 真实 rollout 归档验证数据链路四模块）与 2026-10-11 15:30（TB 远程链路适配收口：交接对齐与两级冒烟）。

来源：TB2.1 裸 rollout campaign 的交接材料（内部部署说明，不在仓库内；下称"交接材料"）与外层 `scripts/tb21/` 参考实现。本文把适配过程中做出的、会影响后续所有 TB run 的决定收拢到一处，每项按背景 / 决定 / 理由写。run id 命名空间与远程续跑的决定单独立档（`tb_run_namespace_20261011.md`），不在这里重复。

---

## 1. 远程环境接线走 `<root>/env.sh`，不改远程代码包

**背景**。runner（外层 `scripts/tb21/` 下的 runner 脚本）默认
`TBENCH_VENV_DIR=${SCRIPT_DIR}/.venv`（脚本第 23 行），即期望 venv 在代码包目录
代码包目录下的 `.venv` 里；基准主机 上实际不存在这个 venv——campaign 用的
Harbor venv 在 `~/agentbrew-tb21/.venv`。另外 runner 检查的凭据变量名是
`OPENAI_API_KEY`，而两处 secrets 文件导出的是 `TBENCH_LLM_KEY`。交接材料
§1.2/§3.5 只写了"远程交互 shell 用 campaign 的交互 shell 环境文件"，
没说我们这条无人值守提交链路怎么接。

**决定**。远程 root 下放一份一次性写好的 `<root>/env.sh`（由
`scripts/smoke/smoke_tb_dryrun.py` 的 `ROOT_ENV_SH` 首次部署，内容已存在且不同
则拒绝覆盖），插件提交时在起 runner 之前 `source` 它
（`src/expedistill/benchmarks/tb/plugin.py` `_submit`，第 852 行）。内容四件事：
source `~/agentbrew-tb21/.env`（密钥）；`OPENAI_API_KEY` 由 `TBENCH_LLM_KEY`
派生；`TBENCH_TENCENT_ENV_FILE` 指向不存在的文件（不让 runner 再 source 代码包
里的 secrets.env，避免同一凭据两处来源）；`PYTHON_BIN`/`HARBOR_BIN` 指向
`~/agentbrew-tb21/.venv`。

**理由**。不动 远程代码包目录 这个 md5 对齐的部署副本——它是外层
campaign 的权威产物，改它等于让我们的适配和 campaign 之间产生漂移。env.sh 放在
我们自己的 root（我们自己的工作根）里，随 run root 走，谁也读得
到改动来源；`_submit` 无条件 source（文件不存在时 `if [ -f ]` 静默跳过），所以
新 root 只要做一次 DRY_RUN 冒烟就完成接线。

## 2. tmux 判模型失败的规则要求异常类型必须是 RuntimeError

**背景**。campaign 的判卷契约（外层 `scripts/tb21/audit_trials.py`
`classify_exception`，第 70 行）是：**只有** `RuntimeError` 且消息里有 tmux /
send-keys 字样才判"模型弄坏了 tmux 会话、计 0 不补跑"。真实数据里 qemu 两题
（qemu-alpine-ssh 等）的 trial 正是以这种 `RuntimeError` tmux 按键失败收场的，
audit 里判 KEEP_MODEL（"tmux按键失败(命令解析)"，见
`tmp/tbval/audit_20261011.json`）。我们的 `classify_result` 初版只匹配消息正则、
不查异常类型。

**决定**。`classify_result` 的 tmux 规则改为双条件：`exception_type ==
"RuntimeError"` **且** 消息命中 `_TMUX_ERROR_RE`
（`src/expedistill/benchmarks/tb/plugin.py`，`_TMUX_ERROR_TYPE` /
`_TMUX_ERROR_RE`，`classify_result` 末段）。类型不在
`_MODEL_EXCEPTIONS`（AgentTimeoutError / ContextLengthExceededError /
OutputLengthExceededError）又不是 tmux-RuntimeError 的异常，一律
`infra_error`。回归测试锁死四个方向，含"infra 异常消息里提到 tmux 仍判 infra"
（`tests/unit/benchmarks/test_tb_plugin.py`
`test_classify_tmux_rule_matches_only_runtime_error`）。

**理由**。判卷口径是预注册的（外层 AGENTS.md 第二节），和 campaign 审计对齐是
硬要求：消息里出现 "tmux" 的基础设施异常（如沙箱起不来时错误文本捎带 tmux）
在 campaign 里会补跑，我们若判成模型失败，等于两套口径、同一批轨迹得出不同
结论。730 条全量对账中该规则与 campaign 审计完全一致（唯一分歧是下文第 6 条
的已知边界）。

## 3. 默认 deadline 16200s（单 trial 天花板 15480s + 裕量）

**背景**。初版默认 deadline 是 10800s（3 小时），按"agent 上限 7200s + verifier
×2 + setup 裕量"估的。对照 campaign 实际开关重算后这个数不对：campaign.env 里
`TBENCH_VERIFIER_OVERRIDE=7200.0` 统一接管 verifier 时限（覆盖生效，×2 乘数不再
叠加），`TBENCH_SETUP_MULTIPLIER=3` 让沙箱启动时限 360s×3=1080s。单 trial 自身的
最坏情况是 7200 + 7200 + 1080 = 15480s，超过 3 小时——10800s 的 deadline 会在
一个合法尚在跑的 trial 中途把它杀掉。

**决定**。`DEFAULT_DEADLINE_SECONDS = 16200.0`（4.5 小时），在 15480s 之上留
传输与调度裕量（`src/expedistill/benchmarks/tb/plugin.py`，`DEADLINE_ENV =
EXPEDISTILL_TB_DEADLINE_SECONDS` 可覆盖）。`.env` 已同步设同一值。

**理由**。deadline 的职责是兜底清僵尸（runner 死了、沙箱挂死），不是正常路径
的一部分；设小了会把仍在合法推进的 trial 误杀成 infra_error 并触发一次全新
重跑（每个 trial 最贵 4.3 小时），设大 1 小时代价只是一次更晚的清理。注释里
把 15480s 的构成写死，是为了 campaign 开关一变（如 verifier override 改回乘数）
就能立刻看出该重算。

## 4. `TBENCH_MAX_TRIAL_RETRIES=0` 是与 campaign 的刻意分歧

**背景**。campaign 里 `TBENCH_MAX_TRIAL_RETRIES=1`，runner 内部对
AgentSetupTimeout / Sandbox / AddTestsDir 三类异常整条重跑一次。交接材料 §3.3
即此值。我们的 K1（`src/expedistill/composites/batch_execute.py`）自己拥有
采样与 infra 重试（B6），retry 游标 `r{n}`、按题熔断、逐单元落盘。

**决定**。`runner_env` 固定 `TBENCH_MAX_TRIAL_RETRIES=0`
（`src/expedistill/benchmarks/tb/plugin.py` `runner_env`，`TBENCH_N_ATTEMPTS=1`、
`TBENCH_N_CONCURRENT=1` 同理）：一次插件调用 = 一个 RunOutcome = 远端一个 trial
目录。runner 内部从不重试。

**理由**。这是刻意的分歧而不是疏漏：runner 内部重试会藏在单元内部，绕开 K1
的落盘、账本与失败记录，一个"单元"实际对应两个 trial 目录，续跑和审计都无法
对账（外层 AGENTS.md 第一节"对照是否真的是对照"的同款事故）。重试归 K1 后，
每次重试发生在新 run id（`r{n}` 游标）下，路径可见、可熔断、可审计。代价是
setup 抖动从 runner 内部消化变成 K1 的一次显式 infra 重试——见第 8 条的容量
指引。

## 5. 用量记账以 trajectory.json 的 final_metrics 为准

**背景**。交接材料 §1.4 说"用量（token）我们没做过落盘——litellm 打了日志但未
汇总"，§6 把 token 汇总列为"我们没有的信息"。核对真实归档发现这个说法不准确：
Harbor 的 `agent/trajectory.json` 每条 trial 都带 `final_metrics`
（`total_prompt_tokens` / `total_completion_tokens`），campaign 只是没做汇总。
另一个关键事实：`final_metrics` 恒大于全部步级 metrics 之和——上下文 handoff 的
摘要 LLM 调用不在 `steps` 里（15:07 log 条目发现 (4)，归档采样实测）。

**决定**。`total_usage` 只读 `final_metrics`
（`src/expedistill/benchmarks/tb/trajectory.py` `total_usage`）；插件把它记成
B7 账本的一行（`plugin.py` `_record_usage`，`request_id` = run id），取不到时
记 null、绝不记 0。**禁止**用步级 metrics 求和当总量——那是系统性低估。

**理由**。步级求和漏掉 handoff 摘要调用，对长轨迹（真轨迹动辄 428 步、多次
handoff）偏差不可忽略；两个口径的差异不是噪声而是结构性的。取 null 不取 0
是 AGENTS.md 第六节第 5 条的既定规则（拿不到用量记 unknown）。live 冒烟已验证
这条链路：三行账目 in=50416 / out=7255 与 `final_metrics` 逐项一致
（`runs/smoke_tb_live_20261011_150323/`）。

## 6. 已知边界（有意接受，不修）

**背景**。730 条全量对账里 `classify_result` 与 campaign 审计只分歧 1 条：
`e1n5_q_qemu-alpine-ssh_1`——agent 在沙箱宿主上改 iptables/eth0 把沙箱自己的
网络搞坏，verifier 连不上镜像，reward.txt 写出 0 但 pytest 没跑。campaign 人工
复核后改判 KEEP_MODEL 计 0（外层 `scripts/tb21/audit_trials.py` 的
`RUN_OVERRIDES`）；我们的预注册规则只能看见"reward=0 且无 pytest 迹象"，判
`pseudo_zero` → infra_error → 重跑。这个规则盲区无法在不引入人工复核的情况下
闭合：规则能看到的证据（verifier 日志无 collected）与"依赖装不上"型伪 0 完全
同形，区别只在 agent 的历史行为里，而那是人看出来的。

**决定**。接受这个边界，不做程序化检测。它意味着极少数"agent 自毁沙箱网络"
的 trial 会被我们当作伪 0 补跑一次；若同一题反复以同一形态回来（我们的重试
上限内反复 pseudo_zero），操作者要人工复核轨迹，而不是任由它烧重试预算。

**理由**。730 条里 1 条（0.14%），误判的代价是多跑一次 trial（有重试上限兜
底），而把"模型自毁环境"错误计 0 的代价是污染对照组——两害相权取前者。把它
写进档是让后续 run 的审计知道这条分歧是预期内的。

第二条边界是 token 估算的系统性偏差：`estimate_tokens`（`len//4`，
`src/expedistill/inject/skills.py:149`）用在 `bank_token_budget`
（`src/expedistill/core/config.py`，默认 4096）的预算检查上。对终端 dump 类
文本（命令回显、代码块）它低估约 41%——一次真实 G1 视图实测估算 40702 vs
真实 57553 token（`tmp/tbval/g1_smoke/`）。也就是说同样字符数的 skill 文本，
"终端风格"的比"散文风格"的真实 token 更接近估算值的 1.4 倍，
`bank_token_budget` 实际比名字看起来的更宽松。接受此偏差：预算是防失控的
软上限而非精确配额，且偏保守方向无害。

## 7. `TBENCH_E2B_RELAY_TEMPLATE` 是死变量

**背景**。交接材料 §3.4 把 `TBENCH_E2B_RELAY_TEMPLATE=code-agent-cfs2-new`
列为常用开关，外层 `scripts/tb21/env.sh.example` 也导出它。但全 vendor 代码
grep 不到任何读取它的地方；`_FORWARDED_KNOBS`（插件转发的 campaign 开关名单）
因此也不含它。

**决定**。不设、不转发这个变量。真正生效的开关是
`HARBOR_E2B_TEMPLATE`（`scripts/tb21/vendor/framework-python/harbor_e2b_modelbest/__init__.py:89`，
默认 `code-agent-new`），作用在我们这条环境类链路（runner →
`tencent_vpc_dns_env` / `tencent_tb2_skill_env` → `tencent_tb2_snapshot` →
`harbor_e2b_modelbest.E2BEnvironment`）上；我们目前没有改模板的需求，用默认值。
vendor 另有两个文件读 `E2B_TEMPLATE`（`e2b_fuse.py`、
`generate_tencent_skill_pack.py`，默认 `code-agent-cfs2-new`），但都不在
runner 的环境类路径上。

**理由**。把这个名字当真开关写进 .env 或转发名单，会让人以后"调了模板"却
什么都没调到——正是外层 AGENTS.md 第一节"核对对照组实际属性、不信名字"要防
的。真要换沙箱模板时改 `HARBOR_E2B_TEMPLATE`，并先确认
`_FORWARDED_KNOBS` 是否需要加它（目前不加，因为没人设它）。

## 8. 容量指引：K1 的 workers 数 = 并发冷启动沙箱数，起步 ≤2

**背景**。campaign 第一批全量的头号死因就是整批并发冷启动：投喂器一次放出一
大批 trial，E2B 沙箱同时冷启动，超时连锁失败（交接材料 §1.3、§4.8 的原话：
"不要整批冷启动"）；campaign 修复后的节奏是每分钟投 2 条。K1 的 `workers`
参数（`src/expedistill/composites/batch_execute.py`）直接决定同时有多少个
trial 在远程起沙箱——每个 worker 一个独立 run id、一个沙箱冷启动。叠加第 4
条：`TBENCH_MAX_TRIAL_RETRIES=0` 把 setup 抖动变成 K1 的显式 infra 重试（新
run id、新沙箱），workers 开得越大、重试越密，雪崩是自我放大的。

**决定**。TB 正式 run 的 `workers` 从 ≤2 起步；监控指标是 run 目录账本里的
infra_error 率（尤其 setup/sandbox 类），稳定后再逐步加，每加一档先观察一个
完整批次。campaign 的另两个上限照旧：agent 7200s、verifier 7200s，由 deadline
兜底（第 3 条）。

**理由**。campaign 用真金白银买来的教训就是冷启动并发是单点脆弱项；我们没
有任何新证据推翻它。infra_error 率是唯一能提前发现"并发过载"的信号——
MAX_TRIAL_RETRIES=0 让它比 campaign 时代更显眼，这是刻意设计的一部分（第 4
条），不是为了零重试而零重试。

## 9. 强模型的 TB 输出预算：实验配置必须给 `max_tokens` ≥32768

**背景**。ModelHandle 的默认 `max_tokens=5120`（`src/expedistill/llm/call.py`，
2026-10-11 12:50 条目引入的默认值）。真实 G1 视图渲染出来约 4 万+ token（一条
162809 字符的真实轨迹视图，`estimate_tokens` 估 40702、真实 57553）；而
DEEPSEEK_up5zdj 在 `reasoning_effort=max` 下会把 5120 的输出预算全部烧在推理
上——实测首调 `finish_reason=length`、内容为空、ModelOutputError（约 4 万
token 真实消耗，`tmp/tbval/g1_smoke/llm_requests/0001_SEGMENT.json`）。

**决定**。TB 实验配置的 `model_options` 必须给强模型显式覆盖：
`{"DEEPSEEK_up5zdj": {"reasoning_effort": "max", "max_tokens": 32768}}`。
32768 已在真实视图上验证可用：4 段、D7 校验干净、57553 in + 4569 out、61 秒
（`tmp/tbval/g1_smoke/usage.jsonl` 第二行，`scripts/smoke/tb_g1_segment_smoke.py`
可复跑）。

**理由**。5120 对"读一条 4 万 token 的终端轨迹再切段"这个任务物理上不够，
失败模式还是最贵的那种：钱已花在推理上、结果一个字没拿到。32768 对 40k 输入
+分段 JSON 输出留了一倍余量；不必再大，因为烧穿的判定（空内容 + length）已经
有测试和账本覆盖（12:50 与 15:30 条目）。附带修复：空内容失败路径原先在读取
`response.usage` 之前抛错，长度烧穿调用的真实消耗在账本里记 null——现在
`ModelOutputError` 携带 usage 与 finish_reason 入账
（`src/expedistill/llm/client.py`、`llm/call.py`、`infra/reliability.py`），
信息不再被丢弃。

## 已知观察（2026-10-11，用户决定暂不处理）

真实数据整链验证（adaptive-rejection-sampler，9 个候选 skill）中裸答正确率 7/9，
接受率被 weak_already_knows 压到 1/9。两种解释未定：考题对文本问答形式偏易/偏泛，
或段级能力弱模型确实多半已具备、缺口集中在全任务组装（这本身是研究发现）。
单题样本不足以下结论。用户决定：暂不改出题方法，先在更多题上观察裸答通过率分布，
再决定是否调整 EXAM_GEN 难度要求或向程序化考题演化。

同轮修复（已合入）：JUDGE 模板禁止错误描述陈述或暗示修法（防把答案递给 G6）；
`EXPEDISTILL_LLM_TIMEOUT_SECONDS` 可调客户端超时（默认 300s）。（合并主线后更正：该环境变量已删除，统一用主线的 `--llm-timeout` 运行参数，避免"设了环境变量却被 pipeline 默认值盖掉"的陷阱。）
