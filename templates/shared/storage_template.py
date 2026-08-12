"""
storage_template.py — Banco local (SQLite) do Diário Executivo

Três tabelas:
  groups   → grupos do WhatsApp descobertos, com flag de monitoramento
  messages → toda mensagem capturada dos grupos monitorados
  digests  → cada diário gerado (texto completo + indicadores)

Nada sai da máquina além das chamadas à API de IA.
Este arquivo vira ~/meu-agente/storage.py durante o setup.
"""

import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path.home() / "meu-agente" / "dados.sqlite"

# Retenção: mensagens mais antigas que isso são apagadas automaticamente
RETENCAO_DIAS = 90


def _db():
    """Abre conexão SQLite (cria o diretório se necessário)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Cria as tabelas se ainda não existirem."""
    conn = _db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            jid        TEXT PRIMARY KEY,
            name       TEXT,
            monitored  INTEGER DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """)

    # group_jid/group_name guardam a ORIGEM da mensagem: o grupo, ou o contato
    # da conversa privada. chat_type diz qual dos dois é.
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id           TEXT PRIMARY KEY,   -- key.id do WhatsApp (dedupe)
            group_jid    TEXT NOT NULL,
            group_name   TEXT,
            chat_type    TEXT DEFAULT 'grupo',  -- 'grupo' | 'privado'
            sender_jid   TEXT,
            sender_name  TEXT,
            text         TEXT,
            msg_type     TEXT,
            quoted_text  TEXT,
            ts           INTEGER NOT NULL,   -- epoch da mensagem
            captured_at  TEXT NOT NULL,
            prefiltered  INTEGER DEFAULT 1,  -- 1 = passou no pré-filtro e vai para a IA
            digest_id    INTEGER             -- preenchido quando entra em um diário
        )
    """)

    # Migração para bancos criados antes das conversas privadas
    colunas = {row["name"] for row in c.execute("PRAGMA table_info(messages)")}
    if "chat_type" not in colunas:
        c.execute("ALTER TABLE messages ADD COLUMN chat_type TEXT DEFAULT 'grupo'")

    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_digest ON messages(digest_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_group ON messages(group_jid)")

    # IDs de toda mensagem que o watcher já examinou — inclusive as que foram
    # descartadas por filtro (grupo não monitorado, número ignorado). Serve
    # para o watcher saber onde parar de paginar sem reexaminar o histórico.
    c.execute("""
        CREATE TABLE IF NOT EXISTS seen_ids (
            id TEXT PRIMARY KEY,
            ts INTEGER NOT NULL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_seen_ts ON seen_ids(ts)")

    c.execute("""
        CREATE TABLE IF NOT EXISTS digests (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            period_start   INTEGER NOT NULL,
            period_end     INTEGER NOT NULL,
            content        TEXT NOT NULL,
            msg_count      INTEGER DEFAULT 0,
            group_count    INTEGER DEFAULT 0,
            analyzed_count INTEGER DEFAULT 0,
            delivered      INTEGER DEFAULT 0,
            created_at     TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


# ── Grupos ───────────────────────────────────────────────────────────────────

def upsert_group(jid: str, name: str, monitored: bool = None):
    """Cadastra/atualiza um grupo. monitored=None preserva o valor atual."""
    conn = _db()
    c = conn.cursor()
    now = datetime.now().isoformat()

    if monitored is None:
        c.execute("""
            INSERT INTO groups (jid, name, monitored, updated_at) VALUES (?, ?, 0, ?)
            ON CONFLICT(jid) DO UPDATE SET name = ?, updated_at = ?
        """, (jid, name, now, name, now))
    else:
        c.execute("""
            INSERT INTO groups (jid, name, monitored, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(jid) DO UPDATE SET name = ?, monitored = ?, updated_at = ?
        """, (jid, name, int(monitored), now, name, int(monitored), now))

    conn.commit()
    conn.close()


def get_group_name(jid: str) -> str:
    conn = _db()
    c = conn.cursor()
    c.execute("SELECT name FROM groups WHERE jid = ?", (jid,))
    row = c.fetchone()
    conn.close()
    return row["name"] if row and row["name"] else jid


def list_groups(only_monitored: bool = False) -> list:
    conn = _db()
    c = conn.cursor()
    if only_monitored:
        c.execute("SELECT jid, name, monitored FROM groups WHERE monitored = 1 ORDER BY name")
    else:
        c.execute("SELECT jid, name, monitored FROM groups ORDER BY name")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ── Mensagens ────────────────────────────────────────────────────────────────

def save_message(msg: dict) -> bool:
    """
    Salva uma mensagem capturada. Retorna False se já existia (dedupe por id).

    Espera as chaves: id, group_jid, group_name, chat_type, sender_jid,
                      sender_name, text, msg_type, quoted_text, ts, prefiltered
    """
    conn = _db()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO messages
                (id, group_jid, group_name, chat_type, sender_jid, sender_name,
                 text, msg_type, quoted_text, ts, captured_at, prefiltered)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            msg["id"],
            msg["group_jid"],
            msg.get("group_name"),
            msg.get("chat_type", "grupo"),
            msg.get("sender_jid"),
            msg.get("sender_name"),
            msg.get("text", ""),
            msg.get("msg_type", "text"),
            msg.get("quoted_text"),
            int(msg.get("ts") or time.time()),
            datetime.now().isoformat(),
            int(msg.get("prefiltered", 1)),
        ))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False   # mensagem repetida — normal no polling
    finally:
        conn.close()


def filter_unseen(ids: list) -> set:
    """
    Dos IDs informados, devolve só os que o watcher ainda não examinou.

    É assim que o watcher sabe quando parar de paginar: se uma página inteira
    já foi examinada, não há nada mais novo atrás dela.
    """
    ids = [i for i in ids if i]
    if not ids:
        return set()

    conn = _db()
    c = conn.cursor()
    vistos = set()
    # SQLite limita o número de parâmetros por consulta; vai em blocos
    for i in range(0, len(ids), 400):
        bloco = ids[i:i + 400]
        marcadores = ",".join("?" * len(bloco))
        c.execute(f"SELECT id FROM seen_ids WHERE id IN ({marcadores})", bloco)
        vistos.update(r["id"] for r in c.fetchall())
    conn.close()

    return set(ids) - vistos


def mark_seen(ids: list):
    """Registra IDs como examinados (independente de terem sido salvos)."""
    ids = [i for i in ids if i]
    if not ids:
        return
    agora = int(time.time())
    conn = _db()
    c = conn.cursor()
    c.executemany("INSERT OR IGNORE INTO seen_ids (id, ts) VALUES (?, ?)",
                  [(i, agora) for i in ids])
    conn.commit()
    conn.close()


def message_exists(msg_id: str) -> bool:
    conn = _db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM messages WHERE id = ?", (msg_id,))
    existe = c.fetchone() is not None
    conn.close()
    return existe


def get_messages(period_start: int, period_end: int, only_prefiltered: bool = True) -> list:
    """Mensagens do período, agrupadas por origem e depois cronológicas."""
    conn = _db()
    c = conn.cursor()
    filtro = "AND prefiltered = 1" if only_prefiltered else ""
    c.execute(f"""
        SELECT * FROM messages
        WHERE ts >= ? AND ts <= ? {filtro}
        ORDER BY chat_type, group_name, ts
    """, (period_start, period_end))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_period_stats(period_start: int, period_end: int) -> dict:
    """Contagens reais do período — vão para o prompt para a IA não ter que contar."""
    conn = _db()
    c = conn.cursor()

    c.execute("SELECT COUNT(*) n FROM messages WHERE ts BETWEEN ? AND ?", (period_start, period_end))
    total = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM messages WHERE ts BETWEEN ? AND ? AND prefiltered = 1",
              (period_start, period_end))
    analisadas = c.fetchone()["n"]

    c.execute("""SELECT COUNT(DISTINCT group_jid) n FROM messages
                 WHERE ts BETWEEN ? AND ? AND chat_type = 'grupo'""",
              (period_start, period_end))
    grupos = c.fetchone()["n"]

    c.execute("""SELECT COUNT(DISTINCT group_jid) n FROM messages
                 WHERE ts BETWEEN ? AND ? AND chat_type = 'privado'""",
              (period_start, period_end))
    privadas = c.fetchone()["n"]

    c.execute("SELECT MIN(ts) a, MAX(ts) b FROM messages WHERE ts BETWEEN ? AND ?",
              (period_start, period_end))
    row = c.fetchone()

    conn.close()
    return {
        "total": total,
        "analisadas": analisadas,
        "grupos": grupos,
        "privadas": privadas,
        "primeira_ts": row["a"],
        "ultima_ts": row["b"],
    }


def mark_messages_digested(period_start: int, period_end: int, digest_id: int):
    conn = _db()
    c = conn.cursor()
    c.execute("""
        UPDATE messages SET digest_id = ?
        WHERE ts BETWEEN ? AND ? AND digest_id IS NULL
    """, (digest_id, period_start, period_end))
    conn.commit()
    conn.close()


def purge_old_messages(dias: int = RETENCAO_DIAS) -> int:
    """Apaga mensagens antigas (LGPD / disco). Diários gerados são preservados."""
    limite = int((datetime.now() - timedelta(days=dias)).timestamp())
    conn = _db()
    c = conn.cursor()
    c.execute("DELETE FROM messages WHERE ts < ?", (limite,))
    apagadas = c.rowcount
    c.execute("DELETE FROM seen_ids WHERE ts < ?", (limite,))
    conn.commit()
    conn.close()
    return apagadas


# ── Diários ──────────────────────────────────────────────────────────────────

def save_digest(period_start: int, period_end: int, content: str,
                msg_count: int, group_count: int, analyzed_count: int) -> int:
    conn = _db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO digests
            (period_start, period_end, content, msg_count, group_count, analyzed_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (period_start, period_end, content, msg_count, group_count,
          analyzed_count, datetime.now().isoformat()))
    digest_id = c.lastrowid
    conn.commit()
    conn.close()
    return digest_id


def mark_digest_delivered(digest_id: int):
    conn = _db()
    c = conn.cursor()
    c.execute("UPDATE digests SET delivered = 1 WHERE id = ?", (digest_id,))
    conn.commit()
    conn.close()


def last_digest() -> dict:
    conn = _db()
    c = conn.cursor()
    c.execute("SELECT * FROM digests ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def digest_exists_for_day(dia: str = None) -> bool:
    """True se já existe diário entregue com created_at no dia (YYYY-MM-DD)."""
    dia = dia or datetime.now().strftime("%Y-%m-%d")
    conn = _db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM digests WHERE created_at LIKE ? AND delivered = 1", (f"{dia}%",))
    existe = c.fetchone() is not None
    conn.close()
    return existe


def get_stats() -> dict:
    """Painel rápido do agente."""
    conn = _db()
    c = conn.cursor()

    c.execute("SELECT COUNT(*) n FROM messages")
    total_msgs = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM messages WHERE ts > strftime('%s','now','-1 day')")
    msgs_24h = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM groups WHERE monitored = 1")
    grupos = c.fetchone()["n"]

    c.execute("""SELECT COUNT(DISTINCT group_jid) n FROM messages
                 WHERE chat_type = 'privado' AND ts > strftime('%s','now','-30 day')""")
    privadas = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM digests")
    diarios = c.fetchone()["n"]

    c.execute("SELECT created_at FROM digests ORDER BY id DESC LIMIT 1")
    row = c.fetchone()

    conn.close()
    return {
        "mensagens_total": total_msgs,
        "mensagens_24h": msgs_24h,
        "grupos_monitorados": grupos,
        "conversas_privadas_30d": privadas,
        "diarios_gerados": diarios,
        "ultimo_diario": row["created_at"] if row else "nenhum ainda",
    }
