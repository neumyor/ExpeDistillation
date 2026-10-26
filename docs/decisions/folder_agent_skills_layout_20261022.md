# folder 交付改成 Agent Skills 目录布局

日期：2026-10-22
一句话说明：folder 交付从平铺的 `.md` 文件改成每个技能一个 `<name>/SKILL.md` 目录，带 YAML 头，由 Harbor 的 Terminus agent 自己发现并生成技能目录。
对应 log.md：2026-10-22 的“folder 交付改为 Agent Skills 目录布局”。

## 背景

远端部署的 Harbor/Terminus 已经有技能发现能力。`claw-harbor` 的
`Terminus2._build_skills_section` 在 `skills_dir` 下执行：

```text
find <skills_dir> -mindepth 2 -maxdepth 2 -name SKILL.md -type f
```

它逐个读取 `SKILL.md`，解析开头的 YAML 头，要求非空的 `name` 和 `description`，
然后生成 `<available_skills>`，拼进首轮 instruction。

我们原来的 folder 交付是平铺的：

```text
/opt/openclaw-skills/<task>/
  SKILL.md
  catalog.md
  bank_merge_0001.md
  traj_tb_xxx.md
```

`skills_dir` 指向 `/opt/openclaw-skills` 时，深度 2 只有任务级
`<task>/SKILL.md`，而它不是技能、没有 YAML 头，所以 Terminus 解析不到任何技能。
插件只能自己把 catalog 拼进 `SKILL.md`，模型再去读，技能正文是否被读完全看模型。

## 决定

folder 交付改成 Agent Skills 目录布局：

```text
/opt/openclaw-skills/<task>/
  SKILL.md                 # benchmark 写的使用说明，深度 1，不会被扫描
  catalog.md               # 兜底目录，不是主入口
  bank-merge-0001/
    SKILL.md               # YAML 头 + 三段正文
  traj-tb-xxx/
    SKILL.md
```

同时：

1. `skills_dir` 指向任务目录 `/opt/openclaw-skills/<task>`，Terminus 的
   `find -mindepth 2 -maxdepth 2` 正好扫到每个 `<skill>/SKILL.md`。
2. 每个 `SKILL.md` 开头写 YAML 头，`name` 与目录名一致，`description` 非空且
   不超过 1024 字符。
3. 插件不再把 catalog 注入任务级 `SKILL.md`。`inline_catalog` 参数删除；
   任务级 `SKILL.md` 只保留一段指向 `<available_skills>` 的短说明。
4. `description` 的拼法做成参数 `description_mode`：
   - `summary`：只放一句话摘要；
   - `when_to_use`：只放 `[When to use]` 触发条件；
   - `summary_then_when_to_use`：摘要加触发条件，默认值。

## 实现位置

- `inject/skill_text.py`：三种描述拼法、`[When to use]` 提取、1024 字符上限。
- `methods/delivery/folder.py`：技能目录名、`<name>/SKILL.md` 路径、manifest、
  catalog 兜底。
- `composites/skill_folder.py`：逐文件写盘时生成 YAML 头；校验路径只能是
  `<safe-name>/SKILL.md`；目录名只允许小写字母、数字、连字符。
- `benchmarks/tb/plugin.py`：任务级 `SKILL.md` 只放使用说明；上传缓存改成递归，
  只打包 manifest 列出的文件；续跑摘要加入 folder 内容摘要。
- `scripts/tb21/vendor/tb21-tencent-skill/python/tencent_sandbox_terminus.py`：
  `TencentSkillTerminus2` 在运行前把 `skills_dir` 设成当前任务目录，让
  Terminus 自己发现技能；删除“读某个固定 SKILL.md”的提示。

## 后果

- 技能目录由 Harbor 生成，插件不再维护一份和技能正文可能漂移的 catalog。
- 任务级 `SKILL.md` 仍然是远端环境的硬性要求
  （`tencent_tb2_skill_env.py` 检查 `source / "SKILL.md"`），保留为使用说明。
- 读取统计 `skill_reads` 改为匹配 `<skill>/SKILL.md` 或同一行的技能目录名；
  只有 `cat SKILL.md` 而没有目录名时无法判断是哪一个技能，不计入。
- 旧 run 的平铺 folder 产物不能复用。新布局的 folder 内容摘要不同，必须新开
  run；旧配置里的 `inline_catalog` 会被参数校验拒绝，需要新配置。
- Harbor 每条 trial 会对每个 `SKILL.md` 执行一次 `cat` 来解析头部；60 个技能
  就是 60 次沙箱命令，启动阶段会多几秒。
- YAML 头解析失败时 Terminus 会静默跳过该技能。materialize 后用
  `tests/unit/composites/test_skill_folder.py` 的同款检查逐个解析，防止静默缺项。
