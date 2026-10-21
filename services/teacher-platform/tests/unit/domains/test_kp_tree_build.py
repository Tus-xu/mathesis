# coding: utf-8
"""build_kp_tree 纯函数单测（2026-10-15 题库分类树治理）。

场景对应线上问题：kp_nodes 是全学段 K12-KGraph 导入（小学/初中/高中），题目只挂
高中章级 code → 旧口径下小学零题章节占满视野、树"对不上"。新口径：
  ① 题量 = 归属集合去重计数（kp_code ∪ kp_path 每题每 code 只计 1 次）自底向上聚合；
  ② 根收缩：子树有题 或 教师自建（source='teacher'）才显示。
"""
from types import SimpleNamespace

from app.domains.catalog.service import build_kp_tree


def node(code: str, name: str, parent_id=None, source: str = 'K12-KGraph'):
    return SimpleNamespace(id=code, code=code, name=name, parent_id=parent_id, source=source)


def test_only_question_bearing_roots_and_teacher_roots_survive():
    # 高中章（有题） + 小学章（零题） + 教师自建（零题但保留）
    ch_hs = node('math_xzxbx1_rjb_ch3', '圆锥曲线的方程')
    ch_primary = node('math_1a_rjb_ch1', '准备课')
    ch_custom = node('custom.abc123', '我的专题', source='teacher')
    sec = node('math_xzxbx1_rjb_ch3_s1', '第一节 椭圆', parent_id='math_xzxbx1_rjb_ch3')
    nodes = [ch_hs, ch_primary, ch_custom, sec]

    tree = build_kp_tree(nodes, own={'math_xzxbx1_rjb_ch3': 94})

    codes = [t['code'] for t in tree]
    assert codes == ['math_xzxbx1_rjb_ch3', 'custom.abc123']  # 零题小学章被收缩
    hs = tree[0]
    assert hs['count'] == 94  # 章自身命中
    # 题挂章级 code → 节自身 0；挂在概念层时父级才会通过聚合得到题量（见下一用例）
    assert hs['children'][0]['count'] == 0


def test_counts_roll_up_from_deepest_node():
    # 题挂在概念层（kp_code=cpt）→ 章/节聚合出题量，祖先不再依赖 kp_path
    ch = node('ch1', '第一章')
    sec = node('ch1_s1', '第一节', parent_id='ch1')
    cpt = node('cpt1', '概念', parent_id='ch1_s1')
    other = node('ch2', '第二章')

    tree = build_kp_tree([ch, sec, cpt, other], own={'cpt1': 7})

    assert len(tree) == 1  # ch2 零题被收缩
    root = tree[0]
    assert root['count'] == 7
    assert root['children'][0]['count'] == 7
    assert root['children'][0]['children'][0]['count'] == 7


def test_no_double_count_when_kp_code_in_kp_path():
    # 线上翻倍事故回归：kp_code 与 kp_path 同时命中同一 code 时，每题只计 1 次
    ch = node('ch1', '第一章')
    tree = build_kp_tree([ch], own={'ch1': 111})
    assert tree[0]['count'] == 111  # 旧口径 exact+subtree 会算出 222


def test_empty_tree_returns_empty_list():
    assert build_kp_tree([], {}) == []


def test_depth_cap_stops_at_four_levels():
    n1 = node('l1', 'L1')
    n2 = node('l2', 'L2', parent_id='l1')
    n3 = node('l3', 'L3', parent_id='l2')
    n4 = node('l4', 'L4', parent_id='l3')
    n5 = node('l5', 'L5', parent_id='l4')
    n6 = node('l6', 'L6', parent_id='l5')

    tree = build_kp_tree([n1, n2, n3, n4, n5, n6], own={'l6': 1})

    # 走到深度 4 为止（build 的 depth<4 才挂 children），l5 之后不再展开
    cur = tree[0]
    depth = 0
    while 'children' in cur:
        cur = cur['children'][0]
        depth += 1
    assert depth == 4
