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

## 当前边界

当前基线只处理调用方已经整理好的深度剖面，不读取设备二进制、不做传感器漂移校正、不将压力自动换算为深度。未来可在 cast/sample/bottle 身份上接入实验室营养盐结果与质控标记。
