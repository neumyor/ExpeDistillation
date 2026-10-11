日期：2026-10-11
一句话：B1 模型客户端采用 openai>=1 SDK 而非老仓库的 langchain 0.0.x + openai 0.27 组合。
对应 log.md 条目：2026-10-11 09:53（实现 README 蓝图：全部七层代码 + 测试落盘）。

背景：SkillExpand 的 GPTWrapper 走 langchain 0.0.181 链 ChatOpenAI（openai 0.27），
其腾讯中继被迫走流式 SSE 并手工拼接。

决定与理由：
- 沿用的行为：任何 OpenAI 兼容端点、环境变量驱动配置、max_retries=0（重试预算归
  调用方/B6，死端点要响亮地失败）、timeout=300 默认。
- 有意不用：中继强制流式、换行剥离、"gpt" 模型名校验（模型名以端点为准）。
- 新增：B6 异常映射（APIStatusError/APIConnectionError→InfrastructureError 等）、
  拒答识别（finish_reason/message.refusal）、None 内容处理、raw_usage 透传。
- 理由：老 SDK 锁死 pydantic<2 与旧生态，新仓库不需要 langchain 的任何链能力；
  openai>=1 的类型化异常与 B6 分类干净对接。

不迁移的部分：runtime/llm_relay.py 的 E2B/腾讯沙箱中继——B1 直连任意兼容端点，
node29 链路可继续在端点前面做转发。
