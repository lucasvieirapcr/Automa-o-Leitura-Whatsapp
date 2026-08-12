#!/usr/bin/env python3
"""
connect_whatsapp.py — Conecta número WhatsApp via QR Code
"""
import json
import time
import sys
import os
import argparse
import subprocess
from urllib.parse import quote
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Evolution API local — apikey padrão (definida no docker-compose)
EVOLUTION_API_KEY = "B6D711FCDE4D4FD5936544120E713976"
DEFAULT_URL = "http://localhost:8080"
DEFAULT_INSTANCE = "meu-agente"

# Preenchidos em main() a partir das flags / variáveis de ambiente
EVOLUTION_URL = DEFAULT_URL
API_KEY = None

def _get_api_key():
    """Chave explícita > variável de ambiente > .env local > padrão."""
    if API_KEY:
        return API_KEY
    if os.environ.get("EVOLUTION_API_KEY"):
        return os.environ["EVOLUTION_API_KEY"]

    env_file = Path.home() / "meu-agente" / "evolution-api" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding='utf-8', errors='ignore').splitlines():
            if line.startswith("API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    # Fallback: tenta a apikey padrão da instalação existente
    return EVOLUTION_API_KEY

def call_api(endpoint, method="GET", data=None):
    """Faz chamada HTTP para Evolution API com apikey obrigatório."""
    import urllib.request
    import urllib.error

    url = f"{EVOLUTION_URL}{endpoint}"
    api_key = _get_api_key()

    headers = {
        "Content-Type": "application/json",
        "apikey": api_key,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode() if data else None,
        headers=headers,
        method=method
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read())
    except Exception as e:
        return {"error": str(e)}

def sem_tela():
    """True quando não há interface gráfica (típico de VPS via SSH)."""
    if sys.platform in ("win32", "darwin"):
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def mostrar_qr_ascii(codigo):
    """
    Desenha o QR Code direto no terminal — única forma de escanear
    quando o setup roda por SSH, sem interface gráfica.
    """
    try:
        import qrcode
    except ImportError:
        print("   ⚠️  Para exibir o QR Code no terminal, instale a biblioteca:")
        print("        pip3 install qrcode")
        print("\n   Alternativa: copie o código abaixo em qualquer gerador de QR online")
        print(f"   e escaneie a imagem gerada:\n\n{codigo}\n")
        return False

    qr = qrcode.QRCode(border=1)
    qr.add_data(codigo)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    return True


def main():
    global EVOLUTION_URL, API_KEY

    parser = argparse.ArgumentParser(description="Conecta o WhatsApp via QR Code")
    parser.add_argument("--ascii", action="store_true",
                        help="força o QR Code no terminal (servidor sem tela)")
    parser.add_argument("--url", help="endereço da Evolution API")
    parser.add_argument("--key", help="apikey da Evolution API")
    parser.add_argument("--instance", help="nome da instância")
    args = parser.parse_args()

    EVOLUTION_URL = (args.url or os.environ.get("EVOLUTION_URL") or DEFAULT_URL).rstrip("/")
    API_KEY = args.key

    print("=" * 60)
    print("📱 Conectando WhatsApp")
    print("=" * 60)
    print(f"   Evolution API: {EVOLUTION_URL}")

    instance_name = args.instance or os.environ.get("INSTANCE_NAME") or DEFAULT_INSTANCE

    # 1. Verificar se instância já existe
    print(f"\n1️⃣  Verificando instância: {instance_name}")
    existing = call_api(f"/instance/fetchInstances", method="GET")
    instances = existing if isinstance(existing, list) else []
    # Evolution API v2: campo "name" direto; v1: dentro de "instance.instanceName"
    instance_names = [
        i.get("name", "") or i.get("instance", {}).get("instanceName", "")
        for i in instances
    ]

    if instance_name in instance_names:
        print(f"   ✅ Instância já existe: {instance_name}")
    else:
        print(f"   Criando instância...")
        result = call_api("/instance/create", method="POST", data={
            "instanceName": instance_name,
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS"
        })
        if "error" in result and "already" not in str(result.get("error", "")):
            print(f"   ❌ Erro ao criar instância: {result['error']}")
            return
        print(f"   ✅ Instância criada")

    # 2. Gerar QR Code
    print("\n2️⃣  Gerando QR Code...")
    qr_result = call_api(f"/instance/connect/{quote(instance_name, safe='')}", method="GET")

    # Evolution API v2: base64 direto; v1: dentro de "qrcode.base64" ou string
    qr_data = qr_result.get("base64")
    qr_code = qr_result.get("code")          # string crua do QR (serve para ASCII)
    if not qr_data:
        qrcode_field = qr_result.get("qrcode")
        if isinstance(qrcode_field, dict):
            qr_data = qrcode_field.get("base64")
            qr_code = qr_code or qrcode_field.get("code")
        elif isinstance(qrcode_field, str):
            qr_data = qrcode_field

    modo_terminal = args.ascii or sem_tela()

    if modo_terminal and qr_code:
        print("   (servidor sem interface gráfica — desenhando o QR Code aqui)\n")
        mostrar_qr_ascii(qr_code)
        print("   📱 WhatsApp no celular → Configurações → Aparelhos Conectados → Conectar Aparelho")
        print("   💡 Se o QR sair cortado, aumente a janela do terminal e rode de novo.")
    elif modo_terminal and qr_data:
        # Servidor sem tela e sem a string crua do QR: salva a imagem e ensina a abrir
        import base64
        img_path = Path.home() / "meu-agente" / "qrcode.png"
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(base64.b64decode(qr_data.split(",")[-1]))
        print(f"   ✅ QR Code salvo em: {img_path}")
        print("\n   Para vê-lo do seu computador, rode NA SUA MÁQUINA:")
        print(f"     scp -P PORTA_SSH usuario@IP_DA_VPS:{img_path} .")
        print("   (-P maiúsculo; a porta SSH da HostGator costuma ser 22022)")
        print("   E abra o arquivo qrcode.png que for baixado.")
    elif qr_data:
        # Salvar QR Code como imagem PNG e abrir no visualizador
        import base64, subprocess, tempfile, os
        img_path = Path(tempfile.gettempdir()) / "agente-qrcode.png"
        try:
            img_bytes = base64.b64decode(qr_data.split(",")[-1])
            img_path.write_bytes(img_bytes)
            if hasattr(os, 'startfile'):
                os.startfile(str(img_path))
            elif sys.platform == 'darwin':
                subprocess.Popen(["open", str(img_path)])
            else:
                subprocess.Popen(["xdg-open", str(img_path)])
            print(f"   ✅ QR Code aberto na tela!")
            print(f"   📱 Abra o WhatsApp no celular → Configurações → Aparelhos Conectados → Conectar Aparelho")
        except Exception:
            print(f"   QR Code (cole em um decoder online): {str(qr_data)[:200]}")
    else:
        print(f"   ⚠️  Resposta inesperada: {qr_result}")

    # 3. Aguardar conexão
    print("\n3️⃣  Aguardando scan do QR Code (até 90s)...")
    for i in range(90):
        status_result = call_api(f"/instance/connectionState/{quote(instance_name, safe='')}", method="GET")
        state = status_result.get("instance", {}).get("state", "") or status_result.get("state", "")

        if state == "open":
            print(f"   ✅ WhatsApp conectado!")
            break

        if i % 15 == 0 and i > 0:
            print(f"   (aguardando... {i}s)")
        time.sleep(1)
    else:
        print("   ⚠️  Timeout — se o QR Code expirou, rode novamente")

    print("\n" + "=" * 60)
    print("✅ WhatsApp conectado!")
    print("=" * 60)
    print(f"\nInstância: {instance_name}")
    print("\nProxima etapa:")
    print("  python3 setup/test_api.py\n")

if __name__ == '__main__':
    main()
