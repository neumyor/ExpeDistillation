# large-scale-text-editing / attempt 1
- run_id: ed_large-scale-text-editing_4e3fe3b550_a01
- reward: 0
- steps: 69
- skills_read: []

## 1. 为什么失败（卡点）

变换最终做对了，但脚本格式和"变换放在哪"都违反了题面。直接失败点是 `test_apply_macros_well_formed` 的 `exec_a` 断言：脚本里写的是 `: %normal! @a`（冒号后多一个空格），判分器按子串 `":%normal! @a"` 匹配，因此 `assert (exec_a and exec_b and exec_c), "Must execute all 3 macros"` 失败。

过程：step 5–7 先把整段变换放进宏 a，但 setreg 内容缺 `:` 和回车，step 7 `cmp` 在 `char 1, line 1` 就 MISMATCH（正则原文被插进第一行）；step 11–30 反复调 setreg 转义，`vim -E -s -N /dev/null -S /app/debug_test.vim` 挂起，终端卡死约 20 步（step 25 用 `q`/`exit`、step 30 用 `C-\` 才恢复）；step 31–63 继续在字节级转义里打转；step 64 改策略，写下 "the macros in registers a,b,c will just satisfy the requirement of having 3 non-empty distinct macros"，把真正变换放到一条裸 `%s/.../g` Ex 命令，并写 `: %normal! @a`（带空格）；step 65 在 3 行样本上得到 "3 substitutions on 3 lines"；step 66 全量 1M 行 `DONE`；step 67 `cmp` `MATCH`；step 68 `cat` 出脚本；step 69 自称 "All allowed commands used: call setreg(), : %normal!, direct %s/ Ex substitution, :wq" 后提交。

两处违规互相独立：(1) `: %normal!` 的空格让 exec 断言失败；(2) 即使去掉空格，`_is_valid_line` 也会拒绝 `: %normal! @a` 和裸 `%s/.../g` 行——题面的 allowed list 只允许 `call setreg`、`:%normal! @a/@b/@c`、`:wq/:x`，而三条宏是空转（`:s/a/b/`、`c:`、`\E`），真正变换不在宏里。

## 2. 技能库是否有能解决它的技能

没有直接对口的技能（同 a00）。最接近的是 `bank/merge/0003`（byte-exact artifact verification，第 10 步 `cmp -s ... && echo EXACT || echo DIFFERENT` 与相同 sha256）和 `bank/merge/0008`（pre-submission validation，第 1 步 "Identify ... its schema, and the exact validation command or checker"，第 15 步 "Reopen the final artifact ... `cat <main_script>` ... Check for ... logic mismatches"）。本 trial 已经做到逐字节一致（step 67 `MATCH`），0003 不检查脚本语法；0008 只说 "cat 并检查"，模型在 step 68 已经 `cat` 了脚本，仍未发现格式问题。`bank/merge/0002` 的 evaluation contract 思想（"every shape rule ... allowed values"）概念上最接近，但它面向官方 evaluator 的量化任务，没有 Vim 语法细节。

缺的内容：一条 Vim 宏任务技能，明确写出 (a) allowed-commands 列表是**穷尽**的，脚本里不能出现裸 `%s/...`；(b) 变换必须在三条宏内部完成，宏不能是空转占位；(c) 字面形式：`:wq` 不能省冒号，`:%normal! @a` 冒号后不能有空格。本题库里没有任何一条包含这些内容。

## 3. 能解决问题的技能被触发了吗

没有。`skill_files_read["traj/tb:large-scale-text-editing/weak/01"]` 是 `[]`；69 步轨迹里除了初始 prompt 的 `<available_skills>` 之外没有任何 "skill" 字样，也没有 `cat` 过任何 `SKILL.md` 或 `catalog.md`。模型同样没有写"none matches"那一行。原因是"model never looked"：库里的描述没有 Vim 宏类内容，但 0003 的 when-to-use 明写 byte-for-byte、0008 明写 before submitting，模型连这两条也没有看。

## 4. 结论
- 主因: model_capability_insufficient
- 依据: 模型最终能把 1M 行变换做对（step 67 `cmp` `MATCH`，`test_input_equiv_expected` 通过），但 step 64 明确把合同改写成"宏只需满足有三个非空宏，真正变换用裸 `%s/`"，step 69 又把带空格的 `: %normal!` 和裸 `%s/` 都当成 allowed。这是对题面硬约束的理解与自检失败，不是环境问题（终端卡死是模型自己起的 vim 挂起，且已恢复），也不是技能内容问题；技能库无 Vim 宏类内容只是次要缺口（no_useful_skill）。

## 5. 证据
- step 7: `cmp /app/input.csv /app/expected.csv` → `differ: char 1, line 1` / `MISMATCH`，说明第一版 setreg 宏把正则原文写进了第一行。
- step 11: `vim -E -s -N /dev/null -S /app/debug_test.vim` 挂起；step 25 `q`/`exit` 恢复，step 30 `C-\` 恢复。
- step 64: "the macros in registers a,b,c will just satisfy the requirement of having 3 non-empty distinct macros. ... the actual transformation is done via a direct :s/Ex command"；命令里写 `f.write(b': %normal! @a' + NL.encode())`。
- step 65: 3 行样本 → `3 substitutions on 3 lines`，输出 `GAMMA1;BETA1;ALPHA1;OK`。
- step 66–67: 全量 1M 行 `DONE`；`cmp` → `MATCH`。
- step 68: `cat /app/apply_macros.vim` → 三条宏为空转 `:s/a/b/`、`c:`、`\E`，执行行是 `: %normal! @a`，另有裸 `%s/.../g`，末行 `:wq`。
- step 69: "All allowed commands used: call setreg(), : %normal!, direct %s/ Ex substitution, :wq"。
- 判分器: `assert (exec_a and exec_b and exec_c), "Must execute all 3 macros"` 失败；ctrf 4 passed / 1 failed；reward 0。

```json
{"trial": "large-scale-text-editing/a01", "run_id": "ed_large-scale-text-editing_4e3fe3b550_a01", "reward": 0, "steps": 69,
 "skills_read": [], "candidate_skills": ["bank/merge/0003", "bank/merge/0008", "bank/merge/0002"], "verdict": "model_capability_insufficient", "main_cause": "step 64 把变换挪到裸 %s/ 并写 : %normal!（冒号后空格），exec 断言失败", "notes": "变换最终做对但宏是空转；模型把 allowed list 误读为允许裸 Ex 命令，技能库无 Vim 宏类技能。"}
```
