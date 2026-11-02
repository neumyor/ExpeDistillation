# sanitize-git-repo / attempt 0（失败）

- trial：`traj/tb:sanitize-git-repo/strong/00`；步数 30；读取的技能：`bank/merge/0004`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里

- 卡点：第 11 步。模型在重写完全部 108 个提交后，删除 `refs/original`、清空 reflog 并执行 `git gc --prune=now`，把原始提交对象彻底清除；判分第三条 `test_no_other_files_changed` 要读取原提交 `d6987af` 作为基准，`git cat-file` 返回 "missing"，测试在调用阶段抛 `ValueError: SHA ... could not be resolved`。
- 过程：
  - 第 1–2 步定位到 4 个假密钥：两个 ray 相关文件（`ray_processing/process.py`、`ray_processing/ray_cluster.yaml`）加一份 tokenized JSON（其内容里嵌着一段含密钥的 git diff）。
  - 第 3 步读了 `bank/merge/0004`（提交前门禁），第 4 步用 `git log -S` 确认密钥在历史里多处出现。
  - 第 9 步先把工作树连同 `.git` 备份到临时目录，再执行 `git filter-branch --tree-filter ... -- --all` 重写全部历史。
  - 第 11 步删除 `refs/original`、removed `.git/logs`、`git reflog expire --expire=now --all`、`git gc --prune=now`。
  - 第 23 步自检 539 个文件 / 829 个 blob / 55754 个树路径，命中 0；第 24–27 步复查占位符、`git fsck` 均干净；第 28 步提交完成。
- 直接原因：工作树清洗本身是**对的**（`test_removal_of_secret_information` 与 `test_correct_replacement_of_secret_information` 都通过），失败完全来自第 11 步的破坏性清理——判分器用来做"没有改别的文件"比对的原始提交被删，第三条测试无法执行。

## 2. 技能库里有没有能解决它的技能

- `bank/merge/0004`（相关性 高）——提交前门禁，明确覆盖 "byte-preserving sanitizers/filters"，要求先备份原物、保留改动前摘要、用独立参照逐项复核。本 trial 读过。但它只到"文件级交付物"这一层，不含 git 历史/对象。
- `traj/tb:filter-js-from-html/strong/00/seg02/skill`（相关性 高）——逐字节原地改写、`.bak`+`cmp`、按规则构造 fixture 验证；是"清洗"本身的正确操作手册。本 trial 未读。
- `bank/merge/0001`（相关性 中低）——覆盖"覆盖固定交付物前先建带哈希的备份"，思路可迁移到"动历史前先把原引用留好"。
- `bank/merge/0003`（相关性 低）——数据产物计数/复核，与本题无关。
- 缺的能力：git 历史清洗的安全边界——何时值得改写历史、改写后哪些引用/对象必须保留、以及"只改题面允许的文件"的全仓库差分核对。

## 3. 能解决问题的技能被触发了吗？为什么没解决

- 触发：读了 `bank/merge/0004`，且大部分照做（备份、逐文件扫描、完成前读回复核）。
- 原因：技能内容不足。它第 1 步要求 "Never overwrite a surviving original until the new content passes verification"，但这里的 "original" 只指文件；git 对象和引用不在它的检查表里，也没有任何一步覆盖"重写历史后判分器可能仍要读原提交"。模型在技能之外自行决定清洗全部历史并做不可逆清理，技能既没有授权也没有拦住这一步。第 2 步 "Write or overwrite only that path" 反而暗示应把改动限制在指定范围，模型把它扩大到了整个仓库历史。

## 4. 结论与依据

- 主因：A_model_capability
- 一句话依据：文件级清洗两条断言全过，但第 11 步在判分只需要当前文件的情况下，删除 `refs/original` 并 `gc --prune=now`，把判分器用作基准的原提交 `d6987af` 清掉，`test_no_other_files_changed` 直接 `ValueError`。
- 次要因素：D_skill_missing（库中没有 git 历史类技能）；E_environment（判分器以历史中段提交做参照，对对象清理极敏感，属于脆弱判分设计）。
