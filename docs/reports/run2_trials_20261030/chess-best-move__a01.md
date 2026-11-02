# chess-best-move / attempt 1（失败）

- trial：`traj/tb:chess-best-move/strong/01`；步数 39；读取的技能：`bank/merge/0000`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：第 32–33 步——模型用自己重建的 FEN 问 stockfish "best move"，得到 `bestmove b2b3`，第 33 步把 `b2b3` 写进 `move.txt` 并重复确认。
- 过程：
  - 第 0–1 步读图并 `cat` 了 `bank-merge-0000/SKILL.md`；第 2 步按该技能的 preflight 检查 `ffmpeg`/`tesseract`/`stockfish`，前两个缺失；
  - 第 2–24 步自己写逐格像素统计与形状 ASCII，第 14 步聚类脚本报 `TypeError`，第 18–20 步重写后得到形状差异表；
  - 第 26 步发现系统里已有 stockfish；第 32 步用 FEN `r1bk1r2/1p3pp1/p1n1p3/3nPqbp/8/P1N5/1P2QPP1/R1B1K2R w KQ - 0 1` 跑 `go perft 1`，`e2e4` 在其合法着法列表里；
  - 同一 FEN 下 `go movetime 10000` 与 `go depth 14` 都返回 `bestmove b2b3`；第 33 步写入 `b2b3`；
  - 第 36 步 MultiPV=10 仍是 `bestmove b2b3`，没有枚举将杀；第 37–38 步确认完成。
- 直接原因：两处独立错误叠加。① 重建 FEN 把黑王和后读反：真实局面黑王在 f5、黑后在 d8，它写成 f5 是后、d8 是王（`r1bk1r2` 与 `3nPqbP`）。这个局面里没有一步杀，stockfish 的自然选择是非杀着 `b2b3`。② 它把题面理解成"要一个最佳着法"，直接取引擎的 `bestmove`，没有按题面 "If there are multiple winning moves, print them all, one per line" 枚举全部将杀着法。判分断言 `assert sorted(move) == sorted(["g2g4", "e2e4"])` 失败，实际 `['b2b3']`。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0000`（相关性 无）——视频帧 + tesseract OCR，不能识别棋子；本 trial 读的就是它。
  - `traj/tb:path-tracing/strong/00/seg01`（相关性 低）——像素级比较模板的思路可作为棋盘识别的起点，但没有棋子模板、没有棋规。
  - `traj/tb:path-tracing/strong/00/seg00`（相关性 低）——图像画像（尺寸、颜色、采样），不解决分类。
  - `bank/merge/0004`（相关性 低）——提交前门禁能核对 `move.txt` 存在与格式，但核对不了"是否列出了全部将杀着法"。
- 缺的能力：棋盘截图→FEN 的识别，以及"枚举所有将杀着法而不是只取引擎的最佳着法"的收尾规则；整库 17 个成员没有 chess/board/piece/FEN 相关内容。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：读了 `bank/merge/0000`（唯一）。
- 原因：它的 [When to use] 被 "moves ... from ... image sequence" 命中，但正文是 ffmpeg 抽帧 + OCR + 覆盖校验；模型按它跑了一次依赖探测后就放弃了。读了但内容不适用。真正需要的棋盘识别技能不存在；而"只取单个 bestmove"的问题即使有棋盘技能也仍然存在。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：棋盘把 f5 黑王读成后、d8 后读成王，且在题面要求多个制胜着法全部打印时只取 stockfish 的单个 `bestmove` 写 `b2b3`；即使补齐棋盘识别技能，这种取单着的方法仍会漏掉另一个将杀着法（`e2e4` 与 `g2g4`）。
- 次要因素：技能库没有棋盘识别类技能（D）；`bank/merge/0000` 的 when-to-use 误命中（C）。
