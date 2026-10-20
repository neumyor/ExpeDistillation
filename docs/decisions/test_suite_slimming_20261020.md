# 测试系统精简

日期：2026-10-20
说明：删掉已验证完成的迁移考证仪器，把版本相关的字面量固定改成契约断言，缩小 golden 矩阵的组合数。
对应 log.md：2026-10-20 测试系统精简。

---

## 背景

当前 `tests/` 有 107 个测试文件、1849 个测试函数、41544 行代码，`src/` 有
29311 行。全量 `pytest` 约 50 秒，运行时间不是问题；问题在改动时的维护成本。

一次"新增一个带默认值的配置开关"（`use_weak_profile`）的实际代价：

- 配置版本从 v4 升到 v5，牵动配置层、升级函数、转换脚本、复用装配器和
  它们的测试；
- 5 个提示词模板改文本，14 个 sha256 字面量和一份完整的 manifest 字面量要
  手工更新；
- 8 个 golden 组合和一份 Wave 4 披露报告要重新生成。

其中只有提示词模板和三个调用点是这个开关本身需要的。其余成本来自三类把
"历史迁移"当成"当前契约"来固定的测试结构。

## 决定

### 一、删除迁移考证仪器

删除：

- `tests/golden/semantics.py`（1165 行）
- `tests/golden/test_semantic_instrument.py`（809 行）
- `tests/golden/extract_baseline.py`
- `tests/fixtures/golden_semantic_baseline/`（420 KB，9 个文件）
- `tests/fixtures/golden_acceptance/`（148 KB）

理由：这套仪器的对象是 2026-10-15 的 v3→v4 迁移，基线取自提交 69904f7，
判据是 E1–E6 六条语义等价准则。迁移已经完成并落地，此后它只在每次提示词或
产物变化时要求重新生成披露报告，不再保护任何当前行为。

保留：`revision_window.py` 里的"修订已落盘、重答未开始"崩点机制和对应测试。
这是 live 路径上唯一由该仪器覆盖、其他测试没有覆盖的中途崩点，迁到
`tests/golden/` 下作为独立模块。

### 二、golden 矩阵从 8 个组合减到 4 个

三个轴（acceptance × delivery × mode）的全组合是 2×2×2。保留：

- `exam-whole_bank-full`
- `exam-retrieval-split`
- `live_run-whole_bank-split`
- `live_run-retrieval-full`

每个轴的两个取值各出现两次，两两交互仍有覆盖。四个组合全部做崩溃续跑检查
（原来只有两个），续跑覆盖不降。产物变化时重新生成的组合数减半。

### 三、把当前版本的冻结字面量改成契约断言

`tests/unit/stages/test_stage0.py` 里的 `_GOLDEN_PROMPT_DIGESTS`（14 个 sha256
字面量）和 `_golden_config`（完整配置字面量）改为：

- 提示词名称集合仍然固定为字面量（防止意外注册）；
- digest 的取值改为与该提示词当前注册值逐项比较；
- `manifest["config"]` 改为与 `config.to_dict()` 比较；
- `tasks_digest` 改为与重新计算的值比较。

这样改提示词文本或加配置字段时，这个测试不再需要人工改数字；它仍然验证
"冻结的就是运行的那一份"这个契约。

`tests/unit/core/test_config.py` 里的 `GOLDEN_TO_DICT_FULL_FORM` 保留一份，
它是序列化形状的唯一固定点。

### 四、版本升级测试保留往返，删掉逐字段合成用例

保留：每个已存在仓库配置都能完成升级并加载；每条已披露的映射改写各一个用例；
输入不被修改。

删除：用合成输入逐字段枚举映射表的类和拒绝对照类（`TestUpgradeV3SyntheticShapes`、
`TestUpgradeV3Refusals` 的大部分、`TestUpgradeV2` 的逐键用例）。这些用例固定的是
已经冻结的升级函数实现细节，不保护当前行为。

### 五、合并同构测试

`tests/unit/stages/test_assemble_reuse.py` 是改动最频繁的文件（60 天内编辑 16 次），
其中 6 个身份差异拒绝用例、4 个 baseline 拒绝用例结构完全相同，合并为参数化
测试，覆盖不变。

### 六、不加的东西

- 不引入测试分层选择器、不按运行时长分类：运行时间不是瓶颈。
- 不新增共享配置构造器：现有各文件的本地 `make_config` 直接构造 dataclass，
  新增带默认值的字段不会让它们失效。
- 不批量改写 476 处错误信息正则：错误信息不随常规改动变化。

## 预期结果

- 删除约 2500 行测试代码、约 570 KB 夹具、约 40 个测试函数；
- 新增提示词文本或可选配置字段时，需要人工同步的测试数量从十余处降到零；
- 产物变化时 golden 重新生成的工作量减半。
