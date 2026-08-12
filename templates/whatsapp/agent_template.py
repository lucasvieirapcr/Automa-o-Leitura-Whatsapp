#!/usr/bin/env python3
"""
agent_template.py — Motor do Diário Executivo (v2.0)

Responsabilidades:
  1. Receber uma mensagem capturada de grupo, aplicar o pré-filtro e salvar
  2. Montar o período de análise (desde o último diário, ou as últimas N horas)
  3. Mandar as mensagens para a IA com o prompt do assistente executivo
  4. Salvar o diário no banco + arquivo .md e publicar no grupo privado

⚠️ Este agente NÃO responde mensagens. A única escrita no WhatsApp é a
   publicação do diário no grupo privado.

Uso:
  python3 agent.py --now              ← gera e publica o diário agora
  python3 agent.py --now --dry-run    ← gera e mostra na tela, sem publicar
  python3 agent.py --horas 24         ← analisa as últimas 24 horas
  python3 agent.py --stats            ← painel do agente
  python3 agent.py --test             ← teste do pré-filtro e da IA
"""

import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))

from agent_core import (
    call_ai, is_relevant, format_messages_block, split_for_whatsapp,
    EMPRESA, EXECUTIVO, MAX_CHARS_LOTE, MAX_TOKENS_DIGEST,
)
from storage import (
    init_db, save_message, get_messages, get_period_stats, save_digest,
    mark_digest_delivered, mark_messages_digested, last_digest,
    purge_old_messages, get_stats,
)
import evolution

logger = logging.getLogger(__name__)

# Janela padrão quando ainda não existe diário anterior
JANELA_PADRAO_HORAS = 24

DIARIOS_DIR = Path.home() / "meu-agente" / "diarios"


# ── 1. Captura ───────────────────────────────────────────────────────────────

def capture_message(msg: dict) -> bool:
    """
    Aplica o pré-filtro e guarda a mensagem no banco.

    Tudo é salvo (para o contador "mensagens analisadas" bater com a
    realidade), mas só o que passa no pré-filtro vai para a IA.

    Returns:
        True se a mensagem é nova, False se já estava no banco.
    """
    texto = (msg.get("text") or "").strip()
    citada = (msg.get("quoted_text") or "").strip()
    msg["prefiltered"] = 1 if is_relevant(texto, quoted_text=citada) else 0
    return save_message(msg)


# ── 2. Período ───────────────────────────────────────────────────────────────

def resolve_period(horas: int = None) -> tuple:
    """
    Define o intervalo a analisar.

    - Se `horas` for informado → últimas N horas.
    - Senão → desde o fim do último diário.
    - Se não existe diário anterior → últimas JANELA_PADRAO_HORAS.

    Returns:
        (period_start_epoch, period_end_epoch)
    """
    agora = int(datetime.now().timestamp())

    if horas:
        return agora - horas * 3600, agora

    anterior = last_digest()
    if anterior and anterior.get("period_end"):
        return int(anterior["period_end"]) + 1, agora

    return agora - JANELA_PADRAO_HORAS * 3600, agora


# ── 3. Geração do diário ─────────────────────────────────────────────────────

def _cabecalho(stats: dict, inicio: int, fim: int) -> str:
    """Contexto factual entregue à IA — ela não precisa contar nada."""
    dt_ini = datetime.fromtimestamp(stats.get("primeira_ts") or inicio)
    dt_fim = datetime.fromtimestamp(stats.get("ultima_ts") or fim)

    return (
        f"CONTEXTO DO PERÍODO (dados já apurados — use exatamente estes números):\n"
        f"- Empresa: {EMPRESA}\n"
        f"- Executivo: {EXECUTIVO}\n"
        f"- Data do resumo: {dt_fim.strftime('%d/%m/%Y')}\n"
        f"- Período analisado: {dt_ini.strftime('%d/%m/%Y %H:%M')} às {dt_fim.strftime('%d/%m/%Y %H:%M')}\n"
        f"- Mensagens capturadas no período: {stats['total']}\n"
        f"- Mensagens enviadas para análise (após remoção de ruído): {stats['analisadas']}\n"
        f"- Grupos com movimento no período: {stats['grupos']}\n"
        f"- Conversas privadas com movimento no período: {stats.get('privadas', 0)}\n"
    )


def _lotes(rows: list, limite: int = MAX_CHARS_LOTE) -> list:
    """Divide as mensagens em lotes que cabem numa chamada de IA."""
    lotes, atual, tamanho = [], [], 0

    for r in rows:
        custo = len(r.get("text") or "") + 120  # texto + metadados do bloco
        if atual and tamanho + custo > limite:
            lotes.append(atual)
            atual, tamanho = [], 0
        atual.append(r)
        tamanho += custo

    if atual:
        lotes.append(atual)
    return lotes


def build_digest(inicio: int, fim: int) -> tuple:
    """
    Gera o texto do diário para o período.

    Volume grande é processado em duas etapas (map → reduce):
      1. cada lote de mensagens vira uma lista de achados relevantes
      2. os achados são consolidados em um único diário no formato final

    Returns:
        (texto_do_diario, stats)
    """
    stats = get_period_stats(inicio, fim)
    rows = get_messages(inicio, fim, only_prefiltered=True)

    if not rows:
        return None, stats

    lotes = _lotes(rows)
    cabecalho = _cabecalho(stats, inicio, fim)

    # ── Caminho simples: cabe em uma chamada ──
    if len(lotes) == 1:
        prompt = (
            f"{cabecalho}\n"
            f"MENSAGENS DO PERÍODO:\n{format_messages_block(lotes[0])}\n\n"
            f"Gere o diário executivo completo no formato definido."
        )
        logger.info(f"🧠 Analisando {len(rows)} mensagens em 1 chamada...")
        return call_ai([{"role": "user", "content": prompt}], MAX_TOKENS_DIGEST), stats

    # ── Caminho map/reduce: volume grande ──
    logger.info(f"🧠 Analisando {len(rows)} mensagens em {len(lotes)} lotes...")

    achados = []
    for i, lote in enumerate(lotes, 1):
        prompt_parcial = (
            f"{cabecalho}\n"
            f"Esta é a PARTE {i} de {len(lotes)} das mensagens do período.\n"
            f"Nesta etapa NÃO gere o diário final. Apenas extraia os assuntos "
            f"relevantes segundo os critérios, um por bloco, com: área/assunto, "
            f"grupo, remetente, data e hora, trecho original, resumo, impacto, "
            f"ação recomendada, responsável, prazo e prioridade.\n"
            f"Se nada nesta parte for relevante, responda apenas: SEM ITENS RELEVANTES.\n\n"
            f"MENSAGENS:\n{format_messages_block(lote)}"
        )
        parcial = call_ai([{"role": "user", "content": prompt_parcial}], MAX_TOKENS_DIGEST)

        if not parcial or parcial.startswith("Erro "):
            logger.error(f"   ✗ lote {i}/{len(lotes)} falhou: {str(parcial)[:120]}")
            continue

        logger.info(f"   ✓ lote {i}/{len(lotes)}")
        if "SEM ITENS RELEVANTES" not in parcial.upper():
            achados.append(parcial)

    if not achados:
        return None, stats

    prompt_final = (
        f"{cabecalho}\n"
        f"Abaixo estão os achados relevantes extraídos de {len(lotes)} partes das "
        f"mensagens do período.\n"
        f"Consolide tudo em UM ÚNICO diário executivo no formato definido: "
        f"junte assuntos repetidos em um só bloco, ordene por prioridade "
        f"(🔴 depois 🟠 depois 🟢), e monte as seções finais "
        f"(itens que exigem atenção, pendências e prazos, clientes mencionados, "
        f"resumo executivo e indicadores).\n"
        f"Não invente nada que não esteja nos achados.\n\n"
        f"ACHADOS:\n\n" + "\n\n---\n\n".join(achados)
    )
    return call_ai([{"role": "user", "content": prompt_final}], MAX_TOKENS_DIGEST), stats


# ── 4. Publicação ────────────────────────────────────────────────────────────

def salvar_arquivo(texto: str, fim: int) -> Path:
    """Guarda uma cópia em markdown para consulta/histórico."""
    DIARIOS_DIR.mkdir(parents=True, exist_ok=True)
    caminho = DIARIOS_DIR / f"diario-{datetime.fromtimestamp(fim).strftime('%Y-%m-%d_%H%M')}.md"
    caminho.write_text(texto, encoding="utf-8")
    return caminho


def run_digest(horas: int = None, deliver: bool = True) -> dict:
    """
    Fluxo completo: apura período → gera diário → salva → publica.

    Returns:
        dict com o resultado (status, digest_id, texto, stats)
    """
    init_db()
    inicio, fim = resolve_period(horas)

    logger.info(
        f"📆 Período: {datetime.fromtimestamp(inicio):%d/%m %H:%M} "
        f"→ {datetime.fromtimestamp(fim):%d/%m %H:%M}"
    )

    texto, stats = build_digest(inicio, fim)

    if not texto:
        if stats["total"] == 0:
            logger.info(
                "📭 Nenhuma mensagem capturada no período — diário não gerado.\n"
                "   Verifique se o watcher está rodando e se os grupos monitorados "
                "tiveram movimento nesse intervalo."
            )
        else:
            logger.info(
                f"📭 {stats['total']} mensagem(ns) capturada(s), mas nenhuma passou "
                f"pelo pré-filtro (tudo classificado como ruído) — diário não gerado."
            )
        return {"status": "vazio", "stats": stats}

    if texto.startswith("Erro "):
        logger.error(f"❌ IA retornou erro: {texto[:200]}")
        return {"status": "erro_ia", "erro": texto, "stats": stats}

    digest_id = save_digest(
        inicio, fim, texto,
        msg_count=stats["total"],
        group_count=stats["grupos"],
        analyzed_count=stats["analisadas"],
    )
    mark_messages_digested(inicio, fim, digest_id)
    caminho = salvar_arquivo(texto, fim)
    logger.info(f"💾 Diário #{digest_id} salvo em {caminho}")

    if deliver:
        partes = split_for_whatsapp(texto)
        if evolution.deliver_parts(partes):
            mark_digest_delivered(digest_id)
            logger.info(f"✅ Diário #{digest_id} publicado no grupo privado ({len(partes)} parte(s))")
        else:
            logger.error("⚠️  Diário gerado e salvo, mas a publicação falhou.")
            return {"status": "nao_publicado", "digest_id": digest_id, "texto": texto, "stats": stats}

    purge_old_messages()
    return {"status": "ok", "digest_id": digest_id, "texto": texto, "stats": stats}


# ── Testes e CLI ─────────────────────────────────────────────────────────────

def test_prefilter() -> bool:
    """Mostra o pré-filtro em ação (usado por setup/test_agent.py)."""
    exemplos = [
        ("Bom dia!", ""),
        ("👍", ""),
        ("ok", ""),
        ("kkkk", ""),
        ("pode pagar", ""),
        ("cliente cancelou", ""),
        ("ok", "Libero o pagamento de R$ 80.000 para o fornecedor?"),
        ("Cliente Alfa reclamou do atraso na entrega e ameaçou cancelar o contrato.", ""),
        ("Sistema de emissão de NF-e está fora do ar desde as 9h.", ""),
        ("Precisamos da aprovação do Eduardo para o orçamento de R$ 45.000 até sexta.", ""),
    ]

    print("Pré-filtro (o que vai para a IA):\n")
    for texto, citada in exemplos:
        marca = "✅ analisa " if is_relevant(texto, quoted_text=citada) else "⏭️  descarta"
        contexto = f'   [respondendo a: "{citada[:40]}..."]' if citada else ""
        print(f"  {marca} → \"{texto[:60]}\"{contexto}")
    return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Diário Executivo — motor de análise")
    parser.add_argument("--now", action="store_true", help="gera o diário agora")
    parser.add_argument("--horas", type=int, help="analisa as últimas N horas")
    parser.add_argument("--dry-run", action="store_true", help="não publica no grupo privado")
    parser.add_argument("--stats", action="store_true", help="painel do agente")
    parser.add_argument("--test", action="store_true", help="testa pré-filtro e IA")

    args = parser.parse_args()
    init_db()

    if args.stats:
        for chave, valor in get_stats().items():
            print(f"  {chave.replace('_', ' ').capitalize()}: {valor}")
        return

    if args.test:
        test_prefilter()
        print("\nTestando IA...")
        r = call_ai([{"role": "user", "content": "Responda apenas: pronto."}], max_tokens=32)
        print(f"  IA: {r[:120]}")
        return

    if args.now or args.horas:
        resultado = run_digest(horas=args.horas, deliver=not args.dry_run)
        if args.dry_run and resultado.get("texto"):
            print("\n" + "=" * 60)
            print(resultado["texto"])
            print("=" * 60)
        print(f"\nStatus: {resultado['status']}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
