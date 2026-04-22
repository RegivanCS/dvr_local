#!/bin/bash
# ============================================================
# DVR Local — Instalador Linux (Ubuntu/Debian)
# Instala como serviço systemd e cria atalho de terminal
#
# Uso: sudo bash install.sh
# ============================================================

set -e

APP_NAME="dvr_local"
INSTALL_DIR="/opt/dvr_local"
SERVICE_USER="dvr"
PYTHON_MIN="3.10"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[AVISO]${NC} $1"; }
err_exit(){ echo -e "${RED}[ERRO]${NC} $1"; exit 1; }

echo "============================================"
echo "  DVR Local — Instalação Linux"
echo "============================================"
echo

# ── Verificar root ────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && err_exit "Execute como root: sudo bash install.sh"

# ── Verificar Python ──────────────────────────────────────────────────────────
PYTHON=$(command -v python3 || true)
[[ -z "$PYTHON" ]] && err_exit "Python 3 não encontrado. Instale: sudo apt install python3 python3-pip python3-venv"
info "Python: $($PYTHON --version)"

# ── Instalar dependências do sistema ──────────────────────────────────────────
info "Instalando dependências do sistema..."
apt-get update -qq
apt-get install -y -qq python3-pip python3-venv ffmpeg libgtk-3-dev libwebkit2gtk-4.0-dev \
    gir1.2-webkit2-4.0 python3-gi python3-gi-cairo gobject-introspection \
    libgirepository1.0-dev 2>/dev/null || true

# ── Criar usuário de serviço ───────────────────────────────────────────────────
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd -r -s /bin/false -d "$INSTALL_DIR" "$SERVICE_USER"
    info "Usuário '$SERVICE_USER' criado."
fi

# ── Copiar arquivos ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(realpath "$SCRIPT_DIR/../..")"

info "Instalando em $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
    --exclude='dist' --exclude='build' --exclude='*.pyc' \
    "$SRC_DIR/" "$INSTALL_DIR/"

# ── Criar ambiente virtual ────────────────────────────────────────────────────
info "Criando ambiente virtual Python..."
$PYTHON -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

# ── Permissões ────────────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR/logs" "$INSTALL_DIR/recordings"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ── Instalar serviço systemd ──────────────────────────────────────────────────
info "Instalando serviço systemd..."
cp "$SCRIPT_DIR/dvr_local.service" /etc/systemd/system/
# Ajusta caminhos no service file
sed -i "s|/opt/dvr_local|$INSTALL_DIR|g" /etc/systemd/system/dvr_local.service
sed -i "s|User=dvr|User=$SERVICE_USER|g" /etc/systemd/system/dvr_local.service

systemctl daemon-reload
systemctl enable dvr_local
systemctl start dvr_local

# ── Atalho de linha de comando ────────────────────────────────────────────────
cat > /usr/local/bin/dvr << EOF
#!/bin/bash
case "\$1" in
    start)   systemctl start dvr_local ;;
    stop)    systemctl stop dvr_local ;;
    restart) systemctl restart dvr_local ;;
    status)  systemctl status dvr_local ;;
    logs)    journalctl -u dvr_local -f ;;
    *)       echo "Uso: dvr {start|stop|restart|status|logs}" ;;
esac
EOF
chmod +x /usr/local/bin/dvr

echo
echo "============================================"
info "DVR Local instalado com sucesso!"
echo "  Interface: http://localhost:8000"
echo "  Comandos:"
echo "    dvr start    — iniciar"
echo "    dvr stop     — parar"
echo "    dvr status   — status"
echo "    dvr logs     — ver logs ao vivo"
echo "============================================"
