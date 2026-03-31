import requests
import time
import json
import os
from datetime import datetime
from flask import Flask
from threading import Thread

WEBHOOK_URL = "https://discord.com/api/webhooks/1488335755645292637/yq9uG31XGY5_5C2loWv-Yg06HRLCL8JfPyA7zEnj9H-qJ9wNfTZZotOp4woLhRjNq651"
STATE_FILE = "enviados.json"

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot online!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

def temporada_atual():
    mes = datetime.now().month

    if mes in [1, 2, 3]:
        return "winter"
    elif mes in [4, 5, 6]:
        return "spring"
    elif mes in [7, 8, 9]:
        return "summer"
    else:
        return "fall"

def carregar_enviados():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def salvar_enviados(lista):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(lista, f)

def enviar_discord(anime):
    titulo = anime.get("title", "Sem título")
    url = anime.get("url", "")
    imagem = anime.get("images", {}).get("jpg", {}).get("image_url", "")
    score = anime.get("score", "N/A")
    eps = anime.get("episodes", "?")
    synopsis = anime.get("synopsis", "Sem sinopse disponível.")

    if synopsis:
        synopsis = synopsis[:250] + "..." if len(synopsis) > 250 else synopsis

    embed = {
        "title": titulo,
        "url": url,
        "description": f"⭐ Nota: {score}\n🎬 Episódios: {eps}\n\n📖 {synopsis}",
        "color": 16753920
    }

    if imagem:
        embed["image"] = {"url": imagem}

    payload = {
        "embeds": [embed]
    }

    response = requests.post(WEBHOOK_URL, json=payload, timeout=15)

    if response.status_code in [200, 204]:
        print(f"Enviado: {titulo}")
    else:
        print("Erro ao enviar pro Discord:", response.status_code, response.text)

def main():
    keep_alive()
    enviados = carregar_enviados()

    while True:
        ano = datetime.now().year
        temp = temporada_atual()
        url = f"https://api.jikan.moe/v4/seasons/{ano}/{temp}"

        print(f"Buscando animes de {temp} {ano}...")

        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json().get("data", [])

            for anime in data:
                anime_id = anime.get("mal_id")

                if anime_id and anime_id not in enviados:
                    enviar_discord(anime)
                    enviados.append(anime_id)
                    salvar_enviados(enviados)
                    time.sleep(2)

        except Exception as e:
            print("Erro:", e)

        print("Esperando 10 minutos para verificar de novo...\n")
        time.sleep(600)

if __name__ == "__main__":
    main()