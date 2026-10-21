"""示例题库预置脚本（题库未导入真题时功能可演示）

内置 38 道高中数学真题风格示例题，覆盖 函数/三角/数列/立体几何/解析几何/导数/概率/
集合/不等式/复数/向量 等模块，难度 easy/medium/hard 混合，题型 choice/blank/solution 混合。
来源统一标注"示例题·教研预置"，is_real_exam=False（示例题不假冒真题）。
每题答案均经人工核算；解答题按 (1)(2) 小问组织（对齐高考大题规格）。

用法：
    cd services/api
    python -m scripts.seed_demo_bank

幂等：hash（规范化题干 sha256）去重，已存在跳过，可重复执行。
embedding best-effort：服务不可用时落 NULL（题库检索主路径不依赖向量）。
"""

import asyncio
import sys
from pathlib import Path

# 确保 app 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.models.database import async_session_factory, init_db
from app.models.knowledge_point import KnowledgePoint
from app.models.question_bank import QuestionBank, stem_hash
from scripts.import_question_bank import _embed_best_effort

_SOURCE = "示例题·教研预置"


def _q(stem, q_type, options, answer, analysis, difficulty, kp_codes):
    """构造一条示例题（source/is_real_exam 统一标注）"""
    return {
        "stem": stem,
        "q_type": q_type,
        "options": options,
        "answer": answer,
        "analysis": analysis,
        "difficulty": difficulty,
        "kp_codes": kp_codes,
        "source": _SOURCE,
        "year": None,
        "is_real_exam": False,
    }


# ==================== 示例题（38 道，答案经人工核算） ====================

DEMO_QUESTIONS: list[dict] = [
    # ---------- 函数（8） ----------
    _q(
        "函数 $f(x)=\\sqrt{x-1}$ 的定义域是",
        "choice",
        {"A": "A. $[1,+\\infty)$", "B": "B. $(1,+\\infty)$", "C": "C. $[0,+\\infty)$", "D": "D. 全体实数"},
        "A",
        "由 $x-1\\ge 0$ 得 $x\\ge 1$，定义域为 $[1,+\\infty)$。",
        "easy",
        ["MATH-G1-FUNC-101"],
    ),
    _q(
        "函数 $f(x)=x^2-2x$ 的单调递减区间是",
        "choice",
        {"A": "A. $(-\\infty,1]$", "B": "B. $[1,+\\infty)$", "C": "C. $(-\\infty,2]$", "D": "D. $[0,1]$"},
        "A",
        "对称轴 $x=1$，开口向上，故在 $(-\\infty,1]$ 上单调递减。",
        "medium",
        ["MATH-G1-FUNC-102"],
    ),
    _q(
        "下列函数中为奇函数的是",
        "choice",
        {"A": "A. $y=x^2$", "B": "B. $y=x^3$", "C": "C. $y=x+1$", "D": "D. $y=|x|$"},
        "B",
        "$f(-x)=(-x)^3=-x^3=-f(x)$，$y=x^3$ 为奇函数；A、D 为偶函数，C 非奇非偶。",
        "easy",
        ["MATH-G1-FUNC-103"],
    ),
    _q(
        "已知幂函数 $f(x)=x^{\\alpha}$ 的图象经过点 $(2,\\frac{1}{4})$，则 $\\alpha=$ ____",
        "blank",
        None,
        "-2",
        "由 $2^{\\alpha}=\\frac{1}{4}=2^{-2}$ 得 $\\alpha=-2$。",
        "medium",
        ["MATH-G1-FUNC-104"],
    ),
    _q(
        "计算：$\\left(\\frac{1}{2}\\right)^{-2}+8^{\\frac{2}{3}}=$ ____",
        "blank",
        None,
        "8",
        "$\\left(\\frac{1}{2}\\right)^{-2}=4$，$8^{\\frac{2}{3}}=(2^3)^{\\frac{2}{3}}=2^2=4$，和为 $8$。",
        "easy",
        ["MATH-G1-FUNC-201"],
    ),
    _q(
        "计算：$\\lg 25+\\lg 4=$ ____",
        "blank",
        None,
        "2",
        "$\\lg 25+\\lg 4=\\lg 100=2$。",
        "easy",
        ["MATH-G1-FUNC-203"],
    ),
    _q(
        "设 $a=\\log_{2}3$，$b=\\log_{\\frac{1}{2}}3$，$c=\\sqrt{3}$，则 $a,b,c$ 的大小关系是",
        "choice",
        {"A": "A. $c>a>b$", "B": "B. $a>c>b$", "C": "C. $a>b>c$", "D": "D. $c>b>a$"},
        "A",
        "$a=\\log_2 3\\in(1,2)$ 且 $3<4\\Rightarrow a<2$；$b=\\log_{\\frac{1}{2}}3=-\\log_2 3<0$；"
        "$c=\\sqrt{3}\\approx1.732$，$a=\\log_2 3\\approx1.585$，故 $c>a>b$。",
        "medium",
        ["MATH-G1-FUNC-204"],
    ),
    _q(
        "已知函数 $f(x)=x^2-2ax+1$。"
        "(1) 当 $a=0$ 时，求 $f(x)$ 在区间 $[1,3]$ 上的最小值；"
        "(2) 求 $f(x)$ 在区间 $[1,3]$ 上的最小值 $g(a)$ 的表达式。",
        "solution",
        None,
        "(1) $2$；(2) $g(a)=\\begin{cases}2-2a,&a\\le 1\\\\1-a^2,&1<a<3\\\\10-6a,&a\\ge 3\\end{cases}$",
        "(1) $a=0$ 时 $f(x)=x^2+1$ 在 $[1,3]$ 上单调递增，最小值 $f(1)=2$。"
        "(2) 对称轴 $x=a$：$a\\le 1$ 时最小值 $f(1)=2-2a$；$1<a<3$ 时最小值 $f(a)=1-a^2$；"
        "$a\\ge 3$ 时最小值 $f(3)=10-6a$。",
        "hard",
        ["MATH-G1-FUNC-102"],
    ),
    # ---------- 三角（6） ----------
    _q(
        "$\\sin\\frac{5\\pi}{6}$ 的值是",
        "choice",
        {"A": "A. $\\frac{1}{2}$", "B": "B. $-\\frac{1}{2}$", "C": "C. $\\frac{\\sqrt{3}}{2}$", "D": "D. $-\\frac{\\sqrt{3}}{2}$"},
        "A",
        "$\\sin\\frac{5\\pi}{6}=\\sin\\left(\\pi-\\frac{\\pi}{6}\\right)=\\sin\\frac{\\pi}{6}=\\frac{1}{2}$。",
        "easy",
        ["MATH-G1-TRIG-102"],
    ),
    _q(
        "已知 $\\sin\\alpha=\\frac{3}{5}$，且 $\\alpha\\in\\left(\\frac{\\pi}{2},\\pi\\right)$，则 $\\cos\\alpha=$ ____",
        "blank",
        None,
        "-4/5",
        "$\\cos\\alpha=\\pm\\sqrt{1-\\frac{9}{25}}=\\pm\\frac{4}{5}$；第二象限余弦为负，故 $\\cos\\alpha=-\\frac{4}{5}$。",
        "medium",
        ["MATH-G1-TRIG-103"],
    ),
    _q(
        "函数 $y=\\sin\\left(2x+\\frac{\\pi}{3}\\right)$ 的最小正周期是",
        "choice",
        {"A": "A. $\\pi$", "B": "B. $2\\pi$", "C": "C. $\\frac{\\pi}{2}$", "D": "D. $4\\pi$"},
        "A",
        "$T=\\frac{2\\pi}{|\\omega|}=\\frac{2\\pi}{2}=\\pi$。",
        "easy",
        ["MATH-G1-TRIG-104"],
    ),
    _q(
        "计算：$\\sin 15^{\\circ}\\cos 15^{\\circ}=$ ____",
        "blank",
        None,
        "1/4",
        "$\\sin 15^{\\circ}\\cos 15^{\\circ}=\\frac{1}{2}\\sin 30^{\\circ}=\\frac{1}{2}\\times\\frac{1}{2}=\\frac{1}{4}$。",
        "medium",
        ["MATH-G1-TRIG-105"],
    ),
    _q(
        "在 $\\triangle ABC$ 中，$a=1$，$b=1$，$C=120^{\\circ}$，则 $c=$",
        "choice",
        {"A": "A. $\\sqrt{3}$", "B": "B. $\\sqrt{2}$", "C": "C. $2$", "D": "D. $3$"},
        "A",
        "余弦定理 $c^2=a^2+b^2-2ab\\cos C=1+1-2\\times(-\\frac{1}{2})=3$，故 $c=\\sqrt{3}$。",
        "medium",
        ["MATH-G1-TRIG-106"],
    ),
    _q(
        "在 $\\triangle ABC$ 中，角 $A,B,C$ 的对边分别为 $a,b,c$，已知 $b=2$，$c=3$，$A=60^{\\circ}$。"
        "(1) 求 $a$；(2) 求 $\\triangle ABC$ 的面积。",
        "solution",
        None,
        "(1) $a=\\sqrt{7}$；(2) $S=\\frac{3\\sqrt{3}}{2}$",
        "(1) 余弦定理 $a^2=b^2+c^2-2bc\\cos A=4+9-12\\times\\frac{1}{2}=7$，$a=\\sqrt{7}$。"
        "(2) $S=\\frac{1}{2}bc\\sin A=\\frac{1}{2}\\times2\\times3\\times\\frac{\\sqrt{3}}{2}=\\frac{3\\sqrt{3}}{2}$。",
        "hard",
        ["MATH-G1-TRIG-106"],
    ),
    # ---------- 数列（4） ----------
    _q(
        "等差数列 $\\{a_n\\}$ 中，$a_1=2$，公差 $d=3$，则 $a_5=$",
        "choice",
        {"A": "A. $14$", "B": "B. $11$", "C": "C. $17$", "D": "D. $20$"},
        "A",
        "$a_5=a_1+4d=2+12=14$。",
        "easy",
        ["MATH-G2-SEQ-101"],
    ),
    _q(
        "等比数列 $\\{a_n\\}$ 中，$a_1=1$，公比 $q=2$，则前 4 项和 $S_4=$ ____",
        "blank",
        None,
        "15",
        "$S_4=\\frac{1\\times(1-2^4)}{1-2}=15$（即 $1+2+4+8=15$）。",
        "easy",
        ["MATH-G2-SEQ-102"],
    ),
    _q(
        "等差数列 $\\{a_n\\}$ 中，$a_3=5$，$a_7=13$，则前 10 项和 $S_{10}=$",
        "choice",
        {"A": "A. $100$", "B": "B. $90$", "C": "C. $110$", "D": "D. $120$"},
        "A",
        "$4d=a_7-a_3=8\\Rightarrow d=2$，$a_1=1$；$S_{10}=10\\times1+\\frac{10\\times9}{2}\\times2=100$。",
        "medium",
        ["MATH-G2-SEQ-101"],
    ),
    _q(
        "已知数列 $\\{a_n\\}$ 的通项公式为 $a_n=\\frac{1}{n(n+1)}$，其前 $n$ 项和为 $S_n$。"
        "(1) 求 $S_n$；(2) 证明：$S_n<1$。",
        "solution",
        None,
        "(1) $S_n=\\frac{n}{n+1}$；(2) 由 $\\frac{n}{n+1}=1-\\frac{1}{n+1}<1$ 得证",
        "(1) 裂项 $a_n=\\frac{1}{n}-\\frac{1}{n+1}$，故 "
        "$S_n=\\left(1-\\frac{1}{2}\\right)+\\left(\\frac{1}{2}-\\frac{1}{3}\\right)+\\cdots+"
        "\\left(\\frac{1}{n}-\\frac{1}{n+1}\\right)=1-\\frac{1}{n+1}=\\frac{n}{n+1}$。"
        "(2) $\\frac{1}{n+1}>0$，故 $S_n=1-\\frac{1}{n+1}<1$。",
        "medium",
        ["MATH-G2-SEQ-103"],
    ),
    # ---------- 立体几何（3） ----------
    _q(
        "棱长为 $2$ 的正方体的外接球半径是",
        "choice",
        {"A": "A. $\\sqrt{3}$", "B": "B. $2$", "C": "C. $2\\sqrt{3}$", "D": "D. $\\frac{\\sqrt{6}}{2}$"},
        "A",
        "体对角线 $2\\sqrt{3}$ 为外接球直径，半径 $R=\\sqrt{3}$。",
        "easy",
        ["MATH-G2-SOLID-101"],
    ),
    _q(
        "圆锥的底面半径为 $1$，母线长为 $2$，则其侧面积为 ____",
        "blank",
        None,
        "2π",
        "侧面积 $S=\\pi r l=\\pi\\times1\\times2=2\\pi$。",
        "easy",
        ["MATH-G2-SOLID-101"],
    ),
    _q(
        "设 $m,n$ 是两条不同的直线，$\\alpha$ 是一个平面，则下列命题正确的是",
        "choice",
        {
            "A": "A. 若 $m\\parallel\\alpha$，$n\\parallel\\alpha$，则 $m\\parallel n$",
            "B": "B. 若 $m\\perp\\alpha$，$n\\perp\\alpha$，则 $m\\parallel n$",
            "C": "C. 若 $m\\parallel\\alpha$，$m\\parallel n$，则 $n\\parallel\\alpha$",
            "D": "D. 若 $m\\perp\\alpha$，$m\\perp n$，则 $n\\parallel\\alpha$",
        },
        "B",
        "垂直于同一平面的两条直线平行，B 正确；A 中 $m,n$ 可相交或异面；"
        "C、D 中 $n$ 可能在平面 $\\alpha$ 内。",
        "medium",
        ["MATH-G2-SOLID-102"],
    ),
    # ---------- 解析几何（5） ----------
    _q(
        "过点 $(1,2)$ 且斜率为 $3$ 的直线方程是",
        "choice",
        {"A": "A. $3x-y-1=0$", "B": "B. $3x-y+1=0$", "C": "C. $x-3y+5=0$", "D": "D. $3x+y-5=0$"},
        "A",
        "点斜式 $y-2=3(x-1)$，即 $3x-y-1=0$。",
        "easy",
        ["MATH-G2-AGEO-101"],
    ),
    _q(
        "圆 $x^2+y^2-2x+4y=0$ 的半径为 ____",
        "blank",
        None,
        "√5",
        "配方得 $(x-1)^2+(y+2)^2=5$，半径 $r=\\sqrt{5}$。",
        "medium",
        ["MATH-G2-AGEO-102"],
    ),
    _q(
        "椭圆 $\\frac{x^2}{25}+\\frac{y^2}{16}=1$ 的焦距是",
        "choice",
        {"A": "A. $6$", "B": "B. $3$", "C": "C. $8$", "D": "D. $10$"},
        "A",
        "$c^2=25-16=9$，$c=3$，焦距 $2c=6$。",
        "medium",
        ["MATH-G2-AGEO-103"],
    ),
    _q(
        "双曲线 $\\frac{x^2}{9}-\\frac{y^2}{16}=1$ 的渐近线方程是",
        "choice",
        {
            "A": "A. $y=\\pm\\frac{4}{3}x$",
            "B": "B. $y=\\pm\\frac{3}{4}x$",
            "C": "C. $y=\\pm\\frac{16}{9}x$",
            "D": "D. $y=\\pm\\frac{9}{16}x$",
        },
        "A",
        "$a=3$，$b=4$，渐近线 $y=\\pm\\frac{b}{a}x=\\pm\\frac{4}{3}x$。",
        "medium",
        ["MATH-G2-AGEO-104"],
    ),
    _q(
        "已知椭圆 $C:\\frac{x^2}{4}+\\frac{y^2}{3}=1$。"
        "(1) 求 $C$ 的离心率；(2) 求 $C$ 在点 $P\\left(1,\\frac{3}{2}\\right)$ 处的切线方程。",
        "solution",
        None,
        "(1) $e=\\frac{1}{2}$；(2) $x+2y-4=0$",
        "(1) $a^2=4$，$b^2=3$，$c=\\sqrt{a^2-b^2}=1$，$e=\\frac{c}{a}=\\frac{1}{2}$。"
        "(2) 验证 $\\frac{1}{4}+\\frac{9/4}{3}=1$，$P$ 在椭圆上；"
        "切线 $\\frac{x\\cdot1}{4}+\\frac{y\\cdot\\frac{3}{2}}{3}=1$，即 $x+2y-4=0$。",
        "hard",
        ["MATH-G2-AGEO-103"],
    ),
    # ---------- 导数（4） ----------
    _q(
        "已知 $f(x)=x^3-2x$，则 $f'(1)=$",
        "choice",
        {"A": "A. $1$", "B": "B. $3$", "C": "C. $-1$", "D": "D. $0$"},
        "A",
        "$f'(x)=3x^2-2$，$f'(1)=3-2=1$。",
        "easy",
        ["MATH-G2-DERIV-102"],
    ),
    _q(
        "曲线 $y=x^2$ 在点 $(1,1)$ 处的切线方程为 ____",
        "blank",
        None,
        "y=2x-1",
        "$y'=2x$，切线斜率 $k=2$，切线 $y-1=2(x-1)$，即 $y=2x-1$。",
        "easy",
        ["MATH-G2-DERIV-105"],
    ),
    _q(
        "函数 $f(x)=x^3-3x$ 的单调递减区间是",
        "choice",
        {"A": "A. $(-1,1)$", "B": "B. $(-\\infty,-1)$", "C": "C. $(1,+\\infty)$", "D": "D. $(-1,+\\infty)$"},
        "A",
        "$f'(x)=3x^2-3=3(x-1)(x+1)<0 \\iff -1<x<1$。",
        "medium",
        ["MATH-G2-DERIV-103"],
    ),
    _q(
        "已知函数 $f(x)=x^3-3x^2+2$。"
        "(1) 求 $f(x)$ 的单调区间与极值；(2) 求 $f(x)$ 在区间 $[-1,4]$ 上的最大值与最小值。",
        "solution",
        None,
        "(1) 增区间 $(-\\infty,0)$、$(2,+\\infty)$，减区间 $(0,2)$；极大值 $f(0)=2$，极小值 $f(2)=-2$；"
        "(2) 最大值 $18$，最小值 $-2$",
        "(1) $f'(x)=3x^2-6x=3x(x-2)$：$x<0$ 或 $x>2$ 时 $f'>0$，$0<x<2$ 时 $f'<0$；"
        "极大值 $f(0)=2$，极小值 $f(2)=8-12+2=-2$。"
        "(2) 比较 $f(-1)=-2$、$f(0)=2$、$f(2)=-2$、$f(4)=64-48+2=18$，"
        "最大值为 $18$，最小值为 $-2$。",
        "hard",
        ["MATH-G2-DERIV-104"],
    ),
    # ---------- 概率（4） ----------
    _q(
        "同时掷两枚质地均匀的骰子，点数之和为 $7$ 的概率是",
        "choice",
        {"A": "A. $\\frac{1}{6}$", "B": "B. $\\frac{1}{9}$", "C": "C. $\\frac{1}{12}$", "D": "D. $\\frac{5}{36}$"},
        "A",
        "和为 7 的有 $(1,6),(2,5),(3,4),(4,3),(5,2),(6,1)$ 共 6 种，$P=\\frac{6}{36}=\\frac{1}{6}$。",
        "easy",
        ["MATH-G2-PROB-101"],
    ),
    _q(
        "已知事件 $A,B$ 相互独立，$P(A)=0.5$，$P(B)=0.4$，则 $P(A\\cup B)=$ ____",
        "blank",
        None,
        "0.7",
        "$P(AB)=0.5\\times0.4=0.2$，$P(A\\cup B)=0.5+0.4-0.2=0.7$。",
        "medium",
        ["MATH-G2-PROB-102"],
    ),
    _q(
        "数据 $1,2,3,4,5$ 的方差是",
        "choice",
        {"A": "A. $2$", "B": "B. $\\sqrt{2}$", "C": "C. $2.5$", "D": "D. $10$"},
        "A",
        "平均数 $3$，方差 $s^2=\\frac{4+1+0+1+4}{5}=2$。",
        "easy",
        ["MATH-G2-PROB-104"],
    ),
    _q(
        "袋中有 3 个红球和 2 个白球，从中不放回地依次取出 2 个球，记 $X$ 为取到红球的个数。"
        "(1) 求 $X$ 的分布列；(2) 求 $E(X)$。",
        "solution",
        None,
        "(1) $P(X=0)=\\frac{1}{10}$，$P(X=1)=\\frac{3}{5}$，$P(X=2)=\\frac{3}{10}$；(2) $E(X)=\\frac{6}{5}$",
        "(1) $P(X=0)=\\frac{C_2^2}{C_5^2}=\\frac{1}{10}$；$P(X=1)=\\frac{C_3^1 C_2^1}{C_5^2}=\\frac{6}{10}=\\frac{3}{5}$；"
        "$P(X=2)=\\frac{C_3^2}{C_5^2}=\\frac{3}{10}$。"
        "(2) $E(X)=0\\times\\frac{1}{10}+1\\times\\frac{3}{5}+2\\times\\frac{3}{10}=\\frac{6}{5}$。",
        "hard",
        ["MATH-G2-PROB-103"],
    ),
    # ---------- 集合/不等式/复数/向量（4） ----------
    _q(
        "已知集合 $A=\\{1,2,3\\}$，$B=\\{2,3,4\\}$，则 $A\\cap B=$",
        "choice",
        {"A": "A. $\\{2,3\\}$", "B": "B. $\\{1,2,3,4\\}$", "C": "C. $\\{1,4\\}$", "D": "D. $\\varnothing$"},
        "A",
        "公共元素为 $2,3$，$A\\cap B=\\{2,3\\}$。",
        "easy",
        ["MATH-G1-SET-101"],
    ),
    _q(
        "当 $x>0$ 时，$x+\\frac{1}{x}$ 的最小值是",
        "choice",
        {"A": "A. $2$", "B": "B. $1$", "C": "C. $\\sqrt{2}$", "D": "D. $4$"},
        "A",
        "基本不等式 $x+\\frac{1}{x}\\ge 2\\sqrt{x\\cdot\\frac{1}{x}}=2$，当且仅当 $x=1$ 时取等号。",
        "easy",
        ["MATH-G1-INEQ-102"],
    ),
    _q(
        "复数 $(1+i)^2=$",
        "choice",
        {"A": "A. $2i$", "B": "B. $2$", "C": "C. $2+2i$", "D": "D. $0$"},
        "A",
        "$(1+i)^2=1+2i+i^2=1+2i-1=2i$。",
        "easy",
        ["MATH-G2-CPLX-101"],
    ),
    _q(
        "已知向量 $\\vec{a}=(1,2)$，$\\vec{b}=(-2,\\lambda)$，且 $\\vec{a}\\perp\\vec{b}$，则 $\\lambda=$ ____",
        "blank",
        None,
        "1",
        "$\\vec{a}\\cdot\\vec{b}=-2+2\\lambda=0$，解得 $\\lambda=1$。",
        "medium",
        ["MATH-G1-VEC-102"],
    ),
]


async def run() -> int:
    await init_db()
    async with async_session_factory() as db:
        # kp_codes 存在性提示（只告警不拦截）
        all_codes = list({c for q in DEMO_QUESTIONS for c in q["kp_codes"]})
        found = set(
            (
                await db.execute(
                    select(KnowledgePoint.code).where(KnowledgePoint.code.in_(all_codes))
                )
            ).scalars().all()
        )
        unknown = [c for c in all_codes if c not in found]
        if unknown:
            print(f"[WARN] {len(unknown)} 个 kp_code 不在 knowledge_points（不影响入库）: {unknown}")

        # hash 幂等去重
        items = [{**q, "hash": stem_hash(q["stem"]), "embedding": None} for q in DEMO_QUESTIONS]
        existing = set(
            (
                await db.execute(
                    select(QuestionBank.hash).where(
                        QuestionBank.hash.in_([item["hash"] for item in items])
                    )
                )
            ).scalars().all()
        )
        fresh = [item for item in items if item["hash"] not in existing]
        skipped = len(items) - len(fresh)
        if not fresh:
            print(f"[DONE] 全部 {skipped} 道示例题已存在，无需入库")
            return 0

        # embedding best-effort（复用导入脚本实现；失败落 NULL 不阻塞）
        embedded = await _embed_best_effort(db, fresh)

        for item in fresh:
            db.add(
                QuestionBank(
                    stem=item["stem"],
                    q_type=item["q_type"],
                    options=item["options"],
                    answer=item["answer"],
                    analysis=item["analysis"],
                    difficulty=item["difficulty"],
                    kp_codes=item["kp_codes"],
                    source=item["source"],
                    year=item["year"],
                    is_real_exam=item["is_real_exam"],
                    embedding=item["embedding"],
                    hash=item["hash"],
                )
            )
        await db.commit()
        print(
            f"[DONE] 示例题入库 {len(fresh)} 道（embedding 成功 {embedded} 条，跳过已存在 {skipped} 道）"
        )
        return 0


def main() -> None:
    rc = asyncio.run(run())
    sys.exit(rc)


if __name__ == "__main__":
    main()
