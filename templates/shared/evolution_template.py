"""
evolution_template.py — Cliente da Evolution API (WhatsApp)

Usado tanto pelo watcher (ler mensagens dos grupos) quanto pelo agent
(entregar o diário no grupo privado).

⚠️ Este agente NÃO responde ninguém. A única escrita no WhatsApp é a
   entrega do diário no grupo privado configurado em DEST_GROUP_JID.

Este arquivo vira ~/meu-agente/evolution.py durante o setup.
"""

import json
import time
import logging
import urllib.request
import urllib.error
from urllib.parse import quote

logger = logging.getLogger(__name__)

# ── Configurações ({{placeholders}} preenchidos durante o setup) ─────────────
# Instalação local:            http://localhost:8080
# Evolution já existente/VPS:  https://seu-endereco.easypanel.host (sem barra no fim)
EVOLUTION_URL = "{{EVOLUTION_URL}}"
EVOLUTION_API_KEY = "{{EVOLUTION_API_KEY}}"

# Nome da instância dentro da Evolution API. Se você já tem um WhatsApp
# conectado, use o nome da instância existente para reaproveitá-la.
INSTANCE_NAME = "{{INSTANCE_NAME}}"

# Nome da instância pronto para entrar na URL. Instâncias criadas pela
# interface costumam ter espaço no nome ("n8n evolution"), e espaço quebra
# a requisição HTTP — por isso todo endpoint usa esta versão codificada.
INSTANCE = quote(INSTANCE_NAME, safe="")

# Grupo privado onde o diário é publicado (ex.: "1203630...@g.us")
DEST_GROUP_JID = "{{DEST_GROUP_JID}}"


def request(endpoint: str, method: str = "GET", data: dict = None, timeout: int = 20):
    """Chamada HTTP à Evolution API. Devolve dict/list, ou {} em caso de erro."""
    url = f"{EVOLUTION_URL}{endpoint}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        logger.error(f"Evolution API {e.code} em {endpoint}: {e.read().decode(errors='ignore')[:200]}")
        return {}
    except Exception as e:
        logger.error(f"Evolution API erro em {endpoint}: {e}")
        return {}


def connection_state() -> str:
    """Retorna 'open' quando o WhatsApp está conectado."""
    r = request(f"/instance/connectionState/{INSTANCE}")
    if isinstance(r, dict):
        return r.get("instance", {}).get("state", "") or r.get("state", "")
    return ""


def fetch_groups() -> list:
    """
    Lista todos os grupos do número conectado.

    Returns:
        [{"jid": "...@g.us", "name": "Financeiro"}, ...]
    """
    r = request(f"/group/fetchAllGroups/{INSTANCE}?getParticipants=false")

    bruto = r if isinstance(r, list) else (r.get("groups", []) if isinstance(r, dict) else [])

    grupos = []
    for g in bruto:
        if not isinstance(g, dict):
            continue
        jid = g.get("id") or g.get("jid") or ""
        nome = g.get("subject") or g.get("name") or jid
        if jid.endswith("@g.us"):
            grupos.append({"jid": jid, "name": nome})
    return grupos


def fetch_messages(count: int = 50, page: int = 1) -> list:
    """
    Busca mensagens da instância, das mais recentes para as mais antigas.

    Na Evolution API v2 quem define o tamanho da página é `offset` (e não
    `count`, que é ignorado); `page` é o número da página. A página 1 traz
    sempre as mensagens mais novas.

    Args:
        count: quantas mensagens por página
        page:  1 = mais recentes, 2 = as anteriores, e assim por diante

    A v2 responde {"messages": {"records": [...]}}; a v1 devolve uma lista
    direta. Os dois formatos são tratados aqui.
    """
    result = request(
        f"/chat/findMessages/{INSTANCE}",
        method="POST",
        data={"page": page, "offset": count, "count": count},
    )

    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "messages" in result:
        dados = result["messages"]
        if isinstance(dados, dict):
            return dados.get("records", [])
        if isinstance(dados, list):
            return dados
    return []


def send_text(jid: str, text: str) -> bool:
    """
    Envia texto para um JID. Usado SOMENTE para publicar o diário
    no grupo privado — o agente nunca responde nos grupos de origem.
    """
    r = request(
        f"/message/sendText/{INSTANCE}",
        method="POST",
        data={"number": jid, "text": text},
        timeout=30,
    )
    ok = bool(r.get("key") or r.get("id")) if isinstance(r, dict) else False
    if ok:
        logger.info(f"📤 Publicado em {jid} ({len(text)} caracteres)")
    else:
        logger.error(f"❌ Falha ao publicar em {jid}: {r}")
    return ok


def deliver_parts(parts: list, jid: str = None, pausa: float = 1.5) -> bool:
    """Publica uma lista de partes no grupo privado, com pausa entre elas."""
    destino = jid or DEST_GROUP_JID
    if not destino or "{{" in destino:
        logger.error("DEST_GROUP_JID não configurado — diário não foi publicado.")
        return False

    tudo_ok = True
    for i, parte in enumerate(parts):
        if not send_text(destino, parte):
            tudo_ok = False
        if i < len(parts) - 1:
            time.sleep(pausa)
    return tudo_ok
