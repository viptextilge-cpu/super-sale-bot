
import os
import anthropic
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "mysecrettoken")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """Ты — помощник магазина Super Sale.
Отвечай ТОЛЬКО на вопросы о магазине Super Sale.
Не упоминай другие магазины и компании.
Отвечай КОРОТКО — максимум 2-3 предложения.
ОЧЕНЬ ВАЖНО: всегда отвечай на том же языке, на котором пишет клиент. Если пишет на грузинском — отвечай на грузинском. Если на русском — на русском. Если на английском — на английском.
Если вопрос не про Super Sale — вежливо скажи что помогаешь только по теме Super Sale."""

processed_messages = set()

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Invalid token", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            mid = event.get("message", {}).get("mid")
            if mid in processed_messages:
                continue
            processed_messages.add(mid)
            sender_id = event["sender"]["id"]
            if "message" in event and "text" in event["message"]:
                user_text = event["message"]["text"]
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=150,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_text}]
                )
                reply = response.content[0].text
                requests.post(
                    "https://graph.facebook.com/v18.0/me/messages",
                    params={"access_token": PAGE_ACCESS_TOKEN},
                    json={"recipient": {"id": sender_id}, "message": {"text": reply}}
                )
    return jsonify({"status": "ok"})
