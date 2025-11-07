# ============================================================
#  SISTEMA DE CONVERSA INTELIGENTE (Z.ai + FastAPI)
#  Contexto incremental + Timeout estendido + Ping Render Free
#  CORS fixo + Integração real com API Z.ai
#  AGORA COM PROMPT GLOBAL GERENCIADO PELO BANCO DE DADOS
#  E HISTÓRICO POR SESSÃO (tipo=2) MANTIDO
# ============================================================

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3, asyncio, random, httpx
from contextlib import asynccontextmanager

# ------------------------------------------------------------
# 1️⃣ Configurações
# ------------------------------------------------------------
API_KEY = "03038b49c41b4bbdb1ce54888b54d223.cOjmjTibnl3uqERW"
API_URL = "https://api.z.ai/api/paas/v4/chat/completions"
DB_FILE = "conversas.db"
RENDER_URL = "https://back-go-ia.onrender.com"
FRONTEND_URL = "https://go-ia.vercel.app"

# Prompt padrão, usado apenas se não houver nenhum no banco na primeira vez.
DEFAULT_SYSTEM_PROMPT = (
 """🎯 **Oi! sou criador de assistente
"""
)

# ------------------------------------------------------------
# 2️⃣ Banco de dados
# ------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Tabela para o histórico de conversas de CADA SESSÃO
    c.execute("""
        CREATE TABLE IF NOT EXISTS conversas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT,
            content TEXT,
            tipo_mensagem INTEGER
        )
    """)
    # Tabela para configurações GLOBAIS do sistema (como o prompt principal)
    c.execute("""
        CREATE TABLE IF NOT EXISTS configuracoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE NOT NULL,
            conteudo TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- Função para buscar o PROMPT GLOBAL do sistema ---
def get_system_prompt():
    """Busca o prompt global na tabela 'configuracoes'. Se não existir, cria-o."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT conteudo FROM configuracoes WHERE nome = 'system_prompt'")
    result = c.fetchone()
    conn.close()

    if result:
        return result[0]
    else:
        # Se não existir, cria o prompt padrão no banco e o retorna
        update_system_prompt(DEFAULT_SYSTEM_PROMPT)
        print("🌱 Prompt de sistema padrão criado no banco de dados.")
        return DEFAULT_SYSTEM_PROMPT

# --- Função para ATUALIZAR o PROMPT GLOBAL no banco de dados ---
def update_system_prompt(novo_conteudo: str):
    """Atualiza o prompt global na tabela 'configuracoes'."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO configuracoes (nome, conteudo) VALUES (?, ?)
    """, ('system_prompt', novo_conteudo))
    conn.commit()
    conn.close()

# --- Função para salvar mensagens na tabela 'conversas' ---
def salvar_mensagem(session_id, role, content, tipo):
    """Salva mensagens de usuário/assistente (tipo 9) ou o contexto da sessão (tipo 2)."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if tipo == 2:
        # ATUALIZA o contexto incremental da SESSÃO (remove anterior e insere novo)
        c.execute("DELETE FROM conversas WHERE session_id=? AND tipo_mensagem=2", (session_id,))
        c.execute(
            "INSERT INTO conversas (session_id, role, content, tipo_mensagem) VALUES (?, ?, ?, 2)",
            (session_id, "system", content),
        )
    else:
        # Insere mensagem normal do usuário ou assistente
        c.execute(
            "INSERT INTO conversas (session_id, role, content, tipo_mensagem) VALUES (?, ?, ?, 9)",
            (session_id, role, content),
        )
    conn.commit()
    conn.close()

# --- Função para buscar o CONTEXTO da SESSÃO ---
def buscar_contexto(session_id):
    """Busca o histórico incremental (tipo 2) de uma sessão específica."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT content FROM conversas WHERE session_id=? AND tipo_mensagem=2", (session_id,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else ""

# ------------------------------------------------------------
# 3️⃣ Lógica principal: enviar à Z.ai e atualizar contexto
# ------------------------------------------------------------
async def atualizar_e_gerar_resposta(session_id: str, nova_mensagem: str):
    try:
        # 1. Salva a mensagem do usuário no histórico
        salvar_mensagem(session_id, "user", nova_mensagem, 9)

        # 2. Busca o contexto incremental DA SESSÃO
        contexto = buscar_contexto(session_id)

        # 3. Busca o prompt GLOBAL do sistema (persistente no banco)
        system_prompt_content = get_system_prompt()

        # Monta o prompt final para a IA, combinando o prompt global, o contexto da sessão e a nova mensagem
        prompt = [
            {"role": "system", "content": system_prompt_content},
            {"role": "system", "content": f"Contexto da conversa até agora:\n{contexto}"},
            {"role": "user", "content": nova_mensagem},
        ]

        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        timeout_config = httpx.Timeout(120.0)

        async with httpx.AsyncClient(timeout=timeout_config) as client:
            resp = await client.post(API_URL, json={"model": "glm-4.5-flash", "messages": prompt}, headers=headers)

        if resp.status_code != 200:
            return f"❌ Erro na API Z.ai: {resp.text}"

        data = resp.json()
        resposta = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        if not resposta:
            return "⚠️ Nenhuma resposta gerada pela API Z.ai."

        # 4. Salva a resposta da assistente no histórico
        salvar_mensagem(session_id, "assistant", resposta, 9)

        # 5. Atualiza o contexto incremental DA SESSÃO com a nova interação
        novo_contexto = f"{contexto}\nUsuário: {nova_mensagem}\nAssistente: {resposta}".strip()
        if len(novo_contexto) > 4000:
            novo_contexto = novo_contexto[-4000:]
        salvar_mensagem(session_id, "system", novo_contexto, 2)

        return resposta

    except Exception as e:
        return f"💥 Erro interno no backend: {str(e)}"

# ------------------------------------------------------------
# 4️⃣ FastAPI + CORS
# ------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Aplicação está iniciando...")
    ping_task = asyncio.create_task(ping_randomico())
    yield
    print("🛑 Aplicação está sendo desligada.")
    ping_task.cancel()
    try:
        await ping_task
    except asyncio.CancelledError:
        print("Tarefa de ping cancelada.")

app = FastAPI(
    title="Z.ai Conversa Inteligente (Contexto Incremental + Timeout)",
    lifespan=lifespan
)

allowed_origins = [
    "http://localhost:4200",
    "http://127.0.0.1:4200",
    FRONTEND_URL,
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Mensagem(BaseModel):
    texto: str
    session_id: str

class PromptUpdate(BaseModel):
    conteudo: str

# ------------------------------------------------------------
# 5️⃣ Rotas
# ------------------------------------------------------------
@app.get("/")
async def home():
    return {"status": "✅ API Z.ai ativa e mantendo contexto incremental."}

@app.post("/mensagem")
async def mensagem(request: Request):
    data = await request.json()
    texto = data.get("texto", "").strip()
    session_id = data.get("session_id", "sessao")

    if not texto:
        return {"resposta": "Por favor, envie uma mensagem válida."}

    resposta = await atualizar_e_gerar_resposta(session_id, texto)
    return {"resposta": resposta}

@app.get("/contexto/{session_id}")
async def get_contexto(session_id: str):
    return {"contexto": buscar_contexto(session_id)}

# --- Endpoint para ATUALIZAR o prompt GLOBAL do sistema ---
@app.post("/prompt/atualizar")
async def atualizar_prompt_endpoint(prompt_data: PromptUpdate):
    """
    Recebe um novo conteúdo para o prompt do sistema e o atualiza no banco de dados.
    Esta é a ÚNICA forma de alterar o prompt global.
    """
    if not prompt_data.conteudo or not prompt_data.conteudo.strip():
        raise HTTPException(status_code=400, detail="O conteúdo do prompt não pode ser vazio.")
    
    update_system_prompt(prompt_data.conteudo.strip())
    return {"status": "success", "message": "Prompt do sistema atualizado com sucesso."}

# --- Endpoint para VISUALIZAR o prompt GLOBAL atual ---
@app.get("/prompt/atual")
async def ver_prompt_atual_endpoint():
    """
    Retorna o conteúdo do prompt do sistema que está salvo no banco de dados.
    """
    prompt_content = get_system_prompt()
    return {"prompt": prompt_content}


# ------------------------------------------------------------
# 6️⃣ Ping Render Free
# ------------------------------------------------------------
async def ping_randomico():
    if not RENDER_URL:
        print("⚠️ RENDER_URL não definido. Ping desativado.")
        return
    while True:
        try:
            async with httpx.AsyncClient() as client:
                await client.get(RENDER_URL)
                print("🔁 Ping enviado para manter ativo.")
        except Exception as e:
            print(f"Erro no ping: {e}")
        await asyncio.sleep(random.randint(300, 600))
