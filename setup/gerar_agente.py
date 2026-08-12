#!/usr/bin/env python3
"""
gerar_agente.py — Gera os arquivos do agente em ~/meu-agente

Lê os templates, substitui todos os {{placeholders}} e grava os cinco
arquivos prontos para rodar. Evita editar arquivo por arquivo na mão.

Cada opção pode vir por flag ou por variável de ambiente. Chaves saem
sempre de variável de ambiente, para não ficarem no histórico do shell.

Exemplo (teste seguro em número pessoal):

  export EVOLUTION_URL="https://sua-evolution.host"
  export EVOLUTION_API_KEY="..."      # já carregada antes
  export AI_API_KEY="..."             # já carregada antes
  python3 setup/gerar_agente.py --teste \\
      --instancia "n8n evolution" \\
      --empresa "Grupo 3 S/A" --executivo "Eduardo Dias" \\
      --grupos "120363999@g.us" \\
      --destino 556195197682

Modos:
  --teste       conversas privadas desligadas, captura as próprias
                mensagens, sem horário automático (você dispara na mão)
  --producao    conversas privadas ligadas, não captura as próprias,
                horários automáticos
"""

import os
import sys
import argparse
import shutil
import re
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

RAIZ = Path(__file__).resolve().parent.parent
TEMPLATES = RAIZ / "templates"

# Onde os arquivos gerados vão. MEU_AGENTE_DIR existe para testar o gerador
# sem escrever na home de verdade.
DESTINO = Path(os.environ.get("MEU_AGENTE_DIR") or (Path.home() / "meu-agente"))

# template → arquivo gerado; True = contém segredo, recebe permissão 600
ARQUIVOS = [
    ("shared/agent_core_template.py", "agent_core.py", True),
    ("shared/storage_template.py", "storage.py", False),
    ("shared/evolution_template.py", "evolution.py", True),
    ("whatsapp/agent_template.py", "agent.py", False),
    ("whatsapp/watcher_template.py", "watcher.py", False),
]

MODELO_PADRAO = {
    "openai": "gpt-5.4-mini",
    "gemini": "gemini-2.5-flash",
    "anthropic": "claude-opus-4-6",
}


def erro(msg, dica=None):
    print(f"\n❌ {msg}")
    if dica:
        print(f"   {dica}")
    sys.exit(1)


def normalizar_jid(valor: str) -> str:
    """Aceita JID completo ou só os dígitos de um número."""
    valor = (valor or "").strip()
    if not valor:
        return ""
    if "@" in valor:
        return valor
    digitos = re.sub(r"\D", "", valor)
    if not digitos:
        erro(f"Destino inválido: {valor}", "Use um JID (...@g.us) ou um número com DDI e DDD")
    return f"{digitos}@s.whatsapp.net"


def normalizar_lista(valor: str, sufixo_jid: bool = False) -> str:
    """Normaliza 'a, b , c' para 'a,b,c'."""
    itens = [i.strip() for i in (valor or "").split(",") if i.strip()]
    if sufixo_jid:
        itens = [normalizar_jid(i) for i in itens]
    return ",".join(itens)


def validar_grupos(valor: str) -> str:
    """
    Normaliza GRUPOS_MONITORADOS para JIDs de grupo (...@g.us).

    Completa o sufixo quando só o ID foi colado, aceitando os dois formatos
    do WhatsApp: o novo (18+ dígitos, ex. 120363410074810635) e o antigo
    (telefone-timestamp, ex. 556181272233-1520522353).

    Recusa número de telefone, que é o erro mais silencioso possível aqui:
    nenhum grupo casa com ele, e o agente sobe sem capturar nada.
    """
    itens = [i.strip() for i in (valor or "").split(",") if i.strip()]

    normalizados, invalidos = [], []
    for item in itens:
        if item.endswith("@g.us"):
            normalizados.append(item)
        elif re.fullmatch(r"\d+-\d+", item):        # formato antigo
            normalizados.append(f"{item}@g.us")
        elif re.fullmatch(r"\d{15,}", item):        # ID de grupo sem sufixo
            normalizados.append(f"{item}@g.us")
        else:
            invalidos.append(item)

    if invalidos:
        erro(
            "Não são grupos: " + ", ".join(invalidos),
            "Isso parece número de telefone. Grupo tem ID longo (18+ dígitos) e\n"
            "   termina em @g.us — descubra com: python3 setup/list_groups.py\n"
            "   Para acompanhar uma CONVERSA PRIVADA (inclusive a sua com você mesmo),\n"
            "   não use --grupos: use --privadas-permitidas com o número.",
        )
    return ",".join(normalizados)


def validar_horarios(valor: str) -> str:
    itens = [i.strip() for i in (valor or "").split(",") if i.strip()]
    for h in itens:
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", h):
            erro(f"Horário inválido: {h}", "Use o formato 24h, ex.: 12:00,18:00")
    return ",".join(itens)


def main():
    p = argparse.ArgumentParser(description="Gera os arquivos do Diário Executivo")

    p.add_argument("--teste", action="store_true",
                   help="modo seguro para número pessoal (não lê privadas, captura as próprias)")
    p.add_argument("--producao", action="store_true",
                   help="modo produção (lê privadas, não captura as próprias)")

    p.add_argument("--provider", default=os.environ.get("AI_PROVIDER", "openai"),
                   choices=["openai", "gemini", "anthropic"])
    p.add_argument("--modelo", default=os.environ.get("AI_MODEL"))
    p.add_argument("--empresa", default=os.environ.get("EMPRESA"))
    p.add_argument("--executivo", default=os.environ.get("EXECUTIVO"))

    p.add_argument("--url", default=os.environ.get("EVOLUTION_URL"))
    p.add_argument("--instancia", default=os.environ.get("INSTANCE_NAME", "meu-agente"))

    p.add_argument("--grupos", default=os.environ.get("GRUPOS_MONITORADOS", ""),
                   help="JIDs separados por vírgula; vazio = todos os grupos")
    p.add_argument("--destino", default=os.environ.get("DEST_GROUP_JID"),
                   help="grupo (...@g.us) ou número que recebe o diário")
    p.add_argument("--horarios", default=os.environ.get("HORARIOS_DIARIO"),
                   help="ex.: 12:00,18:00 — vazio = só manual")
    p.add_argument("--privadas-permitidas", default=os.environ.get("PRIVADAS_PERMITIDAS", ""),
                   help="lista branca: só estes números são lidos nas privadas")
    p.add_argument("--privadas-ignoradas", default=os.environ.get("PRIVADAS_IGNORADAS", ""),
                   help="lista negra de números nas privadas")
    p.add_argument("--capturar-proprias", action="store_true",
                   help="captura também as mensagens que o próprio número envia (sobrescreve o padrão do modo)")
    p.add_argument("--ignorar-proprias", action="store_true",
                   help="ignora as mensagens que o próprio número envia (sobrescreve o padrão do modo)")

    args = p.parse_args()

    if args.teste and args.producao:
        erro("Escolha --teste ou --producao, não os dois")
    if not (args.teste or args.producao):
        erro("Informe o modo: --teste (número pessoal) ou --producao")
    if args.capturar_proprias and args.ignorar_proprias:
        erro("Escolha --capturar-proprias ou --ignorar-proprias, não os dois")

    ai_key = os.environ.get("AI_API_KEY", "")
    evo_key = os.environ.get("EVOLUTION_API_KEY", "")

    faltando = []
    if not ai_key:
        faltando.append("AI_API_KEY (variável de ambiente)")
    if not evo_key:
        faltando.append("EVOLUTION_API_KEY (variável de ambiente)")
    if not args.url:
        faltando.append("--url ou EVOLUTION_URL")
    if not args.empresa:
        faltando.append("--empresa")
    if not args.executivo:
        faltando.append("--executivo")
    if not args.destino:
        faltando.append("--destino")
    if faltando:
        erro("Faltam informações:\n     - " + "\n     - ".join(faltando))

    modo_teste = args.teste
    horarios = validar_horarios(
        args.horarios if args.horarios is not None else ("" if modo_teste else "12:00,18:00")
    )

    grupos = validar_grupos(args.grupos)
    permitidas = normalizar_lista(args.privadas_permitidas)
    destino = normalizar_jid(args.destino)

    # A lista branca já limita a leitura aos números informados, então ela
    # liga as conversas privadas mesmo no modo teste — é assim que se
    # acompanha um contato específico (ou a conversa consigo mesmo).
    monitorar_privadas = bool(permitidas) or not modo_teste

    # Padrão por modo (teste captura para permitir mandar mensagem de prova;
    # produção normalmente ignora), mas a flag explícita sempre vence.
    if args.capturar_proprias:
        capturar_proprias = True
    elif args.ignorar_proprias:
        capturar_proprias = False
    else:
        capturar_proprias = modo_teste

    if not grupos and not permitidas and modo_teste:
        erro(
            "Nada para monitorar",
            "Informe --grupos com JIDs de grupo, ou --privadas-permitidas com números.",
        )

    # Origem igual ao destino nunca é lida (proteção contra loop)
    origens = set(grupos.split(",")) | {normalizar_jid(n) for n in permitidas.split(",") if n}
    if destino in origens:
        erro(
            f"O destino ({destino}) também está na lista de origens",
            "O destino do diário nunca é lido, para não virar loop. Escolha outro\n"
            "   destino — por exemplo um grupo criado só para receber o diário.",
        )

    valores = {
        "AI_PROVIDER": args.provider,
        "AI_MODEL": args.modelo or MODELO_PADRAO[args.provider],
        "AI_API_KEY": ai_key,
        "EMPRESA": args.empresa,
        "EXECUTIVO": args.executivo,
        "EVOLUTION_URL": args.url.rstrip("/"),
        "EVOLUTION_API_KEY": evo_key,
        "INSTANCE_NAME": args.instancia,
        "DEST_GROUP_JID": destino,
        "HORARIOS_DIARIO": horarios,
        "GRUPOS_MONITORADOS": grupos,
        "MONITORAR_PRIVADAS": "True" if monitorar_privadas else "False",
        "CAPTURAR_PROPRIAS": "True" if capturar_proprias else "False",
        "PRIVADAS_PERMITIDAS": permitidas,
        "PRIVADAS_IGNORADAS": normalizar_lista(args.privadas_ignoradas),
    }

    print("=" * 60)
    print(f"⚙️  Gerando o agente — modo {'TESTE' if modo_teste else 'PRODUÇÃO'}")
    print("=" * 60)

    DESTINO.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")

    for origem, nome, secreto in ARQUIVOS:
        caminho_template = TEMPLATES / origem
        if not caminho_template.exists():
            erro(f"Template não encontrado: {caminho_template}")

        texto = caminho_template.read_text(encoding="utf-8")
        for chave, valor in valores.items():
            texto = texto.replace("{{%s}}" % chave, valor)

        sobrando = re.findall(r"\{\{[A-Z_]+\}\}", texto)
        if sobrando:
            erro(f"{nome}: placeholder não preenchido {sobrando}")

        saida = DESTINO / nome
        if saida.exists():
            backup = DESTINO / f"{nome}.{marca}.bak"
            shutil.copy2(saida, backup)
            print(f"   ↩️  {nome} já existia — copiado para {backup.name}")

        saida.write_text(texto, encoding="utf-8")
        if secreto:
            try:
                saida.chmod(0o600)
            except Exception:
                pass
        print(f"   ✅ {nome}{'  (chmod 600)' if secreto else ''}")

    # Resumo — nunca mostra as chaves
    print("\n" + "=" * 60)
    print("📋 Configuração aplicada")
    print("=" * 60)
    print(f"   Empresa .............. {valores['EMPRESA']}")
    print(f"   Executivo ............ {valores['EXECUTIVO']}")
    print(f"   IA ................... {valores['AI_PROVIDER']} / {valores['AI_MODEL']}")
    print(f"   Chave da IA .......... {'*' * 8} ({len(ai_key)} caracteres)")
    print(f"   Evolution ............ {valores['EVOLUTION_URL']}")
    print(f"   Instância ............ {valores['INSTANCE_NAME']}")
    print(f"   Chave da Evolution ... {'*' * 8} ({len(evo_key)} caracteres)")

    g = valores["GRUPOS_MONITORADOS"]
    print(f"   Grupos monitorados ... {g.count(',') + 1 if g else ('nenhum' if permitidas else 'TODOS')}")
    print(f"   Destino do diário .... {valores['DEST_GROUP_JID']}")
    print(f"   Horários ............. {valores['HORARIOS_DIARIO'] or 'nenhum (só manual)'}")
    if not monitorar_privadas:
        print("   Conversas privadas ... NÃO lidas")
    elif permitidas:
        print(f"   Conversas privadas ... somente a lista branca ({permitidas})")
    else:
        print("   Conversas privadas ... TODAS lidas")
    if valores["PRIVADAS_IGNORADAS"]:
        print(f"   Lista negra .......... {valores['PRIVADAS_IGNORADAS']}")
    print(f"   Mensagens próprias ... {'capturadas' if capturar_proprias else 'ignoradas'}")

    print(f"\n   Arquivos em: {DESTINO}")
    print("\nPróximos passos:")
    print("   1️⃣  Verificar tudo:      python3 setup/test_agent.py")
    print(f"   2️⃣  Importar histórico:  python3 {DESTINO}/watcher.py --importar 24")
    print(f"   3️⃣  Diário de teste:     python3 {DESTINO}/agent.py --now --horas 24 --dry-run")
    print()


if __name__ == "__main__":
    main()
