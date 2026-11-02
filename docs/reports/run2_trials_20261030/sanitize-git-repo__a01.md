# sanitize-git-repo / attempt 1（失败）

- trial：`traj/tb:sanitize-git-repo/strong/01`；步数 31；读取的技能：`traj/tb:filter-js-from-html/strong/00/seg02/skill`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里

- 卡点：第 26 步。模型重写历史后删除 `refs/original`、`git reflog expire --expire=now --all`、`git gc --prune=now`，原始提交 `d6987af` 随之消失；判分第三条 `test_no_other_files_changed` 在解析该提交时抛 `ValueError: SHA ... could not be resolved, git returned: ... missing`。
- 过程：
  - 第 1–2 步读了 `traj/tb:filter-js-from-html/.../seg02` 技能，同时扫到 4 个假密钥。
  - 第 9 步用 `git log -S` 发现**第 5 个** `hf_` 开头的 token 也在历史与那份 tokenized JSON 里出现。
  - 第 10 步用 `git bundle create` 备份全部引用；第 11–13 步写正则清洗脚本（含 `hf_[A-Za-z0-9]{20,}`）改写工作树，第 14 步提交 3 个文件。
  - 第 15 步 `filter-branch` 参数写错（usage 报错），第 23–25 步改用 `dirty.map` + `index-filter` 重写全部 blob。
  - 第 26 步删除 `refs/original`、清 reflog、`gc --prune=now`；第 28 步**把自己的 bundle 备份目录也删了**；第 29 步提交完成。
- 直接原因：与 a00 相同——`test_removal_of_secret_information`、`test_correct_replacement_of_secret_information` 都通过，只有第三条因原提交对象被清除而失败。

## 2. 技能库里有没有能解决它的技能

- `traj/tb:filter-js-from-html/strong/00/seg02/skill`（相关性 高）——逐字节原地改写、备份 + `cmp`、按规则 fixture 验证；本 trial 读过，是清洗动作本身最贴的技能。
- `bank/merge/0004`（相关性 高）——提交前门禁，覆盖 "byte-preserving sanitizers/filters" 的独立复核与备份纪律；本 trial 未读。
- `bank/merge/0001`（相关性 中低）——覆盖"改固定交付物前先备份、记哈希、可回滚"。
- `bank/merge/0003`（相关性 低）——数据产物复核，与 git 无关。
- 缺的能力：git 历史重写的副作用管理——哪些引用必须保留、何时不能 `gc --prune=now`、判分器可能仍以哪些提交为基准。

## 3. 能解决问题的技能被触发了吗？为什么没解决

- 触发：读了 `filter-js` 的字节保真技能，且采用了其中的核心做法（`read_bytes`/`write_bytes`、正则替换、改写后复查）。
- 原因：技能只管"单个文件怎么安全改写"，完全没有覆盖 git 历史。它第 1 步要求先 `cp` 出 `.bak` 并 `cmp`、第 5 步要求按"每条规则一个 fixture"验证，这两步本 trial 都没做（没有逐文件备份，也没有规则级 fixture），但即使做了也救不了历史对象被删。决定性动作（重写全部历史 + 删除 `refs/original` + 立即 gc）不在技能覆盖范围内，属于"读了但内容不足"。

## 4. 结论与依据

- 主因：A_model_capability
- 一句话依据：清洗后两条内容断言全过，第 26 步的 `refs/original` 删除 + `gc --prune=now` 让判分基准提交 `d6987af` 变成 missing，第三条测试 `ValueError`；第 28 步还主动删掉了唯一可回滚的 bundle 备份。
- 次要因素：D_skill_missing（无 git 历史技能）；B_skill_content（读到的技能只覆盖单文件字节保真，无法约束仓库级操作）。
