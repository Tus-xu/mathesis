# IFC-001 · SlideSemanticSpec / TemplateSlots / layout_slide() 签名冻结

> 状态：**FROZEN**（V2.2.1 收口②，2026-09-05 用户裁决）· 受影响方：B1（产 spec）→ B4（布局实现）→ B5（契约测试）
> 落库：M1 由 B3 脚手架将本文件原样放入 `services/teacher-platform/docs/handoff/IFC-001-SlideSemanticSpec.md`；此后任何变更走 IFC 流程（02-ARCHITECTURE §8 接口变更流程）。
> 动机：B1/B4 规范各自描述了 `SlideSemanticSpec`，防止两侧各写一份 schema 导致漂移（B1 写一种结构、B4 理解成另一种）；同时落实 README 纪律 14（LLM 禁产像素布局）。

```python
# B4 提供：app/pagination/schemas.py（B1 只 import，单一事实源——禁止 B1 复制定义）
class SlideSemanticSpec(BaseModel):        # B1 deck_graph 逐页 LLM 输出（Pydantic 校验，失败重试 ≤2）
    page_type: Literal['concept_intro', 'visual_definition', 'derivation', 'formula_focus',
                       'worked_example', 'compare', 'common_error', 'practice', 'summary']  # 九种页型词汇表（B2 §3 ppt_slots）
    title: str
    points: list[str]                      # ≤5 条，每条 ≤40 字
    formula: str | None                    # LaTeX；None = 本页无公式
    visual: VisualIntent | None            # {kind: 'preset'|'boardJson', preset?, params?, boardJson?}
    teacher_note: str | None               # 备课提示，不进 slide 正文
    # 禁字段：x / y / w / h / font_size / color / 任何像素值——校验层直接 reject（README 纪律 14）

class TemplateSlots(TypedDict):            # 来源：deck_templates.slots（02-ARCHITECTURE §4.2 注释）
    layout_type: str; pedagogical_role: str; required_slots: list[str]; optional_slots: list[str]
    max_text: int; formula_capacity: int; visual_capacity: int; locked_elements: list[str]

# B4 提供：app/pagination/layout.py（纯函数，确定性布局引擎）
def layout_slide(spec: SlideSemanticSpec, slots: TemplateSlots) -> list[Element]
    # Element 的坐标/字号档位/颜色全部由此唯一产出；golden 向量由 B5 复核（与 paginate_blocks 同体系）
```

**验收钩子（B5，M1）**：`tests/contract/test_ifc001_schema.py` 断言——① `SlideSemanticSpec` 字段集与禁字段 reject 行为；② `layout_slide` 纯函数性（同输入同输出）；③ B1 deck_graph 侧无复制定义（grep 断言 import 路径唯一指向 `app/pagination/schemas.py`）。
