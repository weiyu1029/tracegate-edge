# Claim–Evidence Matrix

作品集最容易失分的地方不是功能少，而是 claim 比證據大。此矩陣把公開敘述綁定到 repository 內可重現的證據。

## 使用規則

- 「實作了」需要自動化測試與可重現 demo。
- 「量測到」需要保存環境、資料量、日期與原始結果。
- 尚未完成的項目只能寫「designed for / planned」，放在 roadmap。
- 手動畫面成功一次只能算 smoke test，不是可靠性保證。

| 公開 claim | 最低證據 | 可以怎麼說 | 不應怎麼說 |
| --- | --- | --- | --- |
| Local-first station | 上游停止時，合法操作仍本地 commit；outbox 增加 | “Built a local-first synthetic traceability station.” | “Works during any network failure.” |
| Durable offline queue | SQLite outbox、outage/retry，以及用同一 DB 重新建立 `Database` / dispatcher 後補送的測試；完整 OS process restart 仍需自動化或保存手動證據 | “Events persist across component re-instantiation and are retried after upstream recovery.”；完成真正 restart evidence 後才可說 survive process restart | “No event can ever be lost.” |
| Idempotent API | 同 request/payload replay test；不同 payload conflict test | “State-changing commands use request-level idempotency.” | “Exactly-once distributed processing.” |
| Idempotent upstream | 重送相同 event，receipt count 不增加 | “The mock receiver deduplicates by event ID.” | “Compatible with every MES.” |
| Route enforcement | 正常站序與跳站測試 | “The domain service rejects out-of-route operations.” | “Production-certified process control.” |
| MAC association | 格式錯、未知、他機 MAC、錯 interface、正確 binding 測試 | “Separates format validation from product/interface association.” | “Secure MAC provisioning system.” |
| Key-part traceability | 未核准料、核准替代料、超量、重複 KPSN 測試 | “Models synthetic BOM groups and unique component bindings.” | “Supports all enterprise BOM rules.” |
| Repair/retest | FAIL → defect → repair → attempt+1 → retest test | “Preserves failure history across repair and retest attempts.” | “Full QMS/repair platform.” |
| Transactional consistency | SQLite trigger 強制 outbox insert 失敗；測試確認 event、WIP、outbox 與 idempotency 沒有半完成狀態 | “A SQLite fault-injection test verifies local station state and outbox writes roll back together.” | “ACID across edge and cloud.” |
| Causal trace ordering | 相同毫秒內建立 event → verification → event，驗證 timeline 與 outbox 依 transaction 內配發的 `audit_sequence` 排列 | “A database-local audit sequence preserves causal ordering across synthetic record types.” | “Globally ordered across devices” 或 “trusted timestamp.” |
| Dead-letter handling | 達最大嘗試、visible degraded health、manual replay test | “Failed delivery becomes visible and replayable.” | “Self-healing without operator action.” |
| Zero external Python dependencies | 乾淨 Python 執行、dependency file/stdlib inspection | “The runtime uses Python’s standard library and SQLite.” | “Zero software supply-chain risk.” |
| Edge-board deployment | 在目標板實際啟動、health、route demo 與重啟證據 | “Ran the same synthetic service on a small Linux edge device.” | “The board is required for the solution.” |
| AI safety boundary | 沒有寫入工具；policy test/architecture review | “AI is intentionally limited to explanation.” | “AI decides whether products pass.” |
| Performance | 可重現 benchmark、硬體/OS/Python/資料量與統計 | “Measured P95 X ms under Y synthetic requests on Z.” | “Real-time” 或 “high performance” without numbers |
| Scale | 定義併發、資料量、持續時間與 error rate | “Completed a Y-event synthetic soak test.” | “Factory scale” without factory-like workload |
| Security | threat model、auth/TLS/RBAC test（目前未完成） | “Runs only on a trusted demo LAN.” | “Secure/production-ready/zero-trust.” |

## Release evidence bundle

每次 tag 建議保留下列檔案（不得包含真實資料）：

```text
evidence/
  VERSION.txt
  TESTS.txt
  ENVIRONMENT.md
  DEMO_RESULTS.json
  BENCHMARK.json          # 只有真的執行才產生
  SCREENSHOTS.md          # 只連結已清理的合成畫面
```

最低內容：

- Commit SHA / release tag。
- Python 與 OS 版本。
- Edge / mock receiver 是否同機或分離。
- Test command、通過/失敗數與時間。
- Demo 的 unique events、receipts、duplicates、pending、dead-letter。
- 若有 benchmark：warm-up、sample count、percentile 算法與限制。

## LinkedIn 貼文範本

以下是安全、可驗證的語氣；方括號必須換成實際測量值，未量測就刪除該句。

> I built TraceGate Edge, an independent synthetic manufacturing traceability project. It models route enforcement, ACN/MAC/component identity, repair/retest attempts, and a SQLite transactional outbox. In a controlled demo, I stopped the mock upstream, committed [N] synthetic station events locally, and delivered [N] unique receipts after recovery with [0] duplicate business records. AI is limited to explaining deterministic reason codes; it never authorizes PASS. This is an educational reference implementation, not a production MES.

只有在你真的停止並重啟 Edge、保存前後相同資料庫與 pending/receipt 證據後，才能另外加入：`Pending events remained available across an Edge process restart.` 目前 repository 沒有自動化 process-restart test。

## Screenshot / 影片證據順序

1. `health` 顯示版本、schema 與初始 outbox。
2. 正確身份驗證與正常過站。
3. 格式有效、關聯錯誤的 MAC 被拒絕。
4. 跳站或未滿足 BOM 被拒絕。
5. Mock upstream 停止；pending 增加。
6. （選做、需另存手動證據）Edge restart；pending 仍存在。這不是目前自動化測試的一部分。
7. Mock upstream 恢復；delivered 增加、receipt 去重。
8. Trace timeline 顯示 FAIL、repair、attempt 2 retest 與最終結果。

影片旁白要區分「畫面看到的事實」與「架構希望達到的目標」。

## 當前狀態標籤

README 每個重要能力可使用以下標籤：

- `Implemented + tested`
- `Implemented; manual evidence only`
- `Designed; not yet implemented`
- `Research idea`

不要用模糊的 “supported” 同時描述四種成熟度。
