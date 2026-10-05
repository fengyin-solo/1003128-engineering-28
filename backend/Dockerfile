FROM python:3.12-slim
WORKDIR /srv/app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# 构建阶段就校验初始化数据清单：清单缺模块/字段对不上时镜像直接构建失败，
# 保证部署出去的镜像一定带着结构正确的同一份 data/seed_manifest.json。
RUN python -m app.seed_loader --check
EXPOSE 8000
# 容器启动时再校验一次（SEED_RECORDS_DIR 可在部署环境注入不同数据量），
# 失败非零退出，编排平台能看到点名的表与原因。
CMD ["sh", "-c", "python -m app.seed_loader --check && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
