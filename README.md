# LLM Gateway 用量記錄分析工具

讀取 LLM Gateway 的 JSON Lines 呼叫記錄，輸出一份中文文字報表，回答「錢花在哪個部門」、「多少錢是白花的」、「哪裡不對勁」三個問題。零第三方相依，純標準庫，單次串流掃過輸入。

---

## 執行方式

**uv**（推薦；需要 Python 3.11+，uv 會在第一次執行時自動取得）

```bash
uv run analyzer.py sample_data.jsonl
```

**直接 python3**（需要系統已有 Python 3.11+）

```bash
python3 analyzer.py sample_data.jsonl
```

**測試**（第一次需要網路下載 pytest；後續離線可跑）

```bash
uv run --with "pytest>=8,<9" pytest
```

---

## Demo

```bash
# 1. 用範例資料看報表（固定種子 42，2,000 筆）
uv run analyzer.py sample_data.jsonl

# 2. 機器可讀輸出（--json 序列化同一份報表結構）
uv run analyzer.py sample_data.jsonl --json

# 3. 更換種子，確認異常偵測結果不依賴種子
uv run generate.py --seed 99 | uv run analyzer.py /dev/stdin

# 4. 把最小樣本門檻設為 0，看到門檻擋掉了什麼
uv run analyzer.py sample_data.jsonl --min-samples 0
```

第 4 行是整個工具最有說明力的 30 秒：錯誤率異常的鑰匙數量從 1 把暴增到 7 把，把一個原本看不見的設計決定變成眼睛看得到的東西。

---

## 範例輸出

執行 `uv run analyzer.py sample_data.jsonl`（seed=42，2,000 筆）：

```
============================================================
LLM Gateway 用量分析報表
============================================================

── 部門費用 ──

  Engineering                      $3.31
  Data                             $2.32
  Product                          $2.27
  HR                               $1.93
  Finance                          $1.87

  總費用                             $11.72

  ⚠  無法歸屬費用：$7.18（佔總費用 61.2%）
     歸屬缺陷：20 筆（有使用者識別碼但查無部門資訊）

── 浪費 ──

  重試浪費         $0.0126  （重試但最終成功的請求裡，失敗嘗試的費用）
  純失敗浪費       $0.2130  （從未成功的請求的全部費用）

  ⚠  1 個請求出現多次成功 - Gateway 可能重複送出，浪費數字可能偏低

── 異常偵測 ──

  重試風暴（60 秒內 ≥ 3 次）：6 個請求
    req-storm-000                         4 次
    req-never-0                           3 次
    …

  錯誤率異常（5 分鐘窗 > 20%，樣本 ≥ 20）：1 把鑰匙
    key-incident                      20.8%  樣本 101

── 各模型延遲（成功請求） ──

  模型                                   P50       P95      筆數
  ------------------------------  --------  --------  ------
  claude-sonnet-5-5                  1216ms     5292ms     721
  claude-haiku-5-5                    793ms     4284ms     597
  gpt-5-4-mini                       1166ms     4060ms     285
  selfhost-qwen-32b                  1097ms     5781ms     167
  gemini-3-5-flash-lite              1038ms     4510ms     155

── 資料品質 ──

  ⚠  未定價模型（費用未計入總額）：unpriced-model-zzz
  ⚠  2 行無法解析，已略過
```

---

## 假設與數字來源

| 參數 | 值 | 來源 |
|---|---|---|
| 一天總筆數 | 15 萬 | 從題一推算：450 萬/月 ÷ 30 |
| 基準錯誤率 | 0.5% | 從題一推算：成功率 SLO ≥ 99.5% |
| prompt token | 平均 3,000、P95 12,000、上限 32k | 題二直接給定 |
| completion token | 平均 500 | 題二直接給定 |
| error / timeout 拆分 | 0.35% / 0.15% | 純假設，僅讓兩種狀態都出現 |
| 直接呼叫佔比 | 60% | 純假設，情境只說「員工也直接呼叫」 |
| 地端模型流量佔比 | 少數（10%） | 從題二推算：4 張 GPU 只服務一個模型 |
| 雲端模型單價 | 見 config.toml | 廠商官方頁，2026-11-07 快照，見 `docs/research/llm-token-pricing.md` |
| 地端模型單價 | 0.25 / 2.00 USD/1M tok | 推導：Lambda H100 $4.59/hr，1,000 tok/s，50% 使用率，prefill 10x faster |

**最小樣本門檻（min_samples=20）** 是本工具唯一刻意偏離題目字面之處。量化理由：在 0.5% 的基準錯誤率下，一把五分鐘內只有 3 次呼叫的健康鑰匙，至少錯 1 次的機率約 1.49%，一天 288 個窗口等於每天約 4.3 次誤報；同樣健康但有 20 次呼叫的鑰匙，機率約 4.5×10⁻⁸，約 200 年一次。差距三十萬倍，完全來自分母大小。

**單價分級簡化**：真實定價依 prompt 長度分級（如 Haiku 5.5 在超過 100k token 後單價跳升），本工具使用單一費率。在目前 P95 為 12,000 token 的量體下，分級影響很小。

**latency_ms 設計落差**：本工具報告的延遲百分位基於 `latency_ms` 單一欄位，無法區分「首 token 回應時間」與「總耗時」。題一訂立的使用者側 SLO（首 token P95 ≤ 1.5 s）需要 Gateway 額外記錄 `ttft_ms`，本報表無法驗證該 SLO。

---

## 每天 1,000 萬筆時

**先死在哪**：浪費計算的 `req_buf`（請求識別碼分組）是記憶體牆。15 萬筆實測約 1.2 秒、記憶體 ≈ 30 MB；按比例外推到 1,000 萬筆，時間約 80 秒仍可接受，但 `req_buf` 會長到 ≈ 2 GB，超過 Python 行程的合理上限。錯誤率的滑動窗 deque 若有大量唯一鑰匙名稱也會同步增長。

**怎麼改**：先把輸入按 `request_id + timestamp` 排序，讓同一請求的嘗試連續出現，`req_buf` 退化為 O(單一請求的最大嘗試數) = O(3)，記憶體問題消失。部門費用、異常偵測、延遲百分位本身已是串流可加，不需要改動。真正到 10 億筆：換 Parquet 儲存，以 Spark 或 Flink 在 `request_id` 分區上做 session aggregation。

**改了之後什麼變難**：(1) 重試分組在 Kafka 分區邊界斷開 - 同一 `request_id` 的 attempt 1 和 attempt 2 可能落在不同分區，需要全域排序或跨分區 join 步驟，otherwise-simple group-by 變成 distributed join。(2) 滑動窗在時間批次邊界也斷開 - 批次 T 的最後一筆記錄和批次 T+1 的第一筆記錄之間的窗口狀態不延續，需要 watermark 機制與 out-of-order buffer。

---

## 未來改善方向

- **單價分級**：依 prompt 長度套用不同費率（如 Haiku 5.5 的 100k 分級），需要在費用計算時引用 `prompt_tokens`。
- **亂序記錄**：目前假設輸入已依時間排序，遇到倒置只警告。若上游 Gateway 合併多個節點的記錄，倒置會是常態；正確處理需要排序步驟或有界亂序緩衝（bounded out-of-order buffer）。
