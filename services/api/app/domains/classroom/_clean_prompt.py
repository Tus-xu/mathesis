# -*- coding: utf-8 -*-
"""
清理 Prompt 中针对 D1/D2/D3 的过拟合内容：
- 删掉 D3 具体建系模板 / D1 D2 具体题解模板
- 保留通用 schema（dihedral/conic/tangent_point/...字段契约）——格式规范本身是通用的
- 把特定题目模板替换成通用证明链方法论
然后做 import 检查。
"""
import pathlib
p = pathlib.Path(r'D:\math-arena\services\api\app\domains\classroom\stage_router.py')
text = p.read_text(encoding='utf-8')

# ========== 1. 清理：二面角契约里的 DCA / ACE 具体名示例（D3特化）改为通用名 ==========
old1 = '''  * **二面角 · 严格字段契约**（立体几何求二面角的页**必须**写 dihedral 断言，由后端用两个平面法向量独立计算核对）：
    ① 先在 plane_points 里定义两个半平面：例如 "DCA":["D","C","A"], "ACE":["A","C","E"]；
    ② 在 dihedral.plane1 / dihedral.plane2 中写这两个平面名；
    ③ dihedral.value 必须写你按 n1·n2/(|n1||n2|) 手算出来的余弦**绝对值**（通常取锐角），后端会用 coordinates 中的真实坐标重新计算；
    ④ **不要"凭感觉"写 1/2、√3/3**——必须用 coordinates 中的真实点坐标严格算。'''
new1 = '''  * **二面角 · 通用计算纪律**（立体几何求二面角的页**必须**写 dihedral 断言，后端会用两个平面法向量独立计算核对）：
    ① 先在 plane_points 里用题目中的**真实点名字**定义两个半平面：如 "PQR":["P","Q","R"], "PQS":["P","Q","S"]（棱是两点PQ，两个面各自第三个点不同）；
    ② 在 dihedral.plane1 / dihedral.plane2 中写相同的平面名；
    ③ dihedral.value 必须写你按 n1·n2/(|n1||n2|) 用 coordinates 里的点坐标手算出来的余弦**绝对值**（通常取锐角）；禁止"凭感觉写0.5或√3/3"；
    ④ 若棱不是水平/竖直方向，**不要用目测**——一律用三点叉乘法向量。'''
assert old1 in text, 'old1 not found!'
text = text.replace(old1, new1, 1)

# ========== 2. 删除：D3 立体几何具体证明链（含具体 A(2,0,0) 等坐标） ==========
# 找到整块：   * **D3 立体几何 · 证明链写作模板** ... dihedral.value = ... |cosθ| 。
import re
# 匹配从 * **D3 开始 到 下一个  * ** 之前或到 Step3 ... 结束 ... |cosθ| 。之后
pattern_D3_block = re.compile(
    r'  \* \*\*D3 立体几何 · 证明链写作模板\*\*[^*]*?'
    r'Step3 \(二面角 D-AC-E\)[^\n]*\*\*dihedral\.value 必须写此值（或等价 √3/3 ≈ 0\.5774，取 4 位小数）\*\*。\n',
    re.DOTALL)
assert pattern_D3_block.search(text), 'D3 block not found!'
# 替换为通用 立体几何通用三步法
generic_solid = '''  * **立体几何 · 通用三步证明链方法论**（任意立几题通用）：
    Step1 (建系)：**从题目条件严格推导坐标**。把"题目给出的线面垂直/面面垂直/平行四边形/边长/夹角"全部写进坐标表推导过程，禁止"默认正方形边长为2""随便令A在原点就完事"——坐标必须满足|AB|=题给值、∠BAD=题给夹角。推荐：
      - 有线面垂直/面面垂直 ⇒ 取公共棱为一轴，垂直向量沿另一轴；
      - 平行四边形：由 向量CD=BE，D=C+(E-B) 推导；
      - 棱锥顶点：由"侧棱相等/垂直底面"推出。
      建系完成后，**立即验算所有题给度量**：|AB|、∠ABC、线面点积=0、面内共线等，若不匹配**必须修正坐标**，不允许带着错误坐标进入后续。
    Step2 (证明)：线线垂直/线面垂直/线线平行一律用**向量点积=0 / 叉乘=0 / 方向向量共线**严格验证，禁止"由图可知""显然"。写出完整的向量计算过程。
    Step3 (度量计算)：
      - 点到平面距离：严格按 d=|n·(P-P0)|/|n|，其中 n=(B-A)×(C-A) 为平面ABC法向量，P0是面上任一点；
      - 二面角：求两个半平面各自的法向量n1,n2（都由棱上两点+面内第三点叉乘得到），cosθ=|n1·n2|/(|n1||n2|) 取绝对值写 dihedral.value；
      - 体积/面积：直接用向量标积/叉积计算。
    **所有数值必须由 coordinates 独立算出，严禁凭常识估算 0.5/√2/√3/√3/3。**'''
text, cnt = pattern_D3_block.subn(lambda _: generic_solid + '\n', text, count=1)
assert cnt == 1, f'D3 block replace failed: count={cnt}'

# ========== 3. 删除：解析几何 D1/D2 类的具体题解（金标准a-b、保守标准答案...）→ 通用解析几何方法论 ==========
pattern_D1D2_block = re.compile(
    r'  \* \*\*解析几何 \(D1/D2类\) · 证明链写作模板\*\*.*?'
    r'消参得 ξ²/c² \+ η²/\(bc/\(a\+c\)\)² = 1 ⇒ 内心轨迹是椭圆。\n',
    re.DOTALL)
m = pattern_D1D2_block.search(text)
assert m, 'D1/D2 block not found!'
generic_analytic = '''  * **解析几何 · 通用证明链方法论**（圆锥曲线/切线/轨迹/最值 通用）：
    **通用工具包（必须在每一步显式使用，禁止跳步）**：
    ① 参数化：椭圆上任意点 M 写成 (a·cosθ, b·sinθ)；双曲线上点写 (a·secθ, b·tanθ)；抛物线上点写 (t²/(2p), t)；消参求轨迹时写出坐标与参数关系再消去参数；
    ② 切线方程：用**隐函数求导**得到斜率 k = dy/dx = -(b²x)/(a²y)（椭圆 x²/a²+y²/b²=1）；或用"判别式 Δ=0"联立直线与椭圆得到切线条件 y=kx±√(a²k²+b²)；切点坐标通式 P(-a²k/D, b²/D), D=√(a²k²+b²)；
    ③ 距离/最值：点到直线距离 d=|Ax₀+By₀+C|/√(A²+B²) 代入**参数化的点**得到关于参数 θ 的表达式，用**辅助角公式** A·cosθ+B·sinθ = √(A²+B²)·sin(θ+φ) 求最大/最小值；或用**柯西不等式** (u₁²+u₂²)(v₁²+v₂²)≥(u₁v₁+u₂v₂)²；对 t 的有理函数 f(t)=N(t)/D(t) 求最值可直接求导 f'(t)=0 找极值点；
    ④ 焦点三角形/内心/重心/垂心：严格用标准公式——内心 I=(|F₁F₂|·M + |MF₂|·F₁ + |MF₁|·F₂)/perimeter（加权平均）；焦半径 |MF₁|=a±c·cosθ（椭圆）；
    ⑤ 轨迹问题：设动点 (ξ,η) 为条件中定义的点，写出 (ξ,η) 与原曲线上参数点 M 的关系，然后消参得到 ξ,η 满足的方程 f(ξ,η)=0；
    ⑥ **显式引用每一个定理名**："由隐函数求导得"、"由焦半径公式"、"由角平分线定理"、"由柯西不等式"、"由辅助角公式"、"由判别式 Δ=0"、"由加权平均公式"，严禁模糊使用"易得""可知"。
    **配套断言契约（通用）**：
    - 只要是圆锥曲线题，必须写 conic{a,b,c,theta} 给出基本参数（题目直接读出来的，不是编造）；
    - 涉及切线：写 tangent_point{k,x,y,latex}（斜率→切点通式代入即得）；
    - 涉及焦点三角形内心/重心：写 inner_point{x,y,latex} 或相应几何点；
    - 涉及距离/式子最大值：写 distance_max{value, latex, method}（method里写用的不等式/导数方法名）。
    **数值校验（通用）**：断言值（切点坐标/内心坐标/距离最大值）必须和 conic 参数一致——distance_max若你写 max=a-b 必须由你的推导过程（辅助角/柯西）严格得到，严禁"凭印象写 a-b 而不推导"。'''
text, cnt = pattern_D1D2_block.subn(lambda _: generic_analytic + '\n', text, count=1)
assert cnt == 1, f'D1D2 block replace failed: count={cnt}'

# ========== 4. 删除：D1中那段过拟合的"距离推导错误，请用正确推导...建议讲授时用 P=(a·cosφ,b·sinφ) ... max = a-b"的过拟合内容（如果还有残留） ==========
# （如果已经被上面的block删掉就不会有匹配，跳过）
suspicious = ['保守标准答案 a−b', 'D1距离 d(t)=√(a²t+b²)', '错误的推导！请用**正确的推导', 'f(t) 在 t∈(0,+∞) 单调递增']
for s in suspicious:
    if s in text:
        print(f'WARN: still has D1 overfitting text: {s}')

p.write_text(text, encoding='utf-8')
print('OK: Prompt cleaned (removed D1/D2/D3 specific templates, kept general schema + methodology).')
