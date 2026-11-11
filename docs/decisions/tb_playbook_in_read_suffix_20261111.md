日期：2026-11-11
一句话：让学习得到的全局 playbook 在 TB 2.1 上通过 skill-read suffix 进入首轮提示词，外部依赖只改 vendor 一个类。
对应 log.md：2026-11-11「TB 2.1 接入学习 playbook」条目

# 决定：TB 的 playbook 放进 skill-read suffix

## 背景

当前版本（V7，`playbook_source: learned`）只在 ALFWorld 上运行。TB 插件没有声明
`playbook_prompt_name`，folder 交付清单里的 `playbook_text` 也没有任何读取方。TB 的首轮提示词由
Harbor 的 Terminus 自己拼接：任务说明 + `<available_skills>` + `skill_read_suffix`，其中
`skill_read_suffix` 写死为 `TencentSkillTerminus2.__init__` 的默认参数，runner 生成的 YAML 不传它。

## 候选方案

1. 改 shell runner，把 playbook 作为 agent 参数传入：改动面是外部脚本，且要处理 YAML 引号。
2. 改 Harbor 的 Terminus 提示词拼接：改动面最大。
3. 让 vendor 的 `TencentSkillTerminus2` 读取任务目录里的 `SKILL_READ_SUFFIX.md`，文件由插件按任务上传：
   外部依赖只改 `skill_read_suffix` 这一处，每次执行用最新的 playbook。

## 选择

方案 3。

- vendor 改动（`tencent_sandbox_terminus.py` 的 `run()`）：任务目录里存在 `SKILL_READ_SUFFIX.md` 就用它的
  内容作为 suffix，否则用内置默认值。没有这个文件的 run（包括全部 bare 臂和不带 playbook 的 folder 臂）
  行为不变。
- 仓库内：
  - `benchmarks/tb/skill_read.py` 把 suffix 文本登记成具名 prompt `TB_TASK_PROMPT_READ`（与 vendor
    默认值逐字节相同）和 `TB_TASK_PROMPT_READ_PLAYBOOK`（playbook 放在 suffix 前面），两者的 digest 进
    manifest。此前这段文本不在 `prompt_digests` 里。
  - 插件只在交付清单带 `playbook_text` 时才渲染并上传 `SKILL_READ_SUFFIX.md`（同时上传到点号任务名的
    连字符别名目录），文本的内容并入 `_conditions` 的 `skill_sha256`，续跑时 playbook 变化会被拒绝。
    不带 playbook 时上传内容和运行条件与此前逐字节相同。
  - `benchmarks/tb/playbook.py`：`playbook_facts`（程序提取 baseline 事实）、写作 prompt
    `TB_PLAYBOOK_WRITE`、`playbook_requirements`（五个带标签的部分）。写作 prompt 内嵌 suffix 现有文本，
    要求不重复其中的 shell 卫生、阈值余量、完成前自检。

## 与 ALFWorld 的差异

- ALFWorld 的 playbook 每一步重新渲染；TB 只在首轮提示词出现一次，之后靠对话历史保留，上下文被摘要或
  交接后是否保留首轮内容未核实，需要在长轨迹上检查。
- 事实指标按终端任务定义：任务成败、失败局的结束方式（超时、宣布完成但判错）、重复命令、报错连续段、
  画面不变的步数、解析失败轮数、宣布完成前是否运行检查类命令。

## 外部依赖的部署

vendor 文件需要同步到基准主机，两边字节一致；改前备份（本地备份 `.bak_20261111_local`）。

## 配置

`configs/experiments/tb21_v7_playbook_20261111.yaml`（exam 与 test 交付带 `playbook_source: learned`）和
`tb21_v7_playbook_nopb_20261111.yaml`（只有 test 交付去掉 playbook；从带 playbook 的 run 分叉 learn 相位后
用它跑 test，得到同库对照臂）。划分沿用 20 题训练、39 题测试。
