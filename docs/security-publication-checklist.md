# 安全與公開發布檢查表

這是把 TraceGate Edge 放上公開 GitHub、LinkedIn 或作品集前的 release gate。每次發布都重新執行，不要因為前一版安全就直接假設下一版也安全。

## 威脅模型與限制

目前版本是單人、受信任 LAN 上的教育 demo：

- 沒有登入、角色權限或多因素驗證。
- 沒有 TLS 終止與正式憑證。
- 本地 SQLite 沒有欄位級加密。
- Demo reset、outbox replay 等管理操作不適合公開網路。
- 標準函式庫 HTTP server 不是 Internet-facing production server。
- Mock upstream 是測試接收端，不是正式 MES。

因此：**只綁定到個人測試網路，絕不做 router port forwarding，也不要部署到公開 IP。**

## 發布前資料檢查

- [ ] 所有工單、ACN、序號、KPSN、MAC、料號與 route 都是虛構值。
- [ ] MAC 使用 locally administered 位元，例如 `02:...`。
- [ ] 沒有真實公司、客戶、供應商、產品代號或內部組織名稱。
- [ ] 沒有真實 IP、hostname、VPN、UNC/共享路徑或內部 URL。
- [ ] 沒有真實資料庫名、schema、table、stored procedure、API method 名。
- [ ] 沒有內部 source、反編譯內容、DLL/PDB、設定檔或專有畫面。
- [ ] 沒有生產缺陷描述、維修紀錄、操作員資訊或可識別個人的 log。
- [ ] 截圖的瀏覽器列、終端 prompt、Wi-Fi、IP、使用者名稱與檔案路徑已裁切或遮蔽。
- [ ] 不提交 `data/`、SQLite、WAL/SHM、coverage、cache 或真實 log。

## Secret 檢查

- [ ] 執行 repository 的 publication check（若提供）：`python3 scripts/public_release_check.py`。
- [ ] 理解 scanner 邊界：它會遍歷 `data/` 並以檔名拒絕 DB/key/非範例 `.env`，但不讀取超過 2 MB、非 UTF-8、含 NUL 的 binary，或 image/PDF/archive 本文。
- [ ] 對 scanner 不讀取的 binary、圖片與 PDF 另做 metadata/OCR/人工檢查；對 Git history 另用專用 secret/history scanner。Publication check 通過不等於沒有資料外洩。
- [ ] 另外執行 `git grep -nEi '(password|secret|token|api[_-]?key|private[_-]?key)'`，逐筆人工判斷。
- [ ] 搜尋常見憑證格式與私鑰開頭：`BEGIN .* PRIVATE KEY`、雲端 access key、JWT。
- [ ] 搜尋 email、電話、住址、真實使用者名稱與絕對 home path。
- [ ] `.env`、credential store、shell history 與 IDE workspace 沒有被追蹤。
- [ ] 如果秘密曾經 commit，光刪除目前檔案不夠：先輪替秘密，再清理 Git history。

## Git 內容檢查

```sh
git status --short
git ls-files
git diff --check
git log --stat --oneline
```

- [ ] `.gitignore` 涵蓋 `data/`, `*.db`, `*.db-*`, `*.sqlite-*`, `*.sqlite3-*`, `.env`, cache 與測試產物。
- [ ] `git ls-files` 沒有二進位 dump、照片、郵件、聊天匯出或臨時附件。
- [ ] README 所有命令能從乾淨 checkout 執行。
- [ ] 連結是 repository 內的相對路徑或公開文件，沒有分享聊天連結。
- [ ] Commit message 不含客戶、事件單、內部站點或敏感資訊。
- [ ] 已決定授權條款；在未選擇 license 前，不暗示他人可任意再利用。

## 功能與可靠性檢查

- [ ] 全部自動化測試通過。
- [ ] 新資料目錄可完成 migration 與 seed。
- [ ] 相同 `request_id + payload` 重送不產生第二筆業務事件。
- [ ] 相同 `request_id` 搭配不同 payload 回 conflict。
- [ ] 錯站、錯 ACN、錯 MAC 關聯、未核准料件與重複 KPSN 都 fail closed。
- [ ] FAIL → repair → new attempt → retest 有測試。
- [ ] 中斷 mock upstream 時 outbox 保留；恢復後只產生 unique receipts。
- [ ] Component re-instantiation test 證明相同 SQLite 檔仍可補送；另以真正 kill/restart 驗證 pending event 仍存在（後者目前需保存手動證據，automated OS process-restart test 尚未加入）。
- [ ] Dead letter 可見，不會被健康頁標成完全 healthy。
- [ ] `health` 與版本資訊和實際執行 artifact 一致。

## API 與網路安全檢查

- [ ] Demo 管理端點只在開發模式啟用，或清楚標示風險。
- [ ] Request body 有大小上限、JSON 型別驗證與安全錯誤回覆。
- [ ] SQL 全部使用參數化 query；不得把掃描值拼接進 SQL。
- [ ] UI 輸出做 HTML escaping，避免把 barcode 當 HTML。
- [ ] Error response 不洩漏 stack trace、檔案路徑或 DB 內容。
- [ ] Upstream URL 來自環境設定；不在 source 寫 token。
- [ ] 只有在受信任 LAN 綁 `0.0.0.0`；否則使用 `127.0.0.1`。
- [ ] 不對 Internet 暴露 8080/8081，也不做公網 port forwarding。

## AI Agent 安全檢查

- [ ] Agent 只能呼叫 read-only health、unit、trace、metrics API。
- [ ] Agent 沒有 station complete、repair、reset、replay 或設定寫入工具。
- [ ] Agent 的回答清楚標為解釋/建議，不是製程授權。
- [ ] PASS/FAIL 只由 deterministic service 根據資料與規則決定。
- [ ] 送給外部模型前移除可識別、機密及未公開資料。
- [ ] Prompt injection 內容不能改變工具權限或 domain rule。

## LinkedIn 發布檢查

- [ ] 使用「synthetic demo / independent educational project」字樣。
- [ ] 不寫「production-grade」、「industrial certified」或「deployed in factory」，除非有公開且可驗證證據。
- [ ] 量測數字包含硬體/OS、資料量、測試方法與日期。
- [ ] 只說實際跑過的 offline event 數、latency 與 test count。
- [ ] 影片清楚拍到上游停止、pending 增加、重啟/恢復與 receipt 去重。
- [ ] 不把邊緣板說成必需品；說明筆電可跑、板子用於獨立故障域與部署實驗。
- [ ] 不把 AI 畫成放行者；畫面顯示 `explain only`。

## 推薦公開聲明

可放在 README 或貼文：

> TraceGate Edge is an independent, synthetic educational project. It does not contain or reproduce any employer, customer, or factory source code, data, schemas, credentials, screenshots, or proprietary workflows. It is not a production MES.

## 正式化之前仍需補足

若要從作品變成真實產品，至少還需要：

- 身份驗證、RBAC、設備憑證與密鑰輪替。
- TLS/mTLS、網路分區、防火牆與安全更新策略。
- Signed artifacts、SBOM、dependency/vulnerability management。
- 備份/還原、磁碟耗損與斷電一致性測試。
- 時鐘同步、審計 retention、隱私與資料治理。
- HA、容量/壓力/長時間 soak test 與故障演練。
- 變更控制、核准流程、正式 runbook 與 on-call ownership。
- 依實際產業要求做安全、品質與法規評估。
