日期：2026-10-20
一句话说明：SearchQA / ALFWorld 上运行与 TB21 live run 同方法序列实验的操作手册。
对应 log.md 条目：2026-10-20 SearchQA live-run 正式实验启动。

# 本地 benchmark live-run 运行手册

适用对象：`benchmark: searchqa` 或 `benchmark: alfworld`，方法序列为
`baseline → learn → live → learn → live → learn → test`，测试相位使用
`whole_bank` 提示词注入（整理阶段把已接受技能压缩到 10 条以内），对照为
`fresh_bare`。

## 1. 环境变量

从仓库根目录加载 `.env`（不进入 git）：

```bash
set -a; . ./.env; set +a
```

SearchQA 需要：

- `EXPEDISTILL_SEARCHQA_EVAL_DIR`：外层 `benchmark/searchqa-eval` 的路径。
- 评测数据已物化在 `<该路径>/data/searchqa_split/{train,val,test}/items.json`。
- 每局步数上限默认 6，可用 `EXPEDISTILL_SEARCHQA_MAX_STEPS` 覆盖。

ALFWorld 需要：

- `EXPEDISTILL_ALFWORLD_EVAL_DIR`：外层 `benchmark/alfworld-eval` 的路径。
- `EXPEDISTILL_ALFWORLD_VENV`：该仓库 `.venv/bin/python` 的路径。
- `ALFWORLD_DATA`：包含 `json_2.1.1/` 与 `logic/` 的数据根目录，
  本机为 `benchmark/alfworld-eval/.data/alfworld`。
- `ALFWORLD_CONFIG`：`benchmark/alfworld-eval/configs/textworld.yaml`。
- `ALFWORLD_BENCH_SRC`：`benchmark/alfworld-eval/src`。
- 每局步数上限默认 50，可用 `EXPEDISTILL_ALFWORLD_MAX_STEPS` 覆盖。

模型端点使用 `.env` 中的 `EXPEDISTILL_LLM_ENDPOINT` 与
`EXPEDISTILL_LLM_API_KEY`。启动前先发一次真实生成请求确认端点可用。

## 2. 生成实验配置

```bash
.venv/bin/python scripts/run/build_local_live_config.py \
    --benchmark searchqa \
    --out configs/experiments/searchqa_split_live_run_20261020.yaml
```

脚本从 SkillOpt 冻结清单读取划分（SearchQA 400 训练 / 1400 测试，
ALFWorld 39 训练 / 134 测试），生成 schema v6 配置，并通过真实插件
加载全部任务做校验。校验现有文件是否与生成结果一致：

```bash
.venv/bin/python scripts/run/build_local_live_config.py \
    --benchmark searchqa \
    --out configs/experiments/searchqa_split_live_run_20261020.yaml --check
```

## 3. 启动

先跑 stage 0 冻结，确认任务集与提示词冻结无误，且不消耗模型调用：

```bash
.venv/bin/python -m expedistill.cli run \
    --config configs/experiments/searchqa_split_live_run_20261020.yaml \
    --run-dir runs/searchqa_split_live_run_20261020 \
    --until-phase -1
```

正式启动（脱离当前进程组，写 pidfile）：

```bash
scripts/run/run_experiment.sh \
    configs/experiments/searchqa_split_live_run_20261020.yaml
```

脚本默认 `WORKERS=64`，可用环境变量覆盖；`RUN_DIR` 可指定运行目录。
脚本输出 run 目录、pidfile、控制台日志路径与断点续跑命令。

## 4. 监控

```bash
RUN=runs/<run 目录>
cat "$RUN/driver.pid"                      # 进程号
tail -n 50 "$RUN/driver.console.log"       # 标准输出与错误
cat "$RUN/pipeline_log.jsonl"              # 每完成一步追加一行
.venv/bin/python -m expedistill.cli summary --run-dir "$RUN"
```

判断进程是否存活用 `kill -0 "$(cat "$RUN/driver.pid")"`，不要使用
`pgrep -f`。每个相位完成时 `pipeline_log.jsonl` 会追加一行；阶段摘要与
用量都在磁盘上。

## 5. 中断与续跑

驱动退出后，用启动脚本打印的同一条命令重跑即可。已完成的单元按确定 ID
跳过，只重试未完成或失败单元。退出码含义：

- `0`：序列跑完；
- `3`：部分单元耗尽基础设施重试，可原命令续跑；
- `1`：配置、冻结协议或模型输出错误，需要人工处理后再续跑。

## 6. 结果与审计

实验结束后先做完整性审计，再引用数字：

```bash
.venv/bin/python scripts/audit/audit_live_run.py --run-dir "$RUN"
```

`summary` 子命令读取 `pipeline_log.jsonl` 与用量账本，不重新执行任何
任务。测试相位的摘要包含 `control`、`with_bank`、`delta`，SearchQA 还会
带 `sub_em` 辅助指标。

## 7. 已知约束

- 本地 benchmark 的动作空间没有文件读写，`folder` / `real_folder`
  交付不可用；测试相位使用 `whole_bank` 注入，整理阶段把已接受技能压缩
  到 10 条以内，保证提示词不超出弱模型上下文。
- `configs/seed/` 只有 TB21 的外部 campaign 成绩，本地实验的测试对照
  使用 `fresh_bare`。
- 弱模型画像默认关闭（`use_weak_profile: false`）。需要时用
  `weak_profile_file` 指向 `configs/profiles/` 下的文本。
- 每局步数上限与 ALFWorld 随机种子通过环境变量控制，不进入 manifest；
  跨运行比较时必须保持相同取值。
