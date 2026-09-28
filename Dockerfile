FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/fidus
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[all]"
RUN git config --system --add safe.directory '*'
WORKDIR /work
ENTRYPOINT ["fidus"]
CMD ["--help"]
