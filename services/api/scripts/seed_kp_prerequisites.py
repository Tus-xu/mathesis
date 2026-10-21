"""知识点前置依赖种子脚本（M2 迭代16 第二批 W5）

向 kp_prerequisites 表扩充高中数学主干前置依赖边（ALEKS precedence
relation，追根溯源数据源）。第一批迁移 m2_010_growth_foundation 已种子
9 条（HS-02→DR-01 等），本脚本沿用同一套编码风格扩展 33 条，
覆盖：集合 → 函数 → 指数对数 → 三角 → 数列 → 导数 → 解析几何 →
立体几何 → 概率统计 的合理前置关系。

编码约定（与既有 9 条一致，两位字母前缀 + 两位数字序号）：
    SET-  集合与常用逻辑用语      HS-   函数        EL-  指数/对数
    TG-   三角                    VE-   向量        SL-  数列
    DR-   导数                    JH-   解析几何    SG-  立体几何
    PL-   概率统计

用法：
    cd services/api
    python -m scripts.seed_kp_prerequisites            # 幂等写入（ON CONFLICT DO NOTHING）
    python -m scripts.seed_kp_prerequisites --check    # 只打印将插入的边，不连数据库

重要说明：依赖边按 code 引用，kp_prerequisites 对 knowledge_points 无外键约束；
knowledge_points 表当前只有 MATH-G1-FUNC-001 等章节级码，可能不含本表细粒度码。
kp 表无对应码的边会静默存在，待题库标注对齐后生效，不影响写入。
幂等：ON CONFLICT DO NOTHING，可重复执行。
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 确保 app 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ========== 前置依赖边表（kp_code, prereq_code）：kp_code 的学习依赖 prereq_code ==========
# 已由 m2_010 迁移种子的 9 条不在此重复（ON CONFLICT DO NOTHING 亦可兼容，但保持清单干净）：
#   DR-01←HS-02  DR-02←DR-01  DR-02←HS-02  DR-03←DR-02
#   TG-03←TG-02  TG-02←TG-01  SL-02←SL-01  JH-02←VE-01  JH-02←HS-02
EDGES: list[tuple[str, str]] = [
    # ===== 集合 → 函数 =====
    ("HS-01", "SET-01"),  # 函数的概念与表示 ← 集合与常用逻辑用语
    ("HS-02", "HS-01"),   # 函数的基本性质（单调性/奇偶性）← 函数的概念与表示
    ("HS-03", "HS-02"),   # 基本初等函数（幂/二次/分式）← 函数的基本性质
    # ===== 指数对数 =====
    ("EL-01", "HS-02"),   # 指数与指数函数 ← 函数的基本性质
    ("EL-02", "EL-01"),   # 对数与对数函数 ← 指数与指数函数（互为反函数）
    # ===== 三角（TG-02←TG-01、TG-03←TG-02 已由迁移种子）=====
    ("TG-01", "EL-02"),   # 任意角/弧度制/三角函数定义 ← 对数与对数函数（必修一主线顺序）
    ("TG-01", "HS-03"),   # 三角函数定义 ← 基本初等函数
    ("TG-04", "TG-03"),   # 解三角形（正余弦定理）← 三角函数图象与性质
    ("TG-04", "TG-02"),   # 解三角形 ← 三角恒等变换
    ("TG-04", "VE-01"),   # 解三角形 ← 平面向量（向量法推导正余弦定理）
    # ===== 向量 =====
    ("VE-01", "TG-01"),   # 平面向量 ← 三角函数定义
    ("VE-02", "VE-01"),   # 空间向量 ← 平面向量
    ("VE-02", "SG-01"),   # 空间向量 ← 空间几何体
    # ===== 数列（SL-02←SL-01 已由迁移种子）=====
    ("SL-01", "HS-01"),   # 数列的概念 ← 函数的概念（数列是定义在正整数集上的函数）
    ("SL-02", "EL-01"),   # 等差等比数列 ← 指数与指数函数（等比 ↔ 指数模型）
    ("SL-03", "SL-02"),   # 数列求和与递推 ← 等差等比数列
    # ===== 导数（DR-01←HS-02、DR-02←DR-01/HS-02、DR-03←DR-02 已由迁移种子）=====
    ("DR-01", "EL-02"),   # 导数的概念与运算 ← 对数与对数函数（含指/对求导公式）
    ("DR-01", "TG-03"),   # 导数的概念与运算 ← 三角函数图象与性质（含正余弦求导公式）
    ("DR-03", "TG-03"),   # 导数的综合应用 ← 三角函数图象与性质
    ("DR-03", "EL-02"),   # 导数的综合应用 ← 对数与对数函数（含指对函数模型）
    # ===== 解析几何（JH-02←VE-01、JH-02←HS-02 已由迁移种子）=====
    ("JH-01", "HS-01"),   # 直线与圆 ← 函数的概念（方程与曲线思想）
    ("JH-02", "JH-01"),   # 圆锥曲线 ← 直线与圆
    ("JH-02", "DR-01"),   # 圆锥曲线 ← 导数的几何意义（切线问题）
    ("JH-03", "JH-02"),   # 直线与圆锥曲线位置关系 ← 圆锥曲线
    ("JH-03", "TG-02"),   # 直线与圆锥曲线位置关系 ← 三角恒等变换（参数方程化简）
    # ===== 立体几何 =====
    ("SG-01", "SET-01"),  # 空间几何体 ← 集合与常用逻辑用语（点线面的集合语言描述）
    ("SG-02", "SG-01"),   # 点线面位置关系 ← 空间几何体
    ("SG-03", "SG-02"),   # 空间向量法解立体几何 ← 点线面位置关系
    ("SG-03", "VE-02"),   # 空间向量法解立体几何 ← 空间向量
    # ===== 概率统计 =====
    ("PL-01", "SET-01"),  # 随机事件与古典概型 ← 集合（事件即样本空间的子集）
    ("PL-02", "PL-01"),   # 条件概率与独立性 ← 随机事件与古典概型
    ("PL-03", "PL-01"),   # 排列组合与二项式定理 ← 古典概型（计数基础）
    ("PL-04", "PL-02"),   # 随机变量及其分布 ← 条件概率与独立性
    ("PL-04", "PL-03"),   # 随机变量及其分布 ← 排列组合与二项式定理
    ("PL-05", "PL-01"),   # 统计与成对数据 ← 随机事件与古典概型
    ("PL-05", "PL-04"),   # 统计与成对数据（独立性检验等）← 随机变量及其分布
]


def check() -> None:
    """--check 模式：只打印边表，不连数据库"""
    print("=" * 60)
    print("kp_prerequisites 待插入前置依赖边（--check，不连库）")
    print("=" * 60)
    seen: set[tuple[str, str]] = set()
    dup = 0
    for kp_code, prereq_code in EDGES:
        tag = ""
        if (kp_code, prereq_code) in seen:
            tag = "  <-- 表内重复!"
            dup += 1
        seen.add((kp_code, prereq_code))
        print(f"  {kp_code:<8} 依赖  {prereq_code}{tag}")
    print(f"\n共 {len(EDGES)} 条边（去重后 {len(seen)} 条，重复 {dup} 条）")
    print("提示: 依赖边按 code 引用，kp 表无对应码的边静默存在、待题库标注对齐后生效。")


async def seed() -> None:
    """幂等写入：ON CONFLICT DO NOTHING"""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.database import async_session_factory, init_db
    from app.models.growth import KpPrerequisite

    print("=" * 60)
    print("kp_prerequisites 前置依赖种子（M2 迭代16 W5）")
    print("=" * 60)

    # 确保表存在（开发用；生产由 Alembic 保证）
    await init_db()
    print("[OK] 数据库表已就绪")

    async with async_session_factory() as db:
        stmt = pg_insert(KpPrerequisite).values(
            [{"kp_code": kp, "prereq_code": pre} for kp, pre in EDGES]
        )
        stmt = stmt.on_conflict_do_nothing(constraint="uq_kp_prereq")
        result = await db.execute(stmt)
        await db.commit()
        inserted = result.rowcount if result.rowcount is not None else -1

    print(f"[DONE] 共 {len(EDGES)} 条边，实际新插入 {inserted} 条（其余已存在，幂等跳过）")
    print("提示: 依赖边按 code 引用，kp 表无对应码的边静默存在、待题库标注对齐后生效。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="扩充 kp_prerequisites 高中数学主干前置依赖边（幂等，可重复执行）"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只打印将插入的边表，不连接数据库",
    )
    args = parser.parse_args()
    if args.check:
        check()
    else:
        asyncio.run(seed())


if __name__ == "__main__":
    main()
