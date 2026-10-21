"""知识库种子数据脚本

插入 5 个知识点 + 1 个教材文档 + 15 个切片（含 embedding 占位）。
用于验证 RAG 三路召回、RRF 融合、拒答闸门。

用法：
    cd services/api
    python -m scripts.seed_knowledge
"""

import asyncio
import sys
import uuid
from pathlib import Path

# 确保 app 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.models.chunk import Chunk
from app.models.database import async_session_factory, init_db
from app.models.knowledge_doc import KnowledgeDoc
from app.models.knowledge_point import KnowledgePoint

# ========== 知识点数据 ==========

KNOWLEDGE_POINTS = [
    {
        "code": "MATH-G1-FUNC-001",
        "name": "函数的概念与基本初等函数",
        "grade": "高一",
        "aliases": ["函数", "一次函数", "二次函数", "幂函数", "function"],
    },
    {
        "code": "MATH-G1-FUNC-002",
        "name": "指数函数与对数函数",
        "grade": "高一",
        "aliases": ["指数函数", "对数函数", "指数", "对数", "log", "ln"],
    },
    {
        "code": "MATH-G1-TRIG-001",
        "name": "三角函数",
        "grade": "高一",
        "aliases": ["三角函数", "正弦", "余弦", "正切", "sin", "cos", "tan"],
    },
    {
        "code": "MATH-G2-DERIV-001",
        "name": "导数及其应用",
        "grade": "高二",
        "aliases": ["导数", "微分", "求导", "极值", "最值", "derivative"],
    },
    {
        "code": "MATH-G2-PROB-001",
        "name": "概率与统计",
        "grade": "高二",
        "aliases": ["概率", "统计", "排列组合", "二项分布", "正态分布"],
    },
]


# ========== 教材文档 ==========

DOC_TITLE = "人教版高中数学必修第一册（2019版）"
DOC_SOURCE_TYPE = "textbook"


# ========== 切片数据 ==========

CHUNKS = [
    {
        "content": (
            "函数的概念：设A、B是非空的数集，如果按照某种确定的对应关系f，"
            "使对于集合A中的任意一个数x，在集合B中都有唯一确定的数f(x)和它对应，"
            "那么就称f:A→B为从集合A到集合B的一个函数，记作y=f(x)，x∈A。"
            "其中x叫做自变量，x的取值范围A叫做函数的定义域，"
            "与x的值相对应的y值叫做函数值，函数值的集合{f(x)|x∈A}叫做函数的值域。"
        ),
        "kp_refs": ["MATH-G1-FUNC-001"],
        "chunk_index": 0,
    },
    {
        "content": (
            "二次函数的一般形式为f(x)=ax²+bx+c（a≠0）。"
            "其图象是一条抛物线，顶点坐标为(-b/2a, (4ac-b²)/4a)。"
            "当a>0时，抛物线开口向上，函数在x=-b/2a处取得最小值；"
            "当a<0时，抛物线开口向下，函数在x=-b/2a处取得最大值。"
            "判别式Δ=b²-4ac决定了函数零点（与x轴交点）的个数。"
        ),
        "kp_refs": ["MATH-G1-FUNC-001"],
        "chunk_index": 1,
    },
    {
        "content": (
            "指数函数的定义：一般地，函数y=aˣ（a>0，且a≠1）叫做指数函数，"
            "其中x是自变量，函数的定义域是R。"
            "指数函数的图象特征：当a>1时，函数单调递增；当0<a<1时，函数单调递减。"
            "指数函数恒过点(0,1)，即a⁰=1。值域为(0,+∞)，图象始终在x轴上方。"
        ),
        "kp_refs": ["MATH-G1-FUNC-002"],
        "chunk_index": 2,
    },
    {
        "content": (
            "对数函数的定义：一般地，函数y=log_a(x)（a>0，且a≠1）叫做对数函数，"
            "其中x是自变量，定义域为(0,+∞)。"
            "对数函数是指数函数的反函数。当a>1时单调递增，当0<a<1时单调递减。"
            "对数函数恒过点(1,0)，即log_a(1)=0。"
            "常用对数：lg(x)=log_10(x)；自然对数：ln(x)=log_e(x)，其中e≈2.71828。"
        ),
        "kp_refs": ["MATH-G1-FUNC-002"],
        "chunk_index": 3,
    },
    {
        "content": (
            "对数运算法则（a>0，a≠1，M>0，N>0）：\n"
            "1. log_a(MN) = log_a(M) + log_a(N)（积的对数等于对数的和）\n"
            "2. log_a(M/N) = log_a(M) - log_a(N)（商的对数等于对数的差）\n"
            "3. log_a(Mⁿ) = n·log_a(M)（幂的对数等于指数乘以对数）\n"
            "4. 换底公式：log_a(b) = ln(b)/ln(a) = lg(b)/lg(a)"
        ),
        "kp_refs": ["MATH-G1-FUNC-002"],
        "chunk_index": 4,
    },
    {
        "content": (
            "三角函数的定义：设角α的终边与单位圆交于点P(x,y)，则：\n"
            "sinα = y（正弦）\n"
            "cosα = x（余弦）\n"
            "tanα = y/x（正切，x≠0）\n"
            "基本关系：sin²α + cos²α = 1（勾股关系）\n"
            "tanα = sinα/cosα（商的关系）\n"
            "诱导公式口诀：奇变偶不变，符号看象限。"
        ),
        "kp_refs": ["MATH-G1-TRIG-001"],
        "chunk_index": 5,
    },
    {
        "content": (
            "正弦函数y=sin(x)的性质：\n"
            "1. 定义域：R\n"
            "2. 值域：[-1, 1]\n"
            "3. 周期：T=2π\n"
            "4. 奇函数：sin(-x)=-sin(x)\n"
            "5. 单调递增区间：[-π/2+2kπ, π/2+2kπ]，k∈Z\n"
            "6. 单调递减区间：[π/2+2kπ, 3π/2+2kπ]，k∈Z\n"
            "7. 对称轴：x=π/2+kπ；对称中心：(kπ, 0)"
        ),
        "kp_refs": ["MATH-G1-TRIG-001"],
        "chunk_index": 6,
    },
    {
        "content": (
            "余弦函数y=cos(x)的性质：\n"
            "1. 定义域：R\n"
            "2. 值域：[-1, 1]\n"
            "3. 周期：T=2π\n"
            "4. 偶函数：cos(-x)=cos(x)\n"
            "5. 单调递增区间：[-π+2kπ, 2kπ]，k∈Z\n"
            "6. 单调递减区间：[2kπ, π+2kπ]，k∈Z\n"
            "7. 对称轴：x=kπ；对称中心：(π/2+kπ, 0)"
        ),
        "kp_refs": ["MATH-G1-TRIG-001"],
        "chunk_index": 7,
    },
    {
        "content": (
            "导数的概念：设函数y=f(x)在点x₀的某邻域内有定义，"
            "当自变量x在x₀处取得增量Δx时，若极限\n"
            "f'(x₀) = lim[Δx→0] [f(x₀+Δx)-f(x₀)]/Δx\n"
            "存在，则称f(x)在x₀处可导，该极限值称为f(x)在x₀处的导数。"
            "导数的几何意义：f'(x₀)是曲线y=f(x)在点(x₀,f(x₀))处切线的斜率。"
        ),
        "kp_refs": ["MATH-G2-DERIV-001"],
        "chunk_index": 8,
    },
    {
        "content": (
            "基本求导公式：\n"
            "1. (xⁿ)' = nxⁿ⁻¹\n"
            "2. (sin x)' = cos x\n"
            "3. (cos x)' = -sin x\n"
            "4. (eˣ)' = eˣ\n"
            "5. (aˣ)' = aˣ·ln(a)\n"
            "6. (ln x)' = 1/x\n"
            "7. (log_a x)' = 1/(x·ln a)\n"
            "运算法则：(f±g)'=f'±g'；(fg)'=f'g+fg'；(f/g)'=(f'g-fg')/g²"
        ),
        "kp_refs": ["MATH-G2-DERIV-001"],
        "chunk_index": 9,
    },
    {
        "content": (
            "导数的应用——求极值和最值：\n"
            "1. 求f'(x)=0的根（驻点）\n"
            "2. 判断驻点两侧f'(x)的符号变化：\n"
            "   - 由正变负→极大值点\n"
            "   - 由负变正→极小值点\n"
            "3. 求闭区间[a,b]上的最值：比较极值点和端点的函数值\n"
            "4. 若f'(x)>0在区间上恒成立，则f(x)在该区间单调递增\n"
            "5. 若f'(x)<0在区间上恒成立，则f(x)在该区间单调递减"
        ),
        "kp_refs": ["MATH-G2-DERIV-001"],
        "chunk_index": 10,
    },
    {
        "content": (
            "概率的基本概念：\n"
            "1. 随机试验：在相同条件下可重复进行，结果不止一个，试验前不能确定哪个结果会出现。\n"
            "2. 样本空间Ω：随机试验所有可能结果的集合。\n"
            "3. 事件：样本空间的子集。\n"
            "4. 古典概型：P(A) = A包含的基本事件数 / 基本事件总数。\n"
            "5. 概率的加法公式：P(A∪B) = P(A) + P(B) - P(A∩B)。\n"
            "6. 若A、B互斥：P(A∪B) = P(A) + P(B)。"
        ),
        "kp_refs": ["MATH-G2-PROB-001"],
        "chunk_index": 11,
    },
    {
        "content": (
            "排列与组合：\n"
            "排列数：A(n,m) = n!/(n-m)!（从n个不同元素中取出m个排成一列）\n"
            "组合数：C(n,m) = n!/[m!(n-m)!]（从n个不同元素中取出m个组成一组）\n"
            "组合数性质：C(n,m) = C(n,n-m)；C(n,m) = C(n-1,m-1) + C(n-1,m)\n"
            "二项式定理：(a+b)ⁿ = ΣC(n,k)·aⁿ⁻ᵏ·bᵏ（k从0到n）"
        ),
        "kp_refs": ["MATH-G2-PROB-001"],
        "chunk_index": 12,
    },
    {
        "content": (
            "条件概率与独立事件：\n"
            "条件概率：P(B|A) = P(AB)/P(A)（在A已发生的条件下B发生的概率）\n"
            "乘法公式：P(AB) = P(A)·P(B|A) = P(B)·P(A|B)\n"
            "事件独立：若P(AB) = P(A)·P(B)，则A、B相互独立。\n"
            "独立与互斥的区别：互斥是A∩B=∅，独立是互不影响。"
        ),
        "kp_refs": ["MATH-G2-PROB-001"],
        "chunk_index": 13,
    },
    {
        "content": (
            "函数的单调性判定：\n"
            "定义法：设x₁<x₂，若f(x₁)<f(x₂)则f在区间上单调递增。\n"
            "导数法：若f'(x)>0则单调递增，若f'(x)<0则单调递减。\n"
            "复合函数单调性：同增异减。\n"
            "常见函数单调性：\n"
            "- y=x²在(-∞,0)递减，(0,+∞)递增\n"
            "- y=1/x在(-∞,0)和(0,+∞)上分别递减\n"
            "- y=√x在[0,+∞)上递增"
        ),
        "kp_refs": ["MATH-G1-FUNC-001"],
        "chunk_index": 14,
    },
]


async def seed():
    """执行种子数据插入"""
    print("=" * 60)
    print("知识库种子数据脚本")
    print("=" * 60)

    # 确保表存在
    await init_db()
    print("[OK] 数据库表已就绪")

    async with async_session_factory() as db:
        # 检查是否已有数据
        existing = await db.execute(select(KnowledgePoint).limit(1))
        if existing.scalar_one_or_none() is not None:
            print("[SKIP] 知识点数据已存在，跳过插入")
            return

        # 1. 插入知识点
        kp_map: dict[str, uuid.UUID] = {}
        for kp_data in KNOWLEDGE_POINTS:
            kp = KnowledgePoint(
                code=kp_data["code"],
                name=kp_data["name"],
                grade=kp_data["grade"],
                aliases=kp_data["aliases"],
            )
            db.add(kp)
            await db.flush()
            kp_map[kp_data["code"]] = kp.id
            print(f"  [KP] {kp_data['code']} - {kp_data['name']}")

        # 2. 插入教材文档
        doc = KnowledgeDoc(
            title=DOC_TITLE,
            source_type=DOC_SOURCE_TYPE,
            status="active",
            meta_={"version": "2019", "publisher": "人民教育出版社"},
        )
        db.add(doc)
        await db.flush()
        print(f"  [DOC] {DOC_TITLE} (id={doc.id})")

        # 3. 插入切片
        for chunk_data in CHUNKS:
            kp_ids = [kp_map[code] for code in chunk_data["kp_refs"] if code in kp_map]
            chunk = Chunk(
                doc_id=doc.id,
                content=chunk_data["content"],
                kp_ids=kp_ids,
                chunk_index=chunk_data["chunk_index"],
                embedding=None,  # M1 阶段不生成向量，降级走 trgm+kp 两路
            )
            db.add(chunk)

        await db.flush()
        print(f"  [CHUNKS] 插入 {len(CHUNKS)} 个切片")

        await db.commit()

    print("\n[DONE] 种子数据插入完成！")
    print(f"  - 知识点: {len(KNOWLEDGE_POINTS)} 个")
    print("  - 文档: 1 个")
    print(f"  - 切片: {len(CHUNKS)} 个")
    print("\n提示: embedding 字段为空，RAG 将降级为 trgm+kp 两路召回。")


if __name__ == "__main__":
    asyncio.run(seed())
