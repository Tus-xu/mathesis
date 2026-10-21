"""批量参数提取 + 参数化渲染 + 替换 AI 自由生成配图。

流程（每题）：
  1. 规则提取（内置高考真题模板，100% 确定）→ 未命中走 LLM 提取（DeepSeek，严格 JSON Schema）；
  2. validate_figure_params() 参数校验（非法即反馈重试/放弃）；
  3. render_figure() 代码精确渲染 SVG；
  4. check_svg_invariants() 几何不变量校验（锥体顶点必须在底面上方等）；
  5. 全部通过才写库：figure_params 存参数、image[0] 替换为新 data URI、
     annotate_meta.figure_gen 记录方法；失败保留原图绝不覆盖。

用法：
    python -m scripts.backfill_figure_params --dry-run --limit 20 --out-dir .tmp/figures
    python -m scripts.backfill_figure_params --method auto            # 默认：规则优先 LLM 兜底
    python -m scripts.backfill_figure_params --method llm --limit 5   # 强制 LLM
    python -m scripts.backfill_figure_params --method rules           # 仅规则
"""

import argparse
import asyncio
import json
import math
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import settings
from app.models.database import async_session_factory
from app.models.question_bank import QuestionBank
from app.services.figure_renderer import (
    FIGURE_SCHEMA_DOC,
    FigureParamsError,
    check_svg_invariants,
    render_figure,
    to_data_uri,
    validate_figure_params,
)

_FIGURE_RE = "棱柱|棱锥|三棱|四棱|正方体|长方体|如图|图所示"
_RENDERER = "figure_renderer_v1"


def _normalize_stem(stem: str) -> str:
    r"""题干归一化：去空白、剥 \mathrm/\mathbf/\mathit 包装、去 $。"""
    s = re.sub(r"\s+", "", stem or "")
    for wrapper in ("mathrm", "mathbf", "mathit"):
        s = re.sub(rf"\\{wrapper}\{{([^{{}}]*)\}}", r"\1", s)
    return s.replace("$", "")

# ---------------------------------------------------------------------------
# 规则提取：内置高考真题参数模板（按题干指纹匹配，确定性）
# ---------------------------------------------------------------------------

def _equi(side: float) -> list[list[float]]:
    r = side / math.sqrt(3.0)
    return [[r * math.cos(math.radians(a)), r * math.sin(math.radians(a))]
            for a in (-90.0, 30.0, 150.0)]


def _rule_cuboid_circum(a: float, b: float, h: float) -> dict:
    """长方体 + 外接球（顶点都在球面上）。"""
    return {"type": "sphere", "params": {
        "r": math.sqrt(a * a + b * b + h * h) / 2,
        "center_label": "O",
        "solid": {"type": "cuboid", "params": {"a": a, "b": b, "h": h}}}}


def _rule_cube_circum(a: float) -> dict:
    return {"type": "sphere", "params": {
        "r": a * math.sqrt(3.0) / 2, "center_label": "O",
        "solid": {"type": "cube", "params": {"a": a}}}}


def _rule_prism_circum(side: float, height: float) -> dict:
    """正三棱柱 + 外接球（所有顶点在球面上）。r=√(h²/4+a²/3)。"""
    return {"type": "sphere", "params": {
        "r": math.sqrt(height * height / 4 + side * side / 3), "center_label": "O",
        "solid": {"type": "triangular_prism",
                  "params": {"base": "equilateral", "side": side, "height": height}}}}


def _rule_right_prism_inscribed_ball(ab: float, bc: float, h: float) -> dict:
    """直三棱柱（AB⊥BC）内接球：r=min(底面内切圆半径, h/2)。

    底面 A(0,0) B(ab,0) C(ab,bc)（直角在 B），平移使底面中心在原点。
    """
    r = min((ab + bc - math.hypot(ab, bc)) / 2, h / 2)
    cx, cy = (ab + 0) / 2 - ab / 2 - 0, bc / 2 - bc / 2  # 内切圆心在 (ab−r, r)
    center = (ab - r - ab / 2, r - bc / 2, h / 2)
    base = [[-ab / 2, -bc / 2], [ab / 2, -bc / 2], [ab / 2, bc / 2]]
    return {"type": "sphere", "params": {
        "r": r, "center": list(center), "center_label": "O",
        "solid": {"type": "triangular_prism",
                  "params": {"base": "custom", "vertices": base, "height": h}}}}


_RULES: list[tuple[re.Pattern, str, callable]] = [
    # 2016 理 11 / 理 10：直三棱柱 AB=6 BC=8 AA1=3，内接球最大体积
    (re.compile(r"直三棱柱ABC-A_\{?1\}?B_\{?1\}?C_\{?1\}?内有一个体积为V的球.*?"
                r"AB\\perpBC,?AB=6,?BC=8,?AA_\{?1\}?=3", re.S),
     "right_prism_inscribed_ball", lambda stem: _rule_right_prism_inscribed_ball(6, 8, 3)),
    # 2010 理 7：长方体 2a×a×a 外接球
    (re.compile(r"长方体的长、宽、高分别为2a、a、a", re.S),
     "cuboid_circum_2a", lambda stem: _rule_cuboid_circum(2, 1, 1)),
    # 2017 理 15：长方体 3×2×1 外接球
    (re.compile(r"长方体的长、宽、高分别为3,2，1", re.S),
     "cuboid_circum_3_2_1", lambda stem: _rule_cuboid_circum(3, 2, 1)),
    # 2010 理 10：正三棱柱所有棱长 a 外接球
    (re.compile(r"三棱柱的侧棱垂直于底面.*?所有棱长都为a", re.S),
     "prism_circum_a", lambda stem: _rule_prism_circum(2, 2)),
    # 2014 理 7：正三棱柱底面边长 2 侧棱 √3，D 为 BC 中点
    (re.compile(r"正三棱柱ABC-A_\{?1\}?B_\{?1\}?C_\{?1\}?的底面边长为2.*?"
                r"侧棱长为\\sqrt\{3\}.*?D为BC中点", re.S),
     "prism_with_D", lambda stem: _rule_prism_midpoint_d(2, math.sqrt(3))),
    # 2016 理 4：体积为 8 的正方体外接球
    (re.compile(r"体积为8的正方体的顶点都在同一球面上", re.S),
     "cube_circum_2", lambda stem: _rule_cube_circum(2)),
    # 2021 理 11：球 O 半径 1，AC⊥BC，AC=BC=1，三棱锥 O-ABC
    (re.compile(r"半径为1的球O的球面上的三个点.*?AC\\perpBC,?AC=BC=1.*?"
                r"三棱[锥雉]O-ABC", re.S),
     "tri_pyramid_O_ABC", lambda stem: _rule_tri_pyramid_oabc()),
    # 2022 理 9 / 理 12：球 O 半径 1，四棱锥顶点为 O，底面四顶点在球面上
    (re.compile(r"球O的半径为1.*?四棱[锥雉]的顶点为O.*?底面的四个顶点均在球O的球面上",
                re.S),
     "quad_pyramid_O", lambda stem: _rule_quad_pyramid_o()),
    # 2022 理 15：正方体 8 顶点选 4
    (re.compile(r"从正方体的8个顶点中任选4个", re.S),
     "cube_8_vertices", lambda stem: {"type": "cube", "params": {"a": 2}}),
    # 2023 甲文 10：三棱锥 P-ABC 正三角形底 2，PA=PB=2，PC=√6
    (re.compile(r"三棱[锥雉]P-ABC中.*?边长为2的等边三角形.*?"
                r"PA=PB=2,?PC=\\sqrt\{6\}", re.S),
     "tri_pyramid_P_ABC_2023", lambda stem: _rule_tri_pyramid_2023()),
    # 2023 甲理 11：四棱锥 P-ABCD 底面正方形 AB=4，PC=PD=3，∠PCA=45°
    (re.compile(r"四棱[锥雉]P-ABCD中.*?底面ABCD为正方形.*?"
                r"AB=4,?PC=PD=3,?\\anglePCA=45", re.S),
     "quad_pyramid_P_ABCD_2023", lambda stem: _rule_quad_pyramid_2023()),
    # 2024 新课标II 7：正三棱台 AB=6，A1B1=2，体积 52/3
    (re.compile(r"正三棱台ABC-A_\{?1\}?B_\{?1\}?C_\{?1\}?的体积为\\frac\{52\}\{3\}.*?"
                r"AB=6,?A_\{?1\}?B_\{?1\}?=2", re.S),
     "tri_frustum_2024", lambda stem: _rule_tri_frustum_2024()),
    # 2010 理 15：正视图为三角形（无合适配图，跳过）
    (re.compile(r"一个几何体的正视图为一个三角形", re.S),
     "skip_no_figure", None),
]


def _rule_prism_midpoint_d(side: float, height: float) -> dict:
    """正三棱柱 + BC 中点 D（用通用多面体表示）。"""
    pts = _equi(side)  # A 前、B 右前、C 左后
    a, b, c = pts
    d = [(b[0] + c[0]) / 2, (b[1] + c[1]) / 2]
    sub = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
    v = {"A": (*a, 0), "B": (*b, 0), "C": (*c, 0),
         "A₁": (*a, height), "B₁": (*b, height), "C₁": (*c, height), "D": (*d, 0)}
    faces = [["A", "C", "B"], ["A₁", "B₁", "C₁"],
             ["A", "B", "B₁", "A₁"], ["B", "C", "C₁", "B₁"], ["C", "A", "A₁", "C₁"]]
    return {"type": "polyhedron", "params": {"vertices": v, "faces": faces}}


def _rule_tri_pyramid_oabc() -> dict:
    """2021 理 11：球心 O 半径 1，AC⊥BC，AC=BC=1。三棱锥 O-ABC。"""
    h = math.sqrt(1 - 0.5)  # O 到底面（AB 中点）的高度 √(1−r_circ²)
    return {"type": "tri_pyramid", "params": {
        "base_points": [[1, 0], [0, 1], [0, 0]],  # A, B, C（直角在 C）
        "apex_pos": [0.5, 0.5, h], "apex": "O", "base": ["A", "B", "C"]}}


def _rule_quad_pyramid_o() -> dict:
    """2022 理 9/12：球 O 半径 1，四棱锥顶点为球心 O。体积最大时高 √3/3。"""
    d = 1 / math.sqrt(3)  # 底面所在高度（距球心）
    s = math.sqrt(2 * (1 - d * d))  # 底面边长
    half = s / 2
    return {"type": "sphere", "params": {
        "r": 1.0, "center": [0, 0, d], "center_label": "O",
        "solid": {"type": "quad_pyramid", "params": {
            "base_points": [[-half, -half], [half, -half], [half, half], [-half, half]],
            "apex_pos": [0, 0, d], "apex": "O",
            "base": ["A", "B", "C", "D"]}}}}


def _rule_tri_pyramid_2023() -> dict:
    """2023 甲文 10：底正三角形边长 2，PA=PB=2，PC=√6。解析求 P。"""
    pts = _equi(2)  # A(0,−r) B(r·√3/2, r/2) C(−r·√3/2, r/2)
    ax, ay = pts[0]
    bx, by = pts[1]
    cx, cy = pts[2]
    # 解 PA=PB：P 在 AB 垂直平分面上；再解 PC 与 PA 距离
    ux, uy = bx - ax, by - ay
    mx, my = (ax + bx) / 2, (ay + by) / 2
    # 参数化：P = (mx,my,0) + t·(AB 法向) + z
    nx, ny = -uy, ux
    nl = math.hypot(nx, ny)
    nx, ny = nx / nl, ny / nl
    # 在 AB 垂直平分面内求与 C 距离 √6、与 A 距离 2 的点
    # P = (mx + t·nx, my + t·ny, z)；由 PA²=4 与 PC²=6 解 t, z
    px0, py0 = mx - cx, my - cy
    # |P−A|²: t² + z² = 4 − |(mx,my)−A|² = 4 − (|AB|/2)² = 3
    k = 4 - ((mx - ax) ** 2 + (my - ay) ** 2)
    # |P−C|²: (px0 + t·nx)² + (py0 + t·ny)² + z² = 6
    #   → t² + 2t(px0·nx + py0·ny) + |(px0,py0)|² + z² = 6
    #   → 2t·s + |p0|² + (k − t²) = 6  → t = (6 − k − |p0|²)/(2s)
    s = px0 * nx + py0 * ny
    p0sq = px0 * px0 + py0 * py0
    t = (6 - k - p0sq) / (2 * s) if abs(s) > 1e-12 else 0.0
    z = math.sqrt(max(k - t * t, 0.0))
    px, py = mx + t * nx, my + t * ny
    return {"type": "tri_pyramid", "params": {
        "base_points": [[ax, ay], [bx, by], [cx, cy]],
        "apex_pos": [px, py, z], "apex": "P", "base": ["A", "B", "C"]}}


def _rule_quad_pyramid_2023() -> dict:
    """2023 甲理 11：底面正方形 AB=4，PC=PD=3，∠PCA=45°。解析得 P=(0,1,2)。"""
    return {"type": "quad_pyramid", "params": {
        "base_w": 4, "base_d": 4,
        "base_points": [[-2, -2], [2, -2], [2, 2], [-2, 2]],
        "apex_pos": [0, 1, 2], "apex": "P", "base": ["A", "B", "C", "D"]}}


def _rule_tri_frustum_2024() -> dict:
    """2024 新课标II 7：正三棱台 AB=6、A1B1=2，体积 52/3 → 高 4/√3。"""
    h = 4 / math.sqrt(3)
    return {"type": "tri_frustum",
            "params": {"bottom_side": 6, "top_side": 2, "height": h}}


def extract_by_rules(stem: str) -> tuple[dict | None, str | None]:
    """规则提取：命中返回 (figure_params, rule_name)；无规则返回 (None, None)；
    规则明确判定无需配图返回 (None, rule_name)。"""
    norm = _normalize_stem(stem)
    for pat, name, builder in _RULES:
        if pat.search(norm):
            if builder is None:
                return None, name  # skip_no_figure
            fig = builder(norm)
            validate_figure_params(fig)  # 规则自校验，失败即抛错暴露 bug
            return fig, name
    return None, None


# ---------------------------------------------------------------------------
# LLM 提取（兜底）
# ---------------------------------------------------------------------------

async def extract_by_llm(stem: str, err_hint: str | None = None) -> dict | None:
    """DeepSeek 提取参数（严格 JSON；带上次失败原因重试一次）。"""
    url = (settings.deepseek_base_url or "").rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    url = url + "/chat/completions"
    hint = f"\n上一次提取失败原因：{err_hint}，请修正后重新输出。" if err_hint else ""
    prompt = (
        "你是高中数学题配图参数提取器。根据题目提取配图参数，"
        f"只输出一个 JSON 对象，不要任何其他文字。{hint}\n\n"
        f"{FIGURE_SCHEMA_DOC}\n\n题目：\n{stem[:600]}"
    )
    body = {
        "model": settings.deepseek_model or "mimo-v2.5-pro",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(url, headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                             json=body)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  [LLM-ERR] 请求失败: {type(e).__name__}: {str(e)[:80]}")
        return None
    # 提取首个 JSON 对象
    try:
        start = content.index("{")
        end = content.rindex("}")
        obj = json.loads(content[start:end + 1])
        if isinstance(obj.get("params"), dict) and obj.get("type"):
            return obj
    except (ValueError, json.JSONDecodeError):
        pass
    print(f"  [LLM-ERR] 响应无合法 JSON: {content[:120]!r}")
    return None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> int:
    method = args.method  # auto / rules / llm
    out_lines: list[str] = []
    rendered_dir: Path | None = None
    if args.out_dir:
        rendered_dir = Path(args.out_dir)
        rendered_dir.mkdir(parents=True, exist_ok=True)

    async with async_session_factory() as s:
        cond = QuestionBank.deleted_at.is_(None)
        if args.only_existing:
            cond &= QuestionBank.image.op("!=")([])
        elif args.only_missing:
            cond &= QuestionBank.image == []
            cond &= QuestionBank.stem.op("~")(_FIGURE_RE)
        else:
            cond &= (
                (QuestionBank.image.op("!=")([]))
                | (QuestionBank.stem.op("~")(_FIGURE_RE))
            )
        stmt = select(QuestionBank).where(cond).order_by(QuestionBank.id)
        if args.limit:
            stmt = stmt.limit(args.limit)
        rows = (await s.execute(stmt)).scalars().all()

        done, skipped, failed = 0, 0, 0
        sem = asyncio.Semaphore(args.concurrency)

        async def one(row: QuestionBank):
            nonlocal done, skipped, failed
            if row.figure_params and not args.force:
                skipped += 1
                return
            fig: dict | None = None
            source = "none"
            if method in ("auto", "rules"):
                fig, rule_name = extract_by_rules(row.stem)
                if rule_name == "skip_no_figure":
                    out_lines.append(f"[SKIP] {str(row.id)[:8]} 规则判定无需配图（保留原图）")
                    skipped += 1
                    return
                source = f"rule:{rule_name}" if fig else "none"
            if fig is None and method in ("auto", "llm"):
                async with sem:
                    fig = await extract_by_llm(row.stem)
                if fig:
                    try:
                        validate_figure_params(fig)
                    except FigureParamsError as e:
                        async with sem:
                            fig = await extract_by_llm(row.stem, str(e)[:200])
                    if fig:
                        source = "llm"
            if fig is None:
                out_lines.append(f"[FAIL] {str(row.id)[:8]} 参数提取失败（保留原图）")
                failed += 1
                return
            try:
                validate_figure_params(fig)
                svg = render_figure(fig)
                problems = check_svg_invariants(fig, svg)
                fatals = [p for p in problems if p["severity"] == "fatal"]
                if fatals:
                    raise FigureParamsError("; ".join(p["msg"] for p in fatals))
            except FigureParamsError as e:
                out_lines.append(
                    f"[FAIL] {str(row.id)[:8]} 渲染校验失败: {str(e)[:100]}（保留原图）")
                failed += 1
                return
            meta = dict(row.annotate_meta or {})
            meta["figure_gen"] = {
                "method": "parametric",
                "renderer": _RENDERER,
                "type": fig.get("type"),
                "source": source,
                "bytes": len(svg),
                "warnings": [p["code"] for p in problems],
            }
            if rendered_dir:
                fname = f"{str(row.id)[:8]}_{fig.get('type')}.svg"
                (rendered_dir / fname).write_text(svg, encoding="utf-8")
            if not args.dry_run:
                row.figure_params = fig
                row.image = [to_data_uri(svg)]
                row.annotate_meta = meta
            done += 1
            out_lines.append(
                f"[OK] {str(row.id)[:8]} {fig.get('type'):<18} via {source:<28} "
                f"{len(svg)}B warnings={[p['code'] for p in problems]}")

        await asyncio.gather(*(one(r) for r in rows))
        if not args.dry_run:
            await s.commit()  # 同一 session 提交（rows 由本 session 加载，脏标记生效）

    out_lines.append(
        f"[DONE] 处理 {len(rows)} 题：成功 {done}，跳过 {skipped}，失败 {failed}"
        + ("（dry-run 未写库）" if args.dry_run else "")
        + (f"，SVG 输出: {rendered_dir}" if rendered_dir else ""))
    report = "\n".join(out_lines)
    print(report)
    if args.report_file:
        Path(args.report_file).write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="参数化配图批量提取/渲染/替换")
    ap.add_argument("--dry-run", action="store_true", help="只渲染不写库")
    ap.add_argument("--method", choices=("auto", "rules", "llm"), default="auto")
    ap.add_argument("--limit", type=int, default=0, help="最多处理题数（0=全部）")
    ap.add_argument("--only-existing", action="store_true", help="仅处理已有配图的题（替换错图）")
    ap.add_argument("--only-missing", action="store_true", help="仅处理无配图但题干依赖图形的题")
    ap.add_argument("--force", action="store_true", help="已有 figure_params 也重新处理")
    ap.add_argument("--out-dir", type=str, default="", help="dry-run 时 SVG 输出目录")
    ap.add_argument("--report-file", type=str, default="", help="报告输出文件（UTF-8）")
    ap.add_argument("--concurrency", type=int, default=2, help="LLM 并发数")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args)))
