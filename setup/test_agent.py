#!/usr/bin/env python3
"""
test_agent.py — Verifica se o Diário Executivo está pronto para rodar

Checa, em ordem:
  1. Arquivos gerados em ~/meu-agente
  2. Import dos módulos (placeholders substituídos?)
  3. WhatsApp conectado
  4. Grupo privado de destino configurado
  5. Chamada real à IA
  6. Pré-filtro de ruído
  7. Banco de dados
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

HOME = Path.home()
AGENTE = HOME / "meu-agente"
ARQUIVOS = ["agent.py", "agent_core.py", "storage.py", "evolution.py", "watcher.py"]


def falhar(msg, dica=None):
    print(f"   ❌ {msg}")
    if dica:
        print(f"      {dica}")
    sys.exit(1)


def main():
    print("=" * 60)
    print("🧪 Testando o Diário Executivo")
    print("=" * 60 + "\n")

    # 1. Arquivos
    print("1️⃣  Verificando arquivos...")
    faltando = [f for f in ARQUIVOS if not (AGENTE / f).exists()]
    if faltando:
        falhar(
            f"Faltam arquivos em {AGENTE}: {', '.join(faltando)}",
            "As etapas anteriores do setup ainda não foram concluídas.",
        )
    print(f"   ✅ {len(ARQUIVOS)} arquivos em {AGENTE}")

    # 2. Import
    print("\n2️⃣  Carregando o agente...")
    sys.path.insert(0, str(AGENTE))
    try:
        import agent
        import agent_core
        import evolution
    except Exception as e:
        falhar(f"Erro ao carregar: {e}", "Provavelmente sobrou algum {{placeholder}} nos arquivos.")
    print("   ✅ Módulos carregados")

    for nome, valor in [
        ("AI_API_KEY", agent_core.AI_API_KEY),
        ("EVOLUTION_API_KEY", evolution.EVOLUTION_API_KEY),
        ("DEST_GROUP_JID", evolution.DEST_GROUP_JID),
    ]:
        if "{{" in str(valor):
            falhar(f"{nome} não foi preenchido", "Refaça a etapa de geração dos arquivos.")

    # 3. WhatsApp
    print("\n3️⃣  Verificando WhatsApp...")
    estado = evolution.connection_state()
    if estado == "open":
        print("   ✅ WhatsApp conectado")
    else:
        print(f"   ⚠️  Estado da conexão: '{estado or 'desconhecido'}'")
        print("      Rode: python3 setup/connect_whatsapp.py")

    # 4. Destino do diário
    print("\n4️⃣  Verificando onde o diário será publicado...")
    destino = evolution.DEST_GROUP_JID
    if destino.endswith("@g.us"):
        grupos = {g["jid"]: g["name"] for g in evolution.fetch_groups()}
        if destino in grupos:
            print(f"   ✅ Grupo de destino: {grupos[destino]}")
        else:
            print(f"   ⚠️  Grupo {destino} não apareceu na lista — confirme se o número participa dele")
    elif any(c.isdigit() for c in destino):
        numero = destino.split("@")[0]
        print(f"   ✅ Destino é uma conversa privada: {numero}")
        print("      (o diário chega como mensagem direta, não em grupo)")
    else:
        falhar(f"DEST_GROUP_JID inválido: {destino}",
               "Use o JID de um grupo (...@g.us) ou um número com DDI e DDD")

    # 5. IA
    print("\n5️⃣  Testando a IA...")
    resposta = agent_core.call_ai(
        [{"role": "user", "content": "Responda apenas com a palavra: pronto"}],
        max_tokens=32,
    )
    if str(resposta).startswith("Erro "):
        falhar(f"IA retornou erro: {resposta[:200]}", "Confira a chave de API.")
    print(f"   ✅ IA respondeu: \"{str(resposta).strip()[:60]}\"")

    # 6. Pré-filtro
    print("\n6️⃣  Testando o filtro de ruído...")
    agent.test_prefilter()

    # 7. Banco
    print("\n7️⃣  Testando o banco de dados...")
    from storage import init_db, get_stats
    init_db()
    stats = get_stats()
    print(f"   ✅ Banco OK: {AGENTE / 'dados.sqlite'}")
    print(f"      Mensagens armazenadas: {stats['mensagens_total']}")
    print(f"      Diários gerados: {stats['diarios_gerados']}")

    print("\n" + "=" * 60)
    print("✅ Tudo pronto!")
    print("=" * 60)
    print("\nPróximos passos:")
    print("  1️⃣  Ligar a captura:      python3 ~/meu-agente/watcher.py")
    print("  2️⃣  Gerar um diário já:   python3 ~/meu-agente/agent.py --now --horas 24")
    print("  3️⃣  Ver sem publicar:     python3 ~/meu-agente/agent.py --now --horas 24 --dry-run\n")


if __name__ == "__main__":
    main()
