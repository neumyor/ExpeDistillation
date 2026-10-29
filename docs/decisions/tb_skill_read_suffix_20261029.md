# 决定：在 TB 执行 prompt 里加终端卫生与判分余量规则

日期：2026-10-29
一句话：把“嵌套 heredoc 纪律 / 死终端恢复 / 性能阈值留 30–50% 余量 / 蒙特卡洛误差远小于判分带宽”写进 `TencentSkillTerminus2.skill_read_suffix`，这是唯一会真正进入远程执行沙盒的 prompt 位置。
对应 log.md：2026-10-29 的“TB 执行 prompt 加终端与阈值纪律；tmux 能力错误改为 infra”条目。

## 背景

P07 的 11 条环境相关失败里有两类只能在执行 prompt 里修：

- `rstan-to-pystan/00`、`make-mips-interpreter/02`：模型嵌套 heredoc 把终端弄死，
  之后 100+ 步无法恢复。技能卡救不了，因为终端死了连 `cat` 都没有输出。
- `rstan-to-pystan/01、/02`、`portfolio-optimization/00`：实现正确，但 rho 差
  <1 个 MC 标准误、加速比 1.118 对 1.2。判分带宽本身偏紧，但 agent 侧也有可做的：
  样本量不够、性能只测一次。

检查发现 `skill_read_prefix` 这个名字在仓库里不存在；实际位置是 vendor 的
`TencentSkillTerminus2.skill_read_suffix` 默认参数，它被追加到每个 trial 的
instruction 后面，由 Harbor 送进远程沙盒。**关键事实**：远程执行读的是 基准主机 上的
副本 基准主机上 vendor 目录下的 `tencent_sandbox_terminus.py`，
不是本工作区的镜像；只改镜像不会影响任何 run。

## 决定

1. 在 基准主机 的副本和本工作区镜像里同时替换该默认文本（两份内容逐字节相同），
   新增两节：
   - **Shell hygiene**：不嵌套 heredoc；多行输入后用 `echo SHELL_OK` 确认；提示符
     不回显时 Ctrl-C → `reset` → 新开 `tmux new-window`，不要继续盲打。
   - **Graded thresholds**：性能阈值留 30–50% 余量（1.2x 要求就目标 1.5x），多次
     测量取中位数；数值估计让 MC 误差远小于判分带宽，换种子重跑到两次都落在带内
     再提交。
2. 原文备份在 基准主机 同目录 `tencent_sandbox_terminus.py.bak_20261029`。
3. 后缀文本 sha256 = `0c0e5ec05859221310e36d108c31d5640ef6dd8384119c9fcea3474c5b788f27`
   （1421 字符 / 11 个换行），本地与远程已核对一致。

## 后果与注意

- 这是一个**行为变更**：按 AGENTS“改一个字也是新实验”，用新文本的 run 必须新建
  目录；已完成的 run（1322、2053 分叉）冻结在旧文本上，不受影响。
- 这段文本**不在 manifest 的 `prompt_digests` 里**（它属于 vendor 代码而不是我们
  注册的 prompt）。所以 run 的 provenance 必须手工引用上面的 hash；这是当前链路的
  一个已知缺口，后续可以考虑把这段文本搬进本仓库并随 manifest 冻结。
- 它只影响远程 TB 执行（live/test）；P03/P05 的纸面考试走本地 LLM 调用，不经过这里。
- 它不修判分器/镜像问题（sshpass、镜像缺依赖、判分带宽本身）；那些仍按前面的
  分析走 benchmark 侧修复或敏感性说明。
