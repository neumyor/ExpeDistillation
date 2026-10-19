# 执行模块（execution）

把交付给的执行单元真正跑一遍，每跑一次留一条运行记录（哪道题、注入了什么、谁做的、
第几次、benchmark 自带判分器给的对错、重试次数、报错原文、轨迹位置）。它不判定成没成，
也不解释为什么失败；没拿到任何结果时（崩溃、断网）才机械重跑。

每个执行阶段都会执行。配置写在执行阶段的 `execution` 里，`name` 选方法。

## 方法一览

| 方法 | 做什么 | 用在哪个阶段 | 参数 |
|---|---|---|---|
| `real` | 在 benchmark 里真做题 | baseline（摸底）、live（实战验收）、test（最终测试） | 见下 |
| `exam` | 现场出考题，弱模型裸答一次、带这道题对应的技能再答一次（判卷、泄题检查归验收） | exam（纸面验收），只能用它 | 无 |
| `real_folder` | 和 `real` 一样真做题，但技能以文件夹形式放进运行环境 | test（最终测试），必须配 `folder` 交付 | 无 |

阶段和方法的搭配在读配置时检查，配错直接报错。

## `real`：真做题

```yaml
execution:
  name: real
  n_attempts: 3
  max_injection_tokens: null
```

baseline（摸底）阶段的写法固定为：

```yaml
execution:
  name: real
  arms: [executor_strong, executor_weak]
```

| 参数 | 含义 | 可选项 | 默认 |
|---|---|---|---|
| `n_attempts` | 弱模型每道题做几次 | 正整数，不能超过对照阶段弱模型做的次数；baseline 相位不能写 | 全局 `n_attempts_weak` |
| `max_injection_tokens` | 注入技能的长度上限（估算 token）。目前只有 live 相位用：一包技能超长时这道题不跑，记为"测不了" | 正整数；`null` = 不设上限 | `null` |
| `arms` | 谁来做。只有 baseline 相位写，且必须是这一个值；其他阶段只跑弱模型，不能写 | `[executor_strong, executor_weak]` | 不写 |

baseline 相位强、弱模型各做几次，看全局的 `n_attempts_strong` / `n_attempts_weak`。

模块边界见 `docs/decisions/execute_learn_module_boundaries_20261016.md`。
