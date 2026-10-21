# RV14 · research export/compile worker 的编译镜像
#
# 钉死策略（COMPILER-PIN-LICENSE-SBOM.md）：
#   - 基镜像：按 image ID 钉死（本地已有 daocloud python:3.12-slim，无 RepoDigest，用内容寻址 ID）
#   - TeX bundle：Debian bookworm 的 texlive 固定包集（构建期一次性安装，版本由 dpkg 记录）
#   - 中文字体：texlive-lang-chinese 的 Fandol 四族（fontset=fandol），无系统字体依赖
#   - QA 工具：poppler-utils（pdfinfo/pdftoppm，页面数与页面图校验用）
#   - 运行期：--network none + --read-only + USER 65534，无任何宏包下载通道
#     （MiKTeX/Tectonic 的 on-the-fly 下载在此镜像中根本不存在）
# 基镜像钉死：本地 tag math-arena-base:py312-slim-pinned = image id
#   sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
FROM math-arena-base:py312-slim-pinned

RUN sed -i 's|deb.debian.org|mirrors.ustc.edu.cn|g; s|security.debian.org/debian-security|mirrors.ustc.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      texlive-xetex \
      texlive-lang-chinese \
      texlive-fonts-recommended \
      texlive-latex-recommended \
      texlive-latex-extra \
      poppler-utils \
 && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /work && chown 65534:65534 /work
WORKDIR /work
USER 65534:65534
ENV HOME=/tmp
