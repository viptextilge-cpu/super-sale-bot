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
ОЧЕНЬ ВАЖНО: всегда отвечай на том же языке, на котором пишет клиент.
ОЧЕНЬ ВАЖНО: никогда не выдумывай товары и их характеристики. Если клиент спрашивает про конкретный товар или цену — отвечай: 'Для подробной информации, пожалуйста, свяжитесь с нами напрямую.'
Если вопрос не про Super Sale — вежливо скажи что помогаешь только по теме Super Sale."""

processed_messages = set()
operator_requested = set()

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
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            mid = event.get("message", {}).get("mid")
            if mid in processed_messages:
                continue
            processed_messages.add(mid)
            sender_id = event["sender"]["id"]

            # Клиент нажал кнопку оператора
            if "postback" in event and event["postback"].get("payload") == "CONTACT_OPERATOR":
                operator_requested.add(sender_id)
                notify_operator(sender_id)
                send_message("260986207108217",
                    f"🔔 НОВЫЙ ЗАПРОС ОПЕРАТОРА!\nКлиент ID: {sender_id}\nОткрой inbox и ответь вручную.")
                continue

            # Если клиент уже запросил оператора — бот молчит
            if sender_id in operator_requested:
                continue

            # Обычное текстовое сообщение
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
    app.run(debug=True)
