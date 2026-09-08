FROM python:3.11-slim
WORKDIR /repo
COPY webui/requirements.txt /tmp/webui-requirements.txt
COPY chatbot/requirements.txt /tmp/chatbot-requirements.txt
RUN pip install --no-cache-dir -r /tmp/webui-requirements.txt -r /tmp/chatbot-requirements.txt pytest==8.3.4 pytest-asyncio==0.25.0 ruff==0.9.4 mypy==1.14.1
COPY . /repo
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/repo/webui:/repo/chatbot
CMD ["python", "scripts/check_stabilization.py"]
