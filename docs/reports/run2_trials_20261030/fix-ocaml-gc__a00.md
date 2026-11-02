# fix-ocaml-gc / attempt 0（失败）

- trial：`traj/tb:fix-ocaml-gc/strong/00`；步数 31；读取的技能：无
- 主因：E_environment

## 1. 为什么失败 / 卡在哪里
- 卡点：判分时任务自带的测试套件重跑命令在判分侧报 `make: *** testsuite: No such file or directory. Stop.`，`tests.txt` 被写成空文件，唯一的断言失败：`assert '40 tests passed' in ''`（`test_outputs.py::test_tests_output`，第 19 行）。
- 过程：
  - 第 0–2 步：确认工作区只有 ocaml 源码树、没有 git 元数据，进入源码目录并开始读 runtime 下的 GC 源码（`runtime/major_gc.c`、`runtime/shared_heap.c`）。
  - 第 3–15 步：反复 grep/阅读 `runtime/shared_heap.c` 的 sweep、pool、freelist、`POOL_FREE_HEADER` 相关代码，定位 free block 合并与遍历逻辑。
  - 第 16–22 步：轨迹按均匀间隔抽样，这几步不在摘要里；从第 23–27 步可见一次完整构建（stdlib、ocamlc.opt、ocamldoc 等依次编译完成）。
  - 第 28 步：运行 `make -C testsuite one DIR=tests/basic`，摘要为 `0 tests failed`、`0 unexpected errors`、`40 tests considered`（"40 tests passed" 一行在抽样输出里被截断）。
  - 第 29–30 步：直接 `mark_task_complete`，在确认提示后再次提交。
- 直接原因：判分侧重跑测试套件时找不到 testsuite 目录，生成的 `tests.txt` 为空；测试只读 `tests.txt` 里的 `"40 tests passed"`，因此断言在空串上失败。模型侧第 28 步自己跑同一个测试套件是 40 个用例、0 失败。
- 与同题成功 attempt 的差异：
  - a00：第 28 步跑完测试套件后第 29 步就完成，全程 31 步，没有把改动与原始文件对照，也没有在完成前单独把 "40 tests passed" 从日志里取出来复述。
  - 成功 a01（67 步）：第 62–63 步以后台方式重跑测试套件并等它结束；第 64 步用 `diff -u` 对照原始 `shared_heap.c`，给出实际改动是 `p += Whsize_hd(hd)` → `p += wh`，并从日志 grep 出 `40 tests passed` 后才在第 65 步完成。
  - 成功 a02（78 步）：第 3 步读了 `bank/merge/0006`，第 34 步抓取上游 `shared_heap.c` 做对照，第 72–73 步显式构建 `testsuite/tools/expect`，第 74–75 步跑出 40 个用例、0 失败后完成。
  - 三者的测试套件在各自容器内都跑通了；差别只出现在判分侧重跑这一步。判分只看 `tests.txt`、不校验补丁本身，所以成功 attempt 多做的补丁取证并没有改变判分口径。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0006`（相关性 中）——"probe-first ... obtaining, configuring, compiling, installing, and functionally verifying software artifacts from source"，含 configure/make/ldd/验证循环；a02 第 3 步读了它并照此构建、验证。但它不含 OCaml GC 语义。
  - `bank/merge/0004`（相关性 低—中）——提交前门禁，要求在发出完成标记前核对产物、端到端行为与最终字节（"end-to-end behavior, and final bytes before issuing the formal completion marker"）；能强化"完成前再跑一次成功判据"，但不会改变判分侧的执行环境。
  - 其余 15 个成员（视频 OCR、候选程序打分、数据统计、模型拟合、异步清理、SQLite WAL、DNA 引物、HTML 过滤、批调度、路径追踪）（相关性 无）。
- 缺的能力：OCaml/C runtime 与 GC 调试（识别 free block 合并后使用过期 header 长度这类 bug）。整库没有 C runtime、内存管理或编译器构建类技能。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：没读（`meta.json` 的 `skills_read` 为空，`skills_read.md` 无内容）。
- 原因：本 trial 的失败与技能无关——它自己的构建和测试套件都成功了，卡点在判分侧重跑测试套件的目录解析。读 `bank/merge/0006` 或 `bank/merge/0004` 至多让验证步骤更可复述，不能修复判分环境里 `make -C testsuite` 的失败。

## 4. 结论与依据
- 主因：E_environment
- 一句话依据：第 28 步模型自己运行 `make -C testsuite one DIR=tests/basic` 得到 40 个用例、0 失败、0 unexpected errors，判分侧同一命令却报 `make: *** testsuite: No such file or directory. Stop.`、`tests.txt` 为空，断言 `'40 tests passed' in ''` 失败——失败发生在判分侧执行，而不是模型的构建/测试结果。
- 同分布内波动：同一任务的 a01/a02 在各自容器里也能跑通同一测试套件并通过判分；只有 a00 的判分侧重跑找不到 testsuite 目录，属于环境侧波动，不是能力或技能上的稳定差距，按更贴切的一侧归为 E。
- 次要因素：a00 只用了 31 步，未像成功 attempt 那样对照原始文件确认补丁、未在完成前单独复述测试证据，验证深度不足（A 的次要面）；库中无 OCaml GC 类技能（D）。
