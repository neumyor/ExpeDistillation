# chess-best-move / attempt 2（失败）

- trial：`traj/tb:chess-best-move/strong/02`；步数 64；读取的技能：`bank/merge/0000`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：第 59 步——程序打印 `mate in 1: []`、`mate in 2: ['h5g3', 'e2d3']`；模型没有回头质疑棋盘重建，而是改按"两步杀"回答，第 61 步把 `e2d3`、`h5g3` 写进 `move.txt`。
- 过程：
  - 第 0–1 步读图并 `cat` 了 `bank-merge-0000/SKILL.md`，检查 `tesseract`/`ffmpeg`/`convert`（都缺）；
  - 第 2–36 步自己做逐格颜色/形状聚类；第 28 步得到 17 个形状簇（e2 是单例白子）；
  - 第 36 步用 FEN `r1bq1r2/1p3pp1/p1n1p3/3nNkbP/8/N1N5/1N2QNP1/R1B1K2R` 跑自制走法生成器，得到 `Kings: None None`、0 个合法着法，说明识别自相矛盾；
  - 第 56 步换成 FEN `r1bq1r2/1p3pp1/p1n1p3/3nPkbN/8/P1B5/1P2QPN1/R1B1K2R`，用 python-chess + stockfish 逐着分析，`e2d3` 给出 `#+1`（即两步杀）；
  - 第 57 步写 `e2d3`；第 59 步明确打印 `mate in 1: []` 后得到两步杀列表；第 61 步把 `e2d3`、`h5g3` 都写入；
  - 第 62–63 步确认完成。
- 直接原因：重建 FEN 至少三格错：c3 应为马（N）写成象（B）、g2 应为兵（P）写成马（N）、h5 应为白兵（P）写成马（N）。真实局面的两个一步杀 `g2g4`（兵）和 `e2e4`（后）在它的棋盘上都不成立，所以 `mate in 1` 为空；它把空集解释成"题目要两步杀"，最终输出 `['e2d3', 'h5g3']`。其中 `h5g3` 在真实局面里不是合法着法（h5 是兵，不是马）。判分断言 `assert sorted(move) == sorted(["g2g4", "e2e4"])` 失败，实际 `['e2d3', 'h5g3']`。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0000`（相关性 无）——视频帧 + tesseract OCR，不能识别棋子；本 trial 读的就是它。
  - `traj/tb:path-tracing/strong/00/seg01`（相关性 低）——像素级比较模板的思路可作为棋盘识别的起点，但没有棋子模板、没有棋规。
  - `traj/tb:path-tracing/strong/00/seg00`（相关性 低）——图像画像（尺寸、颜色、采样），不解决分类。
  - `bank/merge/0004`（相关性 低）——提交前门禁能核对 `move.txt` 存在与格式，但核对不了"是否列出了全部将杀着法"。
- 缺的能力：棋盘截图→FEN 的逐格识别，以及"枚举所有将杀着法"的收尾规则；整库 17 个成员没有 chess/board/piece/FEN 相关内容。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：读了 `bank/merge/0000`（唯一）。
- 原因：它的 [When to use] 被 "moves ... from ... image sequence" 命中，第 1 步还按它检查了 tesseract/ffmpeg/convert，随后完全走自己的颜色/形状聚类。正文是抽帧 + OCR，不能处理棋盘格上的棋子形状，读了但内容不适用；真正需要的棋盘识别技能在库里不存在。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：方法本身是枚举一步杀（第 59 步确实打印了 `mate in 1: []`），是重建棋盘至少三格错（c3、g2、h5）导致空集；空集后它不质疑重建、改答两步杀，写出 `e2d3`/`h5g3`，其中 `h5g3` 在真实局面非法。
- 次要因素：技能库没有棋盘识别类技能（D）；`bank/merge/0000` 的 when-to-use 误命中（C）。
