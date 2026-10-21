"""R21 真实渲染引擎（8201 内嵌，可替换槽位）。

组成：
- fonts.py    字体发现 + fontTools cmap 覆盖检查 + fallback 链
- metrics.py  PIL 文本度量：overflow / overlap / 截断检测
- figures.py  matplotlib 确定性数学图形（数轴 / 函数图 / 韦恩图）
- builder.py  python-pptx 逐页 OOXML 构建（可编辑：真实文本框/形状/图片/备注）
- renderer.py PowerPoint COM 渲染（PNG 逐页导出 + 最终打开检查）
- qa.py       页级 QA 编排（公式字符覆盖 / 图形检查 / 渲染非空）
- store.py    内容寻址 artifact 存储（sha256 键，天然去重）

接口契约（Presenton 形状，保持可替换）：
- POST /api/v1/deck/pages/render  逐页生成+渲染+QA
- POST /api/v1/deck/assemble      装配最终可编辑 PPTX + 打开检查 + contact sheet
- GET  /api/v1/artifacts/{ref}    artifact 元数据
"""
