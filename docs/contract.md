# CTD 采水瓶匹配与质控修订契约

## 1. 范围与基本约定

本能力把 Niskin 采水瓶的闭合深度关联到同一航次（cast）的 CTD 深度剖面，并在线性插值温度、盐度时使用 Python 标准库 `fractions.Fraction` 完成精确有理数运算。实现不使用第三方依赖或网络服务，不修改输入对象；重复调用同一输入必须得到相等且顺序稳定的结果。

现有公开入口 `ctd_bottle_match.process(data)` 的输入、输出、异常和排序保持兼容。公开入口为：

```python
from ctd_bottle_match import process, process_with_qc, ArchiveHandoff
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
PYTHONPATH=src python3 examples/run_archive_handoff.py
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

## 8. 可恢复的归档交接 `ArchiveHandoff`

科学数据中心分两类接收记录：CTD 来源证据记录（kind 为 `sample`）和瓶样关联记录（kind 为 `bottle`）。“已经发出”不等于“已经接收”：瓶样记录只有在其全部来源样本都已被归档端确认接收后才能进入交付批次。能力只包含 Python 业务 API 和本地持久化账本，不包含命令行、网页、远程上传或真实数据中心，仅使用标准库。

```python
from ctd_bottle_match import ArchiveHandoff

handoff = ArchiveHandoff(store_path)
handoff.register(dataset_id, data)
handoff.next_batch(dataset_id, request_id, limit)
handoff.acknowledge(dataset_id, request_id, accepted)
handoff.inspect(dataset_id)
handoff.close()
```

`store_path` 是调用方提供的本地文件路径，父目录必须已存在；文件不存在时创建。`close()` 后可以用同一路径新建实例继续，也可以由另一个进程重开。同一存储只要求写操作串行调用，不要求多写者并发；不同 dataset 的交接事实完全隔离。所有 `dataset_id`、`request_id` 必须是非空字符串，`request_id` 在各自 dataset 内唯一、不同 dataset 可重名。非法参数类型、空身份以及 `register` 以外接口引用未知 dataset 都抛出 `ValueError`。所有返回对象和已存储内容都与调用方输入/返回值脱钩，调用方事后修改原数据或返回值不会影响账本。

### 8.1 数据集冻结与记录构造（`register`）

`register(dataset_id, data)` 的 `data` 与 `process` 同契约（`casts`、`bottles`、`max_gap_m`），完整保留既有校验、规模限制和精确有理数规则；若显式包含 `qc_revisions` 字段则直接抛出 `ValueError`，其他未知字段忽略。校验全部成功后才落盘，校验或注册失败不会留下半个数据集，也不影响已存在数据集。同一 `dataset_id` 重复注册一律 `ValueError`，旧进度不可改写或清空。

注册时立即调用一次匹配并冻结结果，后续拉取不再重新解释调用方数据：

- **瓶样记录**：payload 等于 `process(data)` 的完整瓶样行（含 `matched`、`outside_profile`、`gap_too_wide` 全部状态），不能只交付匹配成功的瓶样。
- **CTD 记录**：只为实际被瓶样行 `source_sample_ids` 引用的样本建立，同一样本只建一份、可支撑多个瓶样；未被引用的剖面样本不参与交接，也不影响完成判断。`matched` 精确命中有一个依赖，插值有两个；`gap_too_wide` 仍交付两个端点样本作为过宽间隔证据，瓶样行不带温盐；`outside_profile` 无来源依赖。
- CTD payload 形状固定为 `{id, cast_id, depth_m, temperature_c, salinity_psu}`，数值沿用基线精确解析并规范化为整数或最简 `numerator/denominator` 字符串，不允许浮点数。
- 每条交付记录形状为 `{kind, id, payload}`，kind 只能是 `sample` 或 `bottle`。记录身份是二元组 `(kind, id)`：sample ID 与 bottle ID 可以相同，账本不依赖任何带分隔符的拼接字符串。

返回 `{dataset_id, sample_count, bottle_count}`，数量为实际参加交接的两类记录数（全部瓶样均 `outside_profile` 时 `sample_count` 为 0，数据集仍可正常完成）。

### 8.2 来源确认门槛与未结批次预留（`next_batch`）

`limit` 必须是 1..1000 的整数（布尔值不视为整数）。对首次出现的 `request_id`，在该调用开始时已持久化的接收事实和预留事实下挑选最多 `limit` 条：

- 尚未接收且未被任何未结批次预留的 CTD 记录可以交付；
- 瓶样记录必须尚未接收、未被预留，并且其全部来源样本都已通过此前成功提交的 `acknowledge` 确认接收；无来源的 `outside_profile` 瓶样可直接交付；
- 同一批次里即使包含某来源样本，也不能让依赖它的瓶样进入同一批次；其他未结批次中的来源同样不算已接收——必须等下一批次。

合格记录先按 kind（`sample` 在前、`bottle` 在后）、再按各自 id 的 Python 字符串升序选择，批次 items 保持同一顺序；与输入数组位置、瓶深或发出时间无关。

非空批次一旦签发，其中每条记录都为该批次预留，直到该批次被 `acknowledge` 结清。允许同时存在多个未结批次：某个批次等待回执不会阻塞其他未预留 CTD 记录或已满足依赖的瓶样，不存在“每个 dataset 只许一个未结批次”的限制。

### 8.3 重试优先级与空批次

- 同一 `request_id` 以同一 `limit` 重试，原样返回第一次签发的 items、payload 和顺序，即使期间已有接收回执、记录曾被拒收后进入其他批次、或整个数据集已完成；不会重新占用已释放的预留。
- 同一 `request_id` 改用不同 `limit` 抛出 `ValueError`。
- 没有合格记录时，签发并永久记录一个 items 为空的批次：空批次自动结清、不预留记录、不阻塞后续请求。之后即使出现新的合格记录，旧请求仍返回原空批次，调用方必须使用新的 `request_id`。

### 8.4 部分回执（`acknowledge`）

`acknowledge(dataset_id, request_id, accepted)` 的 `accepted` 是 `{kind, id}` 数组，可为空，数组顺序不表示业务顺序。完整校验通过后将批次一次性结清：accepted 中的记录成为已接收；批次中其余记录视为本次未接收、解除预留，以后可被新请求再次交付。一次结清覆盖整批，不支持追加确认，也没有过期或自动取消未结批次的机制。

未知 dataset/request、非数组 accepted、非法或重复身份、不属于该批次的身份都抛出 `ValueError`，此时整次操作不生效、不释放任何预留（即使数组里同时包含一个合法身份和一个批次外身份）。

返回 `{dataset_id, request_id, accepted, requeued}`，两个身份数组都按 sample 在前、bottle 在后、id 升序排列，不含 payload。回执永久固定：同一请求用相同 accepted 集合（允许换序）重试，返回第一次回执且不再改动账本；更换集合抛出 `ValueError`。即使旧批次拒收的记录后来已在新批次接收成功，重放旧回执仍返回旧 `requeued`，且不会把这些记录回退成待交付。空批次的 acknowledge 只接受空数组，返回固定空回执。

CTD 的接收确认会实际改变依赖瓶样的交付资格，而不仅是展示字段；瓶样自身未接收时只重交瓶样，不重交其已接收的 CTD 来源。

### 8.5 可观察状态（`inspect`，只读）

返回 `{dataset_id, samples, bottles, open_request_ids, complete}`：

- `samples` 按 id 排序，每项 `{id, state}`，state 为 `pending`、`reserved`（在未结批次中）或 `accepted`；
- `bottles` 按 id 排序，每项 `{id, state, waiting_for_sample_ids}`：已接收为 `accepted`、被预留为 `reserved`，其余按是否仍有未接收来源分为 `blocked` 或 `pending`；`waiting_for_sample_ids` 始终按该瓶样原 `source_sample_ids` 顺序列出尚未接收的依赖，无依赖或全部已接收时为 `[]`；
- `open_request_ids` 按字符串升序列出尚未结清的非空批次；
- `complete` 仅当所有参与交接的 CTD 与瓶样记录都已接收时为 true；全部已发出、暂无可签发记录或仅样本全部接收都不算完成。

### 8.6 持久化与重开恢复

成功返回的注册、批次签发和接收回执都写入本地账本，在 `close()`、对象销毁或另一个进程重开后保持一致，不依赖模块全局对象或调用方内存。每次成功的业务操作对应一次原子持久化边界：业务变化、请求身份绑定和固定返回内容一起生效或一起不生效；实现先在内存快照上执行变更，再以临时文件 + `os.replace` 原子替换账本，持久化失败时回滚内存到上一次已提交状态。因此重开只能看到某次操作完整提交前或完整提交后的状态，不会出现记录被预留但查不到批次、回执已保存但依赖未解锁、或只接收了 accepted 前半部分的中间态。提交已完成但响应未送达时，用同一请求重试取回原结果；尚未提交的失败操作可按当时完整状态重新首次执行。I/O 错误直接向调用方传播，不会伪装成成功。

### 8.7 示例与测试

```bash
PYTHONPATH=src python3 examples/run_archive_handoff.py
PYTHONPATH=src python3 -m unittest tests.test_archive_handoff -v
```

示例展示插值瓶样依赖两个 CTD 端点：只确认一个端点时瓶样保持 `blocked` 不可交付，补齐另一端点确认后新请求才首次交付瓶样，且已接收的来源不会重复发出；流程中包含关闭后由新实例重开账本。
