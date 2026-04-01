import os
import json
import time
import asyncio
import math
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

COOLDOWN_SEGUNDOS = 5
CACHE_SEGUNDOS = 120

cooldowns = {}
cache_memoria = {}

# =========================
# FLASK
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot online!"

def keep_alive():
    def run():
        port = int(os.environ.get("PORT", 10000))
        app.run(host="0.0.0.0", port=port)
    t = Thread(target=run)
    t.daemon = True
    t.start()

# =========================
# DISCORD
# =========================
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# =========================
# UTILS
# =========================
def agora_local():
    return datetime.now().astimezone()

def temporada_atual():
    mes = agora_local().month
    if mes <= 3:
        return "WINTER"
    elif mes <= 6:
        return "SPRING"
    elif mes <= 9:
        return "SUMMER"
    return "FALL"

def nome_temporada_pt(temp):
    return {
        "WINTER": "Inverno",
        "SPRING": "Primavera",
        "SUMMER": "Verão",
        "FALL": "Outono"
    }.get(temp, temp)

def formato_pt(f):
    return {
        "TV": "TV",
        "MOVIE": "Filme",
        "OVA": "OVA",
        "ONA": "ONA",
        "SPECIAL": "Especial"
    }.get(f, f or "N/A")

def status_pt(s):
    return {
        "FINISHED": "Finalizado",
        "RELEASING": "Em lançamento",
        "NOT_YET_RELEASED": "Não lançado"
    }.get(s, s or "N/A")

def melhor_titulo(m):
    return m["title"]["romaji"] or m["title"]["english"] or "Sem título"

def limpar_html(t):
    return t.replace("<br>", "\n").replace("<i>", "").replace("</i>", "")

def traduzir_texto(t):
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": "pt", "dt": "t", "q": t},
            timeout=10
        )
        return "".join([x[0] for x in r.json()[0]])
    except:
        return t

def buscar_imagem_jikan(nome):
    try:
        r = requests.get(JIKAN_URL, params={"q": nome, "limit": 1}, timeout=10)
        data = r.json()["data"]
        if data:
            return data[0]["images"]["jpg"]["large_image_url"]
    except:
        pass
    return ""

def pegar_imagem(m):
    return buscar_imagem_jikan(melhor_titulo(m)) or m["coverImage"]["extraLarge"]

def anilist(query, variables):
    return requests.post(ANILIST_URL, json={"query": query, "variables": variables}).json()["data"]

def cooldown(user, cmd):
    k = f"{user}:{cmd}"
    now = time.time()
    if now - cooldowns.get(k, 0) < COOLDOWN_SEGUNDOS:
        return True
    cooldowns[k] = now
    return False

# =========================
# QUERIES
# =========================
def get_temporada():
    return anilist("""
    query ($s: MediaSeason, $y: Int){
      Page(perPage: 50){
        media(type: ANIME, season: $s, seasonYear: $y){
          id siteUrl title{romaji english} description
          episodes averageScore status format
          coverImage{extraLarge}
          nextAiringEpisode{episode airingAt}
        }
      }
    }""", {"s": temporada_atual(), "y": agora_local().year})["Page"]["media"]

def get_novos():
    return anilist("""
    query{
      Page(perPage: 30){
        media(type: ANIME, status: NOT_YET_RELEASED){
          id siteUrl title{romaji english} description
          episodes averageScore format coverImage{extraLarge}
          startDate{day month year}
        }
      }
    }""", {})["Page"]["media"]

def get_semanal():
    return get_temporada()

# =========================
# NAVIGADOR
# =========================
class Navigator(discord.ui.View):
    def __init__(self, itens, titulo, user):
        super().__init__(timeout=300)
        self.itens = itens
        self.titulo = titulo
        self.user = user
        self.cat = "TV"
        self.page = 0

    def filtrados(self):
        return [i for i in self.itens if i["format"] == self.cat]

    def embed(self):
        f = self.filtrados()
        if not f:
            return discord.Embed(title="Sem resultados")

        m = f[self.page]
        sinopse = traduzir_texto(limpar_html(m.get("description","")))[:350]

        e = discord.Embed(
            title=f"🎌 {self.titulo} • {formato_pt(self.cat)}",
            color=COR_EMBED
        )

        e.add_field(
            name=melhor_titulo(m),
            value=f"""
⭐ {m.get("averageScore") or "N/A"}
📺 {m.get("episodes") or "N/A"}
📡 {status_pt(m.get("status"))}
""",
            inline=False
        )

        e.add_field(name="📖 Sinopse", value=f"```{sinopse}```", inline=False)
        e.set_image(url=pegar_imagem(m))
        e.set_footer(text=f"{self.page+1}/{len(f)}")
        return e

    async def update(self, i):
        self.page = max(0, min(self.page, len(self.filtrados())-1))
        await i.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Voltar")
    async def b1(self, i, b):
        self.page -= 1
        await self.update(i)

    @discord.ui.button(label="Próximo")
    async def b2(self, i, b):
        self.page += 1
        await self.update(i)

    @discord.ui.button(label="TV")
    async def tv(self, i, b):
        self.cat="TV"; self.page=0
        await self.update(i)

    @discord.ui.button(label="Filme")
    async def mv(self, i, b):
        self.cat="MOVIE"; self.page=0
        await self.update(i)

    @discord.ui.button(label="OVA")
    async def ova(self, i, b):
        self.cat="OVA"; self.page=0
        await self.update(i)

    @discord.ui.button(label="ONA")
    async def ona(self, i, b):
        self.cat="ONA"; self.page=0
        await self.update(i)

    @discord.ui.button(label="Fechar", style=discord.ButtonStyle.danger)
    async def close(self, i, b):
        try: await i.message.delete()
        except: pass

# =========================
# COMANDOS
# =========================
@tree.command(name="ping")
async def ping(i):
    await i.response.send_message("🏓 Online!")

@tree.command(name="animetemp")
async def animetemp(i):
    if cooldown(i.user.id,"animetemp"):
        return await i.response.send_message("⏳ calma aí", ephemeral=True)

    await i.response.defer()

    data = await asyncio.to_thread(get_temporada)
    nav = Navigator(data, f"Temporada {nome_temporada_pt(temporada_atual())}", i.user.id)
    await i.followup.send(embed=nav.embed(), view=nav)

@tree.command(name="novo")
async def novo(i):
    await i.response.defer()
    data = await asyncio.to_thread(get_novos)

    for a in data[:8]:
        embed = discord.Embed(
            title=melhor_titulo(a),
            description=f"📅 {a['startDate']['day']}/{a['startDate']['month']}/{a['startDate']['year']}",
            color=COR_EMBED
        )
        embed.set_thumbnail(url=pegar_imagem(a))
        await i.followup.send(embed=embed)

@tree.command(name="semanal")
async def semanal(i):
    await i.response.defer()
    data = await asyncio.to_thread(get_semanal)

    dias = {d:[] for d in ["Seg","Ter","Qua","Qui","Sex","Sab","Dom"]}

    for a in data:
        ep = a.get("nextAiringEpisode")
        if not ep: continue
        dt = datetime.fromtimestamp(ep["airingAt"], tz=timezone.utc).astimezone()
        dia = ["Seg","Ter","Qua","Qui","Sex","Sab","Dom"][dt.weekday()]
        dias[dia].append(f"{melhor_titulo(a)} — Ep {ep['episode']} {dt.strftime('%H:%M')}")

    e = discord.Embed(title="📅 Calendário semanal", color=COR_EMBED)

    for d,lista in dias.items():
        e.add_field(name=d, value="\n".join(lista[:10]) or "Nada", inline=False)

    await i.followup.send(embed=e)

# =========================
# START
# =========================
@client.event
async def on_ready():
    await tree.sync()
    print("Bot online")

if __name__ == "__main__":
    keep_alive()
    client.run(DISCORD_TOKEN)
