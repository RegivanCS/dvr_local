#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Instalar dependências e servir o app.py de forma independente
"""

import subprocess
import sys
import os

print("=" * 70)
print("🚀 Configurando DVR Local - Aplicativo Independente")
print("=" * 70)

# Instalar dependências
print("\n1️⃣ Instalando dependências...")
print("-" * 70)

try:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"])
    print("✓ Dependências instaladas com sucesso!")
except Exception as e:
    print(f"❌ Erro ao instalar dependências: {e}")
    sys.exit(1)

# Criar config padrão se não existir
if not os.path.exists("cameras_config.json"):
    import json
    
    default_config = {
        "cameras": {
            "entrada": {
                "name": "Esntrada",
                "ip": "192.168.1.3",
                "port": "554",
                "user": "admin",
                "password": "Herb1745@",
                "model": "iscee",
                "path": "/snapshot.cgi",
                "enabled": True,
                "protocol": "rtsp"
            },
            "frente": {
                "name": "Frente",
                "ip": "192.168.1.10",
                "port": "554",
                "user": "admin",
                "password": "Herb1745@",
                "model": "iscee",
                "path": "/snapshot.cgi",
                "enabled": True,
                "protocol": "rtsp"
            }
        }
    }
    
    with open("cameras_config.json", "w", encoding="utf-8") as f:
        json.dump(default_config, f, indent=2, ensure_ascii=False)
    
    print("\n2️⃣ Configuração padrão criada!")
    print("   ✓ cameras_config.json (2 câmeras RTSP configuradas)")

print("\n3️⃣ Iniciando servidor...")
print("-" * 70)
print("\n🌐 Acesse: http://localhost:5001/")
print("\n⚙️ Para configurar câmeras: http://localhost:5001/config")
print("🔍 Para buscar câmeras: http://localhost:5001/scan")
print("\n" + "=" * 70)

# Iniciar app
import app as flask_app

if __name__ == "__main__":
    flask_app.app.run(host='0.0.0.0', port=5001, threaded=True, debug=False)
