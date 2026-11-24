# 洋洋农场 · 联网版 —— 云端部署镜像（Koyeb / Render 通用）
# 说明：后端零第三方依赖（纯标准库），镜像内只含代码与静态页；
#       存档写入云端 libsql（Turso），不落在实例临时盘上。
FROM python:3.12-slim

WORKDIR /app

COPY server.py libsql_db.py ./
COPY web ./web

# 平台（Koyeb/Render）会注入 PORT 环境变量并按其暴露端口做健康检查；
# 未注入时默认 8000 兜底。
ENV PORT=8000
EXPOSE 8000

CMD ["python", "-u", "server.py"]