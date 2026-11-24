# data-qd-211 · 海洋 CTD 采水匹配

把 Niskin 采水瓶的实际闭合深度关联到同一航次剖面的 CTD 样本，在允许的采样间隔内精确线性插值温度和盐度，并明确保留越界、间隔过宽和来源样本证据。

本仓主要交付 Python 业务 API 和下游可消费的结构化结果。字段、边界和未支持范围见 docs/contract.md。样例数据均为原创合成数据。

## 安装、测试与演示

Python 3.11+。运行依赖仅标准库；构建固定 setuptools 80.9.0 和 wheel 0.45.1。每个仓库使用独立虚拟环境。

    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python --offline -r requirements-build.lock
    uv pip install --python .venv/bin/python --offline --no-index --no-build-isolation -e .
    .venv/bin/python -m unittest discover -s tests -v
    .venv/bin/python examples/run_sample.py

样例中 5 m 瓶位由 0 m 与 10 m 样本插值得到 20 °C、34.5 PSU；25 m 超出剖面并保留 outside_profile 状态。

## 质控修订双视图

`ctd_bottle_match.process_with_qc(data)` 在兼容现有 `process(data)` 的基础上接受可选 `qc_revisions`。它为每个瓶样返回：

- `initial`：始终使用原始完整剖面，与 `process` 的行一致；
- `final`：按 `order` 应用失效/恢复修订后，只使用最终有效样本重算；
- `changed` 与 `impact`：基于完整业务字段比较，列出真正受修订影响的瓶样；
- `qc_samples`：全部样本的最终质控状态和最后一次修订证据；
- final 行中的 `qc_excluded_sample_ids`：来源区间内最终失效的原始样本。

失效样本不能作为端点或来源证据；有效样本不足两个时返回 `insufficient_valid_samples`。修订输入顺序不影响结果，跨航次修订相互隔离。完整契约见 `docs/contract.md`，QC 示例可运行：

    PYTHONPATH=src python3 examples/run_qc_example.py

最大契约规模（100 个航次、100 万样本、5000 瓶）的压力测试可用以下命令开启：

    CTD_RUN_STRESS=1 PYTHONPATH=src python3 -m unittest tests.test_qc_stress -v

## 当前边界

当前基线只处理调用方已经整理好的深度剖面，不读取设备二进制、不做传感器漂移校正、不将压力自动换算为深度。质控能力只保存和应用调用方提供的有效/无效及恢复结论，不自动检测尖峰。未来可在 cast/sample/bottle 身份上接入实验室营养盐结果。
