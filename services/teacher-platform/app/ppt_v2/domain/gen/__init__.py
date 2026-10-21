"""三端一致性代码生成器。

权威源 = app.ppt_v2.domain.spec（Pydantic）；JSON Schema 与 TypeScript 类型
由本目录脚本生成并提交，任何一侧漂移由 contract test 拦截。

用法：
    python -m app.ppt_v2.domain.gen.gen_schema   # → domain/schema/deck-spec.schema.json
    python -m app.ppt_v2.domain.gen.gen_ts       # → domain/schema/deck-spec.ts（供前端复制）
"""
