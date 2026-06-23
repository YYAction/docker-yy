FROM python:3.13-slim AS base
COPY log_scan.py /code/log_scan.py
RUN pip install flask
CMD [ "python", "/code/log_scan.py" ]