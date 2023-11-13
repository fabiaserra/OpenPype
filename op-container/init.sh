#!/bin/bash

dns_server=${DNS_SERVER:-""}
search_domain=${SEARCH_DOMAIN:-""}

if [[ -z "$dns_server" ]] || [[ -z "$search_domain" ]]; then
  echo "Warning: DNS_SERVER or SEARCH_DOMAIN environment variable is not set."
else
  echo -e "search ${search_domain}\nnameserver ${dns_server}" > /etc/resolv.conf
fi

exec "$@"
