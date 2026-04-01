import os
import json
import time
import math
import asyncio
import requests
from datetime import datetime, timezone
from flask import Flask
from threading import Thread

import discord
from discord import app_commands
from discord.ext import tasks

# =========================
# CONFIG
# =========================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ANILIST_URL = "https://graphql.anilist.co"
JIKAN_URL = "https://api.jikan.moe/v4/anime"
COR_EMBED = 0xFF8C00
ARQUIVO_AUTO = "auto_notificacao.json"

COOLDOWN_SEGUNDOS = 5
CACHE_SEGUNDOS = 120
ITENS_POR_PAGINA = 8

cooldowns = {}
cache_memoria = {}

# =========================
# FLASK PRA RENDER / UPTIMEROBOT
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot online!"

def iniciar_web():
    porta = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=porta)

def keep_alive():
    t = Thread(target=iniciar_web)
    t.daemon = True
    t.start()

# =========================
# DISCORD
# =========================
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# =========================
# FUNÇÕES AUXILIARES
# =========================
def agora_local():
    return datetime.now().astimezone()

def temporada_atual():
    mes = agora_local().month
    if mes in [1, 2, 3]:
        return "WINTER"
    elif mes in [4, 5, 6]:
        return "SPRING"
    elif mes in [7, 8, 9]:
        return "SUMMER"
    return "FALL"

def nome_temporada_pt(temp):
    nomes = {
        "WINTER": "Inverno",
        "SPRING": "Primavera",
        "SUMMER": "Verão",
        "FALL": "Outono"
    }
    return nomes.get(temp, temp)

def nome_dia_pt(data):
    dias = {
        0: "Segunda",
        1: "Terça",
        2: "Quarta",
        3: "Quinta",
        4: "Sexta",
        5: "Sábado",
        6: "Domingo"
    }
    return dias[data.weekday()]

def carregar_auto():
    if os.path.exists(ARQUIVO_AUTO):
        with open(ARQUIVO_AUTO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"canais": [], "avisados": {}}

def salvar_auto(dados):
    with open(ARQUIVO_AUTO, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)

def traduzir_texto(texto):
    if not texto:
        return "Sem sinopse disponível."

    cache_key = f"trad:{texto[:200]}"
    agora = time.time()

    if cache_key in cache_memoria:
        salvo_em, valor = cache_memoria[cache_key]
        if agora - salvo_em < 3600:
            return valor

    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": "pt",
            "dt": "t",
            "q": texto
        }
        resposta = requests.get(url, params=params, timeout=15)
        resposta.raise_for_status()
        dados = resposta.json()

        traducao = ""
        for parte in dados[0]:
            if parte[0]:
                traducao += parte[0]

        traducao = traducao.strip() if traducao.strip() else texto
        cache_memoria[cache_key] = (agora, traducao)
        return traducao
    except Exception:
        return texto

def limpar_html(texto):
    if not texto:
        return "Sem sinopse disponível."

    substituicoes = {
        "<br>": "\n",
        "<br><br>": "\n\n",
        "<i>": "",
        "</i>": "",
        "<b>": "",
        "</b>": "",
        "~!": "||",
        "!~": "||"
    }

    for antigo, novo in substituicoes.items():
        texto = texto.replace(antigo, novo)

    return texto.strip()

def anilist_query(query, variables=None):
    resposta = requests.post(
        ANILIST_URL,
        json={"query": query, "variables": variables or {}},
        timeout=20
    )
    resposta.raise_for_status()
    data = resposta.json()

    if "errors" in data:
        raise Exception(str(data["errors"]))

    return data["data"]

def formatar_timestamp_local(ts):
    if not ts:
        return "Data não informada"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%d/%m/%Y %H:%M")

def mesmo_dia_local(ts):
    if not ts:
        return False
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().date()
    hoje = agora_local().date()
    return dt == hoje

def melhor_titulo(media):
    return (
        media.get("title", {}).get("romaji")
        or media.get("title", {}).get("english")
        or media.get("title", {}).get("native")
        or "Sem título"
    )

def imagem_anilist(media):
    return (
        media.get("coverImage", {}).get("extraLarge")
        or media.get("coverImage", {}).get("large")
        or media.get("coverImage", {}).get("medium")
        or ""
    )

def buscar_imagem_jikan(titulo):
    cache_key = f"imgj:{titulo.lower()}"
    agora = time.time()

    if cache_key in cache_memoria:
        salvo_em, valor = cache_memoria[cache_key]
        if agora - salvo_em < 3600:
            return valor

    try:
        params = {"q": titulo, "limit": 3}
        resposta = requests.get(JIKAN_URL, params=params, timeout=20)
        resposta.raise_for_status()
        dados = resposta.json().get("data", [])

        if not dados:
            cache_memoria[cache_key] = (agora, "")
            return ""

        for item in dados:
            if item.get("type") == "TV":
                imagem = (
                    item.get("images", {}).get("jpg", {}).get("large_image_url")
                    or item.get("images", {}).get("jpg", {}).get("image_url")
                    or ""
                )
                cache_memoria[cache_key] = (agora, imagem)
                return imagem

        primeiro = dados[0]
        imagem = (
            primeiro.get("images", {}).get("jpg", {}).get("large_image_url")
            or primeiro.get("images", {}).get("jpg", {}).get("image_url")
            or ""
        )
        cache_memoria[cache_key] = (agora, imagem)
        return imagem
    except Exception:
        return ""

def pegar_imagem_correta(media):
    titulo = melhor_titulo(media)
    img_jikan = buscar_imagem_jikan(titulo)
    if img_jikan:
        return img_jikan
    return imagem_anilist(media)

def formato_pt(media_format):
    formatos = {
        "TV": "TV",
        "TV_SHORT": "TV Curto",
        "MOVIE": "Filme",
        "SPECIAL": "Especial",
        "OVA": "OVA",
        "ONA": "ONA",
        "MUSIC": "Música"
    }
    return formatos.get(media_format, media_format or "N/A")

def status_pt(status):
    mapa = {
        "FINISHED": "Finalizado",
        "RELEASING": "Em lançamento",
        "NOT_YET_RELEASED": "Não lançado",
        "CANCELLED": "Cancelado",
        "HIATUS": "Hiato"
    }
    return mapa.get(status, status or "N/A")

def montar_linha_anime(media, tipo="geral"):
    titulo = melhor_titulo(media)
    link = media.get("siteUrl", "")
    nota = media.get("averageScore")
    formato = formato_pt(media.get("format"))
    status = status_pt(media.get("status"))
    episodios = media.get("episodes") if media.get("episodes") is not None else "N/A"

    base = f"**[{titulo}]({link})**\n🎞️ {formato} • ⭐ {nota if nota is not None else 'N/A'} • 📺 {episodios} eps • 📡 {status}"

    if tipo == "novo":
        inicio = media.get("startDate", {})
        data_inicio = f"{inicio.get('day') or '??'}/{inicio.get('month') or '??'}/{inicio.get('year') or '????'}"
        base += f"\n🗓️ Estreia: {data_inicio}"

    if tipo == "lancamento":
        prox = media.get("nextAiringEpisode", {})
        base += f"\n⏰ Ep {prox.get('episode', '?')} em {formatar_timestamp_local(prox.get('airingAt'))}"

    if tipo == "temporada":
        prox = media.get("nextAiringEpisode")
        if prox:
            base += f"\n📅 Próximo ep {prox.get('episode', '?')} em {formatar_timestamp_local(prox.get('airingAt'))}"

    return base

def criar_embed_pagina(titulo, descricao_topo, itens, pagina, total_paginas, tipo="geral"):
    embed = discord.Embed(
        title=titulo,
        description=descricao_topo,
        color=COR_EMBED
    )

    inicio = pagina * ITENS_POR_PAGINA
    fim = inicio + ITENS_POR_PAGINA
    bloco = itens[inicio:fim]

    for i, item in enumerate(bloco, start=inicio + 1):
        nome = f"{i}. {melhor_titulo(item)}"
        valor = montar_linha_anime(item, tipo=tipo)
        embed.add_field(name=nome[:256], value=valor[:1024], inline=False)

    if itens:
        imagem = pegar_imagem_correta(bloco[0])
        if imagem:
            embed.set_thumbnail(url=imagem)

    embed.set_footer(text=f"Página {pagina + 1}/{total_paginas} • Total: {len(itens)}")
    return embed

def cache_get(nome):
    agora = time.time()
    if nome in cache_memoria:
        salvo_em, valor = cache_memoria[nome]
        if agora - salvo_em < CACHE_SEGUNDOS:
            return valor
    return None

def cache_set(nome, valor):
    cache_memoria[nome] = (time.time(), valor)

def em_cooldown(user_id, comando):
    chave = f"{user_id}:{comando}"
    agora = time.time()
    ultimo = cooldowns.get(chave, 0)

    if agora - ultimo < COOLDOWN_SEGUNDOS:
        return round(COOLDOWN_SEGUNDOS - (agora - ultimo), 1)

    cooldowns[chave] = agora
    return 0

async def enviar_paginas(interaction, titulo, descricao, itens, tipo="geral"):
    if not itens:
        await interaction.followup.send("Nenhum resultado encontrado.")
        return

    total_paginas = math.ceil(len(itens) / ITENS_POR_PAGINA)

    for pagina in range(total_paginas):
        embed = criar_embed_pagina(titulo, descricao, itens, pagina, total_paginas, tipo=tipo)
        await interaction.followup.send(embed=embed)

# =========================
# QUERIES ANILIST
# =========================
def query_temporada_atual():
    cache_nome = f"temporada:{temporada_atual()}:{agora_local().year}"
    cache = cache_get(cache_nome)
    if cache is not None:
        return cache

    query = """
    query ($season: MediaSeason, $seasonYear: Int, $page: Int, $perPage: Int) {
      Page(page: $page, perPage: $perPage) {
        media(
          type: ANIME,
          season: $season,
          seasonYear: $seasonYear,
          isAdult: false,
          sort: POPULARITY_DESC
        ) {
          id
          siteUrl
          title { romaji english native }
          description(asHtml: false)
          episodes
          averageScore
          status
          format
          coverImage { extraLarge large medium }
          nextAiringEpisode { episode airingAt }
          startDate { year month day }
        }
      }
    }
    """
    variables = {
        "season": temporada_atual(),
        "seasonYear": agora_local().year,
        "page": 1,
        "perPage": 50
    }
    resultado = anilist_query(query, variables)["Page"]["media"]
    cache_set(cache_nome, resultado)
    return resultado

def query_novos_anunciados():
    cache_nome = "novos_anunciados"
    cache = cache_get(cache_nome)
    if cache is not None:
        return cache

    query = """
    query ($page: Int, $perPage: Int) {
      Page(page: $page, perPage: $perPage) {
        media(
          type: ANIME,
          status: NOT_YET_RELEASED,
          isAdult: false,
          sort: POPULARITY_DESC
        ) {
          id
          siteUrl
          title { romaji english native }
          description(asHtml: false)
          episodes
          averageScore
          status
          format
          coverImage { extraLarge large medium }
          nextAiringEpisode { episode airingAt }
          startDate { year month day }
        }
      }
    }
    """
    variables = {"page": 1, "perPage": 50}
    resultado = anilist_query(query, variables)["Page"]["media"]
    cache_set(cache_nome, resultado)
    return resultado

def query_lancamentos_hoje():
    cache_nome = f"lancamentos:{agora_local().strftime('%Y-%m-%d')}"
    cache = cache_get(cache_nome)
    if cache is not None:
        return cache

    query = """
    query ($season: MediaSeason, $seasonYear: Int, $page: Int, $perPage: Int) {
      Page(page: $page, perPage: $perPage) {
        media(
          type: ANIME,
          season: $season,
          seasonYear: $seasonYear,
          status: RELEASING,
          isAdult: false,
          sort: POPULARITY_DESC
        ) {
          id
          siteUrl
          title { romaji english native }
          description(asHtml: false)
          episodes
          averageScore
          status
          format
          coverImage { extraLarge large medium }
          nextAiringEpisode { episode airingAt }
          startDate { year month day }
        }
      }
    }
    """
    variables = {
        "season": temporada_atual(),
        "seasonYear": agora_local().year,
        "page": 1,
        "perPage": 100
    }
    medias = anilist_query(query, variables)["Page"]["media"]

    resultado = [
        media for media in medias
        if media.get("nextAiringEpisode") and mesmo_dia_local(media["nextAiringEpisode"]["airingAt"])
    ]

    cache_set(cache_nome, resultado)
    return resultado

def query_calendario_semanal():
    cache_nome = f"semanal:{temporada_atual()}:{agora_local().year}"
    cache = cache_get(cache_nome)
    if cache is not None:
        return cache

    query = """
    query ($season: MediaSeason, $seasonYear: Int, $page: Int, $perPage: Int) {
      Page(page: $page, perPage: $perPage) {
        media(
          type: ANIME,
          season: $season,
          seasonYear: $seasonYear,
          status: RELEASING,
          isAdult: false,
          sort: POPULARITY_DESC
        ) {
          id
          siteUrl
          title { romaji english native }
          coverImage { extraLarge large medium }
          nextAiringEpisode { episode airingAt }
          format
          status
          averageScore
          episodes
        }
      }
    }
    """
    variables = {
        "season": temporada_atual(),
        "seasonYear": agora_local().year,
        "page": 1,
        "perPage": 100
    }
    resultado = anilist_query(query, variables)["Page"]["media"]
    cache_set(cache_nome, resultado)
    return resultado

# =========================
# EVENTOS
# =========================
@client.event
async def on_ready():
    await tree.sync()
    if not verificar_notificacoes.is_running():
        verificar_notificacoes.start()
    print(f"Bot conectado como {client.user}")

# =========================
# COMANDOS
# =========================
@tree.command(name="ping", description="Testa se o bot está online")
async def ping(interaction: discord.Interaction):
    espera = em_cooldown(interaction.user.id, "ping")
    if espera > 0:
        await interaction.response.send_message(
            f"⏳ Espera {espera}s antes de usar /ping de novo.",
            ephemeral=True
        )
        return

    await interaction.response.send_message("pong 🏓")

@tree.command(name="animetemp", description="Mostra os animes da temporada atual")
async def animetemp(interaction: discord.Interaction):
    espera = em_cooldown(interaction.user.id, "animetemp")
    if espera > 0:
        await interaction.response.send_message(
            f"⏳ Espera {espera}s antes de usar /animetemp de novo.",
            ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        animes = await asyncio.to_thread(query_temporada_atual)
        temp = temporada_atual()
        ano = agora_local().year

        await enviar_paginas(
            interaction,
            f"🎌 Temporada Atual — {nome_temporada_pt(temp)} {ano}",
            "Lista organizada dos animes da temporada atual.",
            animes,
            tipo="temporada"
        )

    except Exception as e:
        await interaction.followup.send(f"Erro ao buscar temporada: `{e}`")

@tree.command(name="novo", description="Mostra novos animes anunciados / próximos")
async def novo(interaction: discord.Interaction):
    espera = em_cooldown(interaction.user.id, "novo")
    if espera > 0:
        await interaction.response.send_message(
            f"⏳ Espera {espera}s antes de usar /novo de novo.",
            ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        animes = await asyncio.to_thread(query_novos_anunciados)

        await enviar_paginas(
            interaction,
            "🆕 Novos Animes Anunciados",
            "Lista organizada dos próximos animes anunciados.",
            animes,
            tipo="novo"
        )

    except Exception as e:
        await interaction.followup.send(f"Erro ao buscar novos animes: `{e}`")

@tree.command(name="lancamento", description="Mostra tudo que lança hoje")
async def lancamento(interaction: discord.Interaction):
    espera = em_cooldown(interaction.user.id, "lancamento")
    if espera > 0:
        await interaction.response.send_message(
            f"⏳ Espera {espera}s antes de usar /lancamento de novo.",
            ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        animes = await asyncio.to_thread(query_lancamentos_hoje)

        await enviar_paginas(
            interaction,
            "📺 Lançamentos de Hoje",
            "Todos os animes encontrados com lançamento hoje.",
            animes,
            tipo="lancamento"
        )

    except Exception as e:
        await interaction.followup.send(f"Erro ao buscar lançamentos: `{e}`")

@tree.command(name="semanal", description="Mostra o calendário semanal da temporada atual")
async def semanal(interaction: discord.Interaction):
    espera = em_cooldown(interaction.user.id, "semanal")
    if espera > 0:
        await interaction.response.send_message(
            f"⏳ Espera {espera}s antes de usar /semanal de novo.",
            ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        animes = await asyncio.to_thread(query_calendario_semanal)
        agenda = {
            "Segunda": [],
            "Terça": [],
            "Quarta": [],
            "Quinta": [],
            "Sexta": [],
            "Sábado": [],
            "Domingo": []
        }

        for anime in animes:
            prox = anime.get("nextAiringEpisode")
            if not prox:
                continue

            airing_at = prox.get("airingAt")
            episodio = prox.get("episode")
            if not airing_at:
                continue

            dt = datetime.fromtimestamp(airing_at, tz=timezone.utc).astimezone()
            dia = nome_dia_pt(dt)
            titulo = melhor_titulo(anime)
            formato = formato_pt(anime.get("format"))

            linha = f"**{titulo}** — {formato} • Ep {episodio or '?'} às {dt.strftime('%H:%M')}"
            if dia in agenda:
                agenda[dia].append((dt, linha))

        for dia in agenda:
            agenda[dia].sort(key=lambda x: x[0])

        embed = discord.Embed(
            title=f"📅 Calendário Semanal — {nome_temporada_pt(temporada_atual())} {agora_local().year}",
            description="Agenda organizada por dia da semana.",
            color=COR_EMBED
        )

        for dia in ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]:
            itens = agenda[dia]
            texto = "\n".join([item[1] for item in itens[:20]]) if itens else "Nenhum anime encontrado."
            embed.add_field(name=dia, value=texto[:1024], inline=False)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"Erro ao montar calendário semanal: `{e}`")

@tree.command(name="autonotify", description="Liga ou desliga notificações automáticas neste canal")
@app_commands.describe(acao="Escolha ligar ou desligar")
@app_commands.choices(acao=[
    app_commands.Choice(name="ligar", value="ligar"),
    app_commands.Choice(name="desligar", value="desligar")
])
async def autonotify(interaction: discord.Interaction, acao: app_commands.Choice[str]):
    espera = em_cooldown(interaction.user.id, "autonotify")
    if espera > 0:
        await interaction.response.send_message(
            f"⏳ Espera {espera}s antes de usar /autonotify de novo.",
            ephemeral=True
        )
        return

    dados = carregar_auto()
    canal_id = interaction.channel_id

    if acao.value == "ligar":
        if canal_id not in dados["canais"]:
            dados["canais"].append(canal_id)
            salvar_auto(dados)
        await interaction.response.send_message("✅ Notificação automática ligada neste canal.")
    else:
        if canal_id in dados["canais"]:
            dados["canais"].remove(canal_id)
            salvar_auto(dados)
        await interaction.response.send_message("🛑 Notificação automática desligada neste canal.")

# =========================
# LOOP AUTOMÁTICO
# =========================
@tasks.loop(minutes=10)
async def verificar_notificacoes():
    await client.wait_until_ready()

    try:
        dados = carregar_auto()
        if not dados["canais"]:
            return

        data_hoje = agora_local().strftime("%Y-%m-%d")
        if data_hoje not in dados["avisados"]:
            dados["avisados"][data_hoje] = []

        animes = await asyncio.to_thread(query_lancamentos_hoje)

        for anime in animes:
            anime_id = anime.get("id")
            if not anime_id or anime_id in dados["avisados"][data_hoje]:
                continue

            prox = anime.get("nextAiringEpisode", {})
            extra = (
                "🔔 Lançamento de hoje\n"
                f"🎞️ Episódio: {prox.get('episode', '?')}\n"
                f"⏰ Horário: {formatar_timestamp_local(prox.get('airingAt'))}"
            )
            embed = await asyncio.to_thread(criar_embed_anime, anime, extra)

            for canal_id in dados["canais"]:
                canal = client.get_channel(canal_id)
                if canal:
                    await canal.send(embed=embed)

            dados["avisados"][data_hoje].append(anime_id)
            salvar_auto(dados)

    except Exception as e:
        print("Erro na notificação automática:", e)

# =========================
# START
# =========================
if __name__ == "__main__":
    print("Iniciando Flask...")
    keep_alive()

    if not DISCORD_TOKEN:
        raise ValueError("DISCORD_TOKEN não foi definido nas variáveis de ambiente.")

    print("Iniciando bot do Discord...")
    client.run(DISCORD_TOKEN)
