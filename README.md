# 🎥 DVR Local — Sistema de Câmeras IP

Visualização remota de câmeras IP via browser, com autenticação, scanner automático e cadastro com um comando.

**Acesso remoto:** https://dvr.regivan.tec.br

---

## 🏗️ Arquitetura

```
[Câmeras IP] ── rede local ──[Roteador]── Internet ──[dvr.regivan.tec.br]
                                  │                         │
                            port forwarding           Flask + Passenger
                            8081 → cam1:80            Apache + ModSecurity
                            8082 → cam2:80            DirectAdmin (Python 3.11)
```

O app roda no servidor e busca snapshots das câmeras **server-side**. O browser só conversa com o servidor.

---

## 🚀 Instalação Local (desenvolvimento)

```bash
pip install -r requirements.txt
python app.py
# Acesse: http://localhost:8000/
```

---

## ☁️ Deploy (DirectAdmin + Phusion Passenger)

### Estrutura no servidor

```
/home/agspejkk/domains/dvr.regivan.tec.br/
├── app.py
├── passenger_wsgi.py       ← ponto de entrada do Passenger
├── cameras_config.json     ← configurações e credenciais
└── public_html/
    └── .htaccess           ← configuração do Passenger
```

### `public_html/.htaccess`

```apache
# DO NOT REMOVE. CLOUDLINUX PASSENGER CONFIGURATION BEGIN
PassengerAppRoot "/home/agspejkk/domains/dvr.regivan.tec.br"
PassengerBaseURI "/"
PassengerPython "/home/agspejkk/virtualenv/domains/dvr.regivan.tec.br/3.11/bin/python"
# DO NOT REMOVE. CLOUDLINUX PASSENGER CONFIGURATION END
AuthType None
```

### `passenger_wsgi.py`

```python
import os
_KEY = 'sua-chave-de-64-caracteres-aqui'  # gere em: https://generate-secret.vercel.app/64
os.environ['DVR_SECRET_KEY'] = _KEY
from app import app as application
application.secret_key = _KEY
```

> ⚠️ A chave precisa ser **fixa** — todos os workers do Passenger compartilham a mesma para não perder sessões.

### `cameras_config.json` (criar manualmente no servidor)

```json
{
  "cameras": {},
  "auth": { "user": "admin", "password": "sua-senha-aqui" }
}
```

---

## 📹 Adicionar Câmeras

### Opção 1 — Scanner automático (recomendado)

Rode **na rede local onde estão as câmeras**:

```bash
# Edite as configurações no topo do arquivo primeiro
python discover_cameras.py
```

O script faz tudo automaticamente:
1. Detecta o IP público do roteador
2. Escaneia toda a subnet `/24` (TCP + HTTP)
3. Atualiza o `cameras_config.json` local (IPs internos)
4. Faz login e cadastra no DVR remoto com IP público + porta redirecionada
5. Exibe o resumo de port forwarding para configurar no roteador

**Configurações do script** (`discover_cameras.py`):

| Variável | Descrição |
|---|---|
| `DVR_URL` | URL do app remoto |
| `DVR_USER` / `DVR_PASSWORD` | Credenciais do DVR app |
| `CAM_USER` / `CAM_PASSWORD` | Credenciais das câmeras |
| `CAM_MODEL` | `generic`, `hikvision`, `dahua`, `intelbras`, `iscee` |
| `PORT_FORWARD_START` | Primeira porta externa do roteador (ex: `8081`) |
| `AUTO_REGISTER` | `True` = cadastra no remoto automaticamente |
| `SAVE_LOCAL_CONFIG` | `True` = salva no `cameras_config.json` local |

### Opção 2 — Manual via interface

Acesse `https://dvr.regivan.tec.br/cameras` → **Adicionar Nova Câmera**

---

## 🔐 Acesso Remoto / Rede Móvel

Para que o servidor acesse as câmeras de fora da rede local:

1. **Configure port forwarding no roteador** — uma porta por câmera:
   ```
   IP_público:8081  →  192.168.1.x:80   (câmera 1)
   IP_público:8082  →  192.168.1.y:80   (câmera 2)
   ```

2. **IP público dinâmico?** Configure DDNS:
   - [No-IP](https://noip.com), [DuckDNS](https://duckdns.org)
   - Ou o DDNS embutido no roteador

3. O `discover_cameras.py` detecta o IP público e exibe o resumo de port forwarding automaticamente.

---

## 📷 Modelos de câmera suportados

| Modelo | Paths testados |
|---|---|
| ISCEE / Genérico | `/snapshot.cgi`, `/tmpfs/auto.jpg`, `/cgi-bin/snapshot.cgi`, `/image.jpg` |
| Hikvision | `/ISAPI/Streaming/channels/1/picture`, `/Streaming/channels/1/picture` |
| Dahua | `/cgi-bin/snapshot.cgi`, `/onvif/snapshot` |
| Intelbras | `/cgi-bin/snapshot.cgi`, `/snapshot.cgi` |
| Genérico | Testa todos os paths acima |

---

## 🗂️ Rotas principais

| Rota | Descrição |
|---|---|
| `/` | Dashboard com câmeras |
| `/login` / `/logout` | Autenticação |
| `/cameras` | Configuração de câmeras |
| `/scan` | Scanner de rede (server-side) |
| `/camera/<id>` | Stream MJPEG |
| `/api/camera/add` | POST — adicionar câmera |
| `/api/camera/edit/<id>` | POST — editar |
| `/api/camera/delete/<id>` | POST — remover |
| `/api/camera/toggle/<id>` | POST — ativar/desativar |
| `/api/auth/set` | POST — alterar credenciais |

---

## 🛡️ Segurança

- Login com `secrets.compare_digest` (resistente a timing attack)
- Cookie `HttpOnly` + `Secure` + `SameSite=Lax`
- User-Agent customizado (evita WAF rule 913113 do ModSecurity)
- Secret key fixa no `passenger_wsgi.py` (consistência entre workers)
- Validação de `next_url` no login (evita open redirect)

---

## ⚠️ Rotas bloqueadas pelo DirectAdmin

O DirectAdmin intercepta algumas rotas antes do Passenger/Flask:

| Rota bloqueada | Alternativa |
|---|---|
| `/config` | `/cameras` ✅ |

Se outras rotas redirecionarem para `:2222`, renomeie-as.

---

## 🔧 Solução de Problemas

| Problema | Solução |
|---|---|
| Câmera não conecta | Verifique IP, porta, usuário/senha e modelo |
| Sessão perdida após login | Verifique se `DVR_SECRET_KEY` está fixo no `passenger_wsgi.py` |
| WAF bloqueando requisições | User-Agent já está configurado como Mozilla |
| Porta 8000 ocupada (local) | Edite o `port=8000` na última linha do `app.py` |

---

## 📦 Dependências

```
flask==3.1.0
requests==2.32.3
```

## 📋 Requisitos

- Python 3.7 ou superior
- Câmeras IP acessíveis na rede

## 🚀 Instalação Rápida

### 1. Instalar Dependências

```bash
pip install -r requirements.txt
```

### 2. Executar Aplicação

```bash
python app.py
```

### 3. Acessar Interface

Abra o navegador em: **http://localhost:8000/**

## ⚙️ Configuração de Câmeras

### Adicionar Câmera Manualmente

1. Acesse **http://localhost:8000/config**
2. Preencha os dados:
   - **Nome**: Ex: "Câmera Entrada"
   - **IP**: Ex: 192.168.1.100
   - **Porta**: Geralmente 80
   - **Usuário**: admin (ou conforme sua câmera)
   - **Senha**: Senha da câmera
   - **Modelo**: Selecione o modelo correto
3. Clique em **Adicionar Câmera**

O sistema irá **testar automaticamente** a conexão antes de salvar!

### Buscar Câmeras Automaticamente

1. Acesse **http://localhost:8000/scan**
2. Clique em **Iniciar Escaneamento**
3. Aguarde alguns minutos enquanto busca na rede
4. Adicione manualmente as câmeras encontradas

## 📷 Modelos Suportados

### ISCEE / Genérico
- Paths testados: `/snapshot.cgi`, `/tmpfs/auto.jpg`, `/cgi-bin/snapshot.cgi`, `/image.jpg`

### Hikvision
- Paths testados: `/ISAPI/Streaming/channels/1/picture`, `/Streaming/channels/1/picture`

### Dahua
- Paths testados: `/cgi-bin/snapshot.cgi`, `/onvif/snapshot`

### Intelbras
- Paths testados: `/cgi-bin/snapshot.cgi`, `/snapshot.cgi`

### Genérico (tentar todos)
- Testa múltiplos paths comuns de câmeras IP

## 🖥️ Instalação em Outro PC

### Copiar Arquivos Necessários

Copie estes arquivos para o outro PC:

```
📁 dvr_local/
├── app.py
├── requirements.txt
└── cameras_config.json (criado automaticamente após configurar)
```

### Instalação no Novo PC

1. **Instalar Python**: https://www.python.org/downloads/
2. **Abrir PowerShell** na pasta do projeto
3. **Instalar dependências**:
   ```bash
   pip install -r requirements.txt
   ```
4. **Executar**:
   ```bash
   python app.py
   ```

## 🔧 Solução de Problemas

### Câmera não conecta

1. Verifique se o IP está correto: `ping SEU_IP_CAMERA`
2. Teste no navegador: `http://SEU_IP_CAMERA/snapshot.cgi`
3. Confirme usuário e senha
4. Tente outro modelo de câmera

### Porta 8000 ocupada

Edite a última linha de `app.py`:

```python
app.run(host='0.0.0.0', port=OUTRA_PORTA, threaded=True, debug=False)
```

### Scanner não encontra câmeras

- Aguarde o escaneamento completo (pode levar 5-10 minutos)
- Verifique se está na mesma rede das câmeras
- Adicione manualmente se souber o IP

## 📝 Configuração Manual de Câmeras

O arquivo `cameras_config.json` é criado automaticamente. Você pode editá-lo manualmente:

```json
{
  "cameras": {
    "1234567890": {
      "name": "Câmera Entrada",
      "ip": "SEU_IP_AQUI",
      "port": "80",
      "user": "SEU_USUARIO",
      "password": "SUA_SENHA",
      "model": "iscee",
      "path": "/snapshot.cgi",
      "enabled": true
    }
  }
}
```

## 🎯 Funcionalidades

- ✅ Visualização simultânea de múltiplas câmeras
- ✅ Modo tela cheia (clique na câmera)
- ✅ Adicionar/remover câmeras via web
- ✅ Ativar/desativar câmeras temporariamente
- ✅ Scanner de rede automático
- ✅ Teste de conexão antes de salvar
- ✅ Suporte a múltiplos modelos de câmera
- ✅ Interface responsiva (funciona em celular)
- ✅ Totalmente standalone (não depende de Agent DVR ou outros softwares)

## 📱 Acesso Remoto

Para acessar de outros dispositivos na rede:

1. Descubra o IP do PC: `ipconfig` (Windows) ou `ifconfig` (Linux/Mac)
2. Acesse de outro dispositivo: `http://SEU_IP:8000`

Exemplo: `http://192.168.1.X:8000`

## 🆘 Suporte

Logs são exibidos no terminal onde você executou `python app.py`.

Verifique mensagens de erro para diagnosticar problemas.

---

**Desenvolvido para execução standalone em qualquer PC com Python instalado.**
