-- Auto-create all databases needed by ZXSY Math Arena
-- This runs automatically on first container start via docker-entrypoint-initdb.d
CREATE DATABASE research_vnext;
CREATE DATABASE teacher_platform;
CREATE DATABASE ppt_studio_v2;
GRANT ALL PRIVILEGES ON DATABASE research_vnext TO postgres;
GRANT ALL PRIVILEGES ON DATABASE teacher_platform TO postgres;
GRANT ALL PRIVILEGES ON DATABASE ppt_studio_v2 TO postgres;
