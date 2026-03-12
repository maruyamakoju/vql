# VQL — Video Query Language
### 映像のSQL: compile once, query deterministically

**未踏IT 2026 応募プロトタイプ**  ·  MIT License

---

## 現在の実装状況

| 状態 | 内容 |
|------|------|
| ✅ 実装済み | VIR データモデル、VQL パーサ（再帰下降）、VQL 実行器、証拠フレーム返却、HTML インタラクティブデモ、**YOLO ベース VIR コンパイラ**（実映像対応） |
| 🔧 デモ版 | 合成監視データ（固定 seed）で動く主デモ。実映像 VIR は YOLOv8n で確認済み（下記参照） |
| 🚧 今後実装 | ByteTrack Re-ID 統合、YOLO-World テキストプロンプト検出、クラウド VIR ストレージ |

---

## コンセプト

```
                 ┌──────────────────────────────────────────────┐
  video.mp4  ──► │  Perception Pipeline  (YOLO / VLM / Re-ID)  │  ← 1回だけ実行
                 └─────────────────────┬────────────────────────┘
                                       │  compile
                                       ▼
                             VIR (Video Intermediate Representation)
                                       │
                  ┌────────────────────┴───────────────────────┐
                  │   VQL Query Engine  (モデル不使用, <1ms)   │
  VQLクエリ  ────►│   deterministic · reproducible             │
                  └────────────────────┬───────────────────────┘
                                       ▼
                         Results: tracks + evidence_frames
```

**核心的な性質**: 同じ VQL クエリ × 同じ VIR = 常に同一結果（決定論的）

---

## クイックスタート

```bash
pip install pillow
python demo_vql.py --no-server      # 合成 VIR + 3クエリ実行 + 決定論証明
python -m pytest tests/ -v          # ユニットテスト 32件

# 実映像 VIR コンパイル (要 ultralytics + opencv-python)
pip install ultralytics opencv-python
python compile_real_vir.py <video.mp4> --out vql_real_vir.json
```

ブラウザデモ: `vql_mitou2026.html` を直接開く（外部依存なし）

---

## 実行ログ（実際の出力）

```
Step 1 ▶ Compile surveillance video → VIR
  Compiling VIR from perception pipeline ... done  (203ms)
  VIR summary:
    source      : entrance_cam_2h.mp4
    duration    : 2h 00m 00s  (7200s)
    zones       : 4  ->  ['A区域', 'B区域', '受付', 'エレベーター前']
    tracks      : 347
    zone_events : 700
    stay_facts  : 350

Step 2 ▶ Execute VQL queries  [NO models -- deterministic]

  Query 1: 14時以降にA区域へ入り5分未満で退出した人物
  ┌─────────────────────────────────────────────────────┐
  │ SELECT   person                                     │
  │ FROM     VIR("entrance_cam_2h.mp4")                 │
  │ WHERE    ENTERS(person, zone("A区域"),               │
  │                 time_range(from="14:00:00",          │
  │                            to  ="15:00:00"))         │
  │   AND    DURATION(person, zone("A区域")) < 5min      │
  │ RETURN   track_id, enter_t, exit_t, duration,       │
  │          evidence_frames(n=2)                       │
  └─────────────────────────────────────────────────────┘
  -> 4 match(es)   exec: 0.41ms [deterministic OK]

  Execution plan:
    VIR_SCAN   347 tracks loaded from 'entrance_cam_2h.mp4'
    ENTERS     zone='A区域' from=14:00:00 to=15:00:00  → 4 candidates
    DURATION   zone='A区域' < 5min                     → 4 candidates
    RETURN     4 result(s)  total_time=0.41ms

  [1] track_047  14:02:11 -> 14:04:33  dur=2m 22s  conf=0.98
  [2] track_083  14:08:45 -> 14:11:20  dur=2m 35s  conf=0.98
  [3] track_126  14:23:07 -> 14:26:52  dur=3m 45s  conf=0.98
  [4] track_194  14:51:33 -> 14:54:18  dur=2m 45s  conf=0.98

  Query 2: 受付を経由してA区域に入った人物のシーケンス
  -> 3 match(es)   exec: 0.66ms [deterministic OK]

  Query 3: 13〜18時にB区域で30分以上滞留した人物
  -> 2 match(es)   exec: 0.31ms [deterministic OK]

Step 3 ▶ Determinism verification
  Running Query 1 a third time ...
  All three runs returned identical result sets:
    - track_047, track_083, track_126, track_194
  [OK] VQL is 100% deterministic.
```

完全な実行ログ: [`vql_demo_log.txt`](vql_demo_log.txt)

---

## 実映像 VIR コンパイル例（ドローン監視映像）

> **実動画 + 実 YOLO 検出** — 合成データではない
> ドローン空撮映像 125s → YOLOv8n 人物検出 → VIR → VQL クエリ

```
Step 1 ▶ Compile real video → VIR
  Input   : ドローン.webm  (125s, 640×360, 30fps)
  Pipeline: YOLOv8n → centroid tracking → zone analysis
  Classes : person (class 0)  conf_thresh=0.20

  VIR summary:
    source      : drone_surveillance.webm
    duration    : 124.9s  (10.0 eff.fps after 3x sampling)
    entities    : 103  (person)
    tracks      : 103
    zone_events : 138
    stay_facts  : 126
    zones       : 北エリア / 左エリア / 中央エリア / 右エリア / 南エリア

  Done (37.6s) → vql_real_vir.json

Step 2 ▶ VQL クエリ実行  [NO models — deterministic]

  Query 1: 南エリアで2秒以上滞留した人物（不審滞留検知）
  ┌──────────────────────────────────────────────────────────┐
  │ SELECT   p                                               │
  │ FROM     VIR("drone_surveillance.webm")                  │
  │ WHERE    STAYS(p, zone("南エリア")) > 2s                  │
  │ RETURN   track_id, duration, evidence_frames(n=1)        │
  └──────────────────────────────────────────────────────────┘
  -> 1 match(es)   exec: 0.18ms [deterministic OK]
  [track_0069]  enter_t=89.20s  exit_t=92.70s  dur=3.5s

  Query 2: 南→中央エリアへ移動した人物（シーケンス検出）
  ┌──────────────────────────────────────────────────────────┐
  │ SELECT   p                                               │
  │ FROM     VIR("drone_surveillance.webm")                  │
  │ WHERE    SEQUENCE(                                       │
  │            ENTERS(p, zone("南エリア")),                   │
  │            ENTERS(p, zone("中央エリア"))                  │
  │          )                                               │
  │ RETURN   track_id, sequence_events                       │
  └──────────────────────────────────────────────────────────┘
  -> 3 match(es)   exec: 0.21ms [deterministic OK]
  [track_0069]  南エリア@89.2s → 中央エリア@92.2s → 右エリア@93.4s → 北エリア@93.9s
  [track_0060]  左エリア@88.5s → 南エリア@88.5s → 中央エリア@89.0s
  [track_0017]  中央エリア@28.4s  (同時 ENTER)

  Query 3: 北エリアで1秒以上滞留した人物
  -> 1 match(es)   exec: 0.10ms [deterministic OK]
  [track_0031]  enter_t=69.50s  exit_t=72.10s  dur=2.6s

Step 3 ▶ 決定論の証明
  Query 1 を3回実行:
    実行1: ['track_0069']
    実行2: ['track_0069']
    実行3: ['track_0069']
  [OK] VQL is 100% deterministic (real video + real YOLO detections)
```

実映像デモログ: [`vql_real_demo_log.txt`](vql_real_demo_log.txt)
実映像 VIR: [`vql_real_vir.json`](vql_real_vir.json)
コンパイラ: `python compile_real_vir.py <video.mp4> --out vql_real_vir.json`

---

## テスト

```
$ python -m pytest tests/ -v
========================== 32 passed in 0.27s ==========================
```

| テストクラス | 件数 | 内容 |
|------------|------|------|
| TestParser | 16 | トークナイザ・パーサ（ENTERS/STAYS/SEQUENCE/TIME_OF_DAY など） |
| TestExecutor | 13 | 実行器（フィルタ・型絞り込み・決定論 100回・10ms以内） |
| TestVIRSerialisation | 3 | VIR → dict/JSON → VIR ラウンドトリップ |

---

## 主デモ（demo_vql.py）について

`demo_vql.py` が返す `track_047` などのトラック ID は **合成データ**（`vql/demo_data.py` の固定 seed ジェネレータ）から来ています。
`vql_demo_surveillance.mp4` も OpenCV で生成した合成映像です。

| コンポーネント | 状態 | 備考 |
|-------------|------|------|
| VQL パーサ・実行器 | ✅ 実装 | 任意の VIR に対して動作 |
| VIR コンパイラ | ✅ 実装 | 実映像 → VIR（上記ドローン例） |
| 主デモの VIR | 🔧 合成 | 固定 seed の Python ジェネレータ。YOLO/ByteTrack 未接続 |
| evidence_frames | 🔧 合成フォールバック | 動画がなければ PIL で描画したモックフレームを返す |

つまり **「問い合わせ層は本物、知覚層は未踏で繋げる」** というプロジェクトです。

---

## VQL 文法（概要）

```
query      := SELECT var FROM vir_expr WHERE predicates RETURN returns
vir_expr   := VIR "(" STRING ")"
predicates := predicate ( AND predicate )*
predicate  := ENTERS(var, zone_expr [, time_range_opt])
            | EXITS(var, zone_expr)
            | STAYS(var, zone_expr) cmp_op duration_lit
            | DURATION(var, zone_expr) cmp_op duration_lit
            | TYPE(var, STRING)
            | SEQUENCE(predicate, predicate+)
            | TIME_OF_DAY(var) IN time_range_expr
duration_lit := NUMBER ("min" | "s" | "sec" | "h")
cmp_op       := "<" | "<=" | ">" | ">=" | "==" | "!="
```

---

## ファイル構成

```
vql/                         ← Python パッケージ (~700 行)
├── vir.py                   │  VIR データモデル (Entity/Track/Zone/ZoneEvent/StayFact)
├── parser.py                │  トークナイザ + 再帰下降パーサ → AST
├── executor.py              │  VQL 実行器  O(tracks × predicates), <1ms
├── demo_data.py             │  合成監視データジェネレータ (seed=42, 決定論的)
└── evidence.py              │  Evidence フレーム抽出・描画 (OpenCV / PIL)
tests/
└── test_vql.py              ← ユニットテスト 32件
demo_vql.py                  ← デモスクリプト（3クエリ + 決定論証明）
vql_mitou2026.html           ← インタラクティブ HTML デモ（外部依存なし）
vql_demo_vir.json            ← 生成済み VIR（347 tracks, 4 zones, 2h）
vql_demo_surveillance.mp4    ← 合成監視映像（30s, OpenCV 生成）
vql_demo_log.txt             ← 実行ログ
vql_demo_output/             ← Evidence frames (18枚, JPEG)
```

---

## 性能

| 操作 | 時間 |
|------|------|
| VIR コンパイル（合成, 347 tracks） | ~200ms |
| VQL クエリ実行（ENTERS + DURATION） | 0.3–0.7ms |
| VIR ロード（JSON キャッシュ） | ~50ms |

クエリ実行はモデルを呼ばないため VIR サイズのみに依存。

---

## 応用ドメイン

| ドメイン | VQL クエリ例 |
|---------|------------|
| 固定監視映像 | 特定区域への不審入室・長時間滞留検出 |
| スポーツ定点映像 | 選手のゾーン侵入回数・パス経路・ペナルティエリア解析 |
| 工場ライン | 作業者動線・危険区域侵入アラート |

---

## 実行環境

- Python 3.9+
- 必須: `pillow`
- 推奨: `opencv-python`（evidence frame 品質向上）
- テスト: `pytest`
