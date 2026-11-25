# InkTwin 详细使用手册 / Detailed User Guide

本手册补充说明 `InkTwin` 在日常作业、字库锻造、原卷叠写、草图绘制与字体使用中的详细技巧与排查方案。

---

## 目录

1. [平板填字帖与零 OCR 建库最佳实践](#1-平板填字帖与零-ocr-建库最佳实践)
2. [在 Word / WPS 中使用编译后的 Personal 字体（启用 `calt` 多变体轮换）](#2-在-word--wps-中使用编译后的-personal-字体启用-calt-多变体轮换)
3. [声明式手绘草图 `handplot` 完整语法与示例](#3-声明式手绘草图-handplot-完整语法与示例)
4. [空白 PDF 原卷叠写：`layout.json` (`boxes` + `marks`) 进阶控制](#4-空白-pdf-原卷叠写layoutjson-boxes--marks-进阶控制)
5. [`InkScene` 逐字编辑器快捷键与常用命令](#5-inkscene-逐字编辑器快捷键与常用命令)
6. [常见问题排查 (FAQ)](#6-常见问题排查-faq)

---

## 1. 平板填字帖与零 OCR 建库最佳实践

1. **推荐笔记软件与导出设置**：
   - 推荐使用 **StarNote / GoodNotes / Notability / Apple Notes**。
   - **关键设置**：导出时务必选择 **矢量 PDF**（不要勾选“拼合/栅格化为图片”）。若软件只支持标准 PDF 导出，保持默认压感钢笔导出即可。
2. **书写注意点**：
   - 请使用**深色笔迹**（纯黑、深灰、深蓝）写在蓝色方框内，**不要涂抹、裁切或遮盖页面四角的 4 个青色实心定位方块**。
   - 遇到高格/扁格（如 `短分线`、`中分线`、`长分线`），横向拉写即可，引擎会自动识别 `width100` 比例并在排版时等比锁高保持 `0.74px` 清瘦笔重。
   - `第 3 页` 第一、二行的 **单笔画格（横H / 竖V / 撇P / 捺N / 点D）** 与 **24 个偏旁格** 会显著提升生字合成与个人风格拟合度，建议认真填写。
3. **跨字库增量追加（边写作业边补字）**：
   ```bash
   # 1) 扫描作业文档中还没收录的汉字，只生成一张含这几个字的小字帖
   inktwin template --tier targeted --from-md week11.md --pack my_hand -o patch.pdf
   # 2) 平板写完导出后，增量合并回原字库（自动去重并更新 9 维画像）
   inktwin harvest filled_patch.pdf -o packs/local/my_hand.hwpack --merge-into my_hand
   ```

---

## 2. 在 Word / WPS 中使用编译后的个人字体（启用 `calt` 多变体轮换）

通过 `inktwin fontbuild` 导出的 `.ttf` / `.otf` 字体包含 OpenType `GSUB calt` 上下文替代特性（同一个字在 1、2、3 处连续出现时会依次轮换 `BASE → .alt1 → .alt2` 变体）。

```bash
inktwin fontbuild --pack my_hand --format ttf -o InkTwin-MyHand.ttf
inktwin fontbuild --pack my_hand --format otf -o InkTwin-MyHand.otf
```

- **Windows 安装**：右键 `.ttf` 或 `.otf` →「为所有用户安装」。
- **Word 开启上下文替代 (`calt`)**：Word 默认可能关闭长文本的上下文替代。选中文字 → `Ctrl+D` 打开「字体」→ 切换到「高级」标签页 → 勾选 **「使用上下文替代 (Use Contextual Alternates)」**。
- **HTML / 网页 / CSS 中使用**：加入 `font-feature-settings: "calt" 1;` 即可生效。

---

## 3. 声明式手绘草图 `handplot` 完整语法与示例

在 Markdown 中放入 ```` ```handplot ```` 代码块，引擎会自动生成**无内置字体 `<text>`、无生硬直线**的压感手绘 SVG：

### 3.1 数轴与不等式区间 (`type: number_line`)

```yaml
type: number_line
width_mm: 92          # 图宽（mm），默认 96
height_mm: 22         # 图高（mm），默认 22
range: [-4, 5]        # 数轴范围
ticks: [-3, -2, 0, 2, 3, 4]  # 可选自定义刻度
label: "x"            # 右端轴标
intervals:
  - from: -2          # 支持 "-inf"
    to: 3             # 支持 "inf"
    open_left: false  # false=实心点 ●, true=白心空心圈 ○
    open_right: true
    shade: true       # 自动绘制手写斜线阴影
    color: "#19378a"  # 可选局部改色
```

### 3.2 直角坐标系、函数曲线与定积分阴影 (`type: func`)

支持安全数学表达式：`x`, `pi`, `e`, `+ - * / ** ^`, `sin, cos, tan, asin, acos, atan, exp, log, sqrt, abs`。

```yaml
type: func
width_mm: 68
height_mm: 46
x_range: [-0.5, 2.8]
y_range: [-0.5, 4.5]
shade:
  expr: "x**2"
  from: 0
  to: 2
curves:
  - expr: "x**2"
    domain: [-0.4, 2.2]
    label: "x^2"
  - expr: "2*x"
    domain: [0, 2.2]
    dashed: true
    color: "#c6332f"
points:
  - x: 2
    y: 4
    label: "P"
    dashed_proj: true
```

### 3.3 物理受力分析图 (`type: fbd`)

`angle_deg` 采用标准极坐标角度（`0`=向右，`-90`=竖直向下，`90`=竖直向上，`180`=向左）：

```yaml
type: fbd
width_mm: 56
height_mm: 44
body:
  shape: incline      # 可选 block | incline | point
  incline_deg: 30
  label: "m"
forces:
  - angle_deg: -90
    len: 1.0
    label: "mg"
  - angle_deg: 120
    len: 0.85
    label: "N"
    color: "#c6332f"
```

---

## 4. 空白 PDF 原卷叠写：`layout.json` (`boxes` + `marks`) 进阶控制

1. 生成初始布局文件：
   ```bash
   inktwin layout-detect blank_exam.pdf -o layout.json --min-slot-mm 15
   ```
2. 典型 `layout.json` 结构（单位均为物理毫米 `mm`）：
   ```json
   {
     "schema": "inktwin-layout-v1",
     "source_pdf": "blank_exam.pdf",
     "pages": [{"page": 0, "width_mm": 210.0, "height_mm": 297.0}],
     "boxes": [
       {"id": "p0_q1_0", "page": 0, "qnum": 1, "kind": "rect", "x_mm": 16.0, "y_mm": 40.0, "w_mm": 178.0, "h_mm": 78.0}
     ],
     "marks": [
       {"qnum": 1, "match": "答", "color": "red"},
       {"line": "p0l2", "scale": 1.06}
     ]
   }
   ```
3. 使用修改后的 `layout.json` 叠印：
   ```bash
   inktwin render solutions.md --overlay blank_exam.pdf --layout-json layout.json --pen-type fountain -o overlaid.pdf
   ```
   更直观的方法是在 `inktwin studio` 的「② 排版 Compose」页面上传原卷 PDF 后直接鼠标拖拽、缩放答题框与改题号。

---

## 5. `InkScene` 逐字编辑器快捷键与常用命令

启动逐字画布：
```bash
inktwin scene editor my_scene
```
常用交互操作：
- **点击选中 / 框选**：选择单个或一批笔画图元，拖动调整位置；
- **按 `R` 键**：对选中字符原地重摇一个不同的真实手写变体；
- **一键红笔订正 / 缩放到格 / 旋转微调**；
- **撤销（Batch Undo）**：所有调整均以增量形式追加在 `edits.json` 中，不破坏基准 `scene.json`，随时可按批次撤回或 `inktwin scene reset my_scene` 一键还原。

命令行批量编辑示例：
```bash
inktwin scene edit my_scene --op reroll --line p0l3
inktwin scene edit my_scene --op pen --line p0l5 --color red
inktwin scene undo my_scene
```

---

## 6. 常见问题排查 (FAQ)

- **`inktwin doctor --json` 提示找不到浏览器？**
  请确保本机已安装 Google Chrome 或 Microsoft Edge，或通过环境变量 `INKTWIN_BROWSER="C:\path\to\chrome.exe"` 手动指定。
- **字帖导回后部分格子显示未识别（`filled_cells` 偏少）？**
  1. 检查平板导出时是否勾选了“矢量 PDF”，纯截图转成的位图 PDF 不包含矢量路径；
  2. 检查是否遮挡或涂白了四角青色定位正方形；
  3. 确认书写颜色为深色墨水（浅灰或白色笔迹会被底纸过滤逻辑忽略）。
- **如何在自己的私人工程里保持个人字库与开源代码分离？**
  所有私有字库请务必放在 `packs/local/*.hwpack` 目录下（已默认写入 `.gitignore`），只把脱敏或公共测试渲染指向 `packs/demo_sample.hwpack`。

