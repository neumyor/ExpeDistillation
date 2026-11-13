# 执行模块（execution）

把交付给的执行单元真正跑一遍，每跑一次留一条运行记录（哪道题、注入了什么、谁做的、
第几次、benchmark 自带判分器给的对错、重试次数、报错原文、轨迹位置）。它不判定成没成，
也不解释为什么失败；没拿到任何结果时（崩溃、断网）才机械重跑。

每个执行阶段都会执行。配置写在执行阶段的 `execution` 里，`name` 选方法。

## 方法一览

| 方法 | 做什么 | 用在哪个阶段 | 参数 |
|---|---|---|---|
| `real` | 在 benchmark 里真做题 | baseline（摸底，只能用它）、test（最终测试） | 见下 |
| `real_folder` | 和 `real` 一样真做题，但技能以文件夹形式放进运行环境 | practice（练习，只能用它）、test（最终测试），必须配 `folder` 交付 | `executor`、`attempts` |

阶段和方法的搭配在读配置时检查，配错直接报错。

`real_folder` 和 `folder` 交付一一配对。

## `real`：真做题

```yaml
execution:
  name: real
```

baseline（摸底）阶段的写法固定为：

```yaml
execution:
  name: real
  arms: [executor_strong, executor_weak]
```

| 参数 | 含义 | 可选项 | 默认 |
|---|---|---|---|
| `arms` | 谁来做。只有 baseline 相位写，且必须是这一个值；其他阶段只跑一个执行者，不能写 | `[executor_strong, executor_weak]` | 不写 |

每道题做几次看全局的 `n_attempts_strong` / `n_attempts_weak`。

## `real_folder`：技能文件夹做题

```yaml
execution:
  name: real_folder
  executor: executor_weak
  attempts: 3
```

| 参数 | 含义 | 可选项 | 默认 |
|---|---|---|---|
| `executor` | 谁来做这个阶段的题 | `executor_weak`、`executor_strong` | `executor_weak` |
| `attempts` | 每道题做几次；不写就取执行者的全局次数（`n_attempts_weak` 或 `n_attempts_strong`） | 不小于 1 的整数 | 不写 |

选 `executor_strong` 时，`compare.external.model_key` 要指向强模型在 campaign 里的那一列
（例如 `dpsk`），否则对照用的是另一个模型的成绩。强模型在 Harbor 里的推理强度由进程环境变量
`TBENCH_REASONING_EFFORT` 决定，不由本配置决定：要和 campaign 的强模型对照一致，就在启动这个
run 的进程里设置它，不要写进共用的密钥文件（那会同时改变弱模型的请求）。

模块边界见 `docs/decisions/execute_learn_module_boundaries_20261016.md`。
