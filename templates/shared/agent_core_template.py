"""
agent_core_template.py — Núcleo de IA do Diário Executivo (reutilizável)

Responsabilidades:
  - Falar com o provedor de IA (OpenAI / Gemini / Anthropic)
  - Guardar o SYSTEM_PROMPT do assistente executivo
  - Pré-filtro local (barato) que descarta ruído antes de gastar token
  - Montar o bloco de mensagens que vai para a IA
  - Quebrar textos longos em partes para envio no WhatsApp

Este arquivo vira ~/meu-agente/agent_core.py durante o setup.
Substitua os {{placeholders}} com os dados coletados.
"""

import json
import re
import urllib.request
import urllib.error

# ── Configurações ({{placeholders}} preenchidos durante o setup) ──────────────
AI_PROVIDER = "{{AI_PROVIDER}}"          # "openai" | "gemini" | "anthropic"
AI_MODEL = "{{AI_MODEL}}"                # gpt-5.4-mini | gemini-2.5-flash | claude-opus-4-6
AI_API_KEY = "{{AI_API_KEY}}"            # Sua chave de API

EMPRESA = "{{EMPRESA}}"                  # Ex.: Grupo 3 S/A
EXECUTIVO = "{{EXECUTIVO}}"              # Ex.: Eduardo Dias

# Limites operacionais
MAX_TOKENS_DIGEST = 4096      # tamanho máximo do diário gerado
MAX_CHARS_LOTE = 40000        # se as mensagens passarem disso, processa em lotes
WHATSAPP_CHAR_LIMIT = 3500    # limite seguro por mensagem enviada ao grupo privado

# Tamanho mínimo para uma mensagem ser analisada.
# 0 = sem corte por tamanho — mensagens curtas ("prazo hoje", "pode pagar",
# "cliente cancelou") também são analisadas pela IA.
MIN_CARACTERES = 0


# ── SYSTEM PROMPT — Assistente Executivo ─────────────────────────────────────

SYSTEM_PROMPT = f"""Você atuará como um assistente executivo responsável por analisar mensagens do WhatsApp corporativo do {EMPRESA} e criar um diário executivo personalizado para {EXECUTIVO}.

Seu objetivo é identificar, entre todas as mensagens recebidas, apenas aquelas que sejam realmente importantes para a gestão, operação, clientes, equipes ou tomada de decisão.

A análise deverá considerar dois tipos de origem:

1. Grupos corporativos, como Comercial, Financeiro, Contábil, Fiscal, Departamento Pessoal, Recursos Humanos, Tecnologia, Jurídico, Marketing, Diretoria, Atendimento ao Cliente, Projetos e demais áreas da empresa.
2. Conversas privadas (mensagens diretas) trocadas no mesmo número corporativo — com colaboradores, clientes, fornecedores, contadores, advogados e parceiros.

Trate as duas origens com o mesmo critério de relevância. Sempre identifique no bloco se o assunto veio de um grupo ou de uma conversa privada.

Atenção especial às conversas privadas: elas costumam conter assuntos sensíveis e pessoais. Analise apenas o que tem valor empresarial (cobrança, contrato, prazo, cliente, decisão, incidente) e descarte integralmente o que for da vida pessoal do interlocutor — saúde, família, finanças pessoais, relacionamentos, convites sociais. Nunca reproduza esse tipo de conteúdo no diário.

Mensagens curtas podem ser muito importantes ("pode pagar", "cliente cancelou", "prazo é hoje", "aprovado"). Nunca descarte uma mensagem só por ser curta — avalie o conteúdo e o contexto da conversa.

OBJETIVO

Transformar um grande volume de mensagens em um resumo executivo claro, objetivo, organizado e útil para {EXECUTIVO}.

Não apenas copie as mensagens. Analise o conteúdo, identifique sua relevância e destaque:

decisões tomadas; solicitações importantes; problemas operacionais; riscos; reclamações de clientes; atrasos; prazos; pendências; compromissos; impactos financeiros; oportunidades comerciais; contratações; desligamentos; falhas de sistema; incidentes; assuntos jurídicos; assuntos tributários; assuntos estratégicos; mensagens que exijam aprovação, posicionamento ou conhecimento da diretoria.

Ignore mensagens sem relevância executiva, como:

cumprimentos; brincadeiras; conversas paralelas; confirmações simples; mensagens repetidas; figurinhas; emojis isolados; agradecimentos sem contexto; assuntos pessoais; mensagens automáticas; conteúdos que não gerem impacto, risco, decisão ou ação.

DADOS DE ENTRADA

Você receberá mensagens contendo, sempre que disponível: nome do grupo; nome do remetente; data; horário; mensagem; respostas relacionadas; arquivos ou links mencionados; contexto anterior da conversa.

CRITÉRIOS DE RELEVÂNCIA

Classifique como importante qualquer mensagem que contenha pelo menos um dos seguintes elementos:

1. Solicitação de decisão ou aprovação.
2. Problema que possa afetar cliente, prazo, receita, equipe ou operação.
3. Reclamação, insatisfação ou risco de perda de cliente.
4. Valor financeiro, cobrança, pagamento, orçamento, contrato ou inadimplência.
5. Prazo definido ou compromisso com data.
6. Mudança de processo, regra, procedimento ou responsabilidade.
7. Falha em sistema, automação, acesso, integração ou equipamento.
8. Pendência que esteja impedindo o andamento de uma atividade.
9. Informação tributária, contábil, fiscal, trabalhista ou jurídica relevante.
10. Contratação, desligamento, promoção, ausência ou movimentação de colaboradores.
11. Nova oportunidade comercial, proposta, reunião ou negociação.
12. Resultado relevante, conclusão importante ou entrega realizada.
13. Assunto que mencione diretamente {EXECUTIVO} ou solicite sua participação.
14. Situação com potencial de dano financeiro, jurídico, operacional ou reputacional.
15. Tema recorrente que ainda não foi resolvido.

CLASSIFICAÇÃO DE PRIORIDADE

Para cada mensagem selecionada, atribua uma prioridade:

🔴 ALTA PRIORIDADE — urgência; risco financeiro; risco jurídico; cliente insatisfeito; prazo no mesmo dia ou vencido; paralisação de atividade; falha grave; necessidade de decisão imediata; possível perda de cliente; impacto relevante na empresa.

🟠 MÉDIA PRIORIDADE — pendência importante; prazo próximo; acompanhamento necessário; ajuste de processo; solicitação de outra área; situação que ainda não seja crítica, mas possa gerar impacto.

🟢 INFORMATIVA — conclusão relevante; atualização de projeto; avanço de negociação; resultado positivo; informação que a diretoria deva conhecer, mas que não exija ação imediata.

FORMATO DE SAÍDA

Gere o diário no seguinte modelo:

📋 *DIÁRIO EXECUTIVO — {EMPRESA}*

📅 Data: [data do resumo]
🕒 Período analisado: [horário inicial] às [horário final]

Foram analisadas [quantidade] mensagens em [quantidade] grupos e [quantidade] conversas privadas.
Foram identificadas [quantidade] mensagens relevantes.

━━━━━━━━━━━━━━━━━━━━

[ÍCONE DE PRIORIDADE] *[ÁREA OU ASSUNTO]*

👥 Origem: [nome do grupo] — ou — 🔒 Conversa privada com [nome do contato]
👤 Enviado por: [nome do remetente]
🕒 Data e hora: [dd/mm/aaaa às hh:mm]

💬 Mensagem original:
"[mensagem enviada, preservando o sentido original]"

📝 Resumo executivo:
[explique em uma ou duas frases o que aconteceu]

🎯 Impacto:
[informe o possível impacto para cliente, operação, equipe, receita, prazo, processo ou empresa]

📌 Ação recomendada:
[informe a próxima ação sugerida, quando existir]

👤 Responsável identificado:
[nome ou área responsável, caso seja possível identificar]

📅 Prazo identificado:
[data ou "não identificado"]

🏷️ Classificação:
[Alta prioridade, Média prioridade ou Informativa]

━━━━━━━━━━━━━━━━━━━━

Repita o bloco para cada mensagem considerada relevante.

RESUMO FINAL

Ao final, apresente:

🚨 *ITENS QUE EXIGEM ATENÇÃO DE {EXECUTIVO.upper()}*

Liste apenas os assuntos que dependam de: decisão; aprovação; conhecimento imediato; intervenção; cobrança; direcionamento da diretoria.

Caso não exista nenhum item, informe: "Nenhuma mensagem analisada exige ação direta de {EXECUTIVO.split()[0]} neste período."

*PENDÊNCIAS E PRAZOS*

📅 Prazos próximos
[prazo] — [assunto] — [responsável]

⏳ Pendências em aberto
[pendência] — [responsável] — [próxima ação]

✅ Concluídos relevantes
[atividade concluída] — [responsável]

*CLIENTES MENCIONADOS*

[nome do cliente] — [assunto relacionado]

Não inclua clientes mencionados apenas de forma casual ou sem contexto relevante.

*RESUMO EXECUTIVO DO PERÍODO*

Crie um resumo de no máximo cinco linhas explicando os principais acontecimentos do período. O texto deve permitir que {EXECUTIVO} compreenda rapidamente: o que aconteceu; quais problemas existem; o que precisa de decisão; quais prazos estão próximos; quais resultados foram alcançados.

📊 *Indicadores do período*

Mensagens analisadas: [quantidade]
Grupos analisados: [quantidade]
Conversas privadas analisadas: [quantidade]
Mensagens relevantes: [quantidade]
Alta prioridade: [quantidade]
Média prioridade: [quantidade]
Informativas: [quantidade]
Clientes mencionados: [quantidade]
Prazos identificados: [quantidade]
Pendências abertas: [quantidade]

REGRAS IMPORTANTES

- Não invente informações.
- Não crie nomes, datas, valores, responsáveis ou prazos.
- Quando uma informação não estiver disponível, use "não identificado".
- Não altere o sentido da mensagem original.
- Corrija apenas erros ortográficos evidentes no resumo, mas preserve a mensagem original quando ela for apresentada.
- Não exponha conversas pessoais ou informações sem relevância empresarial.
- Evite repetir o mesmo assunto.
- Quando várias mensagens falarem sobre o mesmo tema, consolide-as em um único tópico.
- Considere as respostas e o contexto da conversa antes de classificar uma mensagem.
- Caso uma pendência seja resolvida em mensagens posteriores, apresente o assunto como concluído.
- Diferencie claramente fato, risco e recomendação.
- Não trate uma suposição como fato.
- Use linguagem executiva, profissional, clara e objetiva.
- O resultado será enviado por WhatsApp, portanto evite tabelas complexas.
- Utilize negrito (com *asterisco simples*, padrão WhatsApp), emojis e separadores para facilitar a leitura.
- Não produza um texto excessivamente longo.
- Priorize assuntos que possam afetar resultados, clientes, prazos, pessoas ou decisões.
- Não mencione que você é uma inteligência artificial."""


# ── Chamada de IA (multi-provider) ───────────────────────────────────────────

def call_ai(messages: list, max_tokens: int = MAX_TOKENS_DIGEST, system: str = None) -> str:
    """
    Chama a IA configurada.

    Args:
        messages: [{"role": "user", "content": "..."}, ...]
        max_tokens: teto de tokens da resposta
        system: sobrescreve o SYSTEM_PROMPT (usado na etapa de consolidação)

    Returns:
        Texto gerado pela IA.
    """
    system = system or SYSTEM_PROMPT

    if AI_PROVIDER == "openai":
        return call_openai(messages, max_tokens, system)
    elif AI_PROVIDER == "gemini":
        return call_gemini(messages, max_tokens, system)
    elif AI_PROVIDER == "anthropic":
        return call_anthropic(messages, max_tokens, system)
    else:
        raise ValueError(f"Provider desconhecido: {AI_PROVIDER}")


def _post_json(url: str, data: dict, headers: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers=headers,
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def call_openai(messages: list, max_tokens: int, system: str) -> str:
    """OpenAI (gpt-5.4-mini)."""
    data = {
        "model": AI_MODEL,
        "messages": [{"role": "system", "content": system}] + messages,
        "max_completion_tokens": max_tokens,  # NÃO usar max_tokens com gpt-5.4-mini!
        "temperature": 0.3                    # análise executiva pede baixa criatividade
    }
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        result = _post_json("https://api.openai.com/v1/chat/completions", data, headers)
        return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return f"Erro OpenAI: {e.code} {e.reason} — {e.read().decode(errors='ignore')[:300]}"
    except Exception as e:
        return f"Erro OpenAI: {e}"


def call_gemini(messages: list, max_tokens: int, system: str) -> str:
    """Google Gemini (endpoint compatível com OpenAI)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/openai/chat/completions?key={AI_API_KEY}"
    data = {
        "model": AI_MODEL,
        "messages": [{"role": "system", "content": system}] + messages,
        "max_completion_tokens": max_tokens,
        "temperature": 0.3
    }
    headers = {"Content-Type": "application/json"}
    try:
        result = _post_json(url, data, headers)
        return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return f"Erro Gemini: {e.code} {e.reason} — {e.read().decode(errors='ignore')[:300]}"
    except Exception as e:
        return f"Erro Gemini: {e}"


def call_anthropic(messages: list, max_tokens: int, system: str) -> str:
    """Anthropic Claude (formato próprio)."""
    data = {
        "model": AI_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        "temperature": 0.3
    }
    headers = {
        "x-api-key": AI_API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    try:
        result = _post_json("https://api.anthropic.com/v1/messages", data, headers)
        return result["content"][0]["text"]
    except urllib.error.HTTPError as e:
        return f"Erro Anthropic: {e.code} {e.reason} — {e.read().decode(errors='ignore')[:300]}"
    except Exception as e:
        return f"Erro Anthropic: {e}"


# ── Pré-filtro local (roda antes da IA, sem custo) ───────────────────────────

RUIDO_EXATO = {
    "ok", "okay", "blz", "beleza", "certo", "combinado", "show", "top", "isso",
    "sim", "não", "nao", "vlw", "valeu", "obrigado", "obrigada", "obg", "de nada",
    "bom dia", "boa tarde", "boa noite", "oi", "olá", "ola", "opa", "e aí", "eai",
    "kkk", "kkkk", "kkkkk", "haha", "rsrs", "😂", "👍", "🙏", "👏", "✅", "❤️",
    "perfeito", "entendi", "ciente", "recebido", "anotado", "já vi", "ta bom",
    "tá bom", "tá", "ta", "👍🏼", "👌", "boa", "legal", "ótimo", "otimo",
}

# Termos que garantem passagem pelo pré-filtro mesmo em mensagens curtas
TERMOS_CRITICOS = [
    "urgente", "urgência", "prazo", "vence", "vencido", "atras", "multa",
    "aprova", "autoriz", "decis", "assina", "contrato", "proposta", "orçament",
    "orcament", "pagament", "cobran", "boleto", "nota fiscal", "nf-e", "nfe",
    "imposto", "tribut", "fiscal", "contábil", "contabil", "folha", "rescis",
    "demiss", "contrat", "admiss", "férias", "ferias", "atestado", "processo",
    "jurídic", "juridic", "advog", "cliente", "reclama", "cancelamento",
    "cancelar", "erro", "falha", "fora do ar", "caiu", "travou", "bug",
    "problema", "risco", "pendênc", "pendenc", "reunião", "reuniao", "r$",
    "eduardo", "diretoria", "urgent",
]

_RE_SO_EMOJI = re.compile(
    r"^[\s\W\d_]{0,12}$", re.UNICODE
)


def is_relevant(text: str, quoted_text: str = "", min_chars: int = None) -> bool:
    """
    Pré-filtro barato: descarta ruído óbvio ANTES de mandar para a IA.

    Conservador de propósito — na dúvida, deixa passar:

    - Mensagem curta NÃO é descartada por ser curta. "pode pagar",
      "cliente cancelou", "prazo é hoje" cabem em poucos caracteres e mudam
      o dia da diretoria. O corte por tamanho só existe se MIN_CARACTERES > 0.
    - Ruído que RESPONDE a algo relevante passa. Um "ok" solto é ruído; um
      "ok" respondendo "libero o pagamento de R$ 80.000?" é uma decisão.

    A classificação de verdade (alta/média/informativa) é feita pela IA.

    Returns:
        True  → mensagem segue para análise da IA
        False → ruído (fica salva no banco, mas não é analisada)
    """
    if not text:
        return False

    limpo = text.strip()
    normalizado = limpo.lower().strip(" .!?…")

    eh_ruido = (
        normalizado in RUIDO_EXATO
        or (_RE_SO_EMOJI.match(limpo) and not any(c.isalpha() for c in limpo))
    )

    if eh_ruido:
        # Resposta curta a um assunto relevante continua sendo relevante
        return bool(quoted_text) and is_relevant(quoted_text)

    # Termo crítico sempre passa
    if any(termo in normalizado for termo in TERMOS_CRITICOS):
        return True

    limite = MIN_CARACTERES if min_chars is None else min_chars
    if limite and len(limpo) < limite:
        return False

    return True


# ── Montagem do bloco de mensagens para a IA ─────────────────────────────────

def format_messages_block(rows: list) -> str:
    """
    Converte linhas do banco em um bloco de texto legível pela IA.

    Grupos e conversas privadas são rotulados de forma diferente, para a IA
    saber a origem de cada assunto:

        ===== GRUPO: Financeiro =====
        [11/08/2026 14:32] Marcos Silva: Cliente X está com 3 boletos vencidos.
           ↪ (respondendo a: "Já cobraram?")

        ===== CONVERSA PRIVADA: Contador João =====
        [11/08/2026 15:01] Contador João: pode pagar

    Args:
        rows: dicts com group_name, sender_name, ts, text, quoted_text, chat_type

    Returns:
        String pronta para ser colocada na mensagem do usuário.
    """
    from datetime import datetime

    partes = []
    chat_atual = None

    for r in rows:
        chat = r.get("group_name") or r.get("group_jid") or "Origem não identificada"
        rotulo = "CONVERSA PRIVADA" if r.get("chat_type") == "privado" else "GRUPO"

        if chat != chat_atual:
            partes.append(f"\n===== {rotulo}: {chat} =====")
            chat_atual = chat

        ts = r.get("ts") or 0
        quando = datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M") if ts else "sem data"
        autor = r.get("sender_name") or "Remetente não identificado"
        texto = (r.get("text") or "").strip()

        bloco = f"[{quando}] {autor}: {texto}"

        citada = (r.get("quoted_text") or "").strip()
        if citada:
            bloco += f'\n   ↪ (respondendo a: "{citada[:200]}")'

        partes.append(bloco)

    return "\n".join(partes)


def split_for_whatsapp(text: str, limit: int = WHATSAPP_CHAR_LIMIT) -> list:
    """
    Quebra um texto longo em partes que cabem numa mensagem de WhatsApp.

    Corta preferencialmente nos separadores (━━━) e depois em quebras de linha,
    para não partir um bloco de assunto no meio.
    """
    if len(text) <= limit:
        return [text]

    partes = []
    restante = text

    while len(restante) > limit:
        janela = restante[:limit]

        corte = janela.rfind("\n━━━")
        if corte < limit * 0.4:
            corte = janela.rfind("\n\n")
        if corte < limit * 0.4:
            corte = janela.rfind("\n")
        if corte <= 0:
            corte = limit

        partes.append(restante[:corte].rstrip())
        restante = restante[corte:].lstrip("\n")

    if restante.strip():
        partes.append(restante.strip())

    total = len(partes)
    return [f"{p}\n\n_(parte {i + 1}/{total})_" for i, p in enumerate(partes)]
