# InkTwin

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![100% Offline](https://img.shields.io/badge/Mode-100%25%20Offline-444444.svg)](#这是干嘛的)

**帮你“免手抄”的离线手写作业工具**：平时照常在电脑上敲 Markdown 和数学公式，它用**你自己的真实字迹**帮你“写”出一份手写版 PDF；顺手还能把你的字做成 `.ttf` / `.otf` 电脑字体。

*Finish assignments by typing Markdown + LaTeX, render them into vector PDFs in your own handwriting — or compile a personal `.ttf`/`.otf` font. 100% local & offline, no watermark.*

---

## 先看效果

下面几张图都是用仓库自带的公共示例包 [`packs/demo_sample.hwpack`](packs/demo_sample.hwpack) 在本地直接跑出来的，还没录自己的字前就能先试试：

| 方格纸 + 长公式自动换行 | 横线练习本（蓝笔 · 工整模式） | 直接填空白原卷，不盖原题 | 铅笔纸纹质感 |
| :---: | :---: | :---: | :---: |
| ![方格纸效果](docs/gallery/showcase_grid.png) | ![横线本效果](docs/gallery/showcase_ruled8.png) | ![原卷叠写效果](docs/gallery/showcase_overlay_p0.png) | ![铅笔笔触效果](docs/gallery/showcase_pencil.png) |

---

## 这是干嘛的？

平时写高数、大物、概率论作业，在电脑上打字、敲 LaTeX 推导其实快得多，但每次要交手写版，照着屏幕一笔一画抄实在太费手了。

网上现成的手写生成器试过几个，但总觉得不太够用，所以干脆自己写了 **InkTwin**。它和常见工具最大的区别就四点：

1. **真的是你的字**：用的不是网上烂大街、一眼假的现成手写体，而是从你平板笔迹里原封不动抠出来的真字，连分数线、根号、括号和箭头都是你自己的笔迹。
2. **能扛得住理科作业**：积分、分式、长连等式、表格、数轴、函数图像、物理受力图都能手写出来，公式太长会自动换行，不会挤成蚂蚁字。
3. **能直接填空白试卷**：老师发了空白 PDF / DOCX 试卷，它能找空白位置把答案“写”上去，不盖住原题，也可以自己拖框定位置。
4. **纯本地离线**：全程跑在自己电脑上，不收费、没水印，也不用把个人笔迹上传到别人服务器。

---

## 平时怎么用？（一共三步）

**第一步只需要做一次，以后每次写作业只跑第二步和第三步。**

### 第 1 步：采集一次你的字迹
1. **直接下载现成字帖**：标准字帖人人一样，不用自己生成，直接拿现成的就行——[**Tier 1 基础字帖（3 页）**](assets/templates/InkTwin-Tier1.pdf)（数字和常用字母、符号自带 2～3 格多变体，加基本笔画、24 个偏旁和 80 个高频理科汉字），需要更多物理/希腊字母可以顺手下 [**Tier 2 进阶字帖（1 页）**](assets/templates/InkTwin-Tier2.pdf)。
2. **平板抄一遍**：把字帖导进平板（StarNote / GoodNotes / Notability 等），像平时一样写满，导出成**矢量 PDF**（注意别导出成图片）。
3. **自动抠字建库**：把填好的 PDF 丢回程序，它按格子把你的每一笔抠出来，打包成一个专属的“笔迹包”（`.hwpack`）。建完库还能直接在 `inktwin studio` 的「字库变体体检」里逐个看效果，写歪或写糊的变体点一下就能删掉。
   - 如果想在 Word / WPS 里直接打出自己的字，顺手就能把这个笔迹包变成 `.ttf` / `.otf` 字体装进电脑，连打相同字（比如 `000`、`aaa`）还会自动换不同写法。

### 第 2 步：把电脑里的作业“变成手写”
1. 照常在电脑上用 Markdown + LaTeX 写好解题过程（需要画数轴、函数图或受力图，写几行简单配置就行）。
2. 打开自带的本地网页界面（`inktwin studio`），选好：
   - **用什么纸**：方格纸、横线本、作文纸、点阵、米黄纸、空白纸，或者直接上传**空白试卷 PDF**；
   - **用什么笔**：黑笔 / 蓝笔，中性笔 / 圆珠笔 / 钢笔 / 铅笔；
   - **写字状态**：工整（`neat`）、正常（`normal`）、还是赶 DDL（`rush`，越赶越紧凑潦草）。
3. 点生成，程序会自动把正文、公式、符号全部换成你的手写笔画，长公式自动折行；遇到字帖里**没写过的生字**，会先拿你的偏旁和单笔画临时“拼”一个风格相近的顶上——觉得某几个字拼得不够像，还能只针对这几个字吐一张小补丁字帖，补写一下合进字库，越用越像你。

### 第 3 步：检查、微调、导出交作业
- 生成完直接一页页预览；
- 觉得哪一行排得太齐像打印的，或者空隙太大，点一下就能自动调自然；
- 哪怕只对**某一个字**不满意，也能打开单字编辑器直接拖动位置、改大小、切红笔，或者按 `R` 换你的另一种写法；
- 满意了直接导出干净的 PDF 完事。

---

## 3 分钟跑起来

需要 **Python 3.10+**，以及电脑上装了 **Chrome** 或 **Edge**（只用于本地离线排版，不会联网）。

```bash
git clone https://github.com/estuardoestrada128-bot/InkTwin.git
cd InkTwin
pip install -e .

# 检查环境、浏览器和自带资源是否就绪
inktwin doctor
```

看到 `ok=True` 后，直接启动本地工作台最省心（Windows 用户也可以直接**双击项目根目录的 [`start_studio.bat`](start_studio.bat)** 启动，使用期间保持弹出的黑框最小化不关即可）：

```bash
inktwin studio
```

浏览器会自动打开界面，**采字建库（Forge）→ 选纸排版/试卷拖框（Compose）→ 预览微调导出（Preview）** 全都能点点鼠标完成。

---

## 常用命令行速览

习惯敲命令的话，平时主要就用到这几条：

```bash
# 1. 先用自带演示包跑一篇作业看看效果
inktwin render homework.md -o out.pdf --pack demo_sample --paper ruled8 --ink blue --mood neat

# 2. 直接下载 assets/templates/InkTwin-Tier1.pdf（平板填完导出矢量 PDF）-> 建库 -> 编电脑字体
inktwin harvest filled_tier1.pdf -o packs/local/my_hand.hwpack --id my_hand
inktwin fontbuild --pack my_hand --format ttf -o InkTwin-MyHand.ttf

# 3. 遇到没写过的生字，只针对这篇作业出一张补丁字帖，写完合回字库
inktwin template --tier targeted --from-md homework.md -p my_hand -o patch.pdf
inktwin harvest filled_patch.pdf -o packs/local/my_hand.hwpack --merge-into my_hand

# 4. 直接把解答叠写到空白试卷 PDF 上
inktwin render solutions.md --overlay blank_exam.pdf --pack my_hand --pen-type fountain -o overlaid.pdf
```

> 画草图（`handplot`）、Word 开启多变体轮换、空白试卷自定义坐标、单字编辑器快捷键等详细用法，都整理在 [**完整使用手册 docs/GUIDE.md**](docs/GUIDE.md) 里。

---

## 用之前顺便提几句

- **平板导出记得选矢量 PDF**：不要勾选“拼合/栅格化为图片”，不然提不到矢量笔画；写字时也别把四角的青色定位小方块盖住。
- **生字拼接不是魔法**：没写过的字靠你的偏旁和笔画拼出来，混在段里挺自然，但高频字或特别复杂的字还是用 `--tier targeted` 补写一遍最像你自己。
- **开发测试 & Agent 集成**：改了代码可以跑 `python tests/run_tests.py` 和 `python evals/grade.py` 做自检；平时用 Claude Code / Codex / Cursor / Antigravity 的话，也能用 `python install.py --agent both --scope project --link` 注册成 Skill。

---

## 目录与隐私

```text
InkTwin/
├── packs/
│   ├── demo_sample.hwpack   # 自带的公开脱敏示例字库
│   └── local/               # 放你自己的私密 .hwpack（已被 .gitignore 忽略）
├── assets/                  # 离线 KaTeX、汉字笔顺骨架库与兜底字体
├── docs/                    # 详细使用手册与效果截图
├── src/inktwin/             # 核心源码
├── tests/                   # 离线测试套件
└── evals/grade.py           # 排版质量与隐私检查脚本
```

- 自己的 `.hwpack` 放 `packs/local/` 就行，[.gitignore](.gitignore) 默认会拦住它，不怕手滑推到公开仓库。
- 项目以 [MIT License](LICENSE) 开源。用着顺手或者遇到翻车的公式/试卷，欢迎直接提 Issue 或 PR。
