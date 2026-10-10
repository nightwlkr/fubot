#!/bin/bash
set -e

case "$1" in
  start)
    systemctl daemon-reload
    systemctl enable fubot
    systemctl start fubot
    echo "FUBOT started"
    ;;
  stop)
    systemctl stop fubot
    echo "FUBOT stopped"
    ;;
  restart)
    systemctl restart fubot
    echo "FUBOT restarted"
    ;;
  status)
    systemctl status fubot --no-pager
    ;;
  logs)
    journalctl -u fubot -f --since "1 hour ago"
    ;;
  *)
    echo "Usage: fubotctl {start|stop|restart|status|logs}"
    exit 1
    ;;
esac
