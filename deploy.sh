#!/usr/bin/env bash
# deploy.sh — deploy production-ready di Vigil su Ubuntu 22.04+/Debian 12+
# Uso tipico:
#   export VIGIL_REPO_URL="https://github.com/<owner>/<repo>.git"
#   bash deploy.sh

set -Eeuo pipefail

APP_NAME="vigil"
APP_USER="${APP_USER:-ubuntu}"
APP_GROUP="${APP_GROUP:-$APP_USER}"
APP_DIR="${APP_DIR:-/home/${APP_USER}/vigil-backend}"
FRONTEND_DIR="${FRONTEND_DIR:-${APP_DIR}/vigil-frontend}"
WEB_ROOT="${WEB_ROOT:-/var/www/vigil}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
NODE_MAJOR="${NODE_MAJOR:-20}"
REPO_URL="${VIGIL_REPO_URL:-}"
PUBLIC_HOST="${VIGIL_DOMAIN:-$(hostname -I | awk '{print $1}')}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/vigil}"

log() {
  echo "[deploy] $1"
}

run_as_app_user() {
  sudo -u "$APP_USER" bash -lc "$1"
}

log "Aggiorno pacchetti di sistema"
sudo apt-get update
sudo apt-get install -y curl git nginx ufw rsync ca-certificates gnupg python3 python3-pip python3-venv logrotate

if ! command -v node >/dev/null 2>&1 || ! node -v | grep -Eq '^v(18|20|21|22)\.'; then
  log "Installo Node.js ${NODE_MAJOR}.x"
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | sudo -E bash -
  sudo apt-get install -y nodejs
fi

if [ ! -d "$APP_DIR" ]; then
  if [ -z "$REPO_URL" ]; then
    echo "ERRORE: repo non trovato in $APP_DIR e VIGIL_REPO_URL non impostata."
    echo "Carica il progetto sul server oppure esporta VIGIL_REPO_URL prima di lanciare lo script."
    exit 1
  fi
  log "Clono il repository in $APP_DIR"
  sudo git clone "$REPO_URL" "$APP_DIR"
  sudo chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
else
  sudo chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
  if [ -d "$APP_DIR/.git" ]; then
    log "Aggiorno il repository locale"
    run_as_app_user "cd '$APP_DIR' && git pull --ff-only"
  fi
fi

log "Preparo virtualenv e dipendenze Python"
run_as_app_user "cd '$APP_DIR' && $PYTHON_BIN -m venv venv && ./venv/bin/pip install --upgrade pip wheel && ./venv/bin/pip install -r requirements.txt"

if [ ! -f "$APP_DIR/.env" ] && [ -f "$APP_DIR/.env.example" ]; then
  log "Creo .env da .env.example"
  run_as_app_user "cd '$APP_DIR' && cp .env.example .env"
  echo ""
  echo "⚠️  Configura $APP_DIR/.env con i valori reali prima dell'uso in produzione."
fi

log "Installo dipendenze frontend e buildo Vite"
if [ -f "$FRONTEND_DIR/package-lock.json" ]; then
  run_as_app_user "cd '$FRONTEND_DIR' && npm ci && npm run build"
else
  run_as_app_user "cd '$FRONTEND_DIR' && npm install && npm run build"
fi

log "Pubblico il frontend statico in $WEB_ROOT"
sudo mkdir -p "$WEB_ROOT"
sudo rsync -a --delete "$FRONTEND_DIR/dist/" "$WEB_ROOT/"
sudo chown -R www-data:www-data "$WEB_ROOT"

log "Installo il servizio systemd"
sudo cp "$APP_DIR/vigil.service" /etc/systemd/system/vigil.service
sudo mkdir -p /var/log/vigil
sudo chown "$APP_USER:$APP_GROUP" /var/log/vigil
sudo chmod 755 /var/log/vigil

if [ -f "$APP_DIR/vigil.logrotate" ]; then
  log "Installo logrotate per i log applicativi"
  sudo cp "$APP_DIR/vigil.logrotate" /etc/logrotate.d/vigil
  sudo sed -i "s/^  su .*/  su ${APP_USER} ${APP_GROUP}/" /etc/logrotate.d/vigil
  sudo chmod 644 /etc/logrotate.d/vigil
fi

if [ -f "$APP_DIR/tools/backup_vigil_db.sh" ] && [ -f "$APP_DIR/vigil-db-backup.service" ] && [ -f "$APP_DIR/vigil-db-backup.timer" ]; then
  log "Installo backup automatico database"
  sudo install -m 755 "$APP_DIR/tools/backup_vigil_db.sh" /usr/local/bin/backup_vigil_db.sh
  sudo cp "$APP_DIR/vigil-db-backup.service" /etc/systemd/system/vigil-db-backup.service
  sudo cp "$APP_DIR/vigil-db-backup.timer" /etc/systemd/system/vigil-db-backup.timer
  sudo sed -i "s|^User=.*|User=${APP_USER}|" /etc/systemd/system/vigil-db-backup.service
  sudo sed -i "s|^Group=.*|Group=${APP_GROUP}|" /etc/systemd/system/vigil-db-backup.service
  sudo sed -i "s|^Environment=APP_DIR=.*|Environment=APP_DIR=${APP_DIR}|" /etc/systemd/system/vigil-db-backup.service
  sudo sed -i "s|^Environment=DB_PATH=.*|Environment=DB_PATH=${APP_DIR}/vigil.db|" /etc/systemd/system/vigil-db-backup.service
  sudo sed -i "s|^Environment=BACKUP_DIR=.*|Environment=BACKUP_DIR=${BACKUP_DIR}|" /etc/systemd/system/vigil-db-backup.service
  sudo sed -i "s|^Environment=BACKUP_KEEP_DAYS=.*|Environment=BACKUP_KEEP_DAYS=${BACKUP_KEEP_DAYS}|" /etc/systemd/system/vigil-db-backup.service
  sudo mkdir -p "$BACKUP_DIR"
  sudo chown "$APP_USER:$APP_GROUP" "$BACKUP_DIR"
  sudo chmod 750 "$BACKUP_DIR"
fi

sudo systemctl daemon-reload
sudo systemctl enable vigil
sudo systemctl restart vigil

if [ -f /etc/systemd/system/vigil-db-backup.timer ]; then
  sudo systemctl enable vigil-db-backup.timer
  sudo systemctl restart vigil-db-backup.timer
fi

log "Configuro Nginx"
sudo cp "$APP_DIR/nginx.conf" /etc/nginx/sites-available/vigil

if [ -n "${VIGIL_DOMAIN:-}" ]; then
  sudo sed -i "s/server_name _;/server_name ${VIGIL_DOMAIN};/" /etc/nginx/sites-available/vigil
fi

sudo ln -sfn /etc/nginx/sites-available/vigil /etc/nginx/sites-enabled/vigil
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

if [ -n "${VIGIL_DOMAIN:-}" ]; then
  log "Configuro HTTPS con Let's Encrypt"
  sudo apt-get install -y certbot python3-certbot-nginx
  CERTBOT_ARGS=(--nginx -d "$VIGIL_DOMAIN" --non-interactive --agree-tos --redirect)
  if [ -n "$LETSENCRYPT_EMAIL" ]; then
    CERTBOT_ARGS+=(--email "$LETSENCRYPT_EMAIL")
  else
    CERTBOT_ARGS+=(--register-unsafely-without-email)
  fi
  sudo certbot "${CERTBOT_ARGS[@]}" || log "Certbot non completato: verifica DNS e ripeti manualmente"
fi

log "Aggiorno il firewall"
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw --force enable

log "Verifico lo stato API"
if curl -fsS http://127.0.0.1:8000/health >/dev/null; then
  HEALTH_OK="yes"
else
  HEALTH_OK="no"
fi

echo ""
echo "=== Deploy completato ==="
echo "Frontend:    http://${PUBLIC_HOST}/"
echo "API:         http://${PUBLIC_HOST}/api/"
echo "Health:      http://${PUBLIC_HOST}/api/health"
echo "API health:  ${HEALTH_OK}"
echo ""
echo "Comandi utili:"
echo "  sudo systemctl status vigil"
echo "  sudo journalctl -u vigil -f"
echo "  sudo systemctl restart vigil"
echo "  sudo systemctl status vigil-db-backup.timer"
echo "  sudo ls -lh ${BACKUP_DIR}"
echo "  sudo tail -n 50 /var/log/vigil/app.log"
echo "  sudo nginx -t && sudo systemctl reload nginx"
