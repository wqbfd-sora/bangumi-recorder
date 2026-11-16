# Bangumi Recorder

**中文** | [English](README.en.md)

个人看番记录桌面程序（Windows）。布局沿用作者原来手动记录用的 Excel 表格：按季度记录、补番、总榜，评分只存一份。
可以接入 [Bangumi](https://bgm.tv) 搜索作品，自动补全封面、制作公司、监督、开播时间等信息；
每部作品还会记下日文原名和它的**罗马音**（译名有很多种，原名不会变）。

## 运行

需要 Python 3.10+（Anaconda 自带的就行）。第一次先装依赖：

```bat
cd /d "项目文件夹的路径"
pip install -r requirements.txt
```

之后双击 `run.bat`，或者运行 `python run.py`。

第一次打开时会弹出欢迎窗口：可以选择一个 Excel 记录表导入（文件名随意，选择窗口默认打开项目根目录），也可以「从空白开始」，以后随时点工具栏的「导入 Excel」。这个窗口只在第一次出现。**导入只读取 Excel，不会修改原文件。**
导入完成后，点工具栏的「批量关联 Bangumi」，就能自动补全封面和制作信息。
如果导入的是本程序导出的 Excel，会自动识别里面的 Bangumi 关联数据并一起恢复，不用重新关联。

## 功能

| 位置 | 作用 |
|---|---|
| 左侧「总榜」 | 所有已评分作品按分数排名，附「名作之壁」评级 |
| 左侧「全部作品」 | 所有记录，可按任意列排序 |
| 左侧「补番」 | 对应原来的「补番」表 |
| 左侧「季度」 | 按年份 → 季度分组，对应原来的 YYMM 表。按电脑的日期，**当季用红色 ● 标出**（还没有作品也会列出来），已经定档、还没开播的季度用蓝色 ○ 标出 |
| 右上「海报 / 列表」 | 切换封面海报墙和表格；海报上有评分角标、总榜名次、状态角标 |
| 列表里的「状态」「体裁」格 | 按住鼠标弹出选项，拖到想要的那一项松开就改好了；也可以点一下再点选 |
| 右侧面板 | 查看和编辑一部作品：Ctrl+S 保存；在输入框里按回车 = 保存并关闭侧边栏（个人评价里回车是换行，Ctrl+回车 保存并关闭）；右上角 ✕ 或 Esc 关闭，点任意一行重新打开。滚动面板时鼠标划过下拉框不会改动里面的值。终评旁边的「计算器」按 剧情 : 制作 : 节奏（默认 5 : 3 : 2，可改名称、比例、增减项目）算分，结果向上取整到一位小数，分项会跟着作品一起保存。分数可以不打小数点：输 86 就是 8.6（10 是满分），输完自动跳到下一项，回车填入终评 |
| ＋ 添加作品 | 从 Bangumi 搜索（可以按开播季度、体裁、题材筛选；选了季度不填名字，就是列出那一季的动画），自动填好季度、开播时间、星期、集数、体裁、制作公司、监督、系列构成、题材和罗马音；也可以手动添加 |
| 季度新番 | 从 Bangumi 一次拉下一整季开播的动画（默认当季，也可以选上一季或已经定档的未来季度），按平台（TV / WEB / 剧场版 / OVA）、地区、体裁、题材、名称筛选，勾选后一次加进季度表；已经在表里的会标出来、不会重复添加 |
| 批量关联 Bangumi | 自动搜索未关联的作品；名称和季度都对得上的自动选好，其余手动确认。搜不到时可以直接改左边的搜索词（如换成官方译名、日文原名）回车重搜，不会改动作品名 |
| 刷新 Bangumi 数据 | 一键重新获取已关联作品的信息（可选全部或当前页）：评分、排名、封面总是更新；播出季度和开播时间默认以 Bangumi 为准；其他字段可选覆盖或只补空白。结束后会列出季度有变化的作品，以及关联到同一条目、可能关联错的作品 |
| 补全罗马音 | 先在 Bangumi 条目的「别名」里找罗马音，没有再查 [AniList](https://anilist.co)；都没有的按读音自动转写，并在详情里标注。罗马音只显示在右侧详情里 |
| 导出 Excel | 导出成和原表相似的布局（每季一张表 + 补番 + 名作之壁 + 汇总）。每一行后面还带着 Bangumi 关联数据（粉色表头：Bangumi ID〔可点开〕、中文名、评分、排名、封面链接、简介、评分明细、附加信息），可以当完整备份 |
| 评分标准与选项 | 编辑「名作之壁」（分数区间、等级名、颜色、代表作、说明）、观看状态（名称、颜色、海报上要不要显示）、体裁。保存在 `data/config.json`，表格里的评级、分数颜色、下拉选项都跟着变；状态/体裁改名时可以把用到它的作品一起改名 |

字号和字体：在「设置」里调整（默认 11pt、系统字体）。程序自带思源黑体 SC / JP 和 Inter 三款开源免费可商用字体（SIL OFL 1.1，许可证在 `assets/fonts/`），也可以选电脑上装的其他字体。

快捷键：Ctrl+N 添加，Ctrl+F 搜索，Ctrl+S 保存，回车 保存并关闭侧边栏，Esc 关闭侧边栏，Delete 删除选中作品。

### 你的数据不会被改动

- 从 Bangumi 补全时**只填写空白字段**（重新关联、刷新时，播出季度和开播时间以 Bangumi 为准）。评分、状态、个人评价、作品名一律不会改。
- 罗马音同理：只填空白的。之前是「自动转写」的，查到 AniList 的写法后会替换；你手动改过的不会动。
- 唯一的例外是开播时间：Excel 里写成「1.2」这种「月.日」小数的，导入时是推断出来的（`1.1` 可能是 1 月 10 日），关联 Bangumi 后会用准确日期替换。
- 覆盖导入、批量关联之前，都会自动把数据库备份到 `data/backups/`。

## 数据

数据都放在 `data/` 文件夹，备份时复制这个文件夹就行：

- `data/anime.db`：SQLite 数据库，可以用 DB Browser for SQLite 直接打开
- `data/config.json`：评分标准、观看状态、体裁（配置文件，可以在程序里改，也可以用记事本改）
- `data/covers/`：封面缓存
- `data/backups/`：自动备份（保留最近 40 份）。「设置」→「从备份恢复…」可以把数据换回某份备份，换之前会先备份当前数据
- `data/error.log`：程序出错时的记录

## 出错了怎么办

- 操作出错时会弹出对话框：点「显示详情」能看到具体原因，点「复制错误信息」可以直接发给 Claude 排查。
- 程序闪退，或者界面卡住（未响应）超过 15 秒时，当时的情况会记在 `data\logs\crash.log`。下次打开程序会提示「上次程序意外退出了」，并附上记录。
- 平时的运行记录在 `data\logs\app.log`。「设置」里有「打开日志文件夹」按钮。

## 代码结构

```
run.py / run.bat              启动入口
bangumi_recorder/
  config.py                   路径、星期选项
  appconfig.py                配置文件 config.json：评分标准、观看状态、体裁
  db.py                       SQLite 数据层、季度换算
  scoring.py                  评分计算器的加权和取整
  bangumi.py                  Bangumi API v0 客户端与字段转换
  net.py                      网络连接池（多线程安全地复用连接）
  romaji.py                   罗马音：AniList 查询 + pykakasi 自动转写
  excel_io.py                 Excel 记录表导入/导出
  app.py                      QApplication 初始化
  errors.py                   日志、崩溃记录、错误对话框
  single_instance.py          只允许开一个程序窗口
  assets/                     程序图标、下拉箭头等小图；fonts/ 自带字体
  ui/
    main_window.py            主窗口（导航、表格、工具栏）
    table_model.py            表格模型、各视图的列、筛选
    table_view.py             列表的画法（标签、悬停、状态格按住拖选）
    detail_panel.py           右侧详情/编辑面板
    search_dialog.py          Bangumi 搜索（添加/关联，可按季度/体裁/题材筛选）
    season_dialog.py          季度新番（整季拉取、筛选、批量添加）
    season_util.py            当季 / 未开播的判断和显示
    match_dialog.py           批量关联
    dialogs.py                评分标准与选项、设置、导入报告
    fonts.py                  自带字体和字体选项
    poster_wall.py            封面海报墙（卡片的画法）
    covers.py                 封面缓存、没有封面时的占位海报
    style.py                  配色与样式表
    workers.py                后台线程（网络请求不卡界面）
tests/test_core.py            核心逻辑测试
```

想改界面：列的顺序在 `ui/table_model.py` 开头，配色在 `ui/style.py`，海报卡片大小在 `ui/poster_wall.py` 开头（`CARD_W`、`COVER_H`），评分标准/状态/体裁在程序里的「评分标准与选项」（或 `data/config.json`）。

## 测试

测试需要一份 Excel 记录表做测试数据（文件名随意）：复制一份放在项目根目录（和 `run.py` 同一层）就行，测试会用根目录下的第一个 `.xlsx`。`.xlsx` 已经被 `.gitignore` 排除，不会上传。
也可以在命令后面写别的路径，或者设置环境变量 `GUANFAN_XLSX`。

```bat
python tests\test_core.py
```

测试会检查「汇总」里的每个分数都原样导入，并确认原 Excel 文件没有被修改。

界面回归测试（离屏运行，用临时数据库，不碰真实数据）：

```bat
python tests\test_gui.py
```

## 打包成 exe

双击 `build.bat`。它会装好 PyInstaller、打包，并在打包好的版本文件夹里建一个快捷方式「Bangumi Recorder」（不用再手动输入 Y）。完成后程序在：

```
dist\Bangumi Recorder <版本号>\Bangumi Recorder.exe
```

文件夹名里带版本号（例如「Bangumi Recorder 1.0.2」），每个版本打包到各自的文件夹，旧版本的文件夹不用了可以直接删掉。

同时还会生成 `dist\Bangumi-Recorder-<版本号>-windows.zip`，用来上传到 GitHub Releases 给别人下载。这个 zip 是在写入 `data_path.txt` 和快捷方式**之前**打的包，里面不含你电脑上的路径；别人解压后运行，数据会放在 exe 旁边的 `data\` 文件夹。

- exe 和 `python run.py` **共用同一个数据库**：打包时会在 exe 旁边写一个 `data_path.txt`，指向项目里的 `data\` 文件夹。
  想让 exe 用别的数据文件夹，改这个文件里的路径就行（或者删掉它，exe 就用自己旁边的 `data\`）。
- 要把程序拷到别处用，复制整个 `dist\Bangumi Recorder <版本号>\` 文件夹，不能只拷 exe。
- 双击 build.bat 如果提示找不到 python，就在「Anaconda Prompt」里 `cd` 到项目文件夹，再运行 `build.bat`。
- 打包配置在 `Bangumi Recorder.spec`。改了代码之后重新运行 build.bat 就会更新 exe。

## 以后可以加的

- 同步到自己的 Bangumi 收藏（看过/在看 + 评分，需要 Access Token；Bangumi 只接受 1–10 的整数分）
- 统计页：每季平均分、制作公司/题材分布图
- 季度表里拖拽排序

## 许可证

Copyright (C) 2026 Wqbfd.Sora

本程序是自由软件：你可以按照自由软件基金会发布的 **GNU 通用公共许可证（GPL）第 3 版**，或（由你选择）任何更新的版本，重新发布和/或修改它（GPL-3.0-or-later）。
发布本程序是希望它有用，但**不提供任何担保**。完整条款见 [`LICENSE`](LICENSE)。

简单说：任何人都可以使用、修改、再发布本程序；但发布修改过的版本（包括打包好的 exe）时，必须同样以 GPL 开源并附上源代码，不能改成闭源。

### 用到的第三方组件

| 组件 | 用途 | 许可证 |
|---|---|---|
| [PySide6 / Qt](https://www.qt.io/qt-for-python) | 界面 | LGPL-3.0 |
| [pykakasi](https://codeberg.org/miurahr/pykakasi) | 日文转罗马音 | GPL-3.0-or-later |
| [openpyxl](https://openpyxl.readthedocs.io/) | 读写 Excel | MIT |
| [requests](https://requests.readthedocs.io/) | 网络请求 | Apache-2.0 |
| [OpenCC](https://github.com/BYVoid/OpenCC) 词典（`assets/t2s.json` 由它生成） | 繁/日 → 简体 | Apache-2.0 |
| 思源黑体 Noto Sans SC / JP、Inter（`assets/fonts/`） | 自带字体 | SIL OFL 1.1（许可证在同一文件夹） |
| [Bangumi API](https://bangumi.github.io/api/)、[AniList API](https://anilist.co) | 作品数据 | 按各自的使用条款 |
