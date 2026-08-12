#!/usr/bin/env python3
"""
watcher.py — Captura mensagens dos grupos corporativos e agenda o diário

O que ele faz, em loop:
  1. A cada POLL_INTERVAL segundos, pergunta à Evolution API as mensagens novas
  2. Descarta o que não interessa (conversas privadas, o próprio grupo do diário,
     grupos não monitorados) e salva o resto no SQLite
  3. Nos horários configurados, dispara a geração do diário e publica no
     grupo privado

⚠️ O watcher nunca responde ninguém. A única escrita no WhatsApp é o diário.

Execução:
  python3 watcher.py                    ← roda indefinidamente
  launchctl load ~/Library/LaunchAgents/com.meuagente.watcher.plist  ← auto-start macOS
"""

import json
import time
import logging
import sys
import traceback
import argparse
from pathlib import Path
from datetime import datetime, timedelta

BASE = Path.home() / "meu-agente"
BASE.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE / "watcher.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent))

import evolution
from evolution import DEST_GROUP_JID
from agent import capture_message, run_digest
from storage import (
    init_db, upsert_group, list_groups, get_group_name,
    filter_unseen, mark_seen,
)

# ── Configuração ({{placeholders}} preenchidos durante o setup) ──────────────
POLL_INTERVAL = 30            # segundos entre consultas à Evolution API
POLL_COUNT = 50               # mensagens por página (cada uma pesa ~3 KB)

# Quantas páginas o watcher pode percorrer num ciclo. Ele para sozinho ao
# alcançar mensagens já examinadas; este teto existe só para rajadas.
# Capacidade por ciclo = POLL_COUNT × MAX_PAGINAS_POLL
MAX_PAGINAS_POLL = 10

# Horários (24h) em que o diário é gerado e publicado — ex.: "12:00,18:00"
HORARIOS_DIARIO = [h.strip() for h in "{{HORARIOS_DIARIO}}".split(",") if h.strip()]

# Grupos que entram no diário, separados por vírgula — ex.: "1203@g.us,1204@g.us"
# Vazio = todos os grupos do número. Descubra os JIDs com setup/list_groups.py
GRUPOS_MONITORADOS = [g.strip() for g in "{{GRUPOS_MONITORADOS}}".split(",") if g.strip()]

# Conversas privadas (mensagens diretas) também entram no diário.
# ⚠️ Em número pessoal, deixe False — senão o agente lê conversa de família
# e amigo. Se precisar de alguma privada específica, use PRIVADAS_PERMITIDAS.
MONITORAR_PRIVADAS = {{MONITORAR_PRIVADAS}}

# LISTA BRANCA. Se tiver algum número aqui, SOMENTE esses são lidos nas
# conversas privadas — todo o resto é ignorado. Use em número pessoal ou
# em teste, onde ler tudo não é aceitável. Vazio = sem lista branca.
# Ex.: "5511988887777,5511977776666"
PRIVADAS_PERMITIDAS = [n.strip() for n in "{{PRIVADAS_PERMITIDAS}}".split(",") if n.strip()]

# LISTA NEGRA. Números que nunca devem ser lidos — família, amigos.
# Só é consultada quando não existe lista branca.
# Ex.: "5511999998888"
PRIVADAS_IGNORADAS = [n.strip() for n in "{{PRIVADAS_IGNORADAS}}".split(",") if n.strip()]

# Capturar as mensagens enviadas pelo PRÓPRIO número conectado.
# Deixe True para testar mandando mensagens você mesmo; em produção, False
# evita que os recados do executivo entrem no diário dele.
CAPTURAR_PROPRIAS = {{CAPTURAR_PROPRIAS}}

# Atualizar a lista de grupos a cada N segundos
REFRESH_GRUPOS = 3600

STATE_FILE = BASE / "watcher_state.json"


# ── Estado ───────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"ultimo_diario": {}, "last_run": None}


def save_state(state: dict):
    state["last_run"] = datetime.now().isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Extração da mensagem ─────────────────────────────────────────────────────

def _extrair_texto(conteudo: dict) -> tuple:
    """
    Tira o texto de qualquer formato de mensagem do WhatsApp.

    Returns:
        (texto, tipo)
    """
    if not isinstance(conteudo, dict):
        return "", "desconhecido"

    if conteudo.get("conversation"):
        return conteudo["conversation"], "texto"

    ext = conteudo.get("extendedTextMessage") or {}
    if ext.get("text"):
        return ext["text"], "texto"

    img = conteudo.get("imageMessage") or {}
    if "imageMessage" in conteudo:
        return img.get("caption", "") or "[imagem sem legenda]", "imagem"

    vid = conteudo.get("videoMessage") or {}
    if "videoMessage" in conteudo:
        return vid.get("caption", "") or "[vídeo sem legenda]", "video"

    doc = conteudo.get("documentMessage") or conteudo.get("documentWithCaptionMessage") or {}
    if doc:
        inner = doc.get("message", {}).get("documentMessage", doc)
        nome = inner.get("fileName", "arquivo")
        legenda = inner.get("caption", "")
        return f"[documento: {nome}] {legenda}".strip(), "documento"

    if "audioMessage" in conteudo:
        return "[áudio]", "audio"

    if "stickerMessage" in conteudo:
        return "", "figurinha"     # figurinha é ruído por definição

    return "", "desconhecido"


def _extrair_citada(conteudo: dict) -> str:
    """Texto da mensagem que está sendo respondida (contexto da conversa)."""
    ctx = (conteudo.get("extendedTextMessage") or {}).get("contextInfo") or {}
    citada = ctx.get("quotedMessage") or {}
    texto, _ = _extrair_texto(citada)
    return texto[:300]


def _numero_do_jid(jid: str) -> str:
    """Extrai só os dígitos do JID de uma conversa privada."""
    return jid.split("@")[0].split(":")[0]


def extract_message_data(msg: dict, jids_monitorados: set) -> dict:
    """
    Converte a resposta da Evolution API no registro que vai para o banco.

    Aceita duas origens:
      - grupos (@g.us), respeitando GRUPOS_MONITORADOS
      - conversas privadas (@s.whatsapp.net / @lid), se MONITORAR_PRIVADAS

    Devolve {} quando a mensagem deve ser ignorada.
    """
    if not isinstance(msg, dict):
        return {}

    key = msg.get("key")
    if not isinstance(key, dict):
        return {}

    if key.get("fromMe", False) and not CAPTURAR_PROPRIAS:
        return {}

    remote_jid = key.get("remoteJid", "")
    if not remote_jid:
        return {}

    push_name = msg.get("pushName") or "Contato não identificado"

    # Nunca ler o destino do diário — seja grupo ou conversa privada.
    # Sem isso, o próprio diário publicado entraria na análise do próximo,
    # crescendo a cada rodada.
    if _numero_do_jid(remote_jid) == _numero_do_jid(DEST_GROUP_JID):
        return {}

    if remote_jid.endswith("@g.us"):
        # ── Grupo ──
        if jids_monitorados and remote_jid not in jids_monitorados:
            return {}

        chat_type = "grupo"
        chat_jid = remote_jid
        chat_name = get_group_name(remote_jid)
        sender_jid = key.get("participant", "") or key.get("participantAlt", "")
        sender_name = push_name

    else:
        # ── Conversa privada ──
        if not MONITORAR_PRIVADAS:
            return {}

        # Formato LID (novo endereçamento do WhatsApp): o número real vem
        # em remoteJidAlt. Sem isso, o diário mostraria um ID sem sentido.
        if key.get("addressingMode") == "lid" and key.get("remoteJidAlt"):
            chat_jid = key["remoteJidAlt"]
        else:
            chat_jid = remote_jid

        numero = _numero_do_jid(chat_jid)

        # Lista branca tem prioridade: se existe, só ela vale
        if PRIVADAS_PERMITIDAS:
            if numero not in PRIVADAS_PERMITIDAS:
                return {}
        elif numero in PRIVADAS_IGNORADAS:
            return {}

        chat_type = "privado"
        chat_name = f"{push_name} ({numero})"
        sender_jid = chat_jid
        sender_name = push_name

    conteudo = msg.get("message")
    texto, tipo = _extrair_texto(conteudo if isinstance(conteudo, dict) else {})
    if not texto.strip():
        return {}

    return {
        "id": key.get("id", ""),
        "group_jid": chat_jid,
        "group_name": chat_name,
        "chat_type": chat_type,
        "sender_jid": sender_jid,
        "sender_name": sender_name,
        "text": texto.strip(),
        "msg_type": tipo,
        "quoted_text": _extrair_citada(conteudo if isinstance(conteudo, dict) else {}),
        "ts": int(msg.get("messageTimestamp") or time.time()),
    }


# ── Grupos ───────────────────────────────────────────────────────────────────

def refresh_grupos() -> set:
    """
    Atualiza no banco os nomes dos grupos (para o diário citar o nome, e não
    o JID) e devolve o conjunto de grupos monitorados.
    """
    encontrados = evolution.fetch_groups()
    monitorados = set(GRUPOS_MONITORADOS)

    for g in encontrados:
        upsert_group(g["jid"], g["name"], monitored=(not monitorados or g["jid"] in monitorados))

    if monitorados:
        nomes = [g["name"] for g in list_groups(only_monitored=True)]
        logger.info(f"👥 {len(monitorados)} grupo(s) monitorado(s): {', '.join(nomes[:8])}")
    else:
        logger.info(f"👥 Nenhuma seleção configurada — monitorando os {len(encontrados)} grupos do número")

    return monitorados


# ── Captura ──────────────────────────────────────────────────────────────────

def _msg_id(msg) -> str:
    if not isinstance(msg, dict):
        return ""
    return (msg.get("key") or {}).get("id", "") if isinstance(msg.get("key"), dict) else ""


def capturar(jids_monitorados: set, max_paginas: int = MAX_PAGINAS_POLL,
             limite_ts: int = None, silencioso: bool = False) -> int:
    """
    Percorre as mensagens da Evolution API, das mais novas para as mais antigas,
    e guarda o que interessa.

    Para de paginar quando encontra uma página inteira já examinada — ou seja,
    quando alcança o ponto onde parou na última vez. Se uma rajada de mensagens
    chegou entre dois ciclos, ele avança as páginas necessárias sozinho, em vez
    de perder o que passou do tamanho de uma página.

    Args:
        jids_monitorados: grupos a acompanhar (vazio = todos)
        max_paginas: teto de páginas neste ciclo
        limite_ts: para ao alcançar mensagens anteriores a este epoch
                   (usado na importação de histórico)
        silencioso: não loga cada mensagem, só o resumo

    Returns:
        Quantidade de mensagens novas armazenadas.
    """
    novas = 0

    for pagina in range(1, max_paginas + 1):
        mensagens = evolution.fetch_messages(count=POLL_COUNT, page=pagina)
        if not mensagens:
            break

        ids = [_msg_id(m) for m in mensagens]
        nao_examinados = filter_unseen(ids)

        if not nao_examinados:
            break   # página já vista por completo: alcançamos o histórico

        mais_antiga = None
        for msg in mensagens:
            ts = int(msg.get("messageTimestamp") or 0)
            if ts and (mais_antiga is None or ts < mais_antiga):
                mais_antiga = ts

            if _msg_id(msg) not in nao_examinados:
                continue

            dados = extract_message_data(msg, jids_monitorados)
            if not dados or not dados.get("id"):
                continue

            if capture_message(dados):
                novas += 1
                if not silencioso:
                    icone = "🔒" if dados["chat_type"] == "privado" else "👥"
                    logger.info(
                        f"📥 {icone} [{dados['group_name']}] {dados['sender_name']}: "
                        f"{dados['text'][:70]}"
                    )

        mark_seen(ids)

        if limite_ts and mais_antiga and mais_antiga < limite_ts:
            break   # chegamos além do período pedido na importação

        if pagina == max_paginas:
            logger.warning(
                f"⚠️  Teto de {max_paginas} páginas atingido — pode ter sobrado "
                f"mensagem para o próximo ciclo. Aumente MAX_PAGINAS_POLL se repetir."
            )

    return novas


def importar_historico(horas: int) -> int:
    """
    Traz para o banco o que já está guardado na Evolution API.

    Serve para gerar o primeiro diário sem esperar mensagens novas — útil
    quando a Evolution já roda há meses e tem histórico acumulado.
    """
    init_db()
    limite = int((datetime.now() - timedelta(hours=horas)).timestamp())
    jids = refresh_grupos()

    logger.info(f"⏬ Importando as últimas {horas}h de histórico...")
    novas = capturar(jids, max_paginas=2000, limite_ts=limite, silencioso=True)

    if novas:
        logger.info(f"✅ {novas} mensagem(ns) importada(s)")
    else:
        # Zero importadas quase nunca é "não havia mensagem" — costuma ser
        # configuração. Diz o que checar em vez de sair calado.
        logger.warning("⚠️  Nenhuma mensagem importada. Verifique:")
        if jids:
            logger.warning(f"   • os grupos monitorados existem e tiveram movimento nas últimas {horas}h")
            logger.warning(f"     monitorando: {', '.join(sorted(jids))}")
        else:
            logger.warning("   • nenhum grupo selecionado (monitorando todos)")
        if not MONITORAR_PRIVADAS:
            logger.warning("   • conversas privadas estão DESLIGADAS (MONITORAR_PRIVADAS = False)")
        if not CAPTURAR_PROPRIAS:
            logger.warning("   • suas próprias mensagens estão sendo ignoradas (CAPTURAR_PROPRIAS = False)")
        logger.warning(f"   • o período pedido: últimas {horas}h — tente um valor maior")

    return novas


# ── Agendamento ──────────────────────────────────────────────────────────────

def hora_de_gerar(state: dict) -> str:
    """
    Retorna o horário agendado que já passou e ainda não rodou hoje,
    ou None se não há nada a fazer agora.
    """
    agora = datetime.now()
    hoje = agora.strftime("%Y-%m-%d")
    ja_rodou = state.get("ultimo_diario", {})

    for horario in sorted(HORARIOS_DIARIO):
        try:
            h, m = (int(x) for x in horario.split(":"))
        except ValueError:
            continue

        alvo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
        if agora >= alvo and ja_rodou.get(horario) != hoje:
            return horario

    return None


# ── Loop principal ───────────────────────────────────────────────────────────

def watch():
    init_db()
    logger.info("🔍 Watcher do Diário Executivo iniciado")
    logger.info(f"   Horários do diário: {', '.join(HORARIOS_DIARIO) or 'nenhum (apenas manual)'}")
    if not MONITORAR_PRIVADAS:
        logger.info("   Conversas privadas: NÃO são lidas")
    elif PRIVADAS_PERMITIDAS:
        logger.info(f"   Conversas privadas: SOMENTE {len(PRIVADAS_PERMITIDAS)} número(s) da lista branca")
    elif PRIVADAS_IGNORADAS:
        logger.info(f"   Conversas privadas: todas, menos {len(PRIVADAS_IGNORADAS)} número(s) bloqueado(s)")
    else:
        logger.info("   Conversas privadas: TODAS são lidas")
    logger.info(f"   Mensagens próprias (fromMe): {'capturadas' if CAPTURAR_PROPRIAS else 'ignoradas'}")

    if evolution.connection_state() != "open":
        logger.warning("⚠️  WhatsApp não está conectado — rode setup/connect_whatsapp.py")

    state = load_state()
    jids_monitorados = refresh_grupos()
    ultimo_refresh = time.time()

    while True:
        try:
            # 1. Captura
            novas = capturar(jids_monitorados)
            if novas:
                logger.info(f"   {novas} mensagem(ns) nova(s) armazenada(s)")

            # 2. Agendamento do diário
            horario = hora_de_gerar(state)
            if horario:
                logger.info(f"🗓️  Gerando diário do horário {horario}...")
                try:
                    resultado = run_digest(deliver=True)
                    logger.info(f"   Resultado: {resultado['status']}")
                except Exception as e:
                    logger.error(f"Erro ao gerar diário: {e}\n{traceback.format_exc()}")
                finally:
                    # Marca como rodado mesmo em erro, para não entrar em loop
                    state.setdefault("ultimo_diario", {})[horario] = datetime.now().strftime("%Y-%m-%d")
                    save_state(state)

            # 3. Atualização periódica da lista de grupos
            if time.time() - ultimo_refresh > REFRESH_GRUPOS:
                jids_monitorados = refresh_grupos()
                ultimo_refresh = time.time()

            save_state(state)
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            logger.info("⏹️  Watcher encerrado")
            break
        except Exception as e:
            logger.error(f"Erro no loop: {e}\n{traceback.format_exc()}")
            time.sleep(15)


def main():
    parser = argparse.ArgumentParser(description="Captura de mensagens do Diário Executivo")
    parser.add_argument("--importar", type=int, metavar="HORAS",
                        help="importa o histórico já guardado na Evolution API e sai")
    args = parser.parse_args()

    if args.importar:
        importar_historico(args.importar)
        return

    watch()


if __name__ == "__main__":
    main()
