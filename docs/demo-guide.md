# 3–5 分鐘作品展示指南

目標不是快速點完所有功能，而是用一條清楚故事證明三件事：**規則會擋錯、斷線不丟資料、恢復不重複。** 所有輸入皆為合成資料。

## 事前準備

從 repository 根目錄啟動 combined demo：

```sh
TRACEGATE_DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tracegate-demo.XXXXXX")"
TRACEGATE_DATA_DIR="$TRACEGATE_DEMO_DIR" python3 -m tracegate demo
```

另開一個 terminal：

```sh
API=http://127.0.0.1:8080
curl -s "$API/api/v1/health" | python3 -m json.tool
```

瀏覽器可開啟 `http://127.0.0.1:8080/`；目前重點是 JSON API 與可稽核資料，不是假裝完整商用 UI。

> `mktemp` 會建立本次專用的暫存資料夾，降低覆寫既有資料的風險。重新錄影前可呼叫 `POST /api/v1/demo/reset`。結束後先停止程序；確認 `$TRACEGATE_DEMO_DIR` 的實際路徑與內容無誤，再自行移除該暫存資料夾。

如果要先驗證完整流程而不逐條貼 curl，保持服務運行並在另一個 terminal 執行：

```sh
python3 scripts/demo_smoke.py --wait 30 --journey
```

若要加入 upstream outage/recovery 與 50 次本機讀取延遲實測：

```sh
python3 scripts/demo_smoke.py --wait 30 --journey --outage-demo --benchmark 50
```

等價 Make targets 是 `make journey` 與 `make resilience`。腳本最後輸出 `SUMMARY {...}`；保留這份輸出，公開時只引用實際標為 `MEASURED` 的數字。

## 影片腳本

### 0:00–0:30：定位與架構

旁白：

> TraceGate Edge is an independent synthetic manufacturing traceability project. The edge node enforces deterministic route, identity, material, and repair rules locally. SQLite stores current WIP, event history, and a transactional outbox. A separate mock receiver represents the upstream system. AI may explain reason codes, but it never authorizes PASS.

畫面顯示 README 架構圖與 `/api/v1/health`。強調這不是正式 MES，也沒有使用任何真實工廠資料或內部 source。

### 0:30–1:15：先證明 fail closed

先故意要求新產品跳到 MAC 站：

```sh
curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-wrong-station-0001","acn":"ACN-DEMO-0001","station_code":"ST-ID","result":"PASS"}' \
  | python3 -m json.tool
```

應看到 `ROUTE_VIOLATION`，再查 WIP 仍在 `ST-IN`：

```sh
curl -s "$API/api/v1/units/ACN-DEMO-0001" | python3 -m json.tool
```

這一幕的重點：不是 UI 顯示紅字而已，而是 WIP 沒有被錯誤前進。

### 1:15–2:00：身份不是只有格式

正常完成 intake：

```sh
curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-register-0001","acn":"ACN-DEMO-0001","station_code":"ST-IN","result":"PASS"}' \
  | python3 -m json.tool
```

使用格式正確、但屬於 `ACN-DEMO-0002` 的合成 MAC：

```sh
curl -s -X POST "$API/api/v1/mac/check" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-wrong-mac-0001","acn":"ACN-DEMO-0001","interface_name":"wlan0","mac_address":"02:54:47:00:02:01"}' \
  | python3 -m json.tool
```

應看到 `ASSOCIATION_MISMATCH`。接著用正確綁定：

```sh
curl -s -X POST "$API/api/v1/mac/check" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-correct-mac-0001","acn":"ACN-DEMO-0001","interface_name":"wlan0","mac_address":"02:54:47:00:01:01"}' \
  | python3 -m json.tool

curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-identity-pass-0001","acn":"ACN-DEMO-0001","station_code":"ST-ID","result":"PASS"}' \
  | python3 -m json.tool
```

重點：格式有效不等於關聯有效；ACN、interface 與 MAC 必須同時匹配。

### 2:00–3:00：關鍵料與離線 commit

完成一顆 controller 與兩顆 sensor；第二顆 sensor 刻意使用核准替代料：

```sh
curl -s -X POST "$API/api/v1/key-parts/collect" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-kp-controller-0001","acn":"ACN-DEMO-0001","part_no":"CTRL-A100","kpsn":"KPSN-DEMO-CTRL-001"}' \
  | python3 -m json.tool

curl -s -X POST "$API/api/v1/key-parts/collect" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-kp-sensor-0001","acn":"ACN-DEMO-0001","part_no":"SENSOR-T200","kpsn":"KPSN-DEMO-SENS-001"}' \
  | python3 -m json.tool

curl -s -X POST "$API/api/v1/key-parts/collect" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-kp-sensor-0002","acn":"ACN-DEMO-0001","part_no":"SENSOR-T201","kpsn":"KPSN-DEMO-SENS-002"}' \
  | python3 -m json.tool
```

現在停止 synthetic upstream（只有 combined demo 提供此控制）：

```sh
curl -s -X POST "$API/api/v1/demo/mes" \
  -H 'Content-Type: application/json' -d '{"online":false}' \
  | python3 -m json.tool
```

完成 material station：

```sh
curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-material-pass-0001","acn":"ACN-DEMO-0001","station_code":"ST-KP","result":"PASS"}' \
  | python3 -m json.tool

curl -s "$API/api/v1/outbox" | python3 -m json.tool
```

指出：本地 station event 與 WIP 已 commit，但 delivery 是 `RETRY`/`PENDING`，沒有謊稱上游已收到。

### 3:00–4:00：恢復、去重與 trace

把上游恢復：

```sh
curl -s -X POST "$API/api/v1/demo/mes" \
  -H 'Content-Type: application/json' -d '{"online":true}' \
  | python3 -m json.tool

sleep 3
curl -s "$API/api/v1/outbox" | python3 -m json.tool
curl -s "$API/api/v1/units/ACN-DEMO-0001/trace" | python3 -m json.tool
```

再次送出完全相同的 material station request：

```sh
curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-material-pass-0001","acn":"ACN-DEMO-0001","station_code":"ST-KP","result":"PASS"}' \
  | python3 -m json.tool
```

應回既有結果並標示 idempotent replay，不新增 event、不讓 WIP 再前進。

### 4:00–5:00：結論

畫面切到 metrics 與 claim–evidence matrix：

```sh
curl -s "$API/api/v1/metrics" | python3 -m json.tool
```

旁白：

> The important part is not the number of screens. It is the evidence chain: deterministic validation, atomic local state, durable retry, and idempotent receipt. The next steps are authentication, signed configuration, operational tests, and real I/O adapters—not giving an AI model permission to pass products.

## 延伸場景：FAIL → repair → attempt 2 retest

這段適合完整 GitHub demo，不必全部塞進 3–5 分鐘影片。

在 `ST-FT` 產生合成失敗，並把 defect ID 存成 shell 變數：

```sh
DEFECT_ID="$(curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-ft-fail-0001","acn":"ACN-DEMO-0001","station_code":"ST-FT","result":"FAIL","reason_code":"SYNTHETIC_TEST_NG","evidence":{"description":"Injected demo failure"}}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["defect"]["defect_id"])')"
printf '%s\n' "$DEFECT_ID"
```

記錄 repair action，再完成 repair station：

```sh
curl -s -X POST "$API/api/v1/repairs/actions" \
  -H 'Content-Type: application/json' \
  -d "{\"request_id\":\"demo-repair-action-0001\",\"acn\":\"ACN-DEMO-0001\",\"defect_id\":\"$DEFECT_ID\",\"action_code\":\"RESEAT_SYNTHETIC_MODULE\",\"notes\":\"Synthetic repair evidence only\",\"technician\":\"demo-operator\"}" \
  | python3 -m json.tool

curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-repair-pass-0001","acn":"ACN-DEMO-0001","station_code":"ST-RP","result":"PASS"}' \
  | python3 -m json.tool

curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-retest-pass-0001","acn":"ACN-DEMO-0001","station_code":"ST-FT","result":"PASS","evidence":{"test_suite":"synthetic-v1"}}' \
  | python3 -m json.tool
```

Trace 應同時保留 attempt 1 FAIL、repair 與 attempt 2 PASS，不能把先前失敗覆蓋掉。

## 分離故障域（作品影片推薦）

更可信的展示是在兩個程序，甚至兩台機器上執行：

```sh
# Upstream host
TRACEGATE_MOCK_MES_HOST=0.0.0.0 python3 -m tracegate mock-mes

# Edge host；192.0.2.10 僅為文件示例，請換成你的受信任 LAN 位址
TRACEGATE_MES_URL=http://192.0.2.10:8081/api/v1/trace-events \
  python3 -m tracegate edge
```

停止 upstream 程序來製造真實連線失敗；不要只按前端切換鈕。公開畫面前請遮蔽真實 LAN IP 與使用者名稱。

## Demo 驗收

- [ ] 錯站被拒絕，WIP 不變。
- [ ] 格式有效但關聯錯誤的 MAC 被拒絕。
- [ ] BOM 數量滿足前不能完成 material station。
- [ ] Upstream offline 時本地 event/outbox 存在。
- [ ] Online 後變成 delivered。
- [ ] 同 request replay 沒有第二筆 event。
- [ ] Trace 可解釋每一步與 reason code。
- [ ] 影片中沒有真實資料、路徑、IP、帳號或秘密。
