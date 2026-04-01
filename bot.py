import os
import json
import time
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
COR_EMBED = 0xF59E0B
ARQUIVO_AUTO = "auto_notificacao.json"

COOLDOWN_SEGUNDOS = 5
CACHE_SEGUNDOS = 120
NOVO_LIMITE = 20

cooldowns = {}
cache_memoria = {}

# =========================
# FLASK / RENDER
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
    return {
        "WINTER": "Inverno",
        "SPRING": "Primavera",
        "SUMMER": "Verão",
        "FALL": "Outono"
    }.get(temp, temp)

def nome_dia_pt(data):
    return {
        0: "Segunda",
        1: "Terça",
        2: "Quarta",
        3: "Quinta",
        4: "Sexta",
        5: "Sábado",
        6: "Domingo"
    }[data.weekday()]

def formato_pt(f):
    return {
        "TV": "TV",
        "TV_SHORT": "TV Curto",
        "MOVIE": "Filme",
        "OVA": "OVA",
        "ONA": "ONA",
        "SPECIAL": "Especial",
        "MUSIC": "Música"
    }.get(f, f or "N/A")

def status_pt(s):
    return {
        "FINISHED": "Finalizado",
        "RELEASING": "Em lançamento",
        "NOT_YET_RELEASED": "Não lançado",
        "CANCELLED": "Cancelado",
        "HIATUS": "Hiato"
    }.get(s, s or "N/A")

def formatar_data_inicio(start_date):
    if not start_date:
        return "Data não informada"

    dia = start_date.get("day")
    mes = start_date.get("month")
    ano = start_date.get("year")

    if dia and mes and ano:
        return f"{dia:02d}/{mes:02d}/{ano}"
    if mes and ano:
        return f"{mes:02d}/{ano}"
    if ano:
        return str(ano)

    return "Data não informada"

def melhor_titulo(media):
    return (
        media.get("title", {}).get("romaji")
        or media.get("title", {}).get("english")
        or media.get("title", {}).get("native")
        or "Sem título"
    )

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
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": "pt",
                "dt": "t",
                "q": texto
            },
            timeout=15
        )
        r.raise_for_status()
        traducao = "".join([x[0] for x in r.json()[0] if x[0]])
        traducao = traducao.strip() if traducao.strip() else texto
        cache_memoria[cache_key] = (agora, traducao)
        return traducao
    except Exception:
        return texto

def formatar_timestamp_local(ts):
    if not ts:
        return "Data não informada"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%d/%m/%Y %H:%M")

def mesmo_dia_local(ts):
    if not ts:
        return False
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().date()
    return dt == agora_local().date()

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

def imagem_anilist(media):
    return (
        media.get("coverImage", {}).get("extraLarge")
        or media.get("coverImage", {}).get("large")
        or media.get("coverImage", {}).get("medium")
        or ""
    )

def buscar_imagem_jikan(nome):
    cache_key = f"imgj:{nome.lower()}"
    agora = time.time()

    if cache_key in cache_memoria:
        salvo_em, valor = cache_memoria[cache_key]
        if agora - salvo_em < 3600:
            return valor

    try:
        resposta = requests.get(
            JIKAN_URL,
            params={"q": nome, "limit": 3},
            timeout=15
        )
        resposta.raise_for_status()
        dados = resposta.json().get("data", [])

        if not dados:
            cache_memoria[cache_key] = (agora, "")
            return ""

        for item in dados:
            if item.get("type") == "TV":
                img = (
                    item.get("images", {}).get("jpg", {}).get("large_image_url")
                    or item.get("images", {}).get("jpg", {}).get("image_url")
                    or ""
                )
                cache_memoria[cache_key] = (agora, img)
                return img

        primeiro = dados[0]
        img = (
            primeiro.get("images", {}).get("jpg", {}).get("large_image_url")
            or primeiro.get("images", {}).get("jpg", {}).get("image_url")
            or ""
        )
        cache_memoria[cache_key] = (agora, img)
        return img
    except Exception:
        return ""

def pegar_imagem_correta(media):
    titulo = melhor_titulo(media)
    img_jikan = buscar_imagem_jikan(titulo)
    if img_jikan:
        return img_jikan
    return imagem_anilist(media)

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

def carregar_auto():
    if os.path.exists(ARQUIVO_AUTO):
        with open(ARQUIVO_AUTO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"canais": [], "avisados": {}}

def salvar_auto(dados):
    with open(ARQUIVO_AUTO, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)

# =========================
# QUERIES
# =========================
def query_temporada_atual():
    cache_nome = f"temporada:{temporada_atual()}:{agora_local().year}"
    cache = cache_get(cache_nome)
    if cache is not None:
        return cache

    query = """
    query ($s: MediaSeason, $y: Int){
      Page(perPage: 100){
        media(type: ANIME, season: $s, seasonYear: $y, isAdult: false, sort: POPULARITY_DESC){
          id
          siteUrl
          title{romaji english native}
          description(asHtml: false)
          episodes
          averageScore
          status
          format
          coverImage{extraLarge large medium}
          nextAiringEpisode{episode airingAt}
          startDate{day month year}
        }
      }
    }
    """
    resultado = anilist_query(query, {"s": temporada_atual(), "y": agora_local().year})["Page"]["media"]
    cache_set(cache_nome, resultado)
    return resultado

def query_novos_anunciados():
    cache_nome = "novos_anunciados"
    cache = cache_get(cache_nome)
    if cache is not None:
        return cache

    query = """
    query {
      Page(perPage: 100){
        media(
          type: ANIME,
          status: NOT_YET_RELEASED,
          isAdult: false,
          sort: START_DATE_DESC
        ){
          id
          siteUrl
          title{romaji english native}
          description(asHtml: false)
          episodes
          averageScore
          status
          format
          coverImage{extraLarge large medium}
          startDate{day month year}
        }
      }
    }
    """
    resultado = anilist_query(query, {})["Page"]["media"]
    cache_set(cache_nome, resultado)
    return resultado

def query_calendario_semanal():
    cache_nome = f"semanal:{temporada_atual()}:{agora_local().year}"
    cache = cache_get(cache_nome)
    if cache is not None:
        return cache

    query = """
    query ($s: MediaSeason, $y: Int){
      Page(perPage: 100){
        media(
          type: ANIME,
          season: $s,
          seasonYear: $y,
          status: RELEASING,
          isAdult: false,
          sort: POPULARITY_DESC
        ){
          id
          siteUrl
          title{romaji english native}
          description(asHtml: false)
          coverImage{extraLarge large medium}
          nextAiringEpisode{episode airingAt}
          format
          status
          averageScore
          episodes
          startDate{day month year}
        }
      }
    }
    """
    resultado = anilist_query(query, {"s": temporada_atual(), "y": agora_local().year})["Page"]["media"]
    cache_set(cache_nome, resultado)
    return resultado

# =========================
# UI / ANIMETEMP
# =========================
class CategoriaSelect(discord.ui.Select):
    def __init__(self, parent_view):
        self.parent_view = parent_view

        options = [
            discord.SelectOption(label="TV", value="TV", emoji="📺"),
            discord.SelectOption(label="Filme", value="MOVIE", emoji="🎬"),
            discord.SelectOption(label="OVA", value="OVA", emoji="💿"),
            discord.SelectOption(label="ONA", value="ONA", emoji="🌐"),
            discord.SelectOption(label="Especial", value="SPECIAL", emoji="✨"),
        ]

        super().__init__(
            placeholder="Escolha uma categoria",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.cat = self.values[0]
        self.parent_view.page = 0
        await self.parent_view.atualizar(interaction)

class AnimeNavigator(discord.ui.View):
    def __init__(self, itens, titulo, autor_id, timeout=300):
        super().__init__(timeout=timeout)
        self.itens = itens
        self.titulo = titulo
        self.autor_id = autor_id
        self.cat = "TV"
        self.page = 0
        self.add_item(CategoriaSelect(self))

    def filtrados(self):
        return [i for i in self.itens if i.get("format") == self.cat]

    def total_paginas(self):
        f = self.filtrados()
        if not f:
            return 1
        return len(f)

    def criar_embed(self):
        filtrados = self.filtrados()
        nome_cat = formato_pt(self.cat)

        e = discord.Embed(
            title=f"🎌 {self.titulo}",
            description=f"📂 **Categoria:** {nome_cat}",
            color=COR_EMBED
        )

        if not filtrados:
            e.add_field(
                name="Sem resultados",
                value="Essa categoria não possui animes.",
                inline=False
            )
            e.set_footer(text="0 resultados")
            return e

        anime = filtrados[self.page]

        titulo = melhor_titulo(anime)
        link = anime.get("siteUrl", "")
        imagem = pegar_imagem_correta(anime)
        nota = anime.get("averageScore")
        episodios = anime.get("episodes")
        formato = formato_pt(anime.get("format"))
        status = status_pt(anime.get("status"))
        estreia = formatar_data_inicio(anime.get("startDate"))

        prox = anime.get("nextAiringEpisode")
        prox_texto = "N/A"
        if prox:
            prox_texto = f"Ep {prox.get('episode', '?')} • {formatar_timestamp_local(prox.get('airingAt'))}"

        sinopse = anime.get("description") or "Sem sinopse disponível."
        sinopse = limpar_html(sinopse)
        sinopse = traduzir_texto(sinopse)
        if len(sinopse) > 350:
            sinopse = sinopse[:350] + "..."

        info = (
            f"⭐ **Nota:** `{nota if nota is not None else 'N/A'}`\n"
            f"🎞️ **Formato:** `{formato}`\n"
            f"📺 **Episódios:** `{episodios if episodios is not None else 'N/A'}`\n"
            f"📡 **Status:** `{status}`\n"
            f"🗓️ **Estreia:** `{estreia}`\n"
            f"⏭️ **Próximo:** `{prox_texto}`"
        )

        e.add_field(
            name=f"🎬 **[{titulo}]({link})**",
            value=info,
            inline=False
        )

        e.add_field(
            name="📖 **Sinopse**",
            value=sinopse,
            inline=False
        )

        if imagem:
            e.set_image(url=imagem)

        e.set_footer(
            text=f"Página {self.page + 1}/{len(filtrados)} • Use os controles abaixo"
        )

        return e

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "Só quem usou o comando pode mexer nesses botões.",
                ephemeral=True
            )
            return False
        return True

    async def atualizar(self, interaction: discord.Interaction):
        total = self.total_paginas()
        if self.page < 0:
            self.page = 0
        if self.page >= total:
            self.page = total - 1

        await interaction.response.edit_message(
            embed=self.criar_embed(),
            view=self
        )

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.secondary, row=1)
    async def voltar(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        await self.atualizar(interaction)

    @discord.ui.button(label="Próximo", style=discord.ButtonStyle.primary, row=1)
    async def proximo(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        await self.atualizar(interaction)

    @discord.ui.button(label="Fechar", style=discord.ButtonStyle.danger, row=1)
    async def fechar(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.message.delete()
        except Exception:
            pass

# =========================
# UI / SEMANAL
# =========================
class SemanalNavigator(discord.ui.View):
    def __init__(self, agenda, agenda_img, autor_id, temporada_nome, timeout=300):
        super().__init__(timeout=timeout)
        self.agenda = agenda
        self.agenda_img = agenda_img
        self.autor_id = autor_id
        self.temporada_nome = temporada_nome
        self.dia_atual = "Segunda"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "Só quem usou o comando pode mexer nesses botões.",
                ephemeral=True
            )
            return False
        return True

    def criar_embed(self):
        itens = self.agenda.get(self.dia_atual, [])
        imagem = self.agenda_img.get(self.dia_atual, "")

        embed = discord.Embed(
            title=f"📅 Calendário Semanal — {self.temporada_nome}",
            description=f"🗓️ **{self.dia_atual}**",
            color=COR_EMBED
        )

        if not itens:
            embed.add_field(
                name="Sem lançamentos",
                value="Nada previsto para esse dia.",
                inline=False
            )
        else:
            texto = "\n\n".join(itens[:10])
            embed.add_field(
                name="Animes do dia",
                value=texto[:1024],
                inline=False
            )

        if imagem:
            embed.set_thumbnail(url=imagem)

        embed.set_footer(text="Escolha o dia nos botões abaixo")
        return embed

    async def atualizar(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.criar_embed(), view=self)

    @discord.ui.button(label="Seg", style=discord.ButtonStyle.secondary, row=0)
    async def segunda(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.dia_atual = "Segunda"
        await self.atualizar(interaction)

    @discord.ui.button(label="Ter", style=discord.ButtonStyle.secondary, row=0)
    async def terca(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.dia_atual = "Terça"
        await self.atualizar(interaction)

    @discord.ui.button(label="Qua", style=discord.ButtonStyle.secondary, row=0)
    async def quarta(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.dia_atual = "Quarta"
        await self.atualizar(interaction)

    @discord.ui.button(label="Qui", style=discord.ButtonStyle.secondary, row=0)
    async def quinta(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.dia_atual = "Quinta"
        await self.atualizar(interaction)

    @discord.ui.button(label="Sex", style=discord.ButtonStyle.secondary, row=0)
    async def sexta(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.dia_atual = "Sexta"
        await self.atualizar(interaction)

    @discord.ui.button(label="Sáb", style=discord.ButtonStyle.secondary, row=1)
    async def sabado(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.dia_atual = "Sábado"
        await self.atualizar(interaction)

    @discord.ui.button(label="Dom", style=discord.ButtonStyle.secondary, row=1)
    async def domingo(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.dia_atual = "Domingo"
        await self.atualizar(interaction)

    @discord.ui.button(label="Fechar", style=discord.ButtonStyle.danger, row=1)
    async def fechar(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.message.delete()
        except Exception:
            pass

# =========================
# COMANDOS
# =========================
@tree.command(name="ping", description="Mostra se o bot está online")
async def ping(interaction: discord.Interaction):
    espera = em_cooldown(interaction.user.id, "ping")
    if espera > 0:
        await interaction.response.send_message(
            f"⏳ Espera {espera}s antes de usar /ping de novo.",
            ephemeral=True
        )
        return

    await interaction.response.send_message("🏓 Online!")

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
        data = await asyncio.to_thread(query_temporada_atual)
        nav = AnimeNavigator(
            itens=data,
            titulo=f"Temporada {nome_temporada_pt(temporada_atual())}",
            autor_id=interaction.user.id
        )
        await interaction.followup.send(embed=nav.criar_embed(), view=nav)

    except Exception as e:
        await interaction.followup.send(f"Erro ao carregar /animetemp: `{e}`")

@tree.command(name="novo", description="Mostra animes novos anunciados")
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
        data = await asyncio.to_thread(query_novos_anunciados)

        if not data:
            await interaction.followup.send("Nenhum anime novo encontrado.")
            return

        await interaction.followup.send("🆕 **Animes novos anunciados:**")

        for anime in data[:NOVO_LIMITE]:
            titulo = melhor_titulo(anime)
            imagem = pegar_imagem_correta(anime)
            nota = anime.get("averageScore")
            formato = formato_pt(anime.get("format"))
            status = status_pt(anime.get("status"))
            estreia = formatar_data_inicio(anime.get("startDate"))

            sinopse = anime.get("description") or "Sem sinopse disponível."
            sinopse = limpar_html(sinopse)
            sinopse = traduzir_texto(sinopse)
            if len(sinopse) > 250:
                sinopse = sinopse[:250] + "..."

            embed = discord.Embed(
                title=titulo,
                url=anime.get("siteUrl", ""),
                description=f"📖 {sinopse}",
                color=COR_EMBED
            )

            embed.add_field(name="🎞️ Formato", value=formato, inline=True)
            embed.add_field(name="📡 Status", value=status, inline=True)
            embed.add_field(name="⭐ Nota", value=str(nota) if nota is not None else "N/A", inline=True)
            embed.add_field(name="🗓️ Estreia", value=estreia, inline=True)

            if imagem:
                embed.set_thumbnail(url=imagem)

            embed.set_footer(text="Novos animes anunciados")
            await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"Erro ao carregar /novo: `{e}`")

@tree.command(name="semanal", description="Mostra o calendário semanal da temporada")
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

        agenda_img = {
            "Segunda": "",
            "Terça": "",
            "Quarta": "",
            "Quinta": "",
            "Sexta": "",
            "Sábado": "",
            "Domingo": ""
        }

        for anime in animes:
            prox = anime.get("nextAiringEpisode")
            if not prox or not prox.get("airingAt"):
                continue

            dt = datetime.fromtimestamp(prox["airingAt"], tz=timezone.utc).astimezone()
            dia = nome_dia_pt(dt)

            titulo = melhor_titulo(anime)
            formato = formato_pt(anime.get("format"))
            horario = dt.strftime("%H:%M")
            episodio = prox.get("episode", "?")
            nota = anime.get("averageScore")
            site = anime.get("siteUrl", "")

            linha = (
                f"**[{titulo}]({site})**\n"
                f"🎞️ {formato} • ⭐ {nota if nota is not None else 'N/A'}\n"
                f"📺 Ep {episodio} às {horario}"
            )

            agenda[dia].append((dt, linha, anime))

        for dia in agenda:
            agenda[dia].sort(key=lambda x: x[0])
            if agenda[dia]:
                agenda_img[dia] = pegar_imagem_correta(agenda[dia][0][2])
            agenda[dia] = [item[1] for item in agenda[dia]]

        view = SemanalNavigator(
            agenda=agenda,
            agenda_img=agenda_img,
            autor_id=interaction.user.id,
            temporada_nome=nome_temporada_pt(temporada_atual())
        )

        await interaction.followup.send(embed=view.criar_embed(), view=view)

    except Exception as e:
        await interaction.followup.send(f"Erro ao montar /semanal: `{e}`")

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

        animes = await asyncio.to_thread(query_calendario_semanal)

        for anime in animes:
            prox = anime.get("nextAiringEpisode")
            anime_id = anime.get("id")

            if not prox or not anime_id:
                continue

            if not mesmo_dia_local(prox.get("airingAt")):
                continue

            if anime_id in dados["avisados"][data_hoje]:
                continue

            titulo = melhor_titulo(anime)
            imagem = pegar_imagem_correta(anime)

            sinopse = anime.get("description") or "Sem sinopse disponível."
            sinopse = limpar_html(sinopse)
            sinopse = traduzir_texto(sinopse)
            if len(sinopse) > 220:
                sinopse = sinopse[:220] + "..."

            embed = discord.Embed(
                title=f"🔔 {titulo}",
                url=anime.get("siteUrl", ""),
                description="**Novo episódio lançado hoje**",
                color=COR_EMBED
            )

            embed.add_field(
                name="📺 Episódio",
                value=str(prox.get("episode", "?")),
                inline=True
            )
            embed.add_field(
                name="🎞️ Formato",
                value=formato_pt(anime.get("format")),
                inline=True
            )
            embed.add_field(
                name="⏰ Horário",
                value=formatar_timestamp_local(prox.get("airingAt")),
                inline=True
            )

            embed.add_field(
                name="📖 Sinopse",
                value=sinopse,
                inline=False
            )

            if imagem:
                embed.set_thumbnail(url=imagem)

            embed.set_footer(text="Notificação automática de lançamento")

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
@client.event
async def on_ready():
    await tree.sync()
    if not verificar_notificacoes.is_running():
        verificar_notificacoes.start()
    print(f"Bot conectado como {client.user}")

if __name__ == "__main__":
    keep_alive()

    if not DISCORD_TOKEN:
        raise ValueError("DISCORD_TOKEN não foi definido nas variáveis de ambiente.")

    client.run(DISCORD_TOKEN)
