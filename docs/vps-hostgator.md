# Subir o Diário Executivo na VPS da HostGator

Guia completo, do zero até o agente rodando 24 horas por dia. Cada comando é para colar no terminal da VPS, na ordem.

**Tempo estimado:** 40–60 minutos na primeira vez.

---

## ⚠️ Antes de começar — leia estes 4 pontos

**1. Precisa ser VPS, não hospedagem compartilhada.**
Planos de hospedagem de site (compartilhada/cPanel comum) não deixam instalar Docker nem rodar processos em background. Se o seu plano não dá acesso SSH com `root`, ele não serve para isso.

**2. Tamanho mínimo:** 2 GB de RAM e 2 vCPU. A Evolution API sozinha usa entre 400 MB e 1 GB. Com 1 GB de RAM, o servidor trava quando a fila de mensagens cresce.

**3. Sistema operacional:** este guia usa **Ubuntu 22.04 ou 24.04**. Se a sua VPS veio com CentOS/AlmaLinux, os comandos de instalação mudam (`dnf` no lugar de `apt`) — o resto é igual.

**4. Sobre o número do WhatsApp:** use um **número corporativo dedicado**, nunca o seu pessoal. O WhatsApp não tem API oficial para esse tipo de leitura; a Evolution API se conecta como se fosse o WhatsApp Web. Números que se conectam de datacenter e ficam online o tempo todo podem ser bloqueados pelo WhatsApp. Não existe garantia contra isso — se o número for essencial para a operação, avalie o risco antes.

---

## Passo 1 — Entrar na VPS

No painel da HostGator, procure o bloco **Acesso SSH**. Ele mostra três coisas que você vai usar o tempo todo:

- **IP** do servidor
- **Porta** — ⚠️ a HostGator **não usa a porta 22 padrão**. Costuma ser `22022`. Anote a sua.
- **Usuário** (`root`) e o botão de **Alterar senha**

> **Anote a porta agora.** Ela entra em todo comando `ssh` e `scp` deste guia. Se você esquecer, o erro é sempre o mesmo: `Connection refused` na porta 22.

No **seu computador** (PowerShell no Windows, Terminal no Mac/Linux):

```bash
ssh -p 22022 root@SEU_IP_AQUI
```

Troque `22022` pela porta do seu painel e `SEU_IP_AQUI` pelo IP real. Na primeira conexão ele pergunta se confia no servidor — responda `yes`. Depois cole a senha (ela não aparece na tela enquanto você digita, isso é normal).

Se aparecer `root@servidor:~#`, você está dentro.

**Se der `Connection refused`:** a porta está errada. Descubra qual está aberta rodando isto no PowerShell da sua máquina:

```bash
foreach ($p in 22,22022,2222,21098) { $c = New-Object System.Net.Sockets.TcpClient; $r = $c.BeginConnect("SEU_IP_AQUI",$p,$null,$null); if ($r.AsyncWaitHandle.WaitOne(3000,$false) -and $c.Connected) { Write-Host "ABERTA $p" }; $c.Close() }
```

**Se der `Connection timed out`** (em vez de refused): aí não é porta — é a VPS desligada, ainda em provisionamento, ou o seu IP bloqueado no firewall do servidor. Abra chamado na HostGator.

---

## Passo 2 — Deixar o servidor seguro

Nunca rode o agente como `root`. Crie um usuário próprio:

```bash
adduser agente
```

Ele pede uma senha nova (anote) e alguns dados opcionais — pode dar Enter em tudo.

```bash
usermod -aG sudo agente
```

Agora o firewall. **Só a porta do SSH fica aberta para a internet.**

> 🚨 **Atenção — este é o comando que mais trava gente do lado de fora do próprio servidor.**
> O atalho `ufw allow OpenSSH` libera a porta **22**. Como a sua HostGator usa **22022**, ligar o firewall com esse atalho fecharia a porta pela qual você está conectado — e você perderia o acesso na hora, sem conseguir voltar.
> Libere a **sua** porta, explicitamente:

```bash
sudo ufw allow 22022/tcp
```

Troque `22022` pela porta do seu painel. Confira que a regra entrou **antes** de ligar o firewall:

```bash
sudo ufw show added
```

Tem que aparecer `ufw allow 22022/tcp` na lista. Só então ligue:

```bash
sudo ufw --force enable && sudo ufw status
```

**Não feche esta janela do terminal.** Abra uma segunda janela e teste se ainda consegue entrar (`ssh -p 22022 root@SEU_IP`). Se conseguir, está tudo certo. Se não conseguir, use a janela antiga — que continua conectada — para desligar o firewall com `sudo ufw disable` e revisar a regra.

Atualize o sistema:

```bash
apt update && apt upgrade -y
```

Troque para o novo usuário:

```bash
su - agente
```

O prompt vira `agente@servidor:~$`. **Daqui para frente, todos os comandos são nesse usuário.**

---

## Passo 3 — Instalar Docker, Python e Git

```bash
sudo apt install -y python3 python3-pip git curl
```

Docker (script oficial):

```bash
curl -fsSL https://get.docker.com | sudo sh
```

Dê permissão ao seu usuário para usar Docker sem `sudo`:

```bash
sudo usermod -aG docker agente
```

Essa permissão só vale depois de reabrir a sessão. Saia e entre de novo:

```bash
exit
```

```bash
ssh -p 22022 agente@SEU_IP_AQUI
```

Confira se está tudo certo:

```bash
docker run --rm hello-world && python3 --version && git --version
```

Se aparecer "Hello from Docker!" e as versões, pode seguir.

---

## Passo 4 — Baixar o projeto e instalar a Evolution API

```bash
git clone https://github.com/lucasvieirapcr/Automa-o-Leitura-Whatsapp.git && cd Automa-o-Leitura-Whatsapp
```

```bash
python3 setup/install_evolution.py
```

Isso baixa a Evolution API, gera uma chave de API aleatória e sobe o container. Leva de 2 a 5 minutos. Ao final ele mostra onde a chave foi salva (`~/meu-agente/evolution-api/.env`).

### 🔒 Passo obrigatório de segurança

Por padrão a Evolution API fica exposta na internet inteira — e ela tem acesso ao seu WhatsApp. **O firewall do Ubuntu não protege containers Docker**, porque o Docker escreve as próprias regras de rede direto no iptables, passando por cima do UFW. É preciso corrigir isso no arquivo do container.

Abra o arquivo:

```bash
nano ~/meu-agente/evolution-api/docker-compose.yaml
```

Procure a linha de portas, que se parece com isto:

```yaml
    ports:
      - 8080:8080
```

E troque por isto (repare no `127.0.0.1:` na frente):

```yaml
    ports:
      - 127.0.0.1:8080:8080
```

Salve com `Ctrl+O`, Enter, e saia com `Ctrl+X`.

> Se o arquivo se chamar `docker-compose.yml` em vez de `.yaml`, use esse nome. Para descobrir: `ls ~/meu-agente/evolution-api/`

Aplique a mudança:

```bash
cd ~/meu-agente/evolution-api && docker compose up -d --force-recreate && cd ~/Automa-o-Leitura-Whatsapp
```

Confirme que agora a porta só responde localmente:

```bash
curl -s -o /dev/null -w "local: %{http_code}\n" http://127.0.0.1:8080/
```

Deve responder `local: 200`. E, do **seu computador**, `http://SEU_IP:8080` não deve abrir nada — é exatamente esse o objetivo.

---

## Passo 5 — Conectar o WhatsApp pelo terminal

Na VPS não existe tela para abrir a imagem do QR Code, então ele é desenhado no próprio terminal. Instale a biblioteca que faz isso:

```bash
pip3 install qrcode
```

Deixe a janela do terminal **grande** (o QR precisa de espaço) e rode:

```bash
python3 setup/connect_whatsapp.py --ascii
```

O QR Code aparece em blocos de texto. No celular: **WhatsApp → Configurações → Aparelhos Conectados → Conectar Aparelho** e aponte a câmera para a tela.

Quando aparecer `✅ WhatsApp conectado!`, terminou.

**Se o QR sair embaralhado ou cortado:** aumente a janela, diminua a fonte do terminal (`Ctrl+-`) e rode o comando de novo — cada QR vale por cerca de 60 segundos.

**Se preferir escanear de uma imagem:** rode `python3 setup/connect_whatsapp.py` sem o `--ascii`; ele salva `~/meu-agente/qrcode.png` e mostra o comando `scp` para baixar o arquivo no seu computador.

---

## Passo 6 — Configurar o agente

Você tem dois caminhos. O A é o mesmo do produto (o Claude configura tudo conversando); o B é manual, para quem prefere não instalar o Claude na VPS.

### Caminho A — deixar o Claude configurar (recomendado)

```bash
sudo apt install -y nodejs npm && sudo npm install -g @anthropic-ai/claude-code
```

```bash
cd ~/Automa-o-Leitura-Whatsapp && claude
```

Ele faz as perguntas (grupos, grupo privado de destino, conversas privadas, empresa, executivo, horários) e gera os arquivos sozinho. Pule para o Passo 7.

### Caminho B — configurar na mão

Descubra os IDs dos grupos:

```bash
python3 setup/list_groups.py
```

Anote dois grupos da lista: os que você quer **acompanhar** e o **grupo privado** onde o diário será publicado. Os IDs terminam em `@g.us`.

Crie a pasta e copie os arquivos:

```bash
mkdir -p ~/meu-agente && cp templates/shared/*_template.py templates/whatsapp/agent_template.py templates/whatsapp/watcher_template.py ~/meu-agente/
```

```bash
cd ~/meu-agente && mv agent_core_template.py agent_core.py && mv storage_template.py storage.py && mv evolution_template.py evolution.py && mv agent_template.py agent.py && mv watcher_template.py watcher.py
```

Agora edite cada arquivo trocando os `{{...}}`:

```bash
nano ~/meu-agente/agent_core.py
```
Troque `{{AI_PROVIDER}}` (`openai`, `gemini` ou `anthropic`), `{{AI_MODEL}}`, `{{AI_API_KEY}}`, `{{EMPRESA}}` e `{{EXECUTIVO}}`.

```bash
nano ~/meu-agente/evolution.py
```
Troque `{{EVOLUTION_API_KEY}}` (pegue com `grep API_KEY ~/meu-agente/evolution-api/.env`) e `{{DEST_GROUP_JID}}` (o grupo privado).

```bash
nano ~/meu-agente/watcher.py
```
Troque `{{HORARIOS_DIARIO}}` por `12:00,18:00`, `{{GRUPOS_MONITORADOS}}` pelos IDs separados por vírgula (ou deixe vazio para todos) e `{{PRIVADAS_IGNORADAS}}` pelos números pessoais a ignorar (ou vazio).

---

## Passo 7 — Testar

```bash
cd ~/Automa-o-Leitura-Whatsapp && python3 setup/test_agent.py
```

Ele confere arquivos, chaves, conexão do WhatsApp, grupo de destino, IA, filtro e banco. Se algo falhar, a mensagem diz exatamente o quê.

Passando tudo, gere um diário de teste **sem publicar**:

```bash
python3 ~/meu-agente/agent.py --now --horas 24 --dry-run
```

Leia o resultado na tela. Gostou? Publique de verdade:

```bash
python3 ~/meu-agente/agent.py --now --horas 24
```

Confira o grupo privado no celular.

---

## Passo 8 — Deixar rodando para sempre

Sem isso, o agente morre quando você fechar o SSH. O systemd resolve: ele liga o watcher no boot e reinicia se cair.

Crie o arquivo do serviço:

```bash
sudo nano /etc/systemd/system/diario-executivo.service
```

Cole isto (se o seu usuário não se chama `agente`, troque nos três lugares):

```ini
[Unit]
Description=Diario Executivo WhatsApp - watcher
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=agente
WorkingDirectory=/home/agente/meu-agente
ExecStart=/usr/bin/python3 /home/agente/meu-agente/watcher.py
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

`Ctrl+O`, Enter, `Ctrl+X` para salvar.

Ative:

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now diario-executivo
```

Veja se subiu:

```bash
systemctl status diario-executivo
```

Tem que aparecer `active (running)` em verde. Aperte `q` para sair.

Acompanhe ao vivo o que ele está capturando:

```bash
journalctl -u diario-executivo -f
```

`Ctrl+C` para parar de acompanhar (isso **não** para o serviço).

Garanta que a Evolution API também volta sozinha depois de um reboot:

```bash
cd ~/meu-agente/evolution-api && docker compose up -d && cd ~
```

O Docker já reinicia os containers no boot por padrão. Para ter certeza absoluta, reinicie a VPS uma vez e confira:

```bash
sudo reboot
```

Espere 1 minuto, entre de novo por SSH e rode:

```bash
systemctl status diario-executivo && docker ps
```

---

## Comandos do dia a dia

| O que você quer | Comando |
|---|---|
| Ver o agente trabalhando | `journalctl -u diario-executivo -f` |
| Gerar o diário agora | `python3 ~/meu-agente/agent.py --now` |
| Ver o diário sem publicar | `python3 ~/meu-agente/agent.py --now --dry-run` |
| Painel de números | `python3 ~/meu-agente/agent.py --stats` |
| Parar o agente | `sudo systemctl stop diario-executivo` |
| Ligar de novo | `sudo systemctl start diario-executivo` |
| Reiniciar depois de editar config | `sudo systemctl restart diario-executivo` |
| Ver o WhatsApp conectado | `curl -s -H "apikey: $(grep API_KEY ~/meu-agente/evolution-api/.env \| cut -d= -f2)" http://127.0.0.1:8080/instance/connectionState/meu-agente` |
| Ver os diários salvos | `ls ~/meu-agente/diarios/` |
| Ler o último diário | `cat "$(ls -t ~/meu-agente/diarios/*.md \| head -1)"` |

Depois de editar `watcher.py`, `evolution.py` ou `agent_core.py`, **sempre reinicie**:

```bash
sudo systemctl restart diario-executivo
```

---

## Backup

O que importa está em dois lugares: o banco (`dados.sqlite`) e a sessão do WhatsApp (dentro de `evolution-api`). Perder a sessão significa escanear o QR Code de novo.

Backup manual:

```bash
tar czf ~/backup-diario-$(date +%F).tar.gz ~/meu-agente/dados.sqlite ~/meu-agente/diarios ~/meu-agente/evolution-api/.env
```

Baixar para o seu computador (rode **na sua máquina**, não na VPS):

```bash
scp -P 22022 agente@SEU_IP_AQUI:~/backup-diario-*.tar.gz .
```

> No `scp` o `P` é **maiúsculo** (`-P`); no `ssh` é minúsculo (`-p`). Trocar os dois é erro comum.

Backup automático toda madrugada:

```bash
(crontab -l 2>/dev/null; echo "0 3 * * * tar czf ~/backup-diario-\$(date +\%F).tar.gz ~/meu-agente/dados.sqlite ~/meu-agente/diarios ~/meu-agente/evolution-api/.env && find ~ -name 'backup-diario-*.tar.gz' -mtime +14 -delete") | crontab -
```

Isso guarda os últimos 14 dias e apaga os mais antigos sozinho.

---

## Problemas comuns

| Sintoma | Causa provável | Solução |
|---|---|---|
| `ssh: connect to host ... port 22: Connection refused` | SSH não está na porta 22 | use `-p 22022` (ou a porta do painel, bloco "Acesso SSH") |
| Perdi o acesso SSH depois de ligar o `ufw` | liberou a porta 22 em vez da porta real | só o suporte da HostGator resolve (console de recuperação) — por isso teste em uma segunda janela **antes** de fechar a primeira |
| `systemctl status` mostra `failed` | erro de Python no watcher | `journalctl -u diario-executivo -n 50` mostra a linha do erro |
| Diário nunca chega no grupo | `DEST_GROUP_JID` errado | `python3 setup/list_groups.py` e confira o ID em `~/meu-agente/evolution.py` |
| Nenhuma mensagem sendo capturada | WhatsApp desconectou | rode `python3 setup/connect_whatsapp.py --ascii` de novo |
| `Evolution API erro: Connection refused` | container caiu | `cd ~/meu-agente/evolution-api && docker compose up -d` |
| WhatsApp cai sozinho toda hora | mesmo número logado em outro lugar, ou bloqueio | use um número dedicado, só nesse servidor |
| `Erro OpenAI: 401` | chave de IA errada ou sem crédito | corrija `AI_API_KEY` em `~/meu-agente/agent_core.py` e reinicie |
| `Erro ... 429` | limite de requisições da IA | aumente `MAX_CHARS_LOTE` em `agent_core.py` (menos chamadas, lotes maiores) |
| Servidor lento, sem memória | 1 GB de RAM | suba o plano, ou adicione swap: veja abaixo |
| QR Code ilegível no terminal | janela pequena | aumente a janela, `Ctrl+-` para diminuir a fonte, rode de novo |

**Adicionar 2 GB de swap** (paliativo para pouca RAM):

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## Segurança e privacidade — checklist final

- [ ] Firewall ligado, só SSH aberto (`sudo ufw status`)
- [ ] Evolution API presa em `127.0.0.1` no docker-compose (`curl http://SEU_IP:8080` de fora **não** pode responder)
- [ ] Agente rodando com usuário comum, não `root`
- [ ] Chave da IA e chave da Evolution só nos arquivos de `~/meu-agente` — nunca commitadas no Git
- [ ] Backup automático configurado
- [ ] Login por senha do `root` desativado, se você já usa chave SSH
- [ ] **A equipe foi avisada** de que os grupos corporativos são monitorados

Esse último item não é burocracia: monitorar grupos e conversas privadas de colaboradores é tratamento de dados pessoais e entra na LGPD. O diário lê mensagens de pessoas que não escolheram ser lidas. Alinhe com o jurídico e o RH, avise a equipe e use `PRIVADAS_IGNORADAS` para deixar de fora os números pessoais antes de ligar em produção.
