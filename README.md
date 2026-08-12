# Diário Executivo WhatsApp 📋

Um **assistente que lê os grupos corporativos do WhatsApp, separa só o que importa para a diretoria e publica um diário executivo pronto num grupo privado**. Setup em 15 minutos.

> **Ele nunca responde ninguém.** A única mensagem que o agente escreve é o diário, no grupo privado que você escolher.

## ⚡ Quick Start

Cole este comando no terminal e o Claude faz o resto:

```bash
git clone https://github.com/lucasvieirapcr/Automa-o-Leitura-Whatsapp.git && cd Automa-o-Leitura-Whatsapp && claude
```

O Claude abre automaticamente e conduz o setup por você — sem mais nenhum comando.

## 📋 O que você vai ter

- ✅ Captura automática dos **grupos** que você escolher **e das conversas privadas** do mesmo número
- ✅ Filtro que descarta ruído (bom dia, ok, figurinha) antes de gastar token
- ✅ Análise por IA com critérios executivos: risco, prazo, cliente, dinheiro, decisão
- ✅ Diário classificado por prioridade (🔴 alta / 🟠 média / 🟢 informativa)
- ✅ Seções de pendências, prazos, clientes mencionados e indicadores
- ✅ Publicação automática nos horários que você definir
- ✅ Histórico local em SQLite + cópia em markdown de cada diário

## 🏗️ Arquitetura

### Stack
- **Evolution API** — conexão com WhatsApp (open-source, roda local via Docker)
- **SQLite** — mensagens capturadas e diários gerados, tudo na sua máquina
- **Python 3.9+** — sem dependências externas (só biblioteca padrão)
- **Multi-IA** — OpenAI, Gemini ou Anthropic

### Fluxo

```
Mensagens chegam nos grupos corporativos e nas conversas privadas
    ↓
watcher.py consulta a Evolution API a cada 30s (paginando até alcançar
o ponto onde parou, para não perder rajadas de mensagens)
    ↓
Filtra: grupos monitorados + conversas privadas (menos os números bloqueados);
ignora sempre o grupo onde o diário é publicado
    ↓
Pré-filtro local marca ruído (não apaga — só não manda para a IA)
    ↓
Tudo é gravado no SQLite (dedupe por ID da mensagem)
    ↓
No horário configurado (ex.: 12h e 18h):
    ↓
Monta o período: desde o fim do último diário até agora
    ↓
IA analisa com o prompt do assistente executivo
  (volume alto → divide em lotes e consolida no final)
    ↓
Diário salvo no banco + arquivo .md
    ↓
Publicado no grupo privado, quebrado em partes de 3.500 caracteres
```

### Por que duas etapas de filtro?

O pré-filtro local (`is_relevant`) é **barato e conservador**: só descarta o que é ruído por definição — "ok", "bom dia", figurinhas, emoji solto.

Três regras garantem que nada importante se perca:

- **Tamanho não elimina ninguém.** "pode pagar", "cliente cancelou", "prazo é hoje" e "aprovado" são curtíssimas e mudam o dia da diretoria. O corte por tamanho vem desligado (`MIN_CARACTERES = 0`).
- **Termo crítico sempre passa** — `prazo`, `vence`, `multa`, `cliente`, `R$`, `caiu`, `contrato`, e por aí.
- **Ruído que responde a algo relevante passa.** Um "ok" solto é ruído; um "ok" respondendo *"libero o pagamento de R$ 80.000?"* é uma decisão, e vai junto com a mensagem citada.

A classificação de verdade — alta/média/informativa, impacto, ação recomendada — é feita pela IA, que vê o contexto da conversa inteira.

### Conversas privadas

O agente lê também as mensagens diretas do número conectado, porque é ali que chegam cobrança de fornecedor, parecer de advogado, guia do contador e reclamação de cliente.

Quatro proteções:

- `MONITORAR_PRIVADAS = False` — desliga a leitura de privadas de uma vez
- `PRIVADAS_PERMITIDAS` — **lista branca**: se tiver algum número, só esses são lidos. É o ajuste certo para número pessoal ou para liberar apenas contador, advogado e clientes-chave
- `PRIVADAS_IGNORADAS` — lista negra de números que nunca são lidos (família, amigos)
- O `SYSTEM_PROMPT` instrui explicitamente a descartar assunto pessoal (saúde, família, convites, finanças pessoais) e nunca reproduzir esse conteúdo no diário

No diário, cada bloco mostra a origem: `👥 Comercial` ou `🔒 Conversa privada com Contador João`.

## 📁 Estrutura

```
Automa-o-Leitura-Whatsapp/
├── CLAUDE.md                          ← Roteiro do setup guiado
├── README.md                          ← Este arquivo
│
├── setup/
│   ├── check_prerequisites.py         ← Verifica dependências
│   ├── install_evolution.py           ← Instala a Evolution API
│   ├── connect_whatsapp.py            ← Conecta WhatsApp via QR Code
│   ├── list_groups.py                 ← Lista grupos e seus JIDs
│   ├── test_api.py                    ← Testa a chave de IA
│   └── test_agent.py                  ← Verifica a instalação inteira
│
├── templates/
│   ├── shared/
│   │   ├── agent_core_template.py     ← IA + SYSTEM_PROMPT + pré-filtro
│   │   ├── storage_template.py        ← SQLite (grupos, mensagens, diários)
│   │   └── evolution_template.py      ← Cliente da Evolution API
│   │
│   └── whatsapp/
│       ├── agent_template.py          ← Motor do diário (map/reduce + publicação)
│       ├── watcher_template.py        ← Captura contínua + agendamento
│       ├── launchagent_template.plist ← Auto-start no macOS
│       └── systemd_template.service   ← Auto-start no Linux/VPS
│
└── docs/
    ├── prerequisitos.md
    ├── guia.html
    └── vps-hostgator.md               ← Passo a passo da VPS
```

Depois do setup, os arquivos gerados ficam em `~/meu-agente/`:

```
~/meu-agente/
├── agent.py  agent_core.py  storage.py  evolution.py  watcher.py
├── dados.sqlite          ← mensagens + diários
├── diarios/              ← cópia .md de cada diário
├── watcher.log
└── watcher_state.json    ← controle de qual horário já rodou hoje
```

## 🎯 Comandos do dia a dia

```bash
python3 ~/meu-agente/agent.py --now
```

| Comando | O que faz |
|---|---|
| `agent.py --now` | gera o diário do período pendente e publica |
| `agent.py --now --dry-run` | mostra na tela sem publicar |
| `agent.py --horas 24` | analisa as últimas 24 horas |
| `agent.py --horas 168` | fecha a semana |
| `agent.py --stats` | painel: mensagens armazenadas, diários gerados |
| `agent.py --test` | testa o pré-filtro e a conexão com a IA |
| `watcher.py` | liga a captura contínua |
| `watcher.py --importar 48` | traz o histórico já guardado na Evolution API (últimas 48h) |

## ⚙️ Ajustes

| Quero mudar | Onde |
|---|---|
| Grupos acompanhados | `GRUPOS_MONITORADOS` em `~/meu-agente/watcher.py` |
| Ligar/desligar conversas privadas | `MONITORAR_PRIVADAS` em `~/meu-agente/watcher.py` |
| Liberar só alguns contatos privados | `PRIVADAS_PERMITIDAS` em `~/meu-agente/watcher.py` |
| Números privados a ignorar | `PRIVADAS_IGNORADAS` em `~/meu-agente/watcher.py` |
| Capturar as próprias mensagens | `CAPTURAR_PROPRIAS` em `~/meu-agente/watcher.py` |
| Horários do diário | `HORARIOS_DIARIO` em `~/meu-agente/watcher.py` |
| Grupo que recebe o diário | `DEST_GROUP_JID` em `~/meu-agente/evolution.py` |
| O que conta como ruído | `RUIDO_EXATO` / `TERMOS_CRITICOS` / `MIN_CARACTERES` em `~/meu-agente/agent_core.py` |
| Formato e critérios do diário | `SYSTEM_PROMPT` em `~/meu-agente/agent_core.py` |
| Retenção das mensagens | `RETENCAO_DIAS` em `~/meu-agente/storage.py` (padrão: 90 dias) |

Depois de editar `watcher.py`, reinicie o watcher.

## 🔐 Segurança e privacidade

- **Mensagens ficam na sua máquina** — Evolution API local + SQLite local
- **Só o texto filtrado vai para a IA** — ruído e conversa pessoal curta nem saem do computador
- **Sem credenciais no código-fonte do repositório** — as chaves só existem nos arquivos gerados em `~/meu-agente/`
- **Retenção automática** — mensagens com mais de 90 dias são apagadas; os diários ficam
- **O grupo do diário nunca é lido** — evita loop e mantém o resumo fora da análise
- **Aviso à equipe**: monitorar grupos corporativos é tratamento de dados pessoais. Informe os participantes e verifique a política interna da empresa antes de ligar em produção.

## 🚀 Rodar 24/7

- **macOS:** LaunchAgent incluído (`launchagent_template.plist`) — inicia no login e reinicia sozinho
- **Linux/VPS:** Evolution API em Docker + watcher como serviço systemd — passo a passo em [docs/vps-hostgator.md](docs/vps-hostgator.md)

Rodar na VPS é o recomendado: o notebook pode desligar, a VPS não. O WhatsApp desconecta se o servidor ficar dias fora do ar.

## 📚 Documentação

- [Pré-requisitos por SO](docs/prerequisitos.md)
- [Guia visual do setup](docs/guia.html)
- [Subir na VPS (HostGator)](docs/vps-hostgator.md)

## 💬 Suporte

Problemas? Rode `python3 setup/test_agent.py` — ele diz exatamente qual etapa falhou.

## 📄 Licença

MIT — use livremente em produção.

---

**Feito por [ZX LAB](https://zxlab.com.br)**
v2.0 — Diário Executivo
