#!/usr/bin/env bash
# -*- coding: utf-8 -*-
#
# 导出平台用户数据与 AI 对话记录（**默认脱敏**），用于迁移 / 备份 / 研究。
#
# 用法（在服务器上执行）：
#     bash /opt/math-arena/scripts/export_user_data.sh            # 脱敏导出（推荐）
#     bash /opt/math-arena/scripts/export_user_data.sh --raw      # 明文导出（需二次确认）
#
# 同机存在多个 postgres 容器时，用 PG_CONTAINER 显式指定：
#     PG_CONTAINER=math-arena-postgres bash scripts/export_user_data.sh
#
# 输出目录： ./exports/<时间戳>/
#   —— 账号与画像 ——
#   - user_roster.csv            用户名册（手机号掩码，不含 password_hash / email）
#   - student_profile_tags.csv   学情画像 + 用户标签（tags / weak_point_rank 原样 JSONB）
#   - student_tags_flat.csv      用户标签宽表（一行一标签）
#   - student_weak_points.csv    薄弱点宽表（一行一知识点）
#   —— AI 对话记录 ——
#   - conversations.csv          AI 会话清单（标题/摘要/消息数）
#   - messages.csv               **AI 对话消息正文**（role/content/thinking/feedback）
#   - tutor_sessions.csv         AI 辅导会话（题目/提示层级/尝试次数）
#   - research_chat_messages.csv Research 域对话
#   —— 汇总 ——
#   - counts.csv                 各表聚合计数
#   - README.txt                 字段说明与合规提示
#
# 默认脱敏字段：
#   users.phone      -> 138****1234
#   users.email      -> 不导出
#   users.password_hash -> 不导出（任何模式都不导出）
#
# ⚠️ 合规前置（K12 场景含未成年人个人信息）：
#   1. 明文导出、以及导出**对话正文**前，须确认已获监护人单独同意，并做访问留痕。
#   2. 导出物属敏感数据，须加密存储、限制访问、设定保留期限并到期销毁。
#   3. 对外演示/论文投稿请使用脱敏结果或合成数据，不要投放真实学生对话与手机号。
#
set -uo pipefail

RAW=0
if [ "${1:-}" = "--raw" ]; then
  RAW=1
fi

OUT="./exports/$(date +%Y%m%d-%H%M%S)"
mkdir -p "${OUT}"

# ---------- 定位 psql 执行方式 ----------
# 支持 PG_CONTAINER 显式指定；否则优先选与本项目相关的 math-arena 库，
# 避免误选同机上其他 postgres 容器（例如 teacher-postgres）。
ALL_PG="$(docker ps --format '{{.Names}}' 2>/dev/null)"
PG="${PG_CONTAINER:-}"
if [ -z "${PG}" ]; then
  PG="$(printf '%s\n' "${ALL_PG}" | grep -iE 'math[-_]arena.*postgres|postgres.*math[-_]arena' | head -1)"
fi
if [ -z "${PG}" ]; then
  PG="$(printf '%s\n' "${ALL_PG}" | grep -iE 'postgres|postgresql' | head -1)"
fi
if [ -z "${PG}" ]; then
  echo "!! 未找到 postgres 容器，无法导出。"
  echo "   同机容器："
  docker ps -a --format '{{.Names}}\t{{.Status}}' | head -20
  echo "   可用 PG_CONTAINER=<容器名> 显式指定。"
  exit 1
fi
PGU="$(docker exec "${PG}" printenv POSTGRES_USER 2>/dev/null)"; PGU="${PGU:-postgres}"
PGD="$(docker exec "${PG}" printenv POSTGRES_DB   2>/dev/null)"; PGD="${PGD:-postgres}"

echo "容器=${PG}  用户=${PGU}  库=${PGD}"
echo "输出目录=${OUT}"
echo

if [ "${RAW}" = "1" ]; then
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  echo "!! 你正在启用 **明文导出**：手机号将以明文写入 CSV。"
  echo "!! 该导出物属敏感个人信息，须加密存储、限制访问、留痕。"
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  printf "确认继续请输入 YES： "
  read -r ans
  if [ "${ans}" != "YES" ]; then
    echo "已取消。"
    exit 1
  fi
fi

# ---------- 手机号表达式 ----------
if [ "${RAW}" = "1" ]; then
  PHONE_EXPR="u.phone"
else
  PHONE_EXPR="case when u.phone is null or length(u.phone) < 7 then null
                   else left(u.phone,3) || '****' || right(u.phone,4) end"
fi

run_copy() {
  local out="$1"; shift
  local sql="$1"
  # 注意：正文含换行符时，物理行数会大于记录数；权威条数见 counts.csv。
  docker exec -i "${PG}" psql -U "${PGU}" -d "${PGD}" -v ON_ERROR_STOP=1 -c "\\copy (${sql}) to stdout with (format csv, header true)" > "${out}" \
    && echo "  -> ${out} ($(wc -l < "${out}") 物理行)" \
    || echo "  !! 导出失败（表可能不存在，可忽略）: ${out}"
}

# ---------- 1. 用户名册 ----------
echo "[1/10] 用户名册"
run_copy "${OUT}/user_roster.csv" "
select u.id as user_id,
       u.nickname,
       ${PHONE_EXPR} as phone,
       u.status,
       u.onboarding_status,
       u.last_active_role,
       u.created_at,
       coalesce((select array_agg(rb.role order by rb.role)
                 from role_bindings rb
                 where rb.user_id = u.id and rb.deleted_at is null), '{}') as roles
from users u
where u.deleted_at is null
order by u.created_at"

# ---------- 2. 学情画像 + 用户标签（原始 JSONB） ----------
echo "[2/10] 学情画像与用户标签（原始 JSONB）"
run_copy "${OUT}/student_profile_tags.csv" "
select p.user_id,
       p.school_stage,
       p.grade,
       p.learning_style,
       p.current_stage,
       p.tags,
       p.weak_point_rank,
       p.computed_at,
       p.updated_at
from student_profiles p
join users u on u.id = p.user_id and u.deleted_at is null
order by p.user_id"

# ---------- 3. 用户标签宽表（一行一标签，便于统计/图表） ----------
# t.value #>> '{}' 对「字符串数组」返回不带引号的原文，
# 对「对象数组」返回对象 JSON 文本 —— 两种形状都能吃。
echo "[3/10] 用户标签宽表"
run_copy "${OUT}/student_tags_flat.csv" "
select p.user_id,
       t.ordinality as tag_index,
       t.value #>> '{}' as tag,
       p.learning_style,
       p.current_stage,
       p.school_stage,
       p.grade,
       p.computed_at
from student_profiles p
cross join lateral jsonb_array_elements(p.tags) with ordinality as t(value, ordinality)
join users u on u.id = p.user_id and u.deleted_at is null
order by p.user_id, t.ordinality"

# ---------- 4. 薄弱点宽表（一行一知识点） ----------
echo "[4/10] 薄弱点宽表"
run_copy "${OUT}/student_weak_points.csv" "
select p.user_id,
       w.ordinality as rank,
       w.value ->> 'kp_code' as kp_code,
       w.value ->> 'kp_name' as kp_name,
       w.value ->> 'mastery' as mastery,
       w.value ->> 'trend'   as trend
from student_profiles p
cross join lateral jsonb_array_elements(p.weak_point_rank) with ordinality as w(value, ordinality)
join users u on u.id = p.user_id and u.deleted_at is null
order by p.user_id, w.ordinality"

# ---------- 5. AI 会话清单 ----------
echo "[5/10] AI 会话清单"
run_copy "${OUT}/conversations.csv" "
select c.id as conversation_id,
       c.user_id,
       c.active_role,
       c.title,
       c.summary,
       c.message_count,
       c.pinned,
       c.created_at,
       c.updated_at
from conversations c
join users u on u.id = c.user_id and u.deleted_at is null
where c.deleted_at is null
order by c.created_at"

# ---------- 6. AI 对话消息正文（核心） ----------
# content 为模型/用户原文；thinking 为推理过程（若有）；envelope / route_info
# 属内部结构化字段，体量大且含内部路由，默认不导出。
echo "[6/10] AI 对话消息正文"
run_copy "${OUT}/messages.csv" "
select m.conversation_id,
       m.id as message_id,
       c.user_id,
       m.role,
       m.skill_id,
       m.content,
       m.thinking,
       m.feedback,
       m.created_at
from messages m
join conversations c on c.id = m.conversation_id
join users u on u.id = c.user_id and u.deleted_at is null
where m.deleted_at is null
order by m.conversation_id, m.created_at"

# ---------- 7. AI 辅导会话（苏格拉底式追问） ----------
echo "[7/10] AI 辅导会话"
run_copy "${OUT}/tutor_sessions.csv" "
select t.id as tutor_session_id,
       t.user_id,
       t.conversation_id,
       t.question_text,
       t.source,
       t.status,
       t.current_step,
       t.hint_level,
       t.attempts_on_step,
       t.answer_requests,
       t.completed_at,
       t.created_at
from tutor_sessions t
join users u on u.id = t.user_id and u.deleted_at is null
where t.deleted_at is null
order by t.created_at"

# ---------- 8. Research 域对话 ----------
echo "[8/10] Research 域对话"
run_copy "${OUT}/research_chat_messages.csv" "
select m.session_id,
       m.id as message_id,
       m.user_id,
       m.role,
       m.content,
       m.sources,
       m.created_at
from research_chat_messages m
order by m.session_id, m.created_at"

# ---------- 9. 聚合计数 ----------
echo "[9/10] 聚合计数"
run_copy "${OUT}/counts.csv" "
with wanted(name) as (
  values ('users'), ('student_profiles'), ('role_bindings'),
         ('organizations'), ('events'), ('auth_sessions'),
         ('conversations'), ('messages'), ('tutor_sessions'),
         ('ai_calls'), ('research_chat_sessions'), ('research_chat_messages')
)
select p.name as table_name,
       (xpath('/row/c/text()',
              query_to_xml(format('select count(*) as c from %I', p.name),
                           false, true, '')))[1]::text as row_count
from (select w.name from wanted w
      join information_schema.tables t
        on t.table_schema='public' and t.table_name=w.name) p
order by p.name"

# ---------- 10. 说明 ----------
echo "[10/10] 写说明文件"
cat > "${OUT}/README.txt" <<TXT
导出时间： $(date -Iseconds)
导出模式： $([ "${RAW}" = "1" ] && echo "明文(--raw)" || echo "脱敏(默认)")
来源容器： ${PG}   库： ${PGD}

文件说明：
  user_roster.csv           用户名册。phone 列 $([ "${RAW}" = "1" ] && echo "为明文" || echo "已掩码为 138****1234")。
  student_profile_tags.csv  学情画像 + 用户标签。tags / weak_point_rank 为 JSONB 原样导出。
  student_tags_flat.csv     用户标签宽表，一行一标签（tag_index 为原数组下标）。
  student_weak_points.csv   薄弱点宽表，一行一知识点（rank 为原数组下标）。
  conversations.csv         AI 会话清单（标题/摘要/消息数）。
  messages.csv              **AI 对话消息正文**：role / content / thinking / feedback / skill_id。
  tutor_sessions.csv        AI 辅导会话（题目/提示层级/尝试次数）。
  research_chat_messages.csv Research 域对话正文。
  counts.csv                各表聚合计数。

未导出（任何模式都不导出）：
  users.password_hash       口令哈希
  users.email               邮箱
  auth_sessions             会话令牌
  messages.envelope         AI 消息结构化载荷（内部字段，体量大）
  messages.route_info       内部路由信息

合规提示：
  本文件可能包含未成年人个人信息，messages.csv 更含学生原始提问与 AI 回答正文。
  请加密存储、限制访问、留痕，设定保留期限并到期销毁；
  对外演示 / 论文投稿请使用脱敏结果或合成数据。

计数口径：
  控制台打印的是 CSV **物理行数**；messages.csv 等文件的正文含换行符，
  物理行数会显著大于记录数。**权威记录数以 counts.csv 为准。**
TXT
echo "  -> ${OUT}/README.txt"

echo
echo "完成。导出目录： ${OUT}"
echo "打包命令： tar -czf ${OUT}.tar.gz -C $(dirname "${OUT}") $(basename "${OUT}")"
