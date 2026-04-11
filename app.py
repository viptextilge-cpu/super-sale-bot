import os
import anthropic
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "mysecrettoken")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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
            sender_id = event["sender"]["id"]
            if "message" in event and "text" in event["message"]:
                user_text = event["message"]["text"]
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=500,
                    messages=[{"role": "user", "content": user_text}]
                )
                reply = response.content[0].text
                requests.post(
                    "https://graph.facebook.com/v18.0/me/messages",
                    params={"access_token": PAGE_ACCESS_TOKEN},
                    json={"recipient": {"id": sender_id}, "message": {"text": reply}}
                )
    return jsonify({"status": "ok"})
