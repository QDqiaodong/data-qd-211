# CTD 采水瓶匹配、质控修订与分段剖面契约

## 1. 范围与基本约定

本能力把 Niskin 采水瓶的闭合深度关联到同一航次（cast）的 CTD 深度剖面，并在线性插值温度、盐度时使用 Python 标准库 `fractions.Fraction` 完成精确有理数运算。实现不使用第三方依赖或网络服务，不修改输入对象；重复调用同一输入必须得到相等且顺序稳定的结果。

现有公开入口 `ctd_bottle_match.process(data)` 的输入、输出、异常和排序保持兼容。公开入口为：

```python
from ctd_bottle_match import process, process_with_qc, process_with_segments
```

契约规模上限：

- `casts`：1..100 个；
- 每个 cast 的 `samples`：2..10000 个；
- `bottles`：1..5000 个；
- CTD 样本 ID 全局唯一，cast ID 和 bottle ID 分别唯一且为非空字符串；
- 每个 cast 内样本深度严格递增；
- `max_gap_m`、深度、温度、盐度沿用现有有界字符串和非负精确有理数解析规则；
- 输出中的有理数仍使用整数或 `numerator/denominator` 文本，不转换为浮点数。

## 2. 输入契约

基础字段与现有 `process` 相同：

```json
{
  "max_gap_m": "12",
  "casts": [
    {
      "id": "cast-a",
      "samples": [
        {"id": "ctd-0", "depth_m": "0", "temperature_c": "21", "salinity_psu": "34"},
        {"id": "ctd-10", "depth_m": "10", "temperature_c": "19", "salinity_psu": "35"}
      ]
    }
  ],
  "bottles": [
    {"id": "niskin-1", "cast_id": "cast-a", "depth_m": "5"}
  ]
}
```

`process_with_qc(data)` 还接受可选字段 `qc_revisions`。缺省或等价于空数组；若显式提供，则必须是数组。

### 2.1 质控修订对象

每条修订必须是 JSON 对象，字段如下：

| 字段 | 规则 |
| --- | --- |
| `id` | 非空字符串；所有修订全局唯一。 |
| `order` | 正整数；所有修订全局唯一。较小值先生效。JSON/ Python 布尔值不视为整数顺序。 |
| `cast_id` | 必须引用输入中已存在的 cast。 |
| `sample_ids` | 非空数组；每个 ID 必须是该 cast 中存在的样本；同一条修订内不得重复。允许跨修订重复引用同一样本。 |
| `state` | 只能是 `"invalid"` 或 `"valid"`。`valid` 表示复核后恢复可用，不是删除修订历史。 |
| `reason` | 非空字符串，作为最后一次修订证据保留。 |

以下情况均抛出明确的 `ValueError`：未知 cast/sample、跨 cast 引用样本、重复修订 ID、重复 `order`、非法状态、空原因、非正整数或非整数 `order`、空或重复的 `sample_ids`。

所有基础输入和质控修订必须先完整校验，然后才能执行匹配或产生业务结果。修订按 `order` 升序应用，而不是按输入数组顺序应用。每个样本初始为 `valid`；同一样本可被反复失效、恢复、再次失效，最终状态和证据来自 `order` 最大的最后一条修订。

## 3. 现有匹配语义

对完整 CTD 剖面，每个瓶深：

1. 小于首样本深度或大于末样本深度：`outside_profile`；
2. 恰好等于某样本深度：`matched`，只引用该样本，`gap_m` 为 `"0"`；
3. 位于两个相邻样本之间：计算深度差 `gap_m`；
   - `gap_m > max_gap_m`：`gap_too_wide`，保留两个来源样本和间隔，不输出温度、盐度；
   - 否则：`matched`，按精确有理数线性插值，保留两个来源样本和间隔。

不会静默外推，也不会把越界或过宽间隔改成成功匹配。

`process(data)` 继续返回：

```json
{
  "bottles": [
    {
      "id": "niskin-1",
      "cast_id": "cast-a",
      "depth_m": "5",
      "status": "matched",
      "temperature_c": "20",
      "salinity_psu": "69/2",
      "source_sample_ids": ["ctd-0", "ctd-10"],
      "gap_m": "10"
    }
  ],
  "counts": {
    "matched": 1,
    "outside_profile": 0,
    "gap_too_wide": 0
  }
}
```

新增 QC 字段不会改变 `process` 的结果。

## 4. `process_with_qc(data)` 输出

顶层至少包含 `bottles`、`counts`、`qc_samples`、`impact`。

### 4.1 `bottles`

`bottles` 按瓶样 `id` 排序。每行结构为：

```json
{
  "id": "niskin-qc-10",
  "cast_id": "cast-a",
  "initial": { "...": "现有 process 的完整瓶样行" },
  "final": { "...": "最终质控状态下的完整匹配行，并带 qc_excluded_sample_ids" },
  "changed": true
}
```

- `initial`：始终使用该 cast 的全部原始样本，内容与 `process(data)` 中同一瓶样的行完全一致。最终质控状态不得回写或污染该视图。
- `final`：只使用最终状态为 `valid` 的样本。
- `changed`：比较 `initial` 与 `final` 的完整业务字段；不能只比较温度或状态。由于 `qc_excluded_sample_ids` 是仅属于 final 的质控追溯字段，比较时排除该附加元数据。状态、温度、盐度、来源样本、间隔等任一字段不同均为 `true`。

### 4.2 `final` 视图规则

1. 某 cast 最终有效样本少于两个时，该 cast 的所有瓶样统一为：

   ```json
   {
     "status": "insufficient_valid_samples",
     "qc_excluded_sample_ids": []
   }
   ```

   该行不得包含 `temperature_c`、`salinity_psu` 或 `source_sample_ids`。

2. 有效样本不少于两个时：
   - 瓶深在首个/末个有效样本范围之外：`outside_profile`，`qc_excluded_sample_ids` 为 `[]`；
   - 瓶深恰好落在有效样本上：`matched`，只引用该有效样本；
   - 瓶深落在两个相邻有效样本之间：以这两个有效样本为端点，并按原 `max_gap_m` 规则判断；
   - 被失效的原始样本不能作为端点、插值数值来源或缩短间隔的依据。

3. `qc_excluded_sample_ids` 列出该 cast 原始剖面中最终无效、且位于 final 来源区间两个端点之间的样本 ID，按原始深度顺序返回。没有时为 `[]`。
   - 精确命中有效样本时为空；
   - `outside_profile` 时按契约固定为空，不猜测因果；
   - `insufficient_valid_samples` 时固定为空；
   - `gap_too_wide` 仍保留有效端点、间隔和该区间内的失效样本追溯 ID。

### 4.3 `counts`

```json
{
  "initial": {
    "matched": 0,
    "outside_profile": 0,
    "gap_too_wide": 0
  },
  "final": {
    "matched": 0,
    "outside_profile": 0,
    "gap_too_wide": 0,
    "insufficient_valid_samples": 0
  }
}
```

`initial` 独立统计原始剖面视图，`final` 独立统计最终有效样本视图，不能互相覆盖。计数字典保留稳定的状态键；实际出现的状态一定有正确计数。

### 4.4 `qc_samples`

按输入中 cast 的顺序、以及每个 cast 内样本的原始顺序，返回全部样本：

```json
{
  "cast_id": "cast-a",
  "sample_id": "ctd-10",
  "state": "invalid",
  "last_revision_id": "qc-invalidate-ctd-10",
  "reason": "Post-match review found a temperature spike at 10 m."
}
```

没有被任何修订引用的样本，其 `last_revision_id` 和 `reason` 为 `null`。被恢复为 `valid` 的样本仍保留最后一次恢复修订的 ID 和原因。

### 4.5 `impact`

只包含 `changed` 为 `true` 的瓶样，按瓶样 ID 排序：

```json
[
  {
    "bottle_id": "niskin-qc-10",
    "from_status": "matched",
    "to_status": "gap_too_wide"
  }
]
```

即使状态字符串相同，只要完整业务结果不同（例如精确命中变成跨失效样本插值），也必须列入 `impact`。

## 5. 无修订时的兼容结果

当 `qc_revisions` 缺省或为空时：

- 所有样本最终均为 `valid`；
- 每个 `final` 业务字段与现有 `process` 对应行一致，仅额外包含 `"qc_excluded_sample_ids": []`；
- 所有 `changed` 为 `false`；
- `impact` 为 `[]`；
- `qc_samples` 完整列出全部样本且证据字段为 `null`。

## 6. 复杂度与压力边界

设样本总数为 `S`、修订数为 `R`、修订引用总数为 `K`、瓶样数为 `B`、输出的 `qc_excluded_sample_ids` 总数为 `E`。实现采用：

- 一次剖面解析和样本位置索引：`O(S)`；
- 修订排序与应用：`O(R log R + K)`；
- 每个瓶样在有效深度上二分定位：`O(B log S)`；
- 生成失效来源追溯：`O(E)`。

不会为每条修订复制整套剖面，也不会为每个瓶样保存或扫描无关 cast 的修订历史。内存主要用于保存原始样本、每样本最终状态/最后证据、瓶样结果和必须输出的追溯 ID。

最大契约规模的压力测试默认跳过，可运行：

```bash
CTD_RUN_STRESS=1 PYTHONPATH=src python3 -m unittest tests.test_qc_stress -v
```

## 7. 分段剖面入口 `process_with_segments(data)`

CTD 设备在一次下放中可能因记录器重启、泵重启或分段导出，把同一航次保存成多个 segment。每个 segment 内部深度严格递增，但不同 segment 可能覆盖相同深度。`process_with_segments(data)` 先把同一 cast 的全部 segment 规范化成一条 canonical profile，再按第 3 节的既有语义匹配该 cast 的瓶样。该入口不处理质控修订，也不改变 `process`、`process_with_qc` 的输入输出。

### 7.1 分段输入契约

```json
{
  "max_gap_m": "12",
  "segments": [
    {
      "id": "seg-a-1",
      "cast_id": "cast-a",
      "sequence": 1,
      "samples": [
        {"id": "ctd-0", "depth_m": "0", "temperature_c": "21", "salinity_psu": "34"},
        {"id": "ctd-10-old", "depth_m": "10", "temperature_c": "19", "salinity_psu": "35"}
      ]
    },
    {
      "id": "seg-a-2",
      "cast_id": "cast-a",
      "sequence": 2,
      "samples": [
        {"id": "ctd-10-new", "depth_m": "10", "temperature_c": "20", "salinity_psu": "35"},
        {"id": "ctd-20", "depth_m": "20", "temperature_c": "18", "salinity_psu": "36"}
      ]
    }
  ],
  "bottles": [
    {"id": "niskin-1", "cast_id": "cast-a", "depth_m": "15"}
  ]
}
```

`max_gap_m`、深度、温度、盐度沿用第 1 节的有界非负精确数字字符串规则；输出有理数仍只能是整数文本或 `numerator/denominator` 文本。

规模与身份规则：

- `segments` 是 1..1000 个对象；其中唯一 `cast_id` 数量为 1..100，同一 cast 至少有一个 segment；
- segment `id` 是非空字符串且跨所有 segment 全局唯一；`cast_id` 是非空字符串；`sequence` 是正整数且不是布尔值，同一 cast 内唯一；
- 每个 segment 的 `samples` 是 2..10000 个对象；同一 cast 的所有 segment 合计样本数不超过 10000；
- sample `id` 是非空字符串且跨所有 segment 全局唯一；每个 segment 内深度严格递增；
- `bottles` 是 1..5000 个对象，瓶样 ID 全局唯一且非空；每个瓶样 `cast_id` 必须引用至少一个 segment；
- 未知字段可以忽略；缺失、类型错误、空身份、重复身份、重复段序、非正整数或布尔段序、越界规模、不满足严格递增等情况抛出明确的 `ValueError`。

全部输入必须在任何匹配、canonical profile 或审计结果产生前校验完毕；校验失败不会返回部分结果。`segments` 数组位置不参与语义，重叠取舍只由同一 cast 内的 `sequence` 决定。

### 7.2 canonical profile 规范化规则

对每个 cast：

1. 不同深度的样本全部保留，按深度升序组成 canonical profile，深度严格递增。
2. 多个 segment 在同一 cast 出现完全相同深度时，只保留 `sequence` 最大的 segment 中的样本；较大 `sequence` 代表重启后较新、对该重叠点有权威性的记录。同一深度不会因温盐不同而产生两条 canonical 样本，也不会静默平均、取最小值或拼接数值。
3. 每个保留的 canonical 样本带原始 `sample_id` 与所属 `segment_id`；被替换样本不进入 canonical profile，也不能成为瓶样来源，但必须出现在重叠审计中。
4. segment 之间没有样本的深度空档保留为 canonical profile 的真实相邻间隔；不会因为 segment 的 `sequence` 相邻就把空档视为连续测量，也不会自动外推。
5. canonical profile 形成后，瓶样匹配复用第 3 节 `process` 的业务语义：恰好命中 canonical 样本时只引用该样本（`gap_m` 为 `"0"`）；落在相邻样本之间时按 `max_gap_m` 判断并精确有理数插值；越过首尾返回 `outside_profile`；间隔过宽返回 `gap_too_wide`，保留两个 canonical 来源样本和 `gap_m`，不输出温度和盐度。

### 7.3 输出契约

返回对象至少包含 `bottles`、`counts`、`canonical_profiles`。

`canonical_profiles` 按 `cast_id` 升序返回，每个 cast 一项，即使没有重叠也必须出现：

```json
{
  "cast_id": "cast-a",
  "segment_ids": ["seg-a-1", "seg-a-2"],
  "samples": [
    {
      "id": "ctd-0",
      "segment_id": "seg-a-1",
      "depth_m": "0",
      "temperature_c": "21",
      "salinity_psu": "34"
    },
    {
      "id": "ctd-10-new",
      "segment_id": "seg-a-2",
      "depth_m": "10",
      "temperature_c": "20",
      "salinity_psu": "35"
    }
  ],
  "overlap_resolutions": [
    {
      "depth_m": "10",
      "selected_sample_id": "ctd-10-new",
      "selected_segment_id": "seg-a-2",
      "discarded_sample_ids": ["ctd-10-old"]
    }
  ]
}
```

- `segment_ids` 按 `sequence` 升序；`samples` 按 canonical 深度升序；`overlap_resolutions` 按深度升序；
- `samples` 只包含最终选中的样本，字段值与输入完全一致，不输出浮点数、不重写精确数字文本；
- `discarded_sample_ids` 按未选样本的 `sequence` 升序（同一 segment 内按原始样本顺序）列出该深度全部未选样本；
- 没有重叠时 `overlap_resolutions` 必须是 `[]`，不得伪造审计记录；仅打乱 `segments` 数组顺序不改变结果，重复调用结果相等。

`bottles` 按瓶样 `id` 升序返回。每行包含第 3 节匹配行的全部字段，并增加：

```json
"source_segment_ids": ["seg-a-1", "seg-a-2"]
```

- 按 `source_sample_ids` 的顺序去重得到，来源必须是实际 canonical 样本所属的 segment，不能依据 segment 输入顺序填写，也不能包含被替换样本的 segment；
- `matched` 与 `gap_too_wide` 使用实际来源 canonical 样本的 segment；`outside_profile` 固定为 `[]`。

`counts` 与 `process` 完全一致，只统计 canonical profile 实际匹配出的 `matched`、`outside_profile`、`gap_too_wide`；分段数、重叠数不会增加或重复计数。

### 7.4 分段入口的复杂度

设 segment 内样本总数为 `S`、瓶样数为 `B`、重叠深度数为 `O`。实现按深度分桶保存候选样本，每个样本只解析一次：

- 解析与校验：`O(S)`；
- 每个 cast 的 canonical 构建与重叠审计：与该 cast 的样本数及重叠候选数成线性；
- 每个瓶样在 canonical 深度上二分定位：`O(B log S)`；
- 不为每个瓶样复制任何 cast 的 canonical profile，也不为每个重叠点重复扫描全部 segment。内存用于保存必要的候选选择、canonical 样本和审计证据。

最大契约规模（100 个 cast、1000 个 segment、每 cast 至多 10000 个样本、5000 瓶）的压力测试默认跳过，可运行：

```bash
CTD_RUN_STRESS=1 PYTHONPATH=src python3 -m unittest tests.test_segment_stress -v
```

## 8. 示例

可执行示例：

```bash
PYTHONPATH=src python3 examples/run_sample.py
PYTHONPATH=src python3 examples/run_qc_example.py
PYTHONPATH=src python3 examples/run_segments_example.py
```

分段示例输入见 `fixtures/segments_input.json`。其中 10 m 深度同时存在于两个 segment：`sequence = 1` 的 `ctd-10-old`（19 °C）被 `sequence = 2` 的 `ctd-10-new`（20 °C）取代。10 m 瓶样精确命中时只引用新样本及其 segment；15 m 瓶样按新样本与 `ctd-20` 插值，温度为 19 °C、盐度为 `71/2` PSU，来源仍可追溯到 `seg-a-2`；30 m 瓶样落在 20 m 与 40 m 的真实空档之间，间隔 20 m 超过 `max_gap_m = 12`，返回 `gap_too_wide`。重叠审计保留被替换的 `ctd-10-old`：

```json
{
  "id": "niskin-10",
  "cast_id": "cast-a",
  "depth_m": "10",
  "status": "matched",
  "temperature_c": "20",
  "salinity_psu": "35",
  "source_sample_ids": ["ctd-10-new"],
  "gap_m": "0",
  "source_segment_ids": ["seg-a-2"]
}
```

```json
{
  "depth_m": "10",
  "selected_sample_id": "ctd-10-new",
  "selected_segment_id": "seg-a-2",
  "discarded_sample_ids": ["ctd-10-old"]
}
```

QC 示例输入见 `fixtures/qc_input.json`。其中 `ctd-10` 在初次匹配后因尖峰被判定无效，导致原本精确命中的 `niskin-qc-10` 改为由 `ctd-0`、`ctd-20` 判定；两端间隔为 `20 m`，超过 `max_gap_m = 12`，因此 final 为 `gap_too_wide`，同时保留失效样本追溯和修订原因：

```json
{
  "id": "niskin-qc-10",
  "cast_id": "cast-a",
  "initial": {
    "status": "matched",
    "temperature_c": "19",
    "salinity_psu": "35",
    "source_sample_ids": ["ctd-10"],
    "gap_m": "0"
  },
  "final": {
    "status": "gap_too_wide",
    "source_sample_ids": ["ctd-0", "ctd-20"],
    "gap_m": "20",
    "qc_excluded_sample_ids": ["ctd-10"]
  },
  "changed": true
}
```

`qc_samples` 中 `ctd-10` 保留 `last_revision_id` 和具体 `reason`，`impact` 记录 `matched -> gap_too_wide`，因此下游实验室既能使用最终质控结果，也能追溯修订原因和受影响瓶样。
