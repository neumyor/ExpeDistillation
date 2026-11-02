# sanitize-git-repo / attempt 2（失败）

- trial：`traj/tb:sanitize-git-repo/strong/02`；步数 13；读取的技能：`traj/tb:filter-js-from-html/strong/00/seg02/skill`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里

- 卡点：第 4–5 步的替换范围，以及第 8–9 步的破坏性清理。前两条判分（`test_removal_of_secret_information`、`test_correct_replacement_of_secret_information`）都失败，第三条 `test_no_other_files_changed` 与 a00/a01 相同，因原提交 `d6987af` 被 gc 掉而抛 `ValueError`。
- 过程：
  - 第 2 步用 `git log -S` 确认 4 个假密钥出现在历史多个提交里。
  - 第 3 步读了 `filter-js` 的字节保真技能。
  - 第 4–5 步写的清洗脚本只包含 **4 个写死的字面量** 的 `bytes.replace`，随后用 `filter-branch --tree-filter` 重写全部 108 个提交。
  - 第 8–9 步删除 `refs/original`、把 `origin/main` 指到新 HEAD、`reflog expire`、`gc --prune=now`。
  - 第 10 步收尾复查只 grep 这 4 个字面量的占位符，第 11 步提交完成。
- 直接原因：
  - `test_removal` 在断言阶段失败（trace 被截断，未直接显示是哪个字符串）。从可见证据看，同仓库那份 tokenized JSON 里还有一个 `hf_` 开头的第 5 个 token（a01 第 9 步的扫描明确打印过它），而 a02 只按 4 个字面量替换、收尾也只查这 4 个，任何不在列表里的密钥都会原样留下。
  - `test_correct_replacement` 同样失败，与"替换集合不完整"一致（被截断的 trace 无法逐字确认）。
  - `test_no_other_files_changed` 的 `ValueError` 与 a00/a01 完全同因：原提交对象被清除。

## 2. 技能库里有没有能解决它的技能

- `traj/tb:filter-js-from-html/strong/00/seg02/skill`（相关性 高）——字节级原地改写的操作规范；本 trial 读过。但它假设"要替换什么"已经确定，不负责发现全部密钥。
- `bank/merge/0004`（相关性 高）——提交前门禁，要求用独立参照逐项核对、保留改动前摘要；本 trial 未读。若读了，至少会要求在完成前对"所有应删内容"做一遍独立枚举。
- `bank/merge/0001`（相关性 中低）——备份/可回滚与评测循环，可迁移到"改历史前留原引用"。
- `bank/merge/0003`（相关性 低）——计数/聚合复核，与本题无关。
- 缺的能力：全库秘密枚举（除题面字面量外的同模式 token）、git 历史重写的副作用管理。

## 3. 能解决问题的技能被触发了吗？为什么没解决

- 触发：读了 `filter-js` 技能，且用了它的核心手段（二进制读写、字节替换）。
- 原因：技能内容不足，且模型只照搬了最表层的替换动作。技能第 1 步要求先备份 + `cmp`、第 5 步要求"每条规则一个 safe/bad marker 的 fixture"——本 trial 都没有做，因此既没有可回滚的中间态，也没有对"规则是否覆盖全部危险串"的自检。技能本身也没有 git 历史相关内容，决定性动作仍完全在技能覆盖之外。

## 4. 结论与依据

- 主因：A_model_capability
- 一句话依据：只对 4 个写死的字面量做替换并在收尾只复查这 4 个，漏掉了同一 JSON 里已由 a01 证实存在的第 5 个 `hf_` token，导致 `test_removal` 断言失败；同一轮又执行了 `refs/original` 删除 + `gc --prune=now`，使 `test_no_other_files_changed` 因原提交 missing 报错。
- 次要因素：B_skill_content（读到的技能只管字节保真，不含枚举）；D_skill_missing（无 git 历史技能）；E_environment（判分器以历史中段提交做参照，对对象清理极敏感）。
