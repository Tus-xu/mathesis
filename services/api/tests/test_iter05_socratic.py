"""迭代05 引导式解题质量修复单元测试（阶段 2）

覆盖审计项：
- B-P1-17 防泄题短终答盲区补偿（find_leak）
- C-P2-9 文本型多值终答比对（_textual_answer_equal）
- C-P2-13 判答快速通道变量门槛
- C-P2-10 难度交叉校准（步骤数锚点）
- C-P2-11 负向情绪措辞触发安抚
"""

import re

from app.skills.socratic_solver import parsing
from app.skills.socratic_solver.main import SocraticSolverExecutor

# ==================== B-P1-17 防泄题短终答盲区 ====================


class TestShortAnswerLeakGuard:
    def test_short_answer_with_cue_detected(self):
        """1 字符终答 + 揭示语境词 → 检出泄露（原实现盲区）"""
        plan = {"final_answer": "2", "steps": []}
        assert parsing.find_leak("所以答案是 2", plan, 1) == "final_answer"
        assert parsing.find_leak("因此结果为 8", {"final_answer": "8", "steps": []}, 1) == "final_answer"

    def test_short_answer_step_number_not_false_positive(self):
        """步骤序号无揭示语境词 → 不判泄露（防误报）"""
        plan = {"final_answer": "2", "steps": []}
        assert parsing.find_leak("我们来看第 2 步", plan, 1) is None
        assert parsing.find_leak("先算 2 加 3", plan, 1) is None

    def test_two_char_answer_detected(self):
        """2 字符终答（如 55）+ 揭示语境 → 检出"""
        plan = {"final_answer": "55", "steps": []}
        assert parsing.find_leak("可得到 55", plan, 1) == "final_answer"

    def test_long_answer_still_guarded(self):
        """长终答原有检测不受影响"""
        plan = {"final_answer": "x=\\frac{1}{2}", "steps": []}
        assert parsing.find_leak("答案是 $x=\\frac{1}{2}$", plan, 1) == "final_answer"


# ==================== C-P2-9 文本型多值终答比对 ====================
# M2 重构：_textual_answer_equal 随双解交叉验证一并删除（单路深度推理不再需要
# 两条解答的终答一致性比对），对应测试移除。判答快速通道的多值比对由
# check_equivalence / find_leak 路径继续覆盖。


# ==================== C-P2-13 判答快速通道门槛 ====================


class TestJudgeFastpathGate:
    def test_variable_intersection_required(self):
        """含变量式：无变量交集 → 门槛不通过"""
        solver = SocraticSolverExecutor()
        toks_s = set(re.findall(r"[A-Za-z]+", "y+1")) - solver._JUDGE_FN_TOKENS
        toks_t = set(re.findall(r"[A-Za-z]+", "x=2")) - solver._JUDGE_FN_TOKENS
        gate_ok = (not toks_s and not toks_t) or bool(toks_s & toks_t)
        assert not gate_ok  # y vs x 无交集 → 跳过快速通道

    def test_pure_numeric_allowed(self):
        """纯数值式：双方无字母 → 放行"""
        solver = SocraticSolverExecutor()
        toks_s = set(re.findall(r"[A-Za-z]+", "1/2")) - solver._JUDGE_FN_TOKENS
        toks_t = set(re.findall(r"[A-Za-z]+", "0.5")) - solver._JUDGE_FN_TOKENS
        gate_ok = (not toks_s and not toks_t) or bool(toks_s & toks_t)
        assert gate_ok

    def test_shared_variable_allowed(self):
        """含共同变量 x → 放行"""
        solver = SocraticSolverExecutor()
        toks_s = set(re.findall(r"[A-Za-z]+", "x+1")) - solver._JUDGE_FN_TOKENS
        toks_t = set(re.findall(r"[A-Za-z]+", "x=2")) - solver._JUDGE_FN_TOKENS
        gate_ok = (not toks_s and not toks_t) or bool(toks_s & toks_t)
        assert gate_ok

    def test_function_names_not_counted(self):
        """函数名（sin/cos）不计为变量"""
        solver = SocraticSolverExecutor()
        toks = set(re.findall(r"[A-Za-z]+", "sin(x)")) - solver._JUDGE_FN_TOKENS
        assert toks == {"x"}


# ==================== C-P2-10 / C-P2-11 常量与正则存在性 ====================


class TestCalibrationAndEmotion:
    def test_negative_emotion_regex(self):
        """负向情绪措辞正则可命中常见受挫表达"""
        from app.skills.socratic_solver import main as sm

        assert sm._NEGATIVE_EMOTION_RE.search("这道题太难了")
        assert sm._NEGATIVE_EMOTION_RE.search("我完全没思路")
        assert not sm._NEGATIVE_EMOTION_RE.search("我试试用公式法")
