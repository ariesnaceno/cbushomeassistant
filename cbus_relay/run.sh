#!/usr/bin/with-contenv bashio
# Read the add-on options and launch the relay.
set -e

export CNI_HOST="$(bashio::config 'cni_host')"
export CNI_PORT="$(bashio::config 'cni_port')"
export LISTEN_PORT="$(bashio::config 'listen_port')"
export LISTEN_HOST="0.0.0.0"

if [ -z "${CNI_HOST}" ]; then
  bashio::exit.nok "cni_host is not set — enter your CNI IP in the add-on configuration."
fi

bashio::log.info "Starting C-Bus CNI relay: listen :${LISTEN_PORT} -> ${CNI_HOST}:${CNI_PORT}"
exec python3 /cbus_relay.py
