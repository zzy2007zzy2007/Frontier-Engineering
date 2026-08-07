FROM python:3.11-slim

WORKDIR /app

# The evaluator, reference solver and baseline use only the Python standard
# library, so there are no third-party dependencies to install.

COPY . /app

ENV PYTHONUNBUFFERED=1

# Default command evaluates the baseline solver:
#   docker run --rm -it <image>            -> python verification/evaluator.py baseline/solver.py
#   docker run --rm -it <image> <candidate>-> python verification/evaluator.py <candidate>
ENTRYPOINT ["python", "verification/evaluator.py"]
CMD ["baseline/solver.py"]
