# CTD 采水瓶匹配与质控修订契约

## 1. 范围与基本约定

本能力把 Niskin 采水瓶的闭合深度关联到同一航次（cast）的 CTD 深度剖面，并在线性插值温度、盐度时使用 Python 标准库 `fractions.Fraction` 完成精确有理数运算。实现不使用第三方依赖或网络服务，不修改输入对象；重复调用同一输入必须得到相等且顺序稳定的结果。

现有公开入口 `ctd_bottle_match.process(data)` 的输入、输出、异常和排序保持兼容。新增公开入口为：

```python
from ctd_bottle_match import process, process_with_qc
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

## 7. 示例

可执行示例：

```bash
PYTHONPATH=src python3 examples/run_sample.py
PYTHONPATH=src python3 examples/run_qc_example.py
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
