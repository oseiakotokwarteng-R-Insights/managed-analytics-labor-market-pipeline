# Runs the pipeline in a container, so it behaves the same on any machine.
#
#   docker build -t qcew-pipeline .
#   docker run --rm -v "$PWD/data:/app/data" -v "$PWD/outputs:/app/outputs" qcew-pipeline
#
# The database goes in ./data and the logs and CSV exports in ./outputs on your machine.
# Anything after the image name is passed to the program, for example:
#   docker run --rm -v "$PWD/data:/app/data" -v "$PWD/outputs:/app/outputs" qcew-pipeline --years 2024 --export
# BLS asks automated programs to identify themselves: add -e QCEW_USER_AGENT="your-name (you@example.org)"

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Don't run as root.
RUN useradd --create-home --uid 1000 pipeline
WORKDIR /app

# A normal (non-editable) install, so the container also proves the package installs cleanly.
COPY pyproject.toml README.md LICENSE ./
COPY qcew_pipeline ./qcew_pipeline
RUN pip install .

# Folders the program writes to (relative to /app, where it runs from).
RUN mkdir -p /app/data /app/outputs && chown -R pipeline:pipeline /app/data /app/outputs
USER pipeline
VOLUME ["/app/data", "/app/outputs"]

ENTRYPOINT ["qcew-run"]
CMD ["--export"]
