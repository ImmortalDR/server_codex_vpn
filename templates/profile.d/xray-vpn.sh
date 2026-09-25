# Route outbound HTTP(S) through local Xray.
if [ -f /etc/xray-vpn/proxy.env ]; then
  set -a
  # shellcheck disable=SC1091
  . /etc/xray-vpn/proxy.env
  set +a
fi
