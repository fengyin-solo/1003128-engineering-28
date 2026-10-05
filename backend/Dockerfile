FROM python:3.12-slim
WORKDIR /srv/app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# 构建期校验示例数据清单：清单与代码对不上时直接构建失败，和启动期行为一致
RUN python -m app.seed_loader
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
