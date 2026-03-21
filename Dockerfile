FROM public.ecr.aws/docker/library/python:3.13.9-slim

WORKDIR /app

# 配置 apt 源 (兼容 Debian 11/12/13)
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true && \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list 2>/dev/null || true

# 更新 apt 并安装依赖
RUN apt-get update && \
    apt-get install -y openssl tzdata && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 设置时区
RUN ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
RUN echo "Asia/Shanghai" > /etc/timezone

# 配置 pip 源 (为 uv 使用相同源)
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 安装 Python 依赖
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 确保使用虚拟环境中的 Python
ENV PATH="/app/.venv/bin:$PATH"

# 复制应用文件
COPY config/ ./config/
COPY proxy/ ./proxy/
COPY cert/ ./cert/
COPY cli/ ./cli/
COPY routes.py main.py config.yml ./

RUN mkdir -p ca
RUN mkdir -p models

# 生成证书 (如果不在构建时生成，可以放在 entrypoint 中)
# RUN python -m cli cert

ENV PYTHONUNBUFFERED=1
EXPOSE 443

# 创建一个入口脚本，在启动主程序前确保生成证书
RUN echo '#!/bin/sh\npython -m cli cert\nexec "$@"' > /entrypoint.sh && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "main.py"]