# mcmc-sampling-stan / attempt 1（失败）

- trial：`traj/tb:mcmc-sampling-stan/strong/01`；步数 44；读取的技能：`bank/merge/0006`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里

- 卡点：第 41 步。第 16–18 步把源码编译的 rstan 2.32.7 装进私有库 `/app/Rlibs`（`R_LIBS_USER=/app/Rlibs R CMD INSTALL`），第 41 步又用 `cp -a /app/Rlibs/rstan /usr/local/lib/R/site-library/rstan` 把它复制进默认库，覆盖了第 6 步 apt 装好的 rstan 2.32.5 的解析优先级；复制之后只跑了一次 `library(rstan)` 就提交，没有在"默认库"状态下再编译一次模型。
- 过程：
  - 第 0 步：`/app` 只有 `data.csv`；R 4.3.3，rstan 未安装。
  - 第 1 步读 `bank/merge/0006`；第 2–4 步探测编译器、CRAN/apt 源与各版本。
  - 第 6 步 apt 装 `r-cran-rstan`（2.32.5，连带 StanHeaders 2.32.5、BH 1.84.0 等整棵依赖树）。
  - 第 16–18 步在 `/tmp/rstan_src` 源码编译 rstan 2.32.7 到 `/app/Rlibs`。
  - 第 20–38 步写 `hierarchical_model.stan` 与 `analysis.R`（首行 `.libPaths(c('/app/Rlibs', .libPaths()))`），后台跑 4 链 × 10 万迭代，产出 `posterior_alpha_mean.txt` = 2.876366、`posterior_beta_mean.txt` = 16.3749，日志正常。
  - 第 41 步把 rstan 复制进默认库，验证 `library(rstan)` 返回 2.32.7（"loaded from: /usr/local/lib/R/site-library/rstan"）；第 42–43 步提交。
- 直接原因：判分器 6 项测试 5 过 1 挂。挂的是 `test_hierarchical_model_implemented`：测试用 `R --slave -e "library(rstan); ... stan_model(model_code = paste(model_code, collapse='\n'))"` 编译模型，返回码 1，stderr 为 `Error in stan_model(...) : Boost not found; call install.packages('BH')`（ctrf trace 与 test-stdout 均如此，test_outputs.py:49）。同一轮里 `test_rstan_package_installed`、`test_r_script_created_and_used_rstan`（193 s）、`test_stan_model_sampling`（193 s）都过了——即 `library(rstan)` 正常、agent 自己的 `analysis.R`（内部前置 `/app/Rlibs`）也能跑通并采样；只有判分进程"从默认库直接编译模型"这一条路失败。也就是说，第 41 步的复制把默认库里的 rstan 从与默认库 StanHeaders 2.32.5 / BH 1.84.0 配套的 apt 2.32.5 换成了手搓 2.32.7（加载时报 "rstan version 2.32.7 (Stan version 2.32.2)"），而这次改动之后没有用判分器的调用方式复验编译。

### 与同题成功 attempt 的差异

- `successes/mcmc-sampling-stan/a00`（40 步，读取 `bank/merge/0006` + `bank/merge/0004`）：第 4 步直接用 CRAN 的 `install.packages("rstan", dependencies=TRUE)` 装进**默认库**（日志 "Installing package into '/usr/local/lib/R/site-library'"，第 13 步可见 BH 1.90.0）；第 28 步写模型（beta-binomial 边际化版本）；第 34 步 `Rscript -e 'library(rstan); model <- stan_model(file="/app/hierarchical_model.stan"); ...'`——**不带任何 `.libPaths` 前置**——成功编译并采样，第 35–36 步产出同样的 posterior 文件与发散检查。
- 差别一句话：成功者在默认库里装齐整套 CRAN 组件，并在默认库状态下实际**编译 + 采样**过模型；a01 在 apt 基础栈上又覆盖了一个手搓 2.32.7，最后一次库布局改动之后只验证了"包能加载"，判分器真正要跑的 `stan_model` 编译没有在最终状态下复验。

## 2. 技能库里有没有能解决它的技能

- 候选：
  - `bank/merge/0006`（相关性 高，已读）——"probe → 获取/安装 → 功能验证 → 修复循环"的通用安装卡；本案缺的正是"在最终环境状态下做功能验证"这一步，卡里第 1、3、4 步都直接对应。
  - `bank/merge/0004`（相关性 中）——提交前门禁：用判分器会用的解析器/独立参考复读产物；同题成功的 a00 也读了它。它能推动"按判分器的调用方式重跑"，但不能给出 R 库路径知识。
  - `bank/merge/0002`（相关性 低）——"标准库搭贝叶斯模型"；与 R/RStan 环境无关，仅题目领域相近。
  - 其余 14 个成员（视频 OCR、变体打分、数据清点、异步、源码构建的 C 分支、SQLite/二进制、DNA、HTML 过滤、调度器、路径追踪等）与"R 包安装与默认库可见性"无关。
- 缺的能力：R/RStan 环境供给——把包装到**判分进程可见的默认库**、并在一个干净的 R 进程里用判分器同款调用（`library(rstan)` + `stan_model` 编译 + `sampling`）验证三件事同时成立，而不是依赖脚本内部的 `.libPaths` 前缀。

## 3. 能解决问题的技能被触发了吗？为什么没解决

- 触发：读了 `bank/merge/0006`，内容覆盖问题；属于"读了但模型没照做"（部分照做）。
- 原因：0006 第 1 步要求 "Run the task's availability check"，第 3 步 "Never advance on an unverified probe"，第 4 步 "Install only the named missing package or tool, then re-probe. Never skip a failed probe"，摘要承诺 "functionally verifying software artifacts"。agent 的探测做得不少（第 2、14、22、39 步），但在第 41 步改变默认库解析结果之后，只做了 `library(rstan)`（加载 ≠ 编译），没有跑题面要求的可用性检查（编译一个 Stan 模型）。此外 0006 的步骤偏向 C 构建（configure/make/ldd），对 "R 包装到哪个库、谁能看见" 没有专门条文，这是内容的次要缺口——但同题成功者在同一张卡下做对了，说明主要差别在执行，不在卡。

## 4. 结论与依据

- 主因：A_model_capability
- 一句话依据：第 41 步把源码编译的 rstan 2.32.7 覆盖进默认库后只验证了 `library(rstan)`，判分器用默认库跑 `stan_model` 编译立刻报 `Boost not found`；同题成功的 a00 用整套 CRAN 栈在默认库下编译 + 采样通过，说明环境可解、差距在最终状态的验证纪律。
- 次要因素：E（基础镜像没有 rstan；apt 2.32.5 组件与手搓 2.32.7 混搭本身脆弱，是失败的放大器，但不是不可解）；B（`bank/merge/0006` 缺 "R 库可见性" 专条）。
