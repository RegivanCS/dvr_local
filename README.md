# 🎥 DVR Local - Sistema de Câmeras IP

Aplicativo standalone para visualização de múltiplas câmeras IP sem depender de software externo.

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

Abra o navegador em: **http://localhost:5000/**

## ⚙️ Configuração de Câmeras

### Adicionar Câmera Manualmente

1. Acesse **http://localhost:5000/config**
2. Preencha os dados:
   - **Nome**: Ex: "Câmera Entrada"
   - **IP**: Ex: 192.168.1.3
   - **Porta**: Geralmente 80
   - **Usuário**: admin (ou conforme sua câmera)
   - **Senha**: Senha da câmera
   - **Modelo**: Selecione o modelo correto
3. Clique em **Adicionar Câmera**

O sistema irá **testar automaticamente** a conexão antes de salvar!

### Buscar Câmeras Automaticamente

1. Acesse **http://localhost:5000/scan**
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

1. Verifique se o IP está correto: `ping 192.168.1.3`
2. Teste no navegador: `http://192.168.1.3/snapshot.cgi`
3. Confirme usuário e senha
4. Tente outro modelo de câmera

### Porta 5000 ocupada

Edite a última linha de `app.py`:

```python
app.run(host='0.0.0.0', port=8000, threaded=True, debug=False)
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
      "ip": "192.168.1.3",
      "port": "80",
      "user": "admin",
      "password": "Herb1745@",
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
2. Acesse de outro dispositivo: `http://SEU_IP:5000`

Exemplo: `http://192.168.1.7:5000`

## ⚠️ Credenciais das Câmeras ISCEE

Para suas câmeras ISCEE:
- **IP 1**: 192.168.1.3:80
- **IP 2**: 192.168.1.10:80
- **Usuário**: admin
- **Senha**: Herb1745@
- **Modelo**: ISCEE / Genérico

## 🆘 Suporte

Logs são exibidos no terminal onde você executou `python app.py`.

Verifique mensagens de erro para diagnosticar problemas.

---

**Desenvolvido para execução standalone em qualquer PC com Python instalado.**
