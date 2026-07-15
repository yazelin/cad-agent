# cad-agent 產品化第一輪 — 設計

日期:2026-07-16
狀態:已核准(yazelin 拍板「OK 實作」)
定位:自己用+公開展示。UI 質感、體驗回饋、README 門面為主軸;不做多用戶/hosted。

## 決策紀錄

- 產品化對象:自己天天用 + public repo 當 portfolio 門面。
- 功能範圍(全選):建置進度回饋、參數面板、STL/STEP 下載、建置歷史。
- 實作路線:輕拆檔 vanilla(index.html + style.css + app.js),零 build step、零新依賴。
- 不做:多用戶/auth、資料庫持久化、i18n 框架、手機版面、PWA、Vite/React、hosted 版。歷史不跨重啟。

## 1. 視覺與版面

深色工程主控台風,深中性底 + 單一強調色。左 viewer、右側欄;右欄分區:

1. 品牌列 + 建置狀態
2. 對話紀錄(人話進度;AI 腳本收進摺疊區)
3. 參數面板(可編輯欄位)
4. 動作列(下載 STL/STEP、送 render-studio)
5. 輸入框 + 照片上傳

全介面正體中文。空狀態顯示 3-4 個範例晶片,點擊填入輸入框。無 webfont、無框架。

## 2. 前端結構

`cad_agent/web/` 拆成 `index.html`(markup + importmap)、`style.css`、`app.js`(ES module)。
three.js 維持 CDN importmap(brain 本來就需要網路)。

## 3. 後端變更與事件協定

- SSE 新增狀態事件:`{type:"status", stage:"thinking"|"building"|"retry", attempt}`。
  前端顯示「AI 寫腳本中→FreeCAD 建置中→第 N 次自我修復」,進行中鎖按鈕。
- 新 `params.py`:parse 腳本頂部 `UPPERCASE = 數字` 變數;substitute 代回新值。
  `model` 事件夾帶 `params`。單一事實來源在 server,附單元測試。
- `POST /rebuild {params}`:代入目前腳本直接重跑 FreeCAD,不經 claude。
- 歷史:in-memory list(id、script、workdir、label、ts)。
  `GET /history`、`GET /history/{id}/stl`、`POST /history/{id}/restore`。
  重啟即清空(個人工具的刻意 ceiling)。
- `GET /step` 下載端點(404 行為同 /stl)。
- 修 MVP 正確性瑕疵:SSE 單 queue 兩分頁互搶 → per-connection queue fan-out;
  併發 build 無鎖 → busy 鎖,進行中回 409。
- `/agentos/build` 不動。

## 4. README 門面

英文主體保留。頂部加:一行定位 → 真實錄的 demo GIF → CI/license 徽章。
photo-to-CAD 節配實照截圖。加正體中文導讀一段。
文末推廣 footer(GitHub/FB/BMC 三連結,公開專案固定規格)。

## 5. CI 與 release

GitHub Actions:push/PR 跑 pytest(acceptance 無 FreeCAD 自動 skip,CI 不裝 FreeCAD)。
完成並經 yazelin 驗收後 tag `v0.2.0`。

## 6. 驗證計畫

- pytest 全綠(本機含真 FreeCAD acceptance)。
- 手動端到端:真實建置 → 進度階段可見 → 參數秒級重建 → STL/STEP 下載可開 →
  歷史回上一版 → 兩分頁事件不互搶 → 建置中再按回 409。
- demo GIF 以真實 session 錄製,本身即端到端驗收。
- 不涉及任何常駐服務重啟。
