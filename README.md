# VQL — Video Query Language
## 映像のSQL: compile once, query deterministically

**未踏IT 2026 応募デモ**

---

## 概要

VQL (Video Query Language) は映像をデータベースとして扱う新しいクエリ言語です。

```
                 ┌─────────────────────────────────────────────┐
  video.mp4  ──► │  Perception Pipeline  (YOLO / VLM / Re-ID)  │  ← 1回だけ実行
                 └──────────────────────┬──────────────────────┘
                                        │  compile
                                        ▼
                              VIR (Video Intermediate
                               Representation) .json
                                        │
                              ┌─────────┴─────────────────┐
                              │   VQL Query Engine         │
   VQL クエリ  ──────────────► │   (モデル不使用, <10ms)     │
                              └────────────┬──────────────┘
                                           │
                              ┌────────────▼──────────────┐
                              │  Results: tracks + clips  │
                              └───────────────────────────┘
```

**核心的な性質:**
- **決定論的**: 同じ VIR + 同じクエリ = 常に同一結果
- **高速**: クエリ実行は <10ms (モデル推論なし)
- **証跡付き**: 各結果に evidence_frames (入退室瞬間の映像) を付与

---

## クイックスタート

```bash
# 依存関係のインストール
pip install pillow opencv-python

# デモ実行 (VIR生成 + クエリ実行 + 決定論証明)
python demo_vql.py --no-server

# VIR を再生成する場合
python demo_vql.py --no-server --regen
```

インタラクティブ HTML デモ:
```
vql_mitou2026.html  をブラウザで開く
```

---

## デモクエリ (監視カメラドメイン)

### Query 1: 14時以降にA区域へ入り5分未満で退出した人物

```sql
SELECT   person
FROM     VIR("entrance_cam_2h.mp4")
WHERE    ENTERS(person, zone("A区域"),
                time_range(from="14:00:00", to="15:00:00"))
  AND    DURATION(person, zone("A区域")) < 5min
RETURN   track_id, enter_t, exit_t, duration,
         evidence_frames(n=2)
```

**結果: 4件 (0.41ms)**

```
[1] track_047  14:02:11 -> 14:04:33  dur=2m 22s
[2] track_083  14:08:45 -> 14:11:20  dur=2m 35s
[3] track_126  14:23:07 -> 14:26:52  dur=3m 45s
[4] track_194  14:51:33 -> 14:54:18  dur=2m 45s
```

### Query 2: 受付を経由してA区域に入った人物のシーケンス検出

```sql
SELECT   person
FROM     VIR("entrance_cam_2h.mp4")
WHERE    SEQUENCE(
           ENTERS(person, zone("受付")),
           ENTERS(person, zone("A区域"))
         )
  AND    DURATION(person, zone("受付")) < 3min
RETURN   track_id, sequence_events, evidence_frames(n=2)
```

**結果: 3件 (0.66ms)**

### Query 3: 13〜18時にB区域で30分以上滞留した人物

```sql
SELECT   person
FROM     VIR("entrance_cam_2h.mp4")
WHERE    STAYS(person, zone("B区域")) > 30min
  AND    TIME_OF_DAY(person) IN time_range("13:00:00", "18:00:00")
RETURN   track_id, total_stay_duration, evidence_frames(n=2)
```

**結果: 2件 (0.31ms)**

---

## 決定論の証明 (実行ログより)

```
  Running Query 1 a third time ...
  All three runs returned identical result sets:
    - track_047
    - track_083
    - track_126
    - track_194

  [OK] VQL is 100% deterministic.
       Perception error is confined to the VIR compilation layer,
       not the query layer.
```

---

## ファイル構成

```
02081mitou/
├── vql_mitou2026.html          # インタラクティブ HTML デモ (単体で動作)
├── demo_vql.py                 # Python デモスクリプト
├── vql_demo_log.txt            # 実行ログ (出力例)
├── vql_demo_vir.json           # 生成済み VIR (監視カメラ 2h, 347 tracks)
├── vql_demo_surveillance.mp4   # 合成監視映像
├── vql_demo_output/            # Evidence frames (q1_match*_frame*.jpg)
└── vql/                        # VQL Python パッケージ
    ├── __init__.py
    ├── vir.py                  # VIR データモデル
    ├── parser.py               # トークナイザ + 再帰下降パーサ → AST
    ├── executor.py             # VQL エグゼキュータ (O(N) per query)
    ├── demo_data.py            # 合成監視データジェネレータ
    └── evidence.py             # Evidence フレーム抽出・描画
```

---

## VIR (Video Intermediate Representation) の構造

```json
{
  "source": "entrance_cam_2h.mp4",
  "duration_sec": 7200.0,
  "fps": 15.0,
  "zones": [
    {"id": "A区域", "polygon": [[0.28,0.08],[0.72,0.08],[0.72,0.48],[0.28,0.48]]}
  ],
  "entities": [{"id": "entity_047", "type": "person"}],
  "tracks": [{"id": "track_047", "entity_id": "entity_047", "positions": [...]}],
  "zone_events": [
    {"track_id": "track_047", "zone_id": "A区域", "event_type": "ENTER",
     "t_sec": 50531.0, "frame": 757965}
  ],
  "stay_facts": [
    {"track_id": "track_047", "zone_id": "A区域",
     "enter_t": 50531.0, "exit_t": 50673.0, "duration_sec": 142.0}
  ]
}
```

---

## VQL 文法 (BNF 抜粋)

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
cmp_op     := "<" | "<=" | ">" | ">=" | "==" | "!="
```

---

## 性能

| 操作 | 時間 |
|------|------|
| VIR コンパイル (347 tracks, 2h) | ~200ms |
| VQL クエリ実行 (ENTERS+DURATION) | 0.3–0.7ms |
| VIR ロード (キャッシュ済み JSON) | ~50ms |
| 映像サイズ依存 | O(tracks × zones) |

クエリ実行はモデルを呼ばないため、VIR のサイズのみに依存します。

---

## 応用ドメイン

| ドメイン | VQL 用途例 |
|---------|-----------|
| 監視カメラ | 特定区域への不審な入室パターン検出 |
| スポーツ映像 | 選手の特定ゾーン滞留時間・パス経路解析 |
| 工場ライン | 作業者動線・危険区域侵入アラート |
| 小売 | 商品棚前の滞留・購買行動分析 |

---

## 実行環境

- Python 3.9+
- 必須: `pillow`
- 推奨: `opencv-python` (evidence frame 品質向上)
- オプション: `uvicorn` (API サーバ起動)
