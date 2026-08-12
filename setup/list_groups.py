#!/usr/bin/env python3
"""
list_groups.py — Lista os grupos do WhatsApp conectado

Serve para duas escolhas do setup:
  1. Quais grupos corporativos entram no diário
  2. Qual é o grupo privado onde o diário será publicado

Funciona tanto com a Evolution instalada localmente quanto com uma já
existente (VPS, EasyPanel, Docker). Para apontar para outra, use as flags
ou as variáveis de ambiente EVOLUTION_URL / EVOLUTION_API_KEY / INSTANCE_NAME.

Uso:
  python3 setup/list_groups.py           ← lista legível
  python3 setup/list_groups.py --json    ← saída em JSON
  python3 setup/list_groups.py --url https://evo.exemplo.host --key ABC --instance minha
"""
import json
import os
import sys
import argparse
import urllib.request
from urllib.parse import quote
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

DEFAULT_URL = "http://localhost:8080"
DEFAULT_INSTANCE = "meu-agente"
DEFAULT_API_KEY = "B6D711FCDE4D4FD5936544120E713976"

# Preenchidos em main() a partir das flags / variáveis de ambiente
EVOLUTION_URL = DEFAULT_URL
INSTANCE_NAME = DEFAULT_INSTANCE
API_KEY = None


def _get_api_key():
    """Chave explícita > variável de ambiente > .env local > padrão."""
    if API_KEY:
        return API_KEY
    if os.environ.get("EVOLUTION_API_KEY"):
        return os.environ["EVOLUTION_API_KEY"]

    env_file = Path.home() / "meu-agente" / "evolution-api" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    return DEFAULT_API_KEY


def call_api(endpoint, method="GET", data=None):
    req = urllib.request.Request(
        f"{EVOLUTION_URL}{endpoint}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"apikey": _get_api_key(), "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            corpo = resp.read()
            return json.loads(corpo) if corpo else {}
    except Exception as e:
        return {"error": str(e)}


def _instancia():
    # quote() é obrigatório: instância criada pela interface pode ter espaço
    # no nome ("n8n evolution"), e espaço em URL quebra a requisição
    return quote(INSTANCE_NAME, safe="")


def _por_fetch_all_groups():
    """Caminho preferido: pergunta os grupos ao WhatsApp, com nome e tamanho."""
    r = call_api(f"/group/fetchAllGroups/{_instancia()}?getParticipants=false")
    if isinstance(r, dict) and "error" in r:
        return None, r["error"]

    bruto = r if isinstance(r, list) else (r.get("groups", []) if isinstance(r, dict) else [])

    grupos = []
    for g in bruto:
        if not isinstance(g, dict):
            continue
        jid = g.get("id") or g.get("jid") or ""
        if jid.endswith("@g.us"):
            grupos.append({
                "jid": jid,
                "name": g.get("subject") or g.get("name") or jid,
                "participantes": g.get("size") or g.get("participantsCount") or "?",
            })
    return grupos, None


def _por_find_chats():
    """
    Alternativa: lê as conversas já gravadas no banco da Evolution.

    Serve quando o fetchAllGroups devolve erro 500 — o que acontece em
    instâncias com muitos grupos ou com sessão restaurada do banco, porque
    aquele endpoint consulta o WhatsApp grupo por grupo. Este lê do Postgres.
    """
    r = call_api(f"/chat/findChats/{_instancia()}", method="POST", data={})
    if isinstance(r, dict) and "error" in r:
        return None, r["error"]

    itens = r if isinstance(r, list) else (r.get("chats", []) if isinstance(r, dict) else [])

    grupos = []
    for c in itens:
        if not isinstance(c, dict):
            continue
        jid = str(c.get("remoteJid") or c.get("id") or "")
        if jid.endswith("@g.us"):
            grupos.append({
                "jid": jid,
                "name": c.get("name") or c.get("pushName") or c.get("subject") or jid,
                "participantes": "?",
            })
    return grupos, None


def fetch_groups():
    """Tenta o endpoint de grupos; se falhar, cai para a lista de conversas."""
    grupos, erro = _por_fetch_all_groups()

    if not grupos:
        grupos_alt, erro_alt = _por_find_chats()
        if grupos_alt:
            grupos, erro = grupos_alt, None
        elif erro is None:
            erro = erro_alt

    if grupos is None:
        return None, erro

    # Um mesmo grupo pode vir das duas fontes
    unicos = {g["jid"]: g for g in grupos}
    grupos = sorted(unicos.values(), key=lambda x: x["name"].lower())
    return grupos, None


def main():
    global EVOLUTION_URL, INSTANCE_NAME, API_KEY

    parser = argparse.ArgumentParser(description="Lista grupos do WhatsApp")
    parser.add_argument("--json", action="store_true", help="saída em JSON")
    parser.add_argument("--url", help="endereço da Evolution API")
    parser.add_argument("--key", help="apikey da Evolution API")
    parser.add_argument("--instance", help="nome da instância")
    args = parser.parse_args()

    EVOLUTION_URL = (args.url or os.environ.get("EVOLUTION_URL") or DEFAULT_URL).rstrip("/")
    INSTANCE_NAME = args.instance or os.environ.get("INSTANCE_NAME") or DEFAULT_INSTANCE
    API_KEY = args.key

    grupos, erro = fetch_groups()

    if erro:
        if not args.json:
            print("❌ Não consegui falar com a Evolution API.")
            print(f"   Detalhe: {erro}\n")
            print("   Verifique se o WhatsApp está conectado:")
            print("   python3 setup/connect_whatsapp.py\n")
        else:
            print(json.dumps({"error": erro}, ensure_ascii=False))
        sys.exit(1)

    if args.json:
        print(json.dumps(grupos, ensure_ascii=False, indent=2))
        return

    print("=" * 60)
    print("👥 Grupos do WhatsApp conectado")
    print("=" * 60 + "\n")

    if not grupos:
        print("Nenhum grupo encontrado.")
        print("Se o WhatsApp acabou de conectar, aguarde ~1 minuto e rode de novo.\n")
        return

    for i, g in enumerate(grupos, 1):
        print(f"  {i:>2}. {g['name']}")
        print(f"      {g['jid']}  ({g['participantes']} participantes)")

    print(f"\n  Total: {len(grupos)} grupos\n")


if __name__ == "__main__":
    main()
