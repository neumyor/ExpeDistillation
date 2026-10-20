# 弱模型画像开关（use_weak_profile）20261020

日期：2026-10-20
说明：新增实验级开关 `use_weak_profile`，默认关闭弱模型画像；记录位置选择、提示词处理与 schema v5 迁移。
对应 log.md：2026-10-20 12:04 — 新增弱模型画像开关，默认关闭。

## 背景

弱模型画像 `weak_profile` 是一段手写的能力边界描述，原先无条件进入 G2 写作、G6 改写、G10 live 归因和 G11 live 修订的提示词。2026-10-19 的 TB21 重跑里，画像内容开始影响技能写法；用户要求之后的实验默认不再使用画像，只有显式说明的实验才打开。

## 决定

1. 开关放在实验配置层：`ExperimentConfig.use_weak_profile: bool = False`，与 `weak_profile` 相邻，冻结进 manifest。
2. 默认关闭：之后所有没有显式写 `use_weak_profile: true` 的配置都不把画像正文送进提示词。
3. 画像文本继续保留在配置里，作为来源记录和 opt-in 素材；关闭不等于删除文本。
4. 阶段层通过 `ExperimentConfig.effective_weak_profile()` 投影：开启返回画像正文，关闭返回空字符串。方法层与积木层仍然只接收一个字符串，不需要知道开关。
5. 提示词层新增 `profile_section` 变量：开启时是“画像标题 + 正文”，关闭时是中性段落（没有画像，按一般能力终端 agent 写）。只传空字符串不够，因为提示词里还有“先读画像”的固定指令。
6. schema 升到 5；新增纯函数 `upgrade_v4_config`：v4 配置升级时 `use_weak_profile` 默认 false，画像文本原样保留。`load_config` 对 v4 文件做内存升级；旧 run 的 manifest 不迁移，继续在维护分支上运行。
7. 提示词 digest 变化，属于新实验；golden 矩阵与 Wave 4 披露按新行为重新生成，并在披露里新增 `WEAK_PROFILE_SWITCH` 根因。

## 未选的方案

- **只传空字符串**：提示词仍要求“先读画像”，会出现空画像与指令矛盾；不采用。
- **每个方法族各加开关**：画像同时被写作、改写、归因、修订使用，分散开关会不一致；不采用。
- **不加 schema 版本**：按仓库规则，新增配置字段必须升 schema_version；不采用。
- **新增五个 `*_NO_PROFILE` 提示词变体**：能工作，但多五份重复模板和 digest；不采用。

## 后果

- 新实验默认无画像；需要画像时显式写 `use_weak_profile: true`。
- 既有 v4 配置通过 `load_config` 仍可加载，但默认行为变为无画像；要复现旧的画像行为必须显式打开。
- 旧 run 的冻结 manifest 是 v4，当前代码拒绝直接续跑；按 AGENTS 第六节在各自维护分支上续跑。
- golden 矩阵的模型调用数不变（34/18/22/12），但技能正文与请求文本按无画像提示词变化；Wave 4 披露记录了这一差异。

## 工件

- `src/expedistill/core/config.py`
- `src/expedistill/core/config_upgrade.py`
- `src/expedistill/prompts/profile.py`（新建）
- `src/expedistill/prompts/author.py`、`src/expedistill/prompts/attribution.py`
- `src/expedistill/blocks/author.py`、`src/expedistill/blocks/attribution.py`
- `src/expedistill/stages/learn_tasks.py`、`src/expedistill/stages/assemble_reuse.py`
- `scripts/run/convert_v3_configs.py`
- 相关测试与 `tests/fixtures/golden/`、`tests/fixtures/golden_acceptance/`
- `README.md`、`AGENTS.md`、`src/expedistill/methods/author/README.md`
