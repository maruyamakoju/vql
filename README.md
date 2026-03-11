# VQL — Video Query Language
### 映像のSQL: compile once, query deterministically

**未踏IT 2026 応募プロトタイプ**  ·  MIT License

---

## 現在の実装状況

| 状態 | 内容 |
|------|------|
| ✅ 実装済み | VIR データモデル、VQL パーサ（再帰下降）、VQL 実行器、証拠フレーム返却、HTML インタラクティブデモ |
| 🔧 デモ版 | VIR 生成は合成データ（固定 seed の Python ジェネレータ）。実際の YOLO / ByteTrack とは未接続 |
| 🚧 今後実装 | YOLO-World + ByteTrack からの実 VIR コンパイル、Re-ID 統合、クラウド VIR ストレージ |

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
python demo_vql.py --no-server      # VIR 生成 + 3クエリ実行 + 決定論証明
python -m pytest tests/ -v          # ユニットテスト 32件
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
