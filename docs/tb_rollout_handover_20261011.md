# TB2.1 远程 Rollout 链路交接材料

写给「强弱模型交互 / ExpeDistillation-TB」适配侧。来源：TB2.1 裸 rollout 评测 campaign（2026-10-09 ~ 10-11，已收官）。
**本仓库的完整手册在 `scripts/tb21/README.md`（本仓库同一 git 工作区内）**，本文是快速上手 + 那份手册的索引，细节以它为准。

> 密钥约定：**任何密钥值都不写进任何文件/仓库/日志**。密钥只有两处（见 §3），用到时加载进进程环境。

---

## 1. 远程运行方式（一次 rollout 的完整生命周期）

### 1.1 机器与登录

| 项 | 值 |
|---|---|
| 机器 | node29 = `10.31.112.40`（腾讯 VPC 内网，必须从内网可达的机器连） |
| 用户 | `xinhaidong`（sudo 可用但避免高危操作） |
| 登录 | `ssh -S none -o BatchMode=yes -i ~/.ssh/id_ed25519 xinhaidong@10.31.112.40` |
| 工作约束 | 只在 `~/niuyiming/SkillExpand-tbeval/` 和 `/data2/niuyiming/` 下写东西；`/data2/liyishan/*` 只读；不碰他人进程；不用 `pgrep -f`/`pkill` |

### 1.2 远程目录结构

```
/data2/niuyiming/scripts/          # 评测代码包（= 本仓库 scripts/tb21/，已 md5 对齐）
  run_tencent_tb21_smoke_niuyiming.sh   # runner：生成 Harbor job YAML 并启动
  recover_rolling.py                    # 滚动启动器：TSV 队列逐条独立投喂
  campaign.env                          # 一次 campaign 的全部开关（网络/重试/时限）
  audit_trials.py                       # 审计+指标（异常处理契约的代码化）
  archive_trajectories.py               # 最终轨迹归档
  build_rerun_queue.py                  # 从审计缺口生成补跑队列
  rerun_queue_*.tsv                     # 队列：run_id/model/effort/task 四列 TSV
  python/tencent_vpc_dns_env.py         # 沙箱网络修复（DNS/apt 源），离线可测
  vendor/                               # 三层依赖快照：题库89题 + 腾讯适配层 + claw-harbor
  secrets.env                           # 密钥（600 权限，gitignore）
~/niuyiming/SkillExpand-tbeval/     # env.sh（加载 ~/agentbrew-tb21/.env + venv 指向）、logs/
/data2/niuyiming/<run_root>/        # 每代产物：jobs/<run_id>/<task__id>/、configs/、manifest.tsv
  e1_full_q36flash_2att/            # 历史全量（qwen 2条）
  e1_full_dsv4max_5att/             # 历史全量（dpsk 5条）
  e1_fix/ e1_n5/                    # 补跑代
  e1_final_archive/                 # 最终归档：890 条计入指标的轨迹（20GB）
  audit_*.json                      # 审计明细
```

单条 trial 的产物：`jobs/<run_id>/<task__>/<task>__<suffix>/` 下有 `agent/trajectory.json`（模型轨迹）、`verifier/test-stdout.txt`（pytest 输出）、`verifier/reward.txt`（分数）、`result.json`（**trial 级**：`verifier_result.rewards.reward` + `exception_info`）。注意 **run 级** `jobs/<run_id>/result.json` 是另一种 schema（`stats.evals`），别混（我们自己踩过：拿 run 级文件问 trial 级字段，误以为数据丢了）。

### 1.3 提交（射后不管）

不用 nohup。规范做法（Popen + start_new_session，AGENTS.md 的事故换来的）：

```bash
cd /data2/niuyiming/scripts && . ./campaign.env
python3 - <<'EOF'
import os, subprocess
h = os.environ.get("TBENCH_LOGS_DIR", os.path.join(os.getcwd(), "logs"))
p = subprocess.Popen(["python3", "recover_rolling.py",
                      "队列.tsv", "/data2/niuyiming/<新代>/jobs"],
                     env=os.environ.copy(), stdout=open(h + "/xx.log", "ab"),
                     stderr=subprocess.STDOUT, start_new_session=True)
open(h + "/xx.pid", "w").write(f"{p.pid}\n")
EOF
```

队列 TSV 每行 = 一次独立的 runner 调用（1 trial）。启动器每分钟投 2 条、不设并发上限——**不要**整批冷启动（第一批全量的沙箱启动超时就是突发冷启动造成的）。

### 1.4 轮询与取回

- **投喂器判活**：`kill -0 $(cat logs/xx.pid)`（只认 pidfile，禁 `pgrep -f`）；
- **进度**：`find <jobs_dir> -mindepth 2 -maxdepth 2 -name result.json | wc -l`；
- **完成标志**：投喂器日志末尾 `ALL DONE`；
- **轨迹/分数**：trial 目录如上；**用量（token）我们没做过落盘**——litellm 打了日志但未汇总，这块你们要自己加；
- 每题 trial 数：看 `<run_root>/manifest.tsv`（每条记录设置，含全部 TBENCH_* 值）。

### 1.5 skills 模式（如果你们要跑 skill 而不是裸 rollout）

`TBENCH_EVAL_MODE=skills`（默认 bare）。runner 会把 `TBENCH_SKILL_ROOT/<task>/SKILL.md` 经 `tencent_tb2_skill_env` 注入沙箱的 `/opt/openclaw-skills/`。我们只跑过 bare，skills 链路没有在新网络下验证过，注意先 smoke。

---

## 2. 代码清单（同一 git 工作区，双份完全一致）

本地：`/Users/yimingniu/Code/SkillEvolvePaperList/scripts/tb21/`（git，唯一权威源）
远程：`/data2/niuyiming/scripts/`（由 `git archive HEAD` 部署，md5 已对齐）

| 文件 | 角色 |
|---|---|
| `run_tencent_tb21_smoke_niuyiming.sh` | **runner**。从环境变量生成 Harbor job YAML（模型、环境类 import_path、重试白名单、verifier 时限、PyPI 镜像注入），`exec` liyishan 的 `run_tencent_job.py`。所有路径默认相对脚本目录，可用 env 覆盖 |
| `recover_rolling.py` | 滚动投喂器（见 1.3），并把 campaign.env 的全部值记进 manifest |
| `campaign.env` | 一次 campaign 的全部开关（见 §3） |
| `python/tencent_vpc_dns_env.py` | E2B 沙箱启动后修 DNS（VPC DNS 插最前）+ bullseye 启用 snapshot apt 源；`python/test_*.py` 6 个离线用例 |
| `audit_trials.py` | **审计标准**：逐条 trial 判 VALID / RERUN_FAKE0 / RERUN_INFRA / KEEP_MODEL / RUNNING，出 avg@N、pass@N（分母固定 89）；RUN_OVERRIDES 记录人工复核改判 |
| `archive_trajectories.py` | 归档计入指标的前 N 条轨迹到 `e1_final_archive/`（含 manifest 与指标重算交叉验证） |
| `build_rerun_queue.py` | 审计 JSON → 补跑队列 TSV |
| `vendor/tasks/` | 89 题题库快照（Docker 镜像在腾讯镜像仓 `modelbest.tencentcloudcr.com/terminalbench/runtime:<task>`） |
| `vendor/claw-harbor/` | Harbor 0.2.0 源码（**不在 PyPI**，main@37a234f8de64） |
| `vendor/tb21-tencent-skill/python/`、`vendor/framework-python/` | liyishan 的腾讯适配层快照（run_tencent_job.py、E2B 环境类、Terminus agent） |
| `bootstrap.sh` | 新机器复原 venv：python3.12 + `vendor/requirements.txt` + `vendor/claw-harbor` |

**runner 核心内容不贴了**——你就在同一工作区，直接读 `scripts/tb21/run_tencent_tb21_smoke_niuyiming.sh`（约 250 行，注释完整；README §3 有开关表）。

---

## 3. 环境变量与当前实际取值

### 3.1 连接与路径
- host/user/key：见 §1.1；远程代码根 `/data2/niuyiming/scripts`；结果根默认 `TBENCH_RESULTS_ROOT=/data2/niuyiming`
- 队列文件：`rerun_queue_20261010.tsv` / `_20261011.tsv` 可作格式样例

### 3.2 模型
- qwen 臂：`MODEL_NAME=qwen3.6-flash-distill`，**不带** `TBENCH_REASONING_EFFORT`（默认 effort）
- dpsk 臂：`MODEL_NAME=DEEPSEEK_up5zdj`，`TBENCH_REASONING_EFFORT=max`
- 两者都走 `MODEL_API_BASE=https://llm-center.modelbest.co/v1`（OpenAI 兼容，Harbor 里是 `openai/<model>`）

### 3.3 campaign.env 的当前值（启动前 source）
```bash
export TBENCH_VPC_DNS=183.60.83.19                       # VPC DNS，内网镜像解析到 169.254.0.3
export TBENCH_PYPI_MIRROR=https://mirrors.tencentyun.com/pypi/simple/
export TBENCH_APT_SNAPSHOT_BULLSEYE=1                    # 仅 bullseye 题启 snapshot.debian.org
export TBENCH_APT_INTRANET_MIRROR=0
export TBENCH_MAX_TRIAL_RETRIES=1                         # 整条重跑白名单：AgentSetupTimeout/Sandbox/AddTestsDir
export TBENCH_VERIFIER_OVERRIDE=7200.0                    # verifier 时限统一 2h
export TBENCH_SETUP_MULTIPLIER=3                          # 沙箱启动时限 ×3
```

### 3.4 其余常用
`TBENCH_RUN_MODE=selected|full|smoke`、`TBENCH_TASK_NAMES`、`TBENCH_N_ATTEMPTS=1`、`TBENCH_N_CONCURRENT=1`、`RUN_ID`、`JOBS_DIR`、`DRY_RUN`（=1 只生成并校验 YAML，**接新链路先跑 DRY_RUN**）、`TBENCH_E2B_RELAY_TEMPLATE=code-agent-cfs2-new`（E2B relay 沙箱模板名）。

### 3.5 密钥（只指位置，不写值）
- **本地**：`scripts/tb21/secrets.env`（chmod 600，gitignore）——含 `E2B_API_KEY`、`E2B_DOMAIN=ap-beijing.tencentags.com`、`TBENCH_LLM_KEY`（作为 `OPENAI_API_KEY` 传给 Harbor）
- **远程**：`~/agentbrew-tb21/.env`（同三个键）+ `/data2/niuyiming/scripts/secrets.env`（同一文件的部署副本）
- 加载方式：runner 自动 source `<脚本目录>/secrets.env`（`TBENCH_TENCENT_ENV_FILE` 可改）；远程交互 shell 用 `~/niuyiming/SkillExpand-tbeval/env.sh`

### 3.6 轮询参数
没有固定轮询器：投喂器自己按 1 条/30s 节奏投喂；我们外部监控用 60s 轮询 result.json 数量 + pidfile 判活；deadline 无全局值（agent 上限 7200s、verifier 上限 7200s，单 trial 天花板 ≈ 4.5h）。

---

## 4. 坑与经验（全部实际踩过）

1. **伪 0 分**：verifier 依赖装不上时 reward.txt 照样写出 0，但 pytest 根本没跑。判据：verifier 日志无 pytest 的 `collected N items` ⇒ 伪 0 分，必须补跑。每条 0 分都要看这个标志；拿不准的做二次轨迹复核（`audit_trials.py` 的 `RUN_OVERRIDES` + README §5.4）。
2. **沙箱网络**（第一批全量的主要死因，全部由 `campaign.env` 修掉）：
   - 沙箱默认只有公网 DNS，解析不了内网域名 ⇒ VPC DNS 注入；
   - 官方 PyPI 0.19MB/s，uv 0.9.5 不支持断点续传 ⇒ 内网镜像后 torch 865MB 5 秒；
   - Debian bullseye 的 curl 从源里下架（404）⇒ snapshot.debian.org 源；qemu 两题 14 条全伪 0 → 修复后 10/10 全真跑。
   - **公网腾讯镜像会截断大文件，别用**。
3. **GitHub 不走镜像、间歇掐断**（装 uv、uv 拉 Python 解释器、个别题 git clone）：没根治，靠"伪 0 分 → 重跑"消化，同题重试常就好。
4. **两级 result.json**：run 级（`stats.evals`）与 trial 级（`verifier_result`/`exception_info`）schema 不同，读错级别会误判"数据丢失"。
5. **agent 弄坏沙箱自身网络也算模型失败**（例：qemu 题里 agent 改 eth0/iptables 把沙箱网络搞坏，verifier 连不上镜像）——人工复核后 KEEP_MODEL 计 0，不补跑。
6. **运行中的 bash 脚本不能覆写**（bash 惰性读取），改 runner 前先确认无运行中的 trial。
7. **判活禁 `pgrep -f`**（会匹配到 launcher 自身，历史上引发过重复启动）；只认 pidfile+`kill -0`。
8. **投喂节奏**：每分钟 2 条滚动，别整批并发冷启动。
9. **evals 的异常分类契约**是预注册的（tmux 按键失败/AgentTimeout = 模型失败计 0；下载失败/沙箱 RPC/LLM 服务 = 补跑），别看结果后临时改口径——完整表在 README §5。
10. **嵌套 .gitignore 陷阱**：vendor 进来的上游仓库自带 .gitignore，会把真实文件挡出 git（我们丢过 5 个文件，`git add -f` 补回）。vendor 大目录后要 diff 一下"目录里有、git 里没有"的文件。
11. 新机器前提：腾讯 VPC 内网 + python3.12 + `./bootstrap.sh` + secrets.env。离开腾讯内网整套网络修复全部失效。

## 5. 最终基线（你们可以对比/复用）

89 题裸 rollout，每题 5 条有效轨迹（2026-10-11 收官审计）：
- qwen3.6-flash-distill（默认 effort）：avg@5 = 0.4697，pass@5 = 59/89
- DEEPSEEK_up5zdj（effort=max）：avg@5 = 0.6315，pass@5 = 76/89
- 归档：`/data2/niuyiming/e1_final_archive/{qwen,dpsk}/<task>/01..05_<trial>/` + `manifest_*.tsv` + `summary.json`

## 6. 我们没有的信息

- token 用量的落盘与汇总（未实现）；
- skills 评测模式在新网络下的验证（只验证过 bare）；
- GitHub 下载的根治方案（现在是"重跑消化"）。
