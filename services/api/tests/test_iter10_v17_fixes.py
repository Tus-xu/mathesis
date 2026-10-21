"""v1.7：错题摘要 LaTeX 安全截断（练题中心待复习侧栏乱码根修）的单元测试。"""

from app.gateway.student_router import _latex_safe_preview


class TestLatexSafePreview:
    def test_short_text_passthrough(self):
        assert _latex_safe_preview("短文本 $x^2$ 不需要截断", 96) == "短文本 $x^2$ 不需要截断"

    def test_never_cuts_inside_math(self):
        # 截断点落在 $...$ 中间：必须退到完整公式边界，绝不留下不闭合的 $
        text = "已知向量 $\\vec{a} = (2, 1)$，$\\vec{b} = (-1, 3)$，若向量 $\\vec{c}$ 满足 $\\vec{c} = 2\\vec{a} - 4\\vec{b}$，则 $\\vec{c}$ 的坐标为"
        out = _latex_safe_preview(text, 50)
        assert out.count("$") % 2 == 0, f"定界符必须成对闭合: {out!r}"
        assert out.endswith("…")

    def test_extends_to_closing_delimiter_within_grace(self):
        # 截断点在数学段内但闭合 $ 在宽限窗口内：延伸到闭合处
        text = "设函数 $f(x)=x^2-2x+3$" + "，后续的解析文字" * 20
        out = _latex_safe_preview(text, 12)
        assert out.count("$") % 2 == 0
        assert "$f(x)=x^2-2x+3$" in out

    def test_cases_environment_not_cut(self):
        # \\begin{cases} 长公式：要么完整保留到闭合，要么退到公式外边界
        text = "设函数 $f(x)=\\begin{cases}\\dfrac{x^2+2x-3}{x-1},&x<1,\\\\3,&x=1,\\\\\\dfrac{\\sin(e^x)}{x},&x>1\\end{cases}$，则下列结论正确的是（　　）附加大段后续文字"
        out = _latex_safe_preview(text, 60)
        assert out.count("$") % 2 == 0, f"cases 公式不得切断: {out!r}"

    def test_empty_and_none(self):
        assert _latex_safe_preview("", 96) == ""
        assert _latex_safe_preview(None, 96) == ""

    def test_no_math_plain_cut(self):
        text = "这是一段没有任何公式的纯文本，" * 20
        out = _latex_safe_preview(text, 40)
        assert len(out) <= 42  # 40 + …
        assert out.endswith("…")
