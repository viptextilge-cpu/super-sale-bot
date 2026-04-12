import os
import json
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

ЯЗЫК — САМОЕ ВАЖНОЕ ПРАВИЛО:
- Если клиент пишет на грузинском — отвечай ТОЛЬКО на грузинском
- Если клиент пишет на русском — отвечай ТОЛЬКО на русском
- Если клиент пишет на английском — отвечай ТОЛЬКО на английском
- НИКОГДА не меняй язык ответа. Отвечай строго на том языке на котором написал клиент.

ОЧЕНЬ ВАЖНО: никогда не выдумывай товары и их характеристики. Если клиент спрашивает про конкретный товар или цену — отвечай на его языке: для грузинского 'დეტალური ინფორმაციისთვის გთხოვთ დაგვიკავშირდეთ პირდაპირ', для русского 'Для подробной информации, пожалуйста, свяжитесь с нами напрямую.'
Если вопрос не про Super Sale — вежливо скажи на языке клиента что помогаешь только по теме Super Sale."""

OPERATOR_FILE = "/tmp/operator_requested.json"

def load_operator_requested():
    try:
        with open(OPERATOR_FILE, "r") as f:
            return set(json.load(f))
    except:
        return set()

def save_operator_requested(data):
    try:
        with open(OPERATOR_FILE, "w") as f:
            json.dump(list(data), f)
    except:
        pass

processed_messages = set()

def send_message(recipient_id, text):
    requests.post(
        "https://graph.facebook.com/v18.0/me/messages",
        params={"access_token": PAGE_ACCESS_TOKEN},
        json={"recipient": {"id": recipient_id}, "message": {"text": text}}
    )

def send_operator_button(recipient_id):
    requests.post(
        "https://graph.facebook.com/v18.0/me/messages",
        params={"access_token": PAGE_ACCESS_TOKEN},
        json={
            "recipient": {"id": recipient_id},
            "message": {
                "attachment": {
                    "type": "template",
                    "payload": {
                        "template_type": "button",
                        "text": "გსურთ ოპერატორთან დაკავშირება?",
                        "buttons": [
                            {
                                "type": "postback",
                                "title": "👤 ოპერატორთან დაკავშირება",
                                "payload": "CONTACT_OPERATOR"
                            }
                        ]
                    }
                }
            }
        }
    )

def notify_operator(sender_id):
    requests.post(
        "https://graph.facebook.com/v18.0/me/messages",
        params={"access_token": PAGE_ACCESS_TOKEN},
        json={
            "recipient": {"id": sender_id},
            "message": {
                "text": "✅ ოპერატორი მალე დაგიკავშირდებათ!"
            }
        }
    )

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Invalid token", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    operator_requested = load_operator_requested()

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            mid = event.get("message", {}).get("mid")
            if mid in processed_messages:
                continue
            processed_messages.add(mid)
            sender_id = event["sender"]["id"]

            if "postback" in event and event["postback"].get("payload") == "CONTACT_OPERATOR":
                operator_requested.add(sender_id)
                save_operator_requested(operator_requested)
                notify_operator(sender_id)
                send_message("260986207108217",
                    f"🔔 ЗАПРОС ОПЕРАТОРА! Клиент ID: {sender_id}")
                continue

            if sender_id in operator_requested:
                continue

            if "message" in event and "text" in event["message"]:
                user_text = event["message"]["text"]
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=150,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_text}]
                )
                reply = response.content[0].text
                send_message(sender_id, reply)
                send_operator_button(sender_id)

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
