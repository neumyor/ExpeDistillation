# chess-best-move / attempt 0（失败）

- trial：`traj/tb:chess-best-move/strong/00`；步数 68；读取的技能：`bank/merge/0000`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：第 62 步——模型用自己重建的 FEN 跑 python-chess + stockfish，得到 `mating_moves= ['g2g4']`；第 65 步把 `g2g4` 写进 `move.txt` 并复跑同一验证，仍然只有这一步。
- 过程：
  - 第 0–4 步：读图（640×640 RGB），第 2 步 `cat` 了技能文件夹里的 `bank-merge-0000/SKILL.md`，随后自己写颜色直方图与 ASCII 渲染；
  - 第 8–36 步：按 80×80 格统计主色与黑白像素数、聚类棋子形状；第 28 步聚类脚本还报过 `TypeError: 'int' object is not subscriptable`；
  - 第 12 步确认 apt 源里有 stockfish，第 16 步 pip 装 python-chess 失败，之后（第 62 步前）已可用；
  - 第 62 步重建 FEN `r1bq1r2/1p3pp1/p1n1p3/3nPkbP/8/P1B5/1P2QPP1/R1B1K2R w - - 0 1`，遍历 `board.legal_moves` 判 `is_checkmate()`，只得到 `g2g4`；
  - 第 65 步写入 `g2g4`，第 66–67 步确认完成。
- 直接原因：重建 FEN 把白方 c3 的马（N）读成了象（B）。题目环境生成棋盘用的 FEN 是 `.../P1N5/...`（c3=N），与模型的重建只差这一格。c3 的马守住 e4：真实局面里 `e2e4`（白后 e2→e4；测试注释称其为 pawn move，但按 FEN e2 实际是后）是将杀，因为黑王 f5 不能吃一个被马保护的后；换成象后 e4 无人防守，`e2e4` 不再是杀，模型自己的引擎自然查不到。判分断言 `assert sorted(move) == sorted(["g2g4", "e2e4"])` 失败，实际 `['g2g4']`。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0000`（相关性 无）——summary 是 "Extract ordered text, commands, or moves from video or image sequences with ffmpeg and tesseract"，正文是抽帧、OCR、片段合并、帧覆盖校验；棋盘 PNG 上没有可 OCR 的文字，教不了"这一格是马还是象"。
  - `traj/tb:path-tracing/strong/00/seg00`（相关性 低）——第 2 步要求对图像 "print dimensions, mode, unique-pixel count, most common values, and sample rows near top/center"，是图像画像，不是棋子分类。
  - `traj/tb:path-tracing/strong/00/seg01`（相关性 低）——第 7 步 "compare it pixel by pixel ... maximum absolute difference is 0" 是模板匹配的雏形，但它针对"复现参考程序的输出"，没有棋子模板和棋盘语义。
  - `bank/merge/0004`（相关性 低）——提交前门禁只核对产物路径/约束/端到端行为，无法判断 `move.txt` 里的着法是否正确。
- 缺的能力：从棋盘截图恢复 FEN 的逐格识别（把 12 个 Unicode 棋子字形按同一字体渲染成模板、逐格做匹配），以及用棋规引擎枚举全部将杀着法的收尾检查。整库 17 个成员的正文里没有出现 chess / board / piece / FEN 任何一处。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：读了 `bank/merge/0000`（本 trial 唯一读的技能）。
- 原因：它的 [When to use] 写的是 "a task asks for ordered text, commands, or moves that must be read from video frames or an image sequence"，单张棋盘图被这句误命中；正文只处理视频帧与 OCR，模型读完后也没有采用其中的 ffmpeg/tesseract 步骤，而是走自己的颜色/形状分析。属于"读了但内容不适用"；真正需要的棋盘识别技能在库里不存在，所以失败不来自触发覆盖不足，而来自模型自己的棋子分类错误。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：模型已经用 python-chess 正确枚举将杀（第 62 步遍历合法着法并 `is_checkmate()`），唯一的错误是 c3 一格把马看成象，导致 `e2e4` 在其 FEN 里不再是将杀，最终 `move.txt` 只有 `['g2g4']`，判分要求 `['e2e4', 'g2g4']`。
- 次要因素：技能库没有棋盘识别类技能（D）；`bank/merge/0000` 的 when-to-use 把 "image sequence" 扩到单张棋盘图，造成误触发（C）。
