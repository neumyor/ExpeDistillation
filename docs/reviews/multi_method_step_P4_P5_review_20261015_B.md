日期：2026-10-15
一句话说明：P4（curation 槽位双驱动 + authoring exams 开关）与 P5（provider 接缝 + 角色表 None 档，两项按简洁化跳过）的合并评审 B 意见——扩展性与反过度设计视角，审 `git diff 350b40d..475e109`（提交 fea4310 = P4、475e109 = P5），结论：通过，无【偏移约束】级发现，6 条【琐细】。
对应 log.md 条目：2026-10-15 21:55（P5：provider 接缝与角色表 None 档；两项按简洁化跳过留档）；同批覆盖 21:20（P4：curation 槽位双驱动与 exams 开关）。

---

## 一、总体评价

**通过。** P4 是注册表机制的第一次真实复用（curation 是第二个用上"纯表 + 驱动"机制的槽位），P5 是一次总体克制的"按需"收尾。逐条回答本路评审的三个主问题：

1. **none 驱动的原子整写不是过度设计，而是修 bug 修出来的正确形状**。log.md 21:20 记录了初版用 `append_bank` 在续跑场景产生重复行的事故——追加的语义是"可中途续跑的环"（K5 有合并环，所以 append 对 K5 是对的）；none 没有环，bank 内容是 accepted 集的纯函数，追加在重复调用下必然加倍，整写在构造上幂等。`none.py` 里的注释把"K5 为什么 append、none 为什么整写"写在了代码里，配套测试 `test_rerun_is_idempotent_not_doubling` 把这个论证钉成了断言。崩溃点分析成立：load accepted → 预算检查 → 整写 bank → 写 summary，任意点崩溃后重跑都落在相同字节（accepted 集在 stage 4 完结后不可变）。

2. **check_authoring 放在 authoring 槽位是方案预注册的位置，也是正确的位置**。方案 §3 原文就把这个例子定为 check 的用途（"P4 起 'exam 验收或 K5 回归 + exams: false' 冲突"），§7 P4 行写"集中冲突校验"。执行落点逐字兑现：check 挂在注册表表项上（`("authoring", "distill_diagnose")` 的第四个字段），由 stage 0 的五槽位循环（`stage0_freeze.py:283-284`）在冻结前对所有配置跑——坏配置在加载任何任务前响亮停。与 stage 0 特判的对比：特判会让 stage 0 知道三个槽位的语义，每加一个 exams 消费者都要改阶段层，形成新的中心化 if 分支——恰是方案 §1 问题①要消灭的形状。放在开关拥有者（生产者）槽位，消费者枚举与开关同处一地。检查完备：exams 的消费者恰好只有 exam 验收与 agentbrew 整理两个，两条都拒了；消融对（exams: false + live_run + curation: none）可解析且有测试。

3. **P5 的"两做两跳"总体成立，唯一的瑕疵是 ROLE_DEFAULTS 的 None 档**（详见琐细 1）：它是四项里唯一无消费者却落地的——唯一天然使用者（embedding delivery 的新角色）恰好同批被跳过，且其形状对那个预期使用者也不适用（pipeline 的缺角色检查是无条件全局的，届时还得再改）。好在绝对量只有 2 行不可达分支 + 一处注解放宽，无运行时风险，回退或补测试都是十分钟级。

方案 §9 禁止清单 14 项逐项核对：无一引入（providers 是纯表，无自动发现、无基类；K5 本体未搬；无新 hash/版本/审计；目录未迁）。P4 的三件预注册事项全部兑现（curation 槽位 + none；exams: false 放开 + 集中校验；authoring 第二写法按"一出现就做"预注册推迟——未出现，不做）。

## 二、发现清单

分级口径（协议 §10）：【偏移约束】= 违反本方案某条明文；【琐细】= 不影响推进，择机处理；【赞同】= 值得点名肯定的取舍。先结论：**【偏移约束】无，【琐细】6 条，【赞同】8 条。**

### 【赞同】

1. **none 的原子整写：必要且正确**（`src/expedistill/methods/curation/none.py`）。必要性有事故背书（log.md 21:20：初版 append 续跑重复行）；正确性在实现细节上：mkstemp 建在目标目录（同文件系统 rename 原子）、fsync、异常清理、`os.replace`。写盘形状跟随数据流形状，不是炫技。

2. **check_authoring 的位置、时点、完备性三者都对**（`src/expedistill/methods/authoring/__init__.py`、`stage0_freeze.py:283`）。位置 = §3/§7 P4 行预注册；时点 = stage 0 冻结前全槽位 resolve；完备性 = 消费者枚举恰好覆盖（exam 验收、agentbrew 整理），消融对可解析。测试三条（两条冲突 + 一条可解析）与论证一一对应，且第二条测试的注释写明了检查的拒绝顺序（acceptance 先于 curation）——这是把协议行为当成协议来测。

3. **curation 双驱动结构遵守 R1 与同形纪律**（`agentbrew.py` 48 行、`stage5_curation.py` 前端化）。agentbrew 是纯薄壳：`composites.curation` 一行未动（`test_curation_parallel.py` 只改了夹具的 methods 形状，崩溃注入 patch 目标不变——"patch 打空"风险未引入）；stage5 变成与 stage4 同形的 4 行前端（`driver.run(ctx, params)`）；驱动入口名 `run` 与 acceptance 驱动一致（delivery 的 `resolve` 是注入解析特例，差异合理）。

4. **providers 表的形状最小、收益真实**（`src/expedistill/llm/providers.py`，38 行）。纯数据表 + `default_client`，未知名字 ConfigError 列出已知（含 `(nothing registered)` 空表边界），无自动发现、无基类——§9 两项禁令规避。真实解耦收益：pipeline 不再 import 具体客户端类（`OpenAICompatClient` 的直接 import 换成了 `from expedistill.llm import providers`），第 7 层对具体实现的依赖解除；测试接缝从"patch 类方法"正规化为"monkeypatch 表"。环境变量选择符合"端点是运维不是身份"——provider 名不进 manifest，与 v2 语义一致。

5. **两项 P5 跳过的理由都成立，且留痕完整**（方案 §7 P5 行 ③④、log.md 21:55）。embedding 非 chat 调用：唯一消费者（embedding delivery）尚无人提出，建调用路径 = 无消费者的真代码，正是"第二个实现出现才建接口"的正向应用；provider 表已就位，届时按 §8 清单加。TB 真实查询文本：改它会改 tasks_digest = 实验身份，是 benchmark 插件层的方法实验决定而非框架基础设施——归属判断正确。两条都写进了方案 P5 行，符合 §10 留痕。

6. **golden 更新纪律符合 7.1 简化裁决**。8 个 fixture 各恰好一行 `"name": "agentbrew"`，与 METHOD_DEFAULTS 的变化精确对应——"仅 manifest 漂移"由 diff 自证，摘要写进了 log.md（21:20），同提交重生成。provenance 的 base_commit 同步指向新基点（信息记录，非纪律）。

7. **test_curation_none.py 把设计论证转成了可执行断言**。四个测试各钉一个 docstring 声明：裸 accepted 集逐字成库（含非 accepted 被排除）、超预算响亮停机（bank 为空证明没写半截）、空集是合法 bank、重跑幂等不翻倍。`test_params.py` 的 `test_defaults_satisfy_every_contract` 继续看守 METHOD_DEFAULTS。

8. **迁移纪律与升级路径完整**。SUMMARY_DIR/SUMMARY_NAME 从 stage5 搬到 curation 包，旧位置无转发 shim，`test_stage5.py` 同步改从新位置 import；METHOD_DEFAULTS、`upgrade_v2_config`、`test_config.py`、`test_stage0.py` 夹具、`test_stage5.py` 夹具五处 name 化同步，无遗漏角落。stage3 的 exams 门三处改动（完成判据、`_untouched_since_stage3` 检查、条件生成/落盘）逻辑自洽。

### 【琐细】

1. **P5 ② ROLE_DEFAULTS 的 None 档是四项中唯一无消费者却落地的，且形状对预期消费者也不适用**（`core/config.py:94-116,410-414`）。三点：(a) 唯一天然使用者（embedding delivery 的新角色）恰好同批被跳过——跳过消费者的同时建它的接口，是投机抽象的标准形态；(b) 当前是不可达分支（ROLE_DEFAULTS 无 None 条目），且全仓无测试钉住"None 锚位不补默认 + pipeline 响亮拒绝"这条链路（grep `ROLE_DEFAULTS` 的测试全是成员/正向断言）；(c) **形状前瞻性缺陷**：pipeline 的缺角色检查（`pipeline.py:152-158`）对 ROLE_DEFAULTS 全部角色无条件生效，届时若加 `"embedder": None`，whole_bank / retrieval 的 run 也被迫配置 embedder——真需求是方法级的（哪个方法需要哪些角色），档位却是角色级全局的，embedding delivery 真落地时还得再改这里。另：方案 §5 写"None 档位等第一个使用者出现再加"，§7 P5 行初版又把它列为 P5 内容——方案内部两处不一致，执行遵循 P5 行 + 用户明令，故不判偏移。注解放宽（`Dict[str, str]` → `Dict[str, object]）后，值域无白名单（"Strong" 打错会静默映射 strong）这一点更醒目——非本步引入。建议二选一：回退分支与注解放宽（严格按 §5 等使用者出现）；或补一个行为测试（往 ROLE_DEFAULTS 塞 None 验证不补默认、pipeline 拒绝）+ 同步 §5 表述。

2. **none.py 的 30 行手写原子写与 `infra/store.py:111` 的 write_json 逐字同形**（mkstemp 目标目录 / fsync / 异常清理 / os.replace）。原子写是最容易抄错的模式，现在它有了第二份手抄本，将来要同步维护。建议下沉为 store 的通用原子写原语（`write_text` 或 `write_jsonl`），none 改调它——顺带把"整写 JSONL"这个将来 embedding/新方法可能再用的形状变成库存。

3. **§8 清单对"单实现槽位第一次加第二方法"少列四步**（详见第三节实测）。清单对已双方法槽位实测准确；对 authoring/segmentation 这类单实现槽位，真实步骤 = 前置驱动化 + 清单 5 步 + METHOD_DEFAULTS name 化 + upgrade_v2 同步 + golden manifest 更新。方案 §4/§7 P4 行已预注册这个时序，非违规，但 §8 文本值得补一句"单实现槽位首次加第二方法时，参照 P4 curation 的转型 diff"。

4. **check_authoring 的 `.get("name", "exam")` / `.get("name", "agentbrew")` 是死 fallback**。METHOD_DEFAULTS 补全后两槽位的 name 恒存在（`_entry_for` 也依赖这一点），fallback 永不触发，且 fallback 值复制了 METHOD_DEFAULTS 的缺省——两处真相。无害；改直接下标 `["name"]` 更诚实，补全约定被打破时响亮失败。

5. **none.py 收集 accepted 时每个 skill load 两遍**（`if store.load(...).status == "accepted"` 一遍、列表表达式再一遍）。纯效率琐细，先 load 再过滤一遍即可。

6. **落盘路径来源不统一**：agentbrew 驱动全用 `ctx.run_dir`；none 的 bank 用 `store.run_dir`、summary 用 `ctx.run_dir`（两者是同一目录，无行为差异）。统一到一个入口即可。

## 三、新方法步骤实测：假想"第二种 authoring 写法"

现状事实（静态走查，未跑测试）：registry 里 `("authoring", "distill_diagnose")` 的 driver 是 `None`——authoring 槽位没有驱动，stage3 直接消费 params（`resolve_params("authoring", ctx.config).exams`）；`blocks/author.py:297` 的 `write_skill` 已备好 `prompt_name` 参数（P0 零风险准备）；`_entry_for` 规定 name 缺失时要求槽位只有一个已知名字。**因此第二方法注册的一瞬间，所有不写 `authoring.name` 的配置（含 METHOD_DEFAULTS 补全后的缺省）立即 ConfigError（"needs a name"）**。

假想新方法 `cot_author`（换 prompt 策略、skill 结构不变）的完整步骤：

| # | 步骤 | 文件 | 量级 |
|---|---|---|---|
| 0 | **前置：槽位驱动化**（§8 清单未列）——把 stage3 的方法性逻辑抽成 `methods/authoring/distill_diagnose.py` 驱动，stage3 改为 stage5 式前端 | stage3 + 新驱动 | 中型重构：stage3 比 stage5 厚（选择、单元循环、账本、判据都在阶段里），驱动边界要现场设计 + golden 验证 |
| 1 | 新 Params/check（若带新参数）或复用 AuthoringParams + 新驱动文件，`write_skill(..., prompt_name="新名")` | `methods/authoring/` | 按方法 |
| 2 | 注册表加一行 | `methods/registry.py` `_ENTRIES` | 1 行 |
| 3 | 新 prompt 模块 + `prompts/__init__` import 清单 | `prompts/<name>.py` | 2 处；两条静态测试强制 |
| 4 | **METHOD_DEFAULTS["authoring"] 加 `"name": "distill_diagnose"`**（§8 清单未列）——不加则所有现有配置爆 "needs a name"；这是 manifest 身份变化 | `core/config.py` | 1 行 + 身份知会 |
| 5 | **upgrade_v2 同步加 name**（§8 清单未列） | `core/config.py` `upgrade_v2_config` | 1 行 |
| 6 | **golden fixture 的 manifest 部分同提交更新**（§8 清单未列）——8 个组合各一行 | `tests/fixtures/golden/*.json` | 8 行 |
| 7 | 续跑测试 + `test_params.py` 加 case | `tests/unit/methods/` | 按方法 |

对照方案 §8 原清单五条：对**已双方法槽位**（acceptance/curation/delivery）加新方法，清单实测准确——`curation: none` 就是活例：Params + 驱动 + 表一行 + 4 个测试，除文档/golden 外零结构改动（diff 为证）。对**单实现槽位首次加第二方法**，清单少列 0/4/5/6 四件——curation 这次的转型恰好把它们全做了一遍（METHOD_DEFAULTS、upgrade_v2、8 个 golden 的 diff 都在），authoring 未来要走的正是这条路。方案 §4"用户提出第二种写法时立即建"与 §7 P4 行"一出现就做"已预注册该时序，故这是清单文本缺口而非执行违规。

对照 §8 思想实验 B 的量级预言（"新 prompt 策略的 authoring 只改 3 个文件"）：那个预言描述的是**驱动化完成之后**的稳态；加上一次性的 0/4/5/6，第一次的总成本约为预言的 2–3 倍——与 curation 本次转型同量级，属预期内的一次性结构成本。

## 四、【偏移约束】级清单

**无。**

对照过的方案条目：§2（R1 本体不搬——K5 一行未动；共享件下沉无新增）；§3（check 挂表项、纯表非装饰器、`run` 返回 dict 无类型化 Summary）；§4（curation 建于 P4 与 none 同批——兑现；authoring 驱动等第二写法出现——兑现；模型 provider 按需——兑现）；§5（`authoring.exams` 语义与 P4 放开、ROLE_DEFAULTS 不开放——unknown_roles 校验仍在、prompt 冻结未动、无 extends；唯 §5 "None 档等使用者"与 §7 P5 行的字面张力，已按琐细 1 处置并说明不判偏移的理由）；§7 P4 行三件事全部兑现、P5 行四项处置齐整（两做两跳，跳有理由有留痕）；§9（禁止清单 14 项逐项核过，无一引入）；§10（本评审范围限定 diff + 验收证据）。

## 五、结论与建议

P4+P5 通过本路评审，可继续。6 条【琐细】不阻塞，按优先级建议：#1（None 档）建议在下一个提交处理——三选一路径都很小：回退 2 行分支、或补行为测试 + 同步方案 §5 表述；#2（原子写下沉 store 原语）建议与 #1 同批，避免手抄本固化；#3（§8 清单补单实现槽位转型一句）与 #4（死 fallback）是文本级修补；#5/#6 纯洁癖。若主持人在"消融对已可跑"与"接口留痕完整性"之间取舍，#1 的最低成本处置是补测试而非回退——2 行不可达分支配上一个钉住其语义的测试后，就从"投机抽象"变成"带守卫的声明性能力"。
