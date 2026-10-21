"""RV14 · Research export/compile worker 域。

PaperSpec → Markdown / LaTeX / PDF 的**确定性**导出；TeX 编译只在
独立容器（non-root / read-only / no-network）内发生；下载走短时令牌。

铁律（任务书）：
- 不复用 8200/8201/Presenton；raw LaTeX **永不**反写为 canonical PaperSpec；
- 不在 Web/Core 进程运行 TeX（本域 handler 只负责发起 `docker run` 并读取结果）；
- 所有产物携带 manifest（rv14.manifest.v1），哈希可查。
"""
