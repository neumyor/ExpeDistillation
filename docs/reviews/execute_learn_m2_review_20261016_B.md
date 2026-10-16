日期：2026-10-16
一句话说明：执行—学习重构 M2 步骤的验收评审 B——简洁性与反过度设计。
对应 log.md 条目：2026-10-16 M2 执行侧抽取。

## 审查基础

- **diff 范围勘误**：任务给的 `git diff 9c8bf49 6b86e65 -- src tests` 对 src/tests 为空——9c8bf49
  是 M2 **之后**的提交（M1 收尾文档），src/tests 在两者间逐字节相同。实际被审对象是
  **6b86e65 自身的 diff，即 85bfa4f..6b86e65**（6b86e65 的父提交是 85bfa4f），本评审按此审查。
  当前 checkout（9c8bf49）的 src/tests 与 6b86e65 一致，工作区干净。
- **契约**：docs/decisions/execute_learn_implementation_20261015.md 的 §1 纪律（简洁、不加新
  hash、不加影响执行流程的硬检查、完整保留异常分类与恢复机制）、§3（delivery 族 / 注册表缝 /
  D5 去轮化薄壳）、§9 M2 行。
- **复跑验证**（本机全量）：pytest 1958 passed / 3 deselected（live 标记），51s；
  `ruff check --select E9,F` 全过；test_layering 3 项全过；`-k golden` 13 项全过；
  tests/fixtures/golden 在 diff 中零改动；两套崩溃续跑套件文件零改动（在 1958 内通过）；
  blocks/（gen_exam/audit_leak 所在）、core/config.py、prompts/、infra/ 均不在 diff 内。
- **体量**：live_acceptance.py +362/-195（净 +167）；live_run.py +11/-29（净 −18）；
  三新驱动 26+30+45=101 行（含 docstring）；registry.py +57/-7；stage6 +11/-4；新增测试 8 条。

## 逐项结论

### 1. 薄壳中间态的成本 ——【赞同】（audit_row 一处注记）

- **逐壳看**：六个壳的纯代码合计约 44 行（audit_row 2 行、no_op_guard 2 行、
  finalize_group 4 行、apply_revisions 约 9 行、attribute_group 约 13 行、judge_round 约 14 行）。
  壳上的长 docstring 是从原函数**整体搬移**的行为文档加 2–3 行 "Thin shell … deleted in M4"
  注记，不是新增的重复文档。这个体量是 P5 薄壳该有的样子，不是过度结构化。
- **有没有纯增间接层的拆分**：judge_round / attribute_group / apply_revisions / no_op_guard
  四个是承载性的——核签名新增 3–4 个显式路径参数（verdicts_path / attributions_path /
  failures_path / revision_failures_path / round_store_dir / audit_path）、丢掉从 store 推导路径的
  逻辑；不拆就无法兑现 s-8"账本路径作参数"。finalize_group 的壳只做 rounds=_ROUNDS 绑定
  （它本来就没有 _check_round），同样承载。**最薄的是 audit_row**：核签名 (store, task_id,
  round_) 与壳完全相同，拆分的唯一内容是把 `_check_round` 留在壳。严格按最小中间态审，它可以
  不拆（M4 删一行 check 即可）；但 `_check_round` 恰是方案要去的"轮概念"，六个统一走"壳死核留"
  让 M4 的迁移成为纯机械替换，15 行的一次性成本买迁移一致性，判定可接受，不构成偏移。
- **M4 删除路径是否真的铺好**：是，且是结构性的而非只在 docstring 上。grep 实证
  `_check_round` / `round_dir` / `_ledger_path` / `LIVE_DIR_NAME` / `_ROUNDS` 在
  live_acceptance.py 内只出现在定义处与六个壳体（round_dir 另被 live_run 的轮序编排使用，
  该编排本身 M4 随相位驱动消亡）；六个核（`_judge_round` / `_attribute_group` /
  `_apply_revisions` / `_revise_one` / `_no_op_guard` / `_audit_row` / `_finalize_group`）
  零引用这些名字。LIVE_DIR_NAME 与 _ROUNDS 定义处有 "Shell-layer constant … dies with the
  shells in M4" 注释，模块 docstring 写明壳 M4 删。私有层没有过度拆：`_revise_one` 等下层
  helper 只做参数穿透，没有额外分壳。

### 2. resolve_fixed 的形状 ——【赞同】

- **与 resolve 共享 loader 是最小做法**：共用同一张 `_ENTRIES` 纯数据表、新抽的
  `_load_params` / `_load_driver` 共享 helper、同一 build_params 校验与懒 import，两个入口
  只差"名字从哪来 + 有无 config"。"一个 flag 参数"的替代方案会把两个契约搅在一起：resolve
  的契约是"读 config 的选择（含 check 交叉校验）"，resolve_fixed 的契约是"语义不是配置选择
  的固定名"，合进一个函数意味着 config 变成可有可无的参数，比两个小函数更糊。两个调用点也
  没有更简的等价：stage6 直接 import 驱动模块会绕过参数校验与表；live_run 直接 import 兄弟
  槽位被 T8 与 test_methods_do_not_import_each_other 明文禁止——一个机制服务两个调用点即最简。
- **带 check 条目响亮拒绝**：今天五个 delivery 条目全部 check=None（带 check 的是
  authoring/distill_diagnose 与 acceptance/live_run），所以这条分支**在 delivery 上没有真实
  消费者**。判断：这是注册表不变量的忠实表达，不是防御性填充。论据：(a) check 的签名是
  check(params, config)，以 config 为前提，固定解析没有 config，"拒绝"是该不变量唯一的诚实
  行为；(b) resolve_fixed 是跨槽位通用机制——若静默跳过，未来任何条目加 check 时固定解析就
  变成绕过校验的后门且无任何提示；(c) 成本 4 行 + 1 条用**真实** live_run 条目（不是稻草人
  假条目）的测试。防御性代码的形态是吞错 / 默认值 / 不可达兜底，这里是响亮拒绝，方向相反。
- 本项下挂两条琐细（见清单 1、3）。

### 3. 三驱动的契约一致性 ——【赞同】，没有过线

- **统一到哪**：resolve 名字、`(ctx, params)` 前缀、`(method_kwargs, audit_rows)` 返回形状、
  注册表行 `(driver, Params, check)` 四面全部一致；变化只在**操作数尾部**，共三种形状：
  `(bank, test_tasks)`（whole_bank / retrieval / none，K1 臂级标量或解析器）、`(skill)`
  （per_skill，K3 单技能块）、`(store, benchmark)`（group_by_task，K1 逐任务解析器）。
- **这是有意的**：方案 §3 delivery 行本来就写"输入 (bank 行, tasks, store 或相位上下文)"，
  s-10 写"输出形状随执行方式"——操作数异质是方案预告过的，不是失控。再往下统一（给所有
  驱动一个全参 god-signature、各驱动默默忽略）是均匀性表演：它把"哪些参数被忽略"变成隐式
  契约，比今天"每个驱动要什么就收什么 + 契约测试逐驱动钉住"更难审。判断"过没过线"的标尺
  是：每条形状差异能否指到调用方或方案条目——none 镜像 whole_bank（stage6 两臂调用对称）、
  per_skill 对 K3 的 with-skill kwargs（K4 现用 skills_block 一个键，实测匹配）、
  group_by_task 是 _injection_resolver 逐字迁移。全部能指到，没过线。
- 注记：none 收 `(bank, test_tasks)` 却全忽略，是与 whole_bank 同形的代价，docstring 明示
  "ignored on purpose"，可接受。

### 4. per_skill 无消费方 —— 裁决：**留**；【赞同】（附 M4 跟进条件）

- **披露是否如实**：三处如实——模块 docstring 写明"That consumer first appears in M4 …
  M2 deliberately wires no caller"并给出分层理由（K4 在第 5 层不能调第 6 层驱动）；
  测试文件 docstring 写明"per_skill has no caller until the exam execution driver lands in
  M4"；log.md 亦如实记载。
- **存在理由 vs P5 教训的辨析**：P5 教训（无消费者不落地）针对的是形状未定、真出现时
  "平凡可写"的投机工具。per_skill 不属此类：方案 §3 表把它列为 delivery 族注册名（"从 K4
  单技能注入抽出"），§4 的 v4 schema 把 A 相位 delivery 定死为 `{name: per_skill}`——注册表
  行是 M4.3 配置加载的**硬前置**，不是"可能有用的投机代码"。成本 30 行 + 1 条契约测试。
- **方案内部张力如实记录**：§9 M2 行写"三新驱动……并被 stage6/live 经注册表缝调用"，对
  per_skill 字面不可满足——它的调用方（exam 执行驱动）在 M4 才存在，K4 又被分层禁止调它。
  这是 M2 行与分层规则之间的张力，实现选择了唯一分层合法的读法（注册 + 钉契约 + 三处披露），
  不算实现的偏移。
- **跟进条件**：M4 验收必须核 per_skill 获得 exam 执行驱动这个调用方；若 M4.3 结束后它仍无
  调用方，即转为真死代码，删。对 M2 验收的影响：不影响通过。

### 5. 无残留 ——【赞同】

- `_injection_resolver` 删净（grep 全仓零引用），其 docstring 里的语义论证（survivors 即
  pending members、跨 resume 与冻结审计行字节同一）随迁入 group_by_task.py 的 docstring，
  知识未丢；live_run.py 净 −18 行。
- stage6 裸臂硬编码（`skills_injected=None, skills_block=""`）删净。仓库仅剩两处
  `skills_block=""`：live_run 的 `_task_instruction`（G10 输入渲染，非注入点）与 K4 的裸答
  臂（exam 测量编排，M4 才挪调用点）——都是本步范围外、用途不同的调用点，不是残留。
- 无转发 shim、无 @deprecated 别名、无注释掉的代码。stage6 裸臂的 dict→update→**splat 写法
  与既有 with-bank 臂完全同构（该模式 M2 之前就在），是跟随而非新增间接层。
- try/except 审查：resolve_fixed 的 `except KeyError` 是响亮失败转换（转成带 known 列表的
  ConfigError，与 _entry_for 同款），不吞错；无新增防御性 try/except。

### 6. 异常与恢复机制 ——【赞同】

- reliability.py / store.py / runlock.py 零改动（diff 不含这些文件），四类异常处置路径未被
  间接层触碰，核内逐字核对过：`_attribute_group` 保持"终态先写、exhausted 行是回执"（discard
  在前、append 在后）与"耗尽行已存在则补完 discard 直接返回"的幂等分支；`_revise_one` 的
  单技能预算、"仅该技能弃、组继续"原样；InfrastructureError 传播原样；judge 核拥有 verdicts
  的 append-once + 重算比对（s-8 落实，比对不相等仍是 CodeBugError）；`_first_row_from`
  保留"首判为准 + 手改账本在重算比对处响亮失败"的语义（读第一行而非最后一行）。
- 逐单元落盘次序未变：verdicts 行 append 在任何 status 提交之前（提交归 finalize_group）。
- 用户约束核对：未加新 hash（injection_block_sha256 是既有字段，本步只搬代码）；未加影响
  执行流程的硬检查（resolve_fixed 的 check 拒绝在现有两个调用点的路径上不可达——五个
  delivery 条目全无 check）。

### 7. 测试质量 ——【赞同】（一处缺口见琐细 2）

- 8 条新测试逐条看过：none 是精确字典断言（含 `None` 而非 `[]` 的语义钉，正是 retrieval
  占用 `[]` 的坑位）；resolve_fixed 四条分别钉模块身份、unknown 名列表、**必填参数在固定解析
  下仍然响亮失败**（用真实 retrieval 条目而非造一个假必填类）、带 check 拒绝（用真实
  live_run 条目）；group_by_task 的拒空组是真实行为断言（CodeBugError match）。
- 两个"委托等价"断言（per_skill 对 format_skills_block、group_by_task 对 survivors_and_block）
  接近同义反复，但这是"必须用同一渲染器 / 同一推导"契约的最小形式——对着字面字符串断言会把
  渲染格式复制进测试，更糟。没有为凑覆盖的空洞断言；8 条对 101 行驱动 + 注册表扩展比例合适，
  行为面继续由既有验收 / 续跑 / golden 套件覆盖（本步对这些套件零改动、全绿）。
- **缺口**：`_finalize_group` 的泛化分支（非 (0,1) 的 rounds 序列、单轮序列）在今天的壳下
  不可达、无持久化测试；log 里"821 种账本行组合穷举"只证明了 rounds=(0,1) 下新旧决策逐位
  相等（保行为达标），泛化语义本身（末轮定胜负、更早轮 better 接受）要到 M4 才被消费、目前
  未被任何测试钉住。该比对脚本也未入库。

## 琐细清单

1. **resolve_fixed 与 _entry_for 的 unknown-name 错误构造逐字重复**（known 列表 + 同一条
   ConfigError 消息，约 6 行）。`_entry_for(slot, {"name": name})` 一行即可替代 try/except +
   known 列举。下次触碰 registry.py 时收敛，不阻塞。
2. **_finalize_group 泛化语义无持久化测试**：821 组合只证 (0,1) 等价。建议 M4.3 的 E1/E6
   之外补一个 `_finalize_group` 直测（如 rounds=(0,) 与两元素非 (0,1) 序列各一），或把比对
   脚本入库复现。
3. `_resolve_entry` 的 check 分支重复 `importlib.import_module(entry.params)`（_load_params
   内已 import；幂等无害，纯小疣）。
4. **AGENTS.md §四 的 methods/delivery 目录注释仍只列 whole_bank/retrieval 两个驱动**（现为
   五个）。方案 §7 把文档同步排在 M4.6，可接受；届时逐字核对，防止这处注释继续漂移。
5. **族首参 ctx 五个 delivery 驱动全部不用**（M2 之前 whole_bank/retrieval 即如此，三个新
   驱动为族形一致沿用）。M4 接线时若确认无驱动需要它，趁机收掉，别让它长成永久的 cargo cult。

## 结论

**M2 验收通过。**

七项逐一核过：薄壳成本约 44 行纯代码、六核结构性地不读轮常量、M4 删除路径在 docstring 与
结构两层都铺好；resolve_fixed 是共享表 + 共享校验的最小切法，check 拒绝是不变量的忠实表达；
三驱动操作数异质是方案预告的形状且每条可指到调用方；per_skill 裁决留、披露完备、附 M4 获得
调用方的跟进条件；旧代码删净无 shim；异常分类、恢复次序、首判为准未被间接层触碰；8 条新测试
是契约钉而非凑数。偏移约束 0 条、琐细 5 条（均不阻塞）。验收基线全部复跑通过：1958 全绿、
golden 13 项零 diff、两套续跑套件原样通过、ruff 与 test_layering 通过。
