# DVR Local — Deployment & Instalação

## Visão Geral

O **DVR Local v1.1** é um sistema de vigilância com câmeras IP que:
- Gera ao vivo fluido (5fps via ffmpeg contínuo)
- Oferece zoom digital interativo
- Grava automaticamente com limite de armazenamento cíclico
- Funciona 24/7 ligado em um PC/servidor dedicado
- **Não precisa** de VS Code, Chrome, Edge ou dependências externas após instalação

---

## Arquitetura

```
┌─────────────────────────────────────────┐
│  dvr_launcher.py (ou .exe/systemd)     │  ← Interface principal
├─────────────────────────────────────────┤
│  ┌──────────────────────────────────┐   │
│  │ Serviços em background:          │   │
│  │  • rtsp_proxy.py      (5fps)     │   │
│  │  • tunnel_relay.py    (Tunnel)   │   │
│  │  • motion_recorder.py (IA)       │   │
│  │  • recordings_relay.py (Storage) │   │
│  │  • app.py Flask (8000)           │   │
│  └──────────────────────────────────┘   │
└─────────────────────────────────────────┘
        ↓
    http://127.0.0.1:8000 (local)
    https://dvr.regivan.tec.br (remoto, via Cloudflare)
```

---

## 3 Formas de Deployment

### 1️⃣ **Versão Portátil (Simples)**

Para um pendrive ou pasta local sem instalação:

```bash
# Na pasta do DVR:
python -m venv .venv
.venv\Scripts\activate   # Windows
source .venv/bin/activate  # Linux/Mac

pip install -r requirements.txt
python dvr_launcher.py
```

Ou simplesmente clicar em `dvr_launcher.exe` se já compilado.

---

### 2️⃣ **Windows: Instalador .exe**

#### Pré-requisito
- [Inno Setup 6](https://jrsoftware.org/isinfo.php)

#### Build & Install

```bash
cd build\windows
build.bat
```

Gera:
- `dist\dvr_launcher\` — versão portátil pronta
- `dist\DVR_Local_Setup_v1.1.exe` — instalador (se Inno Setup disponível)

#### Usando o instalador:
- Duplo clique em `DVR_Local_Setup_v1.1.exe`
- Escolha o destino (padrão: `C:\Program Files\DVR Local`)
- Opção de criar atalho na Área de Trabalho
- Opção de iniciar automaticamente com Windows
- Clique Finish → inicializa automaticamente

**Após instalar:**
- Ícone na Área de Trabalho → clique para rodar
- Interface abre no browser (http://127.0.0.1:8000)
- Ícone na bandeja (tray) do Windows se `pystray` estiver instalado

---

### 3️⃣ **Linux: Instalação como Serviço**

#### Pré-requisito
```bash
sudo apt-get install -y python3-pip python3-venv ffmpeg
```

#### Instalar como serviço systemd

```bash
sudo bash build/linux/install.sh
```

Ele irá:
1. Criar usuário `dvr`
2. Instalar em `/opt/dvr_local`
3. Configurar serviço systemd (`dvr_local.service`)
4. Criar comando CLI `dvr`

#### Controle do serviço

```bash
# Iniciar DVR
sudo dvr start

# Parar DVR
sudo dvr stop

# Status
sudo dvr status

# Ver logs ao vivo
sudo dvr logs

# Inicialização automática (já habilitada pós-instalação)
sudo systemctl enable dvr_local
```

**Acesso à interface:**
- Local: http://127.0.0.1:8000
- Remoto: https://dvr.regivan.tec.br (se tunnel configurado)

---

## Configuração Inicial

### 1. Login
- **Usuário:** `admin`
- **Senha:** `!Rede!123` (padrão)

### 2. Adicionar Câmeras
Vá para **⚙️ Configurar**:
1. Clique no botão **[+]** para adicionar câmera
2. Preencha:
   - **IP:** 192.168.1.5 (exemplo)
   - **Porta:** 80
   - **Usuário/Senha:** credenciais da câmera
   - **Path:** `/snapshot.cgi` (depende do modelo)
   - **Nome:** Nome exibido (ex: "Câmera Portão")
3. Clique **Testar** → se conectar, salva automaticamente

### 3. Ativar Detecção de Movimento
- Dashboard: clique **⏺️ Gravar todas** para ativar

### 4. Configurar Armazenamento Cíclico
- **⚙️ Configurar** → **Armazenamento e Gravação Cíclica**
  - **Limite máximo:** (ex: 50 GB)
  - **Reservar livre:** (ex: 10 GB)
  - Salva automaticamente

Quando atingir o limite, arquivos mais antigos são deletados automaticamente.

---

## Recursos da v1.1

✅ **Launcher GUI** — Bandeja do sistema + janela embutida (pywebview)
✅ **Ao vivo fluido** — 5 fps via ffmpeg contínuo
✅ **Zoom digital** — +/- no fullscreen, scroll mouse, pinch em touch
✅ **Gravação cíclica** — Limite de armazenamento configurável
✅ **Auto-heal** — Câmeras desativadas são reativadas automaticamente
✅ **Responsividade** — Mobile/tablet ready
✅ **Instaladores Windows/Linux** — Sem necessidade de VS Code

---

## Troubleshooting

### "Port 8000 already in use"
```bash
# Windows: killar processo
netstat -ano | findstr :8000
taskkill /PID <pid> /F

# Linux: killar processo
sudo lsof -i :8000
sudo kill -9 <pid>
```

### Câmera não conecta
1. Verifique credenciais em **⚙️ Configurar**
2. Teste em: `http://<camera_ip>:<camera_port><path>` via browser
3. Verifique firewall — não bloqueie a porta da câmera

### Gravações não aparecem
- Vá para **🎞️ Gravações**
- Se vazio, ative detecção: clique **⏺️ Gravar todas**
- Aguarde detecção de movimento (5-10 minutos)

---

## Comandos Úteis

### Windows (PowerShell)

```powershell
# Ver processos DVR
Get-Process python | Where-Object {$_.CommandLine -match 'dvr|rtsp'}

# Ver logs do app
Get-Content logs/app.log -Tail 50

# Parar todos DVR
Get-Process python | Where-Object {$_.CommandLine -match 'dvr|rtsp'} | Stop-Process -Force
```

### Linux

```bash
# Ver logs
journalctl -u dvr_local -f

# Ver consumo de disco
du -sh /opt/dvr_local/recordings

# Reiniciar serviço
sudo systemctl restart dvr_local

# Remover instalação
sudo systemctl stop dvr_local
sudo rm -rf /opt/dvr_local /etc/systemd/system/dvr_local.service
sudo systemctl daemon-reload
```

---

## Versioning

- **v1.0** — Estável com ao vivo fluido, zoom digital, gravação cíclica
- **v1.1** — Launcher GUI, instaladores Windows/Linux, bandeja do sistema

---

## Licença & Suporte

DVR Local | RegivanCS  
GitHub: https://github.com/RegivanCS/dvr_local

Desenvolvido e mantido por Regivan Carvalho Dos Santos
