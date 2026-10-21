#!/usr/bin/env bash
# RV09 · 本机 PostgreSQL 生命周期封装
#
# 为什么需要它：
#   本机没有 Docker（Docker Desktop 服务 Stopped 且无管理员权限 register 服务），
#   但存在一个原生 PostgreSQL 15.18 集群（`D:\PostgreSQL\pgsql` + `C:\Users\<user>\pgdata\data`）。
#   该集群**无法**跨工具调用存活 —— 宿主在每个命令结束后回收进程树。
#   因此每次需要数据库的命令都必须「先起 PG、跑完、干净关停」。
#
# 干净关停（-m fast）是必要的：脏关停会让下次启动的崩溃恢复 + fsync 耗时 40s+。
#
# 用法：
#   scripts/rv09_pg.sh start      # 启动（幂等：已监听则直接返回）
#   scripts/rv09_pg.sh stop       # 干净关停
#   scripts/rv09_pg.sh status
#   scripts/rv09_pg.sh psql <db> "<sql>"
set -uo pipefail

PGBIN="/d/PostgreSQL/pgsql/bin"
PGDATA="C:/Users/<user>/pgdata/data"
PGLOG="C:/Users/<user>/pgdata/pg.log"
PGPORT="${RV09_PGPORT:-54329}"

export PGPASSWORD="${PGPASSWORD:-postgres}"

is_up() {
  "$PGBIN/pg_isready.exe" -h 127.0.0.1 -p "$PGPORT" -q 2>/dev/null
}

case "${1:-}" in
  start)
    if is_up; then echo "PG already up on $PGPORT"; exit 0; fi
    "$PGBIN/pg_ctl.exe" -D "$PGDATA" -l "$PGLOG" \
      -o "-p $PGPORT -c listen_addresses=127.0.0.1" -w start 2>&1 | tail -3
    is_up && echo "PG UP on $PGPORT"
    ;;
  stop)
    if ! is_up; then echo "PG not running"; exit 0; fi
    "$PGBIN/pg_ctl.exe" -D "$PGDATA" -m fast -w stop 2>&1 | tail -2
    ;;
  status)
    if is_up; then echo "PG UP on $PGPORT"; else echo "PG DOWN"; fi
    ;;
  psql)
    shift
    "$PGBIN/psql.exe" -h 127.0.0.1 -p "$PGPORT" -U postgres "$@"
    ;;
  *)
    echo "usage: $0 {start|stop|status|psql <args>}" >&2
    exit 2
    ;;
esac
