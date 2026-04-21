# ✦ 字幕小幫手

一個以 Python + Tkinter 打造的 YouTube 字幕與影片下載工具，介面採溫柔玫瑰粉配色。

---

## 功能

| 功能 | 說明 |
|------|------|
| 字幕預覽 | 即時抓取並顯示於預覽區，支援字元統計 |
| 字幕下載 | 儲存為 `.txt`，檔名自動帶日期與影片 ID |
| 時間軸模式 | 切換是否在每行前加上 `[MM:SS]` 時間戳 |
| 多語言支援 | 自動偵測 / 繁中 / 簡中 / 英 / 日 / 韓 |
| 影片下載 | 透過 `yt-dlp` 下載 MP4（最佳/720p/480p）或純音訊（MP3/M4A） |
| 複製全部 | 一鍵複製預覽區內容到剪貼簿 |
| Toast 通知 | 右下角淡入淡出提示，取代彈窗干擾 |

---

## 環境需求

- Python 3.10+
- macOS（字體使用 SF Pro / Menlo，Windows 需自行調整 `FONTS`）
- ffmpeg（影片合併必要，見下方安裝說明）

---

## 安裝

```bash
# 建立虛擬環境
python3 -m venv .venv
source .venv/bin/activate

# 安裝 Python 套件
pip install youtube-transcript-api yt-dlp

# 安裝 ffmpeg（用於合併影音串流）
brew install ffmpeg
```

---

## 啟動

```bash
.venv/bin/python "YouTube Transcript Downloader Pro.py"
```

---

## 支援的 YouTube 連結格式

```
https://www.youtube.com/watch?v=VIDEO_ID
https://youtu.be/VIDEO_ID
https://www.youtube.com/embed/VIDEO_ID
https://www.youtube.com/v/VIDEO_ID
```

---

## 影片下載格式說明

| 格式 | 說明 | 需要 ffmpeg |
|------|------|:-----------:|
| MP4 最佳畫質 | 最高解析度，自動合併影音 | ✓ |
| MP4 720p | 限制高度 ≤ 720px | ✓ |
| MP4 480p | 限制高度 ≤ 480px | ✓ |
| MP3 僅音訊 | 最高品質 MP3 | ✓ |
| M4A 僅音訊 | 最高品質 M4A | ✓ |

> 720p / 480p 若不安裝 ffmpeg，yt-dlp 會輸出兩個分離檔案（影像 + 音訊）。

---

## 專案結構

```
youtube-transcript-api/
├── YouTube Transcript Downloader Pro.py   # 主程式（單檔）
├── README.md
└── .venv/                                 # 虛擬環境（不納入版控）
```

---

## 主要常數位置

| 常數 | 說明 |
|------|------|
| `COLORS` | 所有 UI 色彩，修改此處即可換主題 |
| `FONTS` | 字體與大小設定 |
| `LANG_MAP` | 語言選單對應的 yt-transcript 語言代碼 |
| `VIDEO_FORMATS` | 影片下載格式與對應的 yt-dlp 參數 |
| `VIDEO_ID_PATTERNS` | YouTube URL 解析正規表達式 |

---

## 已知限制

- 部分影片的字幕由 YouTube 自動生成，品質因影片而異
- 受版權保護或私人影片無法下載
- 下載逾時上限為 10 分鐘（`subprocess.run timeout=600`）
