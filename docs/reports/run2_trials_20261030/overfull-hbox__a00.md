# overfull-hbox / attempt 0（失败）

- trial：`traj/tb:overfull-hbox/strong/00`；步数 11；读取的技能：无
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里

- 卡点：第 3 步（一次性全族替换）+ 第 8 步（把这份候选定为最终 `input.tex`）。
- 过程：
  - 第 0–2 步：读 `main.tex`（textwidth 只有 2.5in 的窄版式）、`input.tex`（19 行）、`synonyms.txt`（110 行）；基线 `pdflatex` 多处 overfull（7–8、9–10、13–14 行等）。
  - 第 3 步：写脚本把 `synonyms.txt` 每一行的**所有成员**都小写化后映射到该行最短成员，再对 `input.tex` 全文逐 token 替换（输出时恢复首字母大小写），写到 `/tmp/cand/`，编译得 `NO_OVERFULL_WARNINGS`。
  - 第 4 步：把这个候选复制回 `/app/input.tex`（文件从 5186 B 变为 4926 B），重编译无 overfull；同时用它自己的校验器比对原/候选：两边都 `lower()`，再按 `split(',')` 得到的家族判断 `al in fam and bl in fam`，计数为 0 处非法。
  - 第 5 步：恢复原文件，看 diff（89 个 token 被换）。
  - 第 6–7 步：又构造 cand2/cand3 做对照，cand3 仍有 overfull（`8.14934pt`），未采用。
  - 第 8 步：最终把第 3 步的候选写回 `/app/input.tex`（4926 B），重编译 `NO_OVERFULL`，同一套宽松校验器再次报通过；第 9–10 步提交。
- 直接原因：判分器 4 项测试 3 过 1 挂。`test_compilation_successful`、`test_no_overfull_hboxes`、`test_main_synonyms_not_modified` 都通过，唯一失败的是 `test_input_file_matches`（test_outputs.py:109）：`assert w == w_agent or (w in synonyms and w_agent in synonyms[w])`，报 `AssertionError: modified input.tex must only modify words in synonyms.txt`，首个非法对是 `'Middle' == 'Hub'`。判分器用**原样大小写**建同义词表（trace 里的字典键含 `'Actions'`、`'Bearing'`、`'Behavior'`、`'Conduct'`、`'Demeanor'`、`'Deportment'` 这类首字母大写的词），而第 3 步的映射用小写键，因此把文本中以大写形式出现的 `Middle` 经 `middle→hub` 改成了 `Hub`——判分器不认这个替换。agent 自己的校验器同样是小写化后比较（第 4、7、8 步），与变换共用同一套宽松语义，所以打印的是 "invalid changed tokens: 0"：校验器无法复现判分器的精确规则。

### 与同题成功 attempt 的差异

- `successes/overfull-hbox/a01`（17 步，未读技能）：第 5 步先手工替换 6 个**小写**词（curious→odd、unsought→unwanted、excursions→trips、responsiveness→reaction、temperament→mood、pathfinder→pioneer）；第 7–11 步做候选扫描；第 12 步的最终映射只取**每行第一个词**作为原词（`orig = parts[0]`），且 `skip={'prominent','responsiveness'}`，编译得 `NO_OVERFULL_HBOX`。
- `successes/overfull-hbox/a02`（17 步，未读技能）：第 3–14 步全部用 `perl -pi -e 's/\bword\b/.../g'` 做**逐个小写词**替换，每换一批就重编译看 overfull 数字，最终 `NO_OVERFULL`。
- 差别一句话：两次成功都只改写小写形式的词、且只动"行首词→同族词"这一受限集合，并逐次编译验证；a00 一次性对整族做全集替换，改到了大写形式 `Middle`，提交前又用与变换同源的宽松校验器自证，恰好绕过了唯一会挂的那条断言。

## 2. 技能库里有没有能解决它的技能

- 候选：
  - `bank/merge/0004`（相关性 中，未读）——提交前门禁：用独立参考复读产物（第 4 步 "Use an independent reference, not only a read-back of your own write"；第 6 步 "Read it back with the same serializer or parser the grader will use"）。本案正是违反了这条原则：校验器与变换同源。但它不专门针对"替换契约"。
  - `bank/merge/0001`（相关性 中，未读）——基线 + 单因素变体 + evaluator 打分 + 记录；本案有可用的 evaluator（`pdflatex` 的 overfull 数字），两次成功实际上就在做这个循环，失败方却是一次性替换后直接收工。
  - `bank/merge/0003`（相关性 低）——数据清点/计数核验；与文本改写无关。
  - 其余成员（视频 OCR、异步、构建、SQLite/二进制、DNA、HTML 过滤、调度器、路径追踪）不相关。
- 缺的能力：按"精确替换契约"改写文本——从契约源（`synonyms.txt`）用**与判分器相同的解析口径**（原样大小写、原样分隔符）枚举允许的 (原词, 替换词) 对，先产出替换清单并逐条自检，再落盘；库里没有这类成员。

## 3. 能解决问题的技能被触发了吗？为什么没解决

- 触发：没读任何技能（`skills_read` 为空）。
- 原因：`bank/merge/0004` 的 trigger（"your plan overwrites an existing file"、"a deliverable that will be graded on disk"）其实覆盖本案，但模型没读；`bank/merge/0001` 的 "有 evaluator 就做变体打分" 同样适用，也没读。即便读了 0004，它只能要求"用独立参考/判分器同款解析口径复读"，能不能挡住大小写错误仍取决于模型是否照做。本 trial 的直接问题是变换与自检同源（都用小写化 + `split(',')` 的宽松家族判定），因此不是"技能覆盖不到"，而是模型自己选了比契约更宽的变换、又用同宽的尺子量自己。

## 4. 结论与依据

- 主因：A_model_capability
- 一句话依据：第 3 步对整个同义词族做全集替换，把大写形式 `Middle` 改成 `Hub`，判分器按原样大小写建表判为非法；第 4/7/8 步的自检器把两边 `lower()` 后比对，报 "invalid changed tokens: 0"，无法发现该违规；同题两次成功都只替换小写词并逐次编译验证。
- 次要因素：C（`bank/merge/0004` 的 trigger 覆盖本案但未被读取）；D（库中无"精确替换契约"类技能——从契约文件按判分器口径解析允许替换对）。
