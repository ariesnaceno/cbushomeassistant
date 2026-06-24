ARG BUILD_FROM
FROM ${BUILD_FROM}

# Home Assistant base images are Alpine-based; add Python.
RUN apk add --no-cache python3

COPY cbus_relay.py /cbus_relay.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
