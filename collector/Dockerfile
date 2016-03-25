FROM ubuntu:14.04
RUN \
  apt-get update && \
  apt-get install -y python-pip && \
  rm -rf /var/lib/apt/lists/*

ADD requirements.txt requirements.txt
ADD catalyze_collector.py catalyze_collector.py
ADD collector.py collector.py

RUN pip install -r requirements.txt

CMD gunicorn --workers=2 --log-level error --log-file=- --bind 0.0.0.0:$COLLECTOR_PORT 'collector:build_app()'
