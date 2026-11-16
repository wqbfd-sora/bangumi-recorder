# Bangumi Recorder

[中文](README.md) | **English**

A personal desktop app (Windows) for logging the anime you've watched. The layout follows the
Excel sheet the author used to keep by hand: seasonal lists, a catch-up list (补番), and an overall ranking. Each score
is stored only once.
It connects to [Bangumi](https://bgm.tv) to search for titles and fill in cover art, studio,
director, air date and more. Each title also records its original Japanese name and its
**romaji** form, because translated titles vary but the original name doesn't.

## Running

Requires Python 3.10+ (the one bundled with Anaconda works). Install the dependencies once:

```bat
cd /d "path\to\the\project\folder"
pip install -r requirements.txt
```

After that, double-click `run.bat` or run `python run.py`.

On first launch a welcome dialog lets you pick an Excel record sheet to import (any file name; the picker
opens in the project root), or start from an empty list and import later with 导入 Excel (Import Excel).
The dialog only appears once.
**Importing only reads the Excel file. The original file is never modified.**
After importing, click "批量关联 Bangumi" (Batch link Bangumi) in the toolbar to fill in covers and
production info automatically. If you import a file this app exported, its Bangumi link data is recognized
and restored too, so nothing needs relinking.

## Features

| Where | What it does |
|---|---|
| Sidebar: 总榜 (Ranking) | All scored titles ranked by score, with tiers from the 名作之壁 rubric |
| Sidebar: 全部作品 (All titles) | Every record, sortable by any column |
| Sidebar: 补番 (Catch-up) | Matches the original 补番 sheet |
| Sidebar: 季度 (Seasons) | Grouped by year, then season; matches the original YYMM sheets. Based on the computer's date, **the current season is marked with a red ●** (listed even before it has any titles), and announced seasons that haven't started yet get a blue ○ |
| Top right: 海报 / 列表 (Posters / List) | Switch between the cover poster wall and the table. Posters show a score badge, ranking position and a status badge |
| 状态 (Status) and 体裁 (Format) cells in the list | Press and hold to pop up the options, drag to the one you want and release; a plain click also opens the menu for a second click |
| Right panel | View and edit a title. Ctrl+S saves; Enter in an input saves and closes the panel (in the review box Enter is a new line, Ctrl+Enter saves and closes); the ✕ button or Esc closes it, and clicking any row opens it again. Scrolling the panel no longer changes dropdown values the mouse passes over. The 计算器 (Calculator) button next to the score works out a score from story : production : pacing (5 : 3 : 2 by default; names, weights and the number of items can be changed), rounded up to one decimal place. The per-item scores are saved with the title. Scores can be typed without the decimal point: 86 means 8.6 (10 is full marks); focus moves to the next item automatically and Enter fills in the score |
| ＋ 添加作品 (Add title) | Search Bangumi, optionally filtered by airing season, format and genre tags (pick a season and leave the name empty to list that whole season); season, air date, weekday, episode count, format, studio, director, series composition, genre tags and romaji are filled in automatically. Manual entry also works |
| 季度新番 (Season lineup) | Pulls every anime airing in a season from Bangumi in one go (the current season by default; previous seasons and announced future ones work too). Filter by platform (TV / WEB / movie / OVA), region, format, genre and name, tick the ones you want and add them to the season sheet at once. Titles already in your list are marked and never added twice |
| 批量关联 Bangumi (Batch link) | Searches for every unlinked title. Matches where both name and season agree are pre-selected; you confirm the rest. If a title isn't found, edit the search text on the left (e.g. the official translation or the Japanese name) and press Enter to search that row again; your title itself isn't changed |
| 刷新 Bangumi 数据 (Refresh Bangumi data) | Re-fetches linked titles in one go (all, or just the current page). Score, rank and cover are always updated; season and air date follow Bangumi by default; other fields are either overwritten or only filled in when blank. Afterwards it lists titles whose season changed and titles linked to the same entry (likely mislinked) |
| 补全罗马音 (Fill in romaji) | Looks for the romaji title in the Bangumi entry's aliases first, then on [AniList](https://anilist.co). Titles found in neither are transliterated from their reading and flagged. Romaji is shown only in the right-hand detail panel |
| 导出 Excel (Export) | Exports in a layout similar to the original: one sheet per season, plus 补番, 名作之壁 and 汇总. Each row also carries its Bangumi link data (pink headers: Bangumi ID as a clickable link, Chinese title, score, rank, cover URL, summary, score breakdown, extra info), so the file works as a full backup |
| 评分标准与选项 (Rubric & options) | Edit the 名作之壁 rubric (score range, tier name, color, example, notes), the watching statuses (name, color, whether posters show it) and the formats. Stored in `data/config.json`; tier labels, score colors and dropdowns follow it. Renaming a status/format can rename it on every title that uses it |

Font size and typeface: adjust them in 设置 (Settings); the default is 11pt with the system font. The app ships three free, commercially usable open-source fonts (Noto Sans SC, Noto Sans JP, Inter; SIL OFL 1.1, licenses in `assets/fonts/`), and any installed font can be chosen too.

Shortcuts: Ctrl+N add, Ctrl+F search, Ctrl+S save, Enter save and close the panel, Esc close the panel, Delete removes the selected title.

### Your data stays yours

- Filling in from Bangumi **only writes to blank fields** (when relinking or refreshing, season and air date follow Bangumi). Your score, status, personal review and
  title are never changed.
- Romaji works the same way: only blank fields are filled. A romaji that was auto-transliterated is
  replaced once AniList has the official form; anything you typed yourself is left alone.
- One exception is the air date. Dates written in the spreadsheet as `month.day` decimals (e.g. `1.2`)
  are guesses made during import: `1.1` could mean January 10. Once the title is linked to Bangumi,
  the guess is replaced with the exact date.
- The database is backed up to `data/backups/` automatically before a replacing import or a batch link.

## Data

Everything lives in the `data/` folder. Copy that folder to back up:

- `data/anime.db`: SQLite database; can be opened directly with DB Browser for SQLite
- `data/config.json`: rubric, statuses and formats (a config file; edit it in the app or in a text editor)
- `data/covers/`: cover image cache
- `data/backups/`: automatic backups (the latest 40 are kept). 设置 (Settings) → 从备份恢复… (Restore from backup) switches the data back to any backup, after first backing up the current data
- `data/error.log`: error log

## When something goes wrong

- If an action fails, a dialog appears. Click 显示详情 (Show Details) to see the cause, or 复制错误信息
  (Copy error info) to send it to Claude.
- If the app crashes, or the window freezes ("Not Responding") for more than 15 seconds, what was happening is recorded in `data\logs\crash.log`, and the next launch tells you
  the app closed unexpectedly and shows the record.
- The everyday log is `data\logs\app.log`. 设置 (Settings) has a button that opens the log folder.

## Code layout

```
run.py / run.bat              Entry points
bangumi_recorder/
  config.py                   Paths, weekday list
  appconfig.py                config.json: rubric, statuses, formats
  db.py                       SQLite data layer, season conversion
  scoring.py                  Weighting and rounding for the score calculator
  bangumi.py                  Bangumi API v0 client and field mapping
  net.py                      Network session pool (safe connection reuse across threads)
  romaji.py                   Romaji: AniList lookup + pykakasi transliteration
  excel_io.py                 Excel record sheet import / export
  app.py                      QApplication setup
  errors.py                   Logging, crash records, error dialogs
  single_instance.py          Allows only one app window
  assets/                     App icon, dropdown arrows and other small images; fonts/ bundled fonts
  ui/
    main_window.py            Main window (sidebar, table, toolbar)
    table_model.py            Table model, columns per view, filtering
    table_view.py             How the list is drawn (tags, hover, press-and-drag status)
    detail_panel.py           Right-hand detail / edit panel
    search_dialog.py          Bangumi search (add / link, with season / format / genre filters)
    season_dialog.py          Season lineup (fetch a whole season, filter, add in bulk)
    season_util.py            Current / upcoming season detection and display
    match_dialog.py           Batch linking
    dialogs.py                Rubric & options, settings, import report
    fonts.py                  Bundled fonts and font choices
    poster_wall.py            Cover poster wall (how each card is drawn)
    covers.py                 Cover cache; generated placeholder posters for titles without a cover
    style.py                  Colors and stylesheet
    workers.py                Background threads (network calls don't freeze the UI)
tests/test_core.py            Core logic tests
```

To adjust the UI: column order is at the top of `ui/table_model.py`, colors are in `ui/style.py`,
poster card size is at the top of `ui/poster_wall.py` (`CARD_W`, `COVER_H`), and the rubric / statuses / formats
are edited in 评分标准与选项 (or `data/config.json`).

## Tests

The tests need an Excel record sheet as test data (any file name): put a copy in the project root (next to
`run.py`); the tests use the first `.xlsx` there. `.xlsx` files are excluded by `.gitignore`, so it is never uploaded. You can also pass another path after the command, or set the
`GUANFAN_XLSX` environment variable.

```bat
python tests\test_core.py
```

The tests check that every score in 汇总 is imported exactly as written, and that the original
Excel file is left unchanged.

UI regression tests (run off-screen against a temporary database, never your real data):

```bat
python tests\test_gui.py
```

## Building an .exe

Double-click `build.bat`. It installs PyInstaller, builds the app, and creates a shortcut
"Bangumi Recorder" inside the built version's folder (no Y/N prompt). The result is:

```
dist\Bangumi Recorder <version>\Bangumi Recorder.exe
```

The folder name includes the version number (e.g. "Bangumi Recorder 1.0.2"). Each version gets its own folder; delete old
version folders once you no longer need them.

- The .exe and `python run.py` **share the same database**: the build writes a `data_path.txt` next to
  the .exe pointing at the project's `data\` folder. Edit that file to use a different data folder, or
  delete it to make the .exe use a `data\` folder next to itself.
- To use the app elsewhere, copy the whole `dist\Bangumi Recorder <version>\` folder, not just the .exe.
- If double-clicking build.bat says python isn't found, open "Anaconda Prompt", `cd` to the project
  folder and run `build.bat` there.
- The build settings live in `Bangumi Recorder.spec`. After changing the code, run build.bat again to
  update the .exe.

## Possible additions

- Sync to your own Bangumi collection (watched / watching + score; needs an Access Token, and
  Bangumi only accepts whole-number scores from 1 to 10)
- A statistics page: average score per season, studio / genre breakdown charts
- Drag-and-drop reordering within a season

## License

Copyright (C) 2026 Wqbfd.Sora

This program is free software: you can redistribute it and/or modify it under the terms of the
**GNU General Public License** as published by the Free Software Foundation, either **version 3** of the
License, or (at your option) any later version (GPL-3.0-or-later).
It is distributed in the hope that it will be useful, but **WITHOUT ANY WARRANTY**. See [`LICENSE`](LICENSE)
for the full terms.

In short: anyone may use, modify and redistribute it, but any distributed modified version (including a
built .exe) must also be released under the GPL with its source code; it can't be made closed-source.

### Third-party components

| Component | Used for | License |
|---|---|---|
| [PySide6 / Qt](https://www.qt.io/qt-for-python) | UI | LGPL-3.0 |
| [pykakasi](https://codeberg.org/miurahr/pykakasi) | Japanese → romaji | GPL-3.0-or-later |
| [openpyxl](https://openpyxl.readthedocs.io/) | Excel import / export | MIT |
| [requests](https://requests.readthedocs.io/) | HTTP | Apache-2.0 |
| [OpenCC](https://github.com/BYVoid/OpenCC) dictionaries (`assets/t2s.json` is generated from them) | Traditional / Japanese → Simplified | Apache-2.0 |
| Noto Sans SC / JP, Inter (`assets/fonts/`) | Bundled fonts | SIL OFL 1.1 (licenses in the same folder) |
| [Bangumi API](https://bangumi.github.io/api/), [AniList API](https://anilist.co) | Anime data | Their respective terms of use |
