#!/usr/bin/env bash
# -*- coding: utf-8 -*-
#
# 只读检查：平台上是否存在用户数据。
#
# 设计约束（务必遵守）：
#   1. 只输出「聚合计数」与「表是否存在」，**不导出任何个人信息**
#      （不 select phone / email / password_hash / 学情画像内容）。
#   2. 纯读，可重复执行，不产生任何写入。
#   3. 用于回答「部署到服务器的这版到底有没有用户数据」。
#
# 用法（在服务器上执行；仓库已随 scp -r 同步到 /opt/math-arena）：
#     bash /opt/math-arena/scripts/check_user_data.sh
#
# 若报 "Connection closed by remote host"，说明本机 IP 被 sshd/fail2ban 限流，
# 与本脚本无关，请先在服务器侧解封：
#     fail2ban-client status sshd
#     fail2ban-client set sshd unbanip <你的公网IP>
#
set -uo pipefail

echo "=================================================="
echo " 1. 容器状态"
echo "=================================================="
if ! command -v docker >/dev/null 2>&1; then
  echo "!! 未安装 docker 或不在 PATH，无法继续。"
  exit 1
fi
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null | head -20

PG="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -iE 'postgres|postgresql|(^|_)db($|_)' | head -1)"
if [ -z "${PG}" ]; then
  echo
  echo "!! 未找到 postgres 容器。全部容器如下："
  docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | head -30
  exit 1
fi

PGU="$(docker exec "${PG}" printenv POSTGRES_USER 2>/dev/null)"
PGD="$(docker exec "${PG}" printenv POSTGRES_DB 2>/dev/null)"
PGU="${PGU:-postgres}"
PGD="${PGD:-postgres}"

echo
echo "=================================================="
echo " 2. 数据库容器"
echo "=================================================="
echo "container = ${PG}"
echo "user      = ${PGU}"
echo "database  = ${PGD}"

echo
echo "=================================================="
echo " 3. 聚合计数（仅计数，不含任何个人信息）"
echo "=================================================="
# query_to_xml 做动态表计数：表不存在则自动跳过，不会报错中断。
docker exec -i "${PG}" psql -U "${PGU}" -d "${PGD}" -tA -v ON_ERROR_STOP=1 <<'SQL'
with wanted(name) as (
  values ('users'), ('student_profiles'), ('role_bindings'),
         ('organizations'), ('events'), ('auth_sessions')
),
present as (
  select w.name
  from wanted w
  join information_schema.tables t
    on t.table_schema = 'public' and t.table_name = w.name
)
select
  rpad(p.name, 18) || ' = ' ||
  (xpath('/row/c/text()',
         query_to_xml(format('select count(*) as c from %I', p.name),
                      false, true, '')))[1]::text
from present p
order by p.name;
SQL

echo
echo "=================================================="
echo " 4. 结论提示"
echo "=================================================="
echo "users 计数 > 0  → 平台上确实已有注册用户。"
echo "users 计数 = 0  → 只有表结构，没有实际用户。"
echo
echo "如需进一步排查，请使用「脱敏」方式，例如手机号掩码："
echo "  select count(*), left(phone,3) || '****' || right(phone,4) from users group by 2;"
echo
echo ">> 本脚本不会、也不应导出明文手机号 / email / password_hash / 学情画像。"
