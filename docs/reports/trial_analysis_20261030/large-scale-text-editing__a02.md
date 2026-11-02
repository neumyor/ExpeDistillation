# large-scale-text-editing / attempt 2
- run_id: ed_large-scale-text-editing_4e3fe3b550_a02
- reward: 0
- steps: 22
- skills_read: []

## 1. 为什么失败（卡点）

变换全部做对，唯一失败是脚本第 1 行的写法：`call setreg('a', ':s/ //ge' . nr2char(13))`。这里用 Vimscript 函数 `nr2char(13)` 加 `.` 拼接来生成回车，违反题面 "NO Vimscript functions" 和 `call setreg('a', "your_keystrokes_here")` 的单行字面量形式；判分器 `_is_valid_line` 要求 setreg 行以 `')` 或 `")` 结尾，该行以 `))` 结尾，因此报 `Invalid command on line 1: call setreg('a', ':s/ //ge' . nr2char(13))`。

过程：step 7 写出三条宏（a 去空格、b 倒序、c 大写+`;OK`），全部用 `. nr2char(13)`；step 8–9 在 5 行样本上发现 c 宏没生效（`GaMmA1;BeTa1;alpha1`，无大写/`;OK`）；step 10 把 c 改成 normal-mode `0$A;OK\x1bgUU` 并测试成功；step 11–12 在 1M 行副本上跑完，`cmp` `MATCH`；step 13–15 复制到 `/app/apply_macros.vim`，干净副本再跑 `EXIT=0`、`BYTE_FOR_BYTE_MATCH`；step 17 `cat -A` 显示第 1、2 行仍带 `nr2char`；step 18 自称 "Contains only allowed commands: 3 call setreg(), 3 :%normal! @X, :wq" 且 "Macro content uses basic vim keystrokes"；step 19–20 用题目命令再跑一次，`RC=0`、`FINAL_MATCH`；step 21–22 提交。

其余 4 项判分全过：脚本存在、vim 退出 0、input≡expected、三宏非空/互异/总键数 <200。

## 2. 技能库是否有能解决它的技能

没有直接对口的技能（同 a00）。最接近的是 `bank/merge/0003`（byte-exact artifact verification）与 `bank/merge/0008`（pre-submission validation），前者只覆盖逐字节比较（本 trial 已过，step 12/20 `MATCH`），后者第 15 步只说 "Reopen the final artifact ... `cat <main_script>` ... Check for ... logic mismatches"（模型在 step 17 已经 `cat -A` 看过文件）；`bank/merge/0002` 的 evaluation contract 思想（"every shape rule ... allowed values"）也不含 Vim 语法。三者都覆盖不到"setreg 必须是单个引号字面量、不能调用 `nr2char`/`.` 拼接"。

缺的内容：一条 Vim 宏任务技能，明确写出 (a) setreg 内容必须是单行字面量字符串；(b) 允许用双引号字符串里的 `\n`/`\r`/`\x1b` 转义来嵌入回车/ESC，不能用 `nr2char` 等 Vimscript 函数或 `.` 拼接；(c) 交付前逐行对照题面的 allowed-commands 模板。同题 a00 在同一技能库下用 `call setreg('a', ":s/ //ge\n")` 的合法写法完成了同样的变换，说明这条知识不依赖技能也能得到；但库里确实没有把它写成检查项的技能。

## 3. 能解决问题的技能被触发了吗

没有。`skill_files_read["traj/tb:large-scale-text-editing/weak/02"]` 是 `[]`；22 步轨迹里除了初始 prompt 的 `<available_skills>` 之外没有任何 "skill" 字样，也没有 `cat` 过任何 `SKILL.md` 或 `catalog.md`。模型没有写"none matches"那一行。原因是"model never looked"：库里的描述没有 Vim 宏类内容；0003 的 byte-for-byte 与 0008 的 before-submitting 与本题表面匹配，但模型没有查看。

## 4. 结论
- 主因: model_capability_insufficient
- 依据: 变换 100% 正确（step 12/20 `cmp`/`sha256` `MATCH`，功能三项判分全过），失败纯粹是把题面明确禁止的 `nr2char` 写进 setreg，并在 step 18 把这条脚本判成 "only allowed commands"。同题 a00 用 `\n` 的合法写法完成了同样的变换，说明这是规范的遵循/自检问题，不是知识或技能内容问题；技能库无 Vim 宏类内容只是次要缺口（no_useful_skill）。

## 5. 证据
- step 7: `call setreg('a', ':s/ //ge' . nr2char(13))`、`call setreg('b', ... . nr2char(13))`、`call setreg('c', ':s/.*/\U&/;s/$/;OK/' . nr2char(13))`。
- step 8–9: 5 行样本输出 `GaMmA1;BeTa1;alpha1`，c 宏未生效。
- step 10: c 宏改为 `call setreg('c', "0\$A;OK\x1bgUU")`，测试输出 `GAMMA1;BETA1;ALPHA1;OK`。
- step 11–12: 1M 行副本跑完；`cmp /tmp/test_full.csv /app/expected.csv` → `MATCH`。
- step 13–15: 复制到 `/app/apply_macros.vim`（198 字节）；干净副本 `EXIT=0`；`BYTE_FOR_BYTE_MATCH`。
- step 17: `cat -A /app/apply_macros.vim` 显示第 1、2 行仍为 `... . nr2char(13))`。
- step 18: "Contains only allowed commands: 3 call setreg(), 3 :%normal! @X, :wq"。
- step 19–20: 题目命令 `RC=0`；`cmp` → `FINAL_MATCH`。
- 判分器: `_is_valid_line` 对第 1 行返回 False，`AssertionError: Invalid command on line 1: call setreg('a', ':s/ //ge' . nr2char(13))`；ctrf 4 passed / 1 failed；reward 0。

```json
{"trial": "large-scale-text-editing/a02", "run_id": "ed_large-scale-text-editing_4e3fe3b550_a02", "reward": 0, "steps": 22,
 "skills_read": [], "candidate_skills": ["bank/merge/0003", "bank/merge/0008", "bank/merge/0002"], "verdict": "model_capability_insufficient", "main_cause": "变换与 1M 行全量正确，但 setreg 用了题面禁止的 nr2char(13)，well_formed 判分失败", "notes": "同题 a00 用合法的反斜杠 n 写法完成同样变换；技能库无 Vim 宏/setreg 语法类技能。"}
```
