# mcmc-sampling-stan / attempt 2（失败）

- trial：`traj/tb:mcmc-sampling-stan/strong/02`；步数 41；读取的技能：`bank/merge/0006`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里

- 卡点：第 3 步 + 第 37 步。第 3 步把 rstan 及其全部依赖装进**私有库** `/app/RLib`（`install.packages(..., .libPaths(c("/app/RLib", ...)))`）；此后每一次加载/编译验证（第 22、24、37 步）都在命令里前置 `.libPaths(c("/app/RLib", ...))`，从未把这些包暴露给默认库。第 37 步是提交前最后一次编译检查，仍然带着这个前缀，所以它验证的是"我的脚本能跑"，不是"判分进程能跑"。
- 过程：
  - 第 0 步：`/app` 只有 `data.csv`；R 4.3.3，rstan 未安装。
  - 第 1 步读 `bank/merge/0006`；第 2 步查 CRAN 版本：rstan 2.32.7、StanHeaders 2.39.1、`LinkingTo` 含 BH ≥ 1.75。
  - 第 3 步一次性把 inline/gridExtra/RcppParallel/loo/pkgbuild/QuickJSR/ggplot2/RcppEigen/BH/StanHeaders/rstan 装进 `/app/RLib`；第 10 步补 gfortran/lapack/r-base-dev，第 14 步补 cmake，第 18 步清 00LOCK 后重装 rstan。
  - 第 22 步 `library(rstan)` 成功（2.32.7 + StanHeaders 2.39.1）。
  - 第 24 步首次冒烟编译失败：StanHeaders 2.39 不接受旧数组语法（`Ill-formed declaration ... old array syntax`）；第 33 步按新语法重写 `hierarchical_model.stan`（`array[N] int<lower=0> y;`）并写 `analysis.R`（首行 `.libPaths(c("/app/RLib", .libPaths()))`）。
  - 第 34–36 步后台跑 4 链 × 10 万迭代，产出 `posterior_alpha_mean.txt` = 2.86729815285、`posterior_beta_mean.txt` = 16.3288242718。
  - 第 37 步 `Rscript -e '.libPaths(...); library(rstan); sm <- stan_model(file=...)'` 打印 "stan model compiles ok"；第 38 步最终核对；第 39–40 步提交。
- 直接原因：判分器 6 项测试 4 过 2 挂。`test_rstan_package_installed` 与 `test_hierarchical_model_implemented` 都失败在 `Error in library(rstan) : there is no package called 'rstan'`——判分进程用的是**默认 `.libPaths`**（不含 `/app/RLib`），看不到 agent 装的包。其余 4 项（`test_r_script_created_and_used_rstan` 124 s、posterior alpha/beta、`test_stan_model_sampling` 125 s）通过，因为 `analysis.R` 自己会 `.libPaths` 前置私有库，脚本本身是自洽可跑的。也就是说：交付物能跑，但"环境里装好了 rstan"这件事在判分口径下不成立。

### 与同题成功 attempt 的差异

- `successes/mcmc-sampling-stan/a00`（40 步，读取 `bank/merge/0006` + `bank/merge/0004`）：同样从 CRAN 装 rstan，但**装进默认库**（第 4 步 `install.packages("rstan", dependencies=TRUE)`，日志 "Installing package into '/usr/local/lib/R/site-library'"）；第 34 步与第 36 步的编译、采样命令都不带 `.libPaths` 前缀，等于在判分器同款环境下验证过全链路。
- 差别一句话：a02 私有库安装 + 私有库验证（自证），成功者默认库安装 + 默认库验证；第 34 行 `analysis.R` 的 `.libPaths` 前置让 a02 的所有自检都成立，恰好掩盖了判分框看不到包的问题。

## 2. 技能库里有没有能解决它的技能

- 候选：
  - `bank/merge/0006`（相关性 高，已读）——安装/构建/功能验证通用卡，摘要里明确有 "PATH installation"：把装好的东西放到**后续进程可见**的位置，而不是只让自己的 shell/脚本可见。本案正是这条没被迁移到 R 库。
  - `bank/merge/0004`（相关性 中）——提交前门禁：按判分器的路径/环境复读产物；a00 读了它。它能提醒"按评分侧的方式重放"，但对库路径同样只给原则不给细节。
  - `bank/merge/0002`（相关性 低）——贝叶斯/依赖模型构建（标准库路线），与 R 包环境无关。
  - 其余 14 个成员与本题无关。
- 缺的能力：R 包"安装位置 = 判分进程默认库"的供给与校验——用干净 R 进程（无 `.libPaths` 前缀）验证 `library(rstan)` / `stan_model` / `sampling` 三件事，或把私有库内容复制/软链进默认库后再验证。

## 3. 能解决问题的技能被触发了吗？为什么没解决

- 触发：读了 `bank/merge/0006`，内容相关但被"部分照做"。
- 原因：0006 第 1 步 "Run the task's availability check"、第 4 步 "Install only the named missing package or tool, then re-probe"，以及摘要的 "PATH installation" 都指向"装完要在目标进程可见的位置复测"。agent 把复测做成了带 `.libPaths` 前缀的复测（第 22、24、37 步），等价于在自己的环境里自证；第 37 步打印 "stan model compiles ok" 之后没有任何一次**去掉前缀**的检查。卡本身对 C 构建有 configure/make/ldd 细节，对 R 包库路径没有专条（次要内容缺口），但同题成功者用同一张卡做对了，所以主因在执行。

## 4. 结论与依据

- 主因：A_model_capability
- 一句话依据：rstan 与全部依赖只装进私有 `/app/RLib`，所有验证都前置 `.libPaths`；判分器用默认库 `library(rstan)` 直接报 "there is no package called 'rstan'"，同题成功 a00 装进默认库并在默认库下跑通编译与采样。
- 次要因素：B（`bank/merge/0006` 无 R 库可见性专条）；E 不成立（同一镜像、同一任务，a00 已证明可解）。
