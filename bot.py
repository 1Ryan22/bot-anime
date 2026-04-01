import os
import json
import requests
from datetime import datetime, timezone
from flask import Flask
from threading import Thread

import discord
from discord import app_commands
from discord.ext import tasks

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ANILIST_URL = "https://graphql.anilist.co"
COR_EMBED = 0xFF8C00
ARQUIVO_AUTO = "auto_notificacao.json"

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

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

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

        return traducao.strip() if traducao.strip() else texto
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

def criar_embed_anime(media, titulo_extra=None):
    titulo = melhor_titulo(media)
    url = media.get("siteUrl", "")
    imagem = (
        media.get("coverImage", {}).get("extraLarge")
        or media.get("coverImage", {}).get("large")
        or media.get("coverImage", {}).get("medium")
        or ""
    )

    nota = media.get("averageScore")
    episodios = media.get("episodes")
    sinopse = media.get("description") or "Sem sinopse disponível."

    sinopse = limpar_html(sinopse)
    sinopse = traduzir_texto(sinopse)

    if len(sinopse) > 450:
        sinopse = sinopse[:450] + "..."

    nota_texto = nota if nota is not None else "N/A"
    episodios_texto = episodios if episodios is not None else "N/A"

    descricao = f"⭐ Nota: {nota_texto}\n🎬 Episódios: {episodios_texto}\n\n📖 {sinopse}"

    if titulo_extra:
        descricao = f"{titulo_extra}\n\n{descricao}"

    embed = discord.Embed(
        title=titulo,
        url=url,
        description=descricao,
        color=COR_EMBED
    )

    if imagem:
        embed.set_image(url=imagem)

    return embed

def query_temporada_atual():
    query = """
    query ($season: MediaSeason, $seasonYear: Int, $page: Int, $perPage: Int) {
      Page(page: $page, perPage: $perPage) {
        media(
          type: ANIME,
          format: TV,
          season: $season,
          seasonYear: $seasonYear,
          status_in: [RELEASING, NOT_YET_RELEASED],
          isAdult: false,
          sort: POPULARITY_DESC
        ) {
          id
          siteUrl
          title { romaji english native }
          description(asHtml: false)
          episodes
          averageScore
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
        "perPage": 5
    }
    return anilist_query(query, variables)["Page"]["media"]

def query_novos_anunciados():
    query = """
    query ($page: Int, $perPage: Int) {
      Page(page: $page, perPage: $perPage) {
        media(
          type: ANIME,
          format: TV,
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
          coverImage { extraLarge large medium }
          nextAiringEpisode { episode airingAt }
          startDate { year month day }
        }
      }
    }
    """
    variables = {"page": 1, "perPage": 5}
    return anilist_query(query, variables)["Page"]["media"]

def query_lancamentos_hoje():
    query = """
    query ($season: MediaSeason, $seasonYear: Int, $page: Int, $perPage: Int) {
      Page(page: $page, perPage: $perPage) {
        media(
          type: ANIME,
          format: TV,
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
        "perPage": 30
    }
    medias = anilist_query(query, variables)["Page"]["media"]

    return [
        media for media in medias
        if media.get("nextAiringEpisode") and mesmo_dia_local(media["nextAiringEpisode"]["airingAt"])
    ]

def query_calendario_semanal():
    query = """
    query ($season: MediaSeason, $seasonYear: Int, $page: Int, $perPage: Int) {
      Page(page: $page, perPage: $perPage) {
        media(
          type: ANIME,
          format: TV,
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
        }
      }
    }
    """
    variables = {
        "season": temporada_atual(),
        "seasonYear": agora_local().year,
        "page": 1,
        "perPage": 30
    }
    return anilist_query(query, variables)["Page"]["media"]

@client.event
async def on_ready():
    await tree.sync()
    if not verificar_notificacoes.is_running():
        verificar_notificacoes.start()
    print(f"Bot conectado como {client.user}")

@tree.command(name="ping", description="Testa se o bot está online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong 🏓")

@tree.command(name="animetemp", description="Mostra os animes da temporada atual")
async def animetemp(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        animes = query_temporada_atual()
        temp = temporada_atual()
        ano = agora_local().year

        if not animes:
            await interaction.followup.send("Não encontrei animes da temporada atual.")
            return

        await interaction.followup.send(f"🎌 **Temporada atual:** {nome_temporada_pt(temp)} {ano}")

        for anime in animes:
            extra = f"📢 Anime da temporada: {nome_temporada_pt(temp)} {ano}"
            prox = anime.get("nextAiringEpisode")
            if prox:
                extra += f"\n📅 Próximo episódio: {prox.get('episode', '?')} em {formatar_timestamp_local(prox.get('airingAt'))}"
            await interaction.followup.send(embed=criar_embed_anime(anime, extra))

    except Exception as e:
        await interaction.followup.send(f"Erro ao
