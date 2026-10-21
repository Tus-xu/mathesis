"""Visual Planning Layer 版本元数据（避免 __init__ 循环导入）。"""
VERSION = "r21.1-vpl-1.0.0"

# 参数化数学对象（slide.math_object）在 figure 槽位中的资产占位 id。
# 与 8201 引擎（presenton_adapter.engine.layout_resolver.MATH_OBJECT_ASSET_ID）同字面量：
# 8200 用它把「页承载 math_object」表达为 figure 槽位，8201 渲染时替换为确定性 PNG。
MATH_OBJECT_ASSET_ID = "__math_object__"

