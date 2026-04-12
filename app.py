import os
import json
import hmac
import hashlib
import logging
import threading
from collections import deque
from typing import Optional, Set

import anthropic
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("super-sale-bot")

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "mysecrettoken")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")
APP_SECRET = os.environ.get("APP_SECRET")  # optional but recommended
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
PORT = int(os.environ.get("PORT", 10000))

OPERATOR_FILE = os.environ.get("OPERATOR_FILE", "/tmp/operator_requested.json")
SETTINGS_FILE = os.environ.get("SETTINGS_FILE", "/tmp/bot_settings.json")
MAX_PROCESSED_IDS = int(os.environ.get("MAX_PROCESSED_IDS", "5000"))

SYSTEM_PROMPT = (
    "Ty - pomoshnik magazina Super Sale. "
    "Otvechay TOLKO na voprosy o magazine Super Sale. "
    "Ne upominay drugie magaziny. "
    "Otvechay KOROTKO - maksimum 2-3 predlozheniya. "
    "YAZYK: esli klient pishet na gruzinskom - otvechay na gruzinskom. "
    "Esli na russkom - otvechay na russkom. "
    "Esli na angliyskom - otvechay na angliyskom. "
    "NIKOGDA ne menyay yazyk. "
    "OCHEN VAZHNO: nikogda ne vydumyvay tovary i tseny."
)

CONTACT_OPERATOR_PAYLOAD = "CONTACT_OPERATOR"
SET_OPERATOR_COMMAND = "/iamoperator"
RESET_TO_BOT_COMMANDS = {"/bot", "BOT_ON", "RETURN_TO_BOT", "RESET_OPERATOR"}

processed_messages: Set[str] = set()
processed_queue = deque(maxlen=MAX_PROCESSED_IDS)
processed_lock = threading.Lock()
file_lock = threading.Lock()

session = requests.Session()
client = None


def validate_env() -> None:
    missing = []
    if not PAGE_ACCESS_TOKEN:
        missing.append("PAGE_ACCESS_TOKEN")
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )


def init_clients() -> None:
    global client
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def is_valid_meta_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    if not APP_SECRET:
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("Missing or invalid X-Hub-Signature-256")
        return False

    received_sig = signature_header.split("=", 1)[1]
    computed_sig = hmac.new(
        APP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(received_sig, computed_sig)


def remember_processed(message_id: Optional[str]) -> None:
    if not message_id:
        return

    with processed_lock:
        if message_id in processed_messages:
            return

        if len(processed_queue) == processed_queue.maxlen:
            oldest = processed_queue.popleft()
            processed_messages.discard(oldest)

        processed_queue.append(message_id)
        processed_messages.add(message_id)


def is_processed(message_id: Optional[str]) -> bool:
    if not message_id:
        return False
    with processed_lock:
        return message_id in processed_messages


def read_json_file(path: str, default):
    with file_lock:
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("Failed to read JSON file: %s", path)
            return default


def write_json_file(path: str, data) -> None:
    with file_lock:
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            logger.exception("Failed to write JSON file: %s", path)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                logger.exception("Failed to cleanup temp file: %s", tmp_path)


def load_operator_requested() -> Set[str]:
    data = read_json_file(OPERATOR_FILE, [])
    if not isinstance(data, list):
        return set()
    return {str(x) for x in data}


def save_operator_requested(users: Set[str]) -> None:
    write_json_file(OPERATOR_FILE, sorted(list(users)))


def add_operator_requested(user_id: str) -> None:
    users = load_operator_requested()
    users.add(user_id)
    save_operator_requested(users)


def remove_operator_requested(user_id: str) -> None:
    users = load_operator_requested()
    if user_id in users:
        users.remove(user_id)
        save_operator_requested(users)


def is_operator_requested(user_id: str) -> bool:
    return user_id in load_operator_requested()


def load_settings() -> dict:
    data = read_json_file(SETTINGS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_settings(data: dict) -> None:
    write_json_file(SETTINGS_FILE, data)


def get_operator_psid() -> Optional[str]:
    settings = load_settings()
    operator_psid = settings.get("operator_psid")
    return str(operator_psid) if operator_psid else None


def set_operator_psid(psid: str) -> None:
    settings = load_settings()
    settings["operator_psid"] = str(psid)
    save_settings(settings)


def facebook_post(payload: dict) -> Optional[dict]:
    try:
        response = session.post(
            "https://graph.facebook.com/v18.0/me/messages",
            params={"access_token": PAGE_ACCESS_TOKEN},
            json=payload,
            timeout=(5, 15)
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        logger.exception("Facebook API request failed")
        return None


def send_text_message(recipient_id: str, text: str) -> Optional[dict]:
    logger.info("Sending text message to %s", recipient_id)
    return facebook_post({
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    })


def send_operator_button(recipient_id: str) -> Optional[dict]:
    logger.info("Sending operator button to %s", recipient_id)
    return facebook_post({
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": "gsurt operatortan dakavshireba?",
                    "buttons": [
                        {
                            "type": "postback",
                            "title": "Operatortan dakavshireba",
                            "payload": CONTACT_OPERATOR_PAYLOAD
                        }
                    ]
                }
            }
        }
    })


def notify_user_operator_requested(sender_id: str) -> None:
    send_text_message(sender_id, "Operator male dagikavshirdebat!")


def notify_user_back_to_bot(sender_id: str) -> None:
    send_text_message(sender_id, "Tkveni chat-i kvlav bots gadaeca. Shegidzliat mogvcerot.")


def notify_operator_new_request(sender_id: str) -> None:
    operator_psid = get_operator_psid()
    if not operator_psid:
        logger.warning("Operator PSID is not set yet")
        return
    send_text_message(operator_psid, f"ZAPROS OPERATORA! Klient ID: {sender_id}")


def notify_operator_bot_restored(sender_id: str) -> None:
    operator_psid = get_operator_psid()
    if not operator_psid:
        logger.warning("Operator PSID is not set yet")
        return
    send_text_message(operator_psid, f"BOT VERNULSYA DLYA KLIENTA ID: {sender_id}")


def extract_anthropic_text(response) -> str:
    texts = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "")
            if text:
                texts.append(text)
    return "\n".join(texts).strip()


def generate_reply(user_text: str) -> str:
    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=150,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_text}]
        )
        reply = extract_anthropic_text(response)
        if not reply:
            return "Izvinite, seychas ne mogu otvetit. Poprobuyte eshche raz."
        return reply
    except Exception:
        logger.exception("Anthropic request failed")
        return "Izvinite, seychas vremennaya oshibka. Poprobuyte cherez paru minut."


def event_id_for_postback(sender_id: str, event: dict) -> str:
    timestamp = event.get("timestamp", "")
    payload = event.get("postback", {}).get("payload", "")
    return f"postback:{sender_id}:{timestamp}:{payload}"


def event_id_for_message(sender_id: str, event: dict) -> str:
    mid = event.get("message", {}).get("mid")
    if mid:
        return f"mid:{mid}"
    timestamp = event.get("timestamp", "")
    return f"message:{sender_id}:{timestamp}"


def handle_postback(sender_id: str, event: dict) -> None:
    event_id = event_id_for_postback(sender_id, event)
    if is_processed(event_id):
        return
    remember_processed(event_id)

    payload = event.get("postback", {}).get("payload")
    if payload == CONTACT_OPERATOR_PAYLOAD:
        add_operator_requested(sender_id)
        notify_user_operator_requested(sender_id)
        notify_operator_new_request(sender_id)


def handle_operator_commands(sender_id: str, user_text: str) -> bool:
    text = (user_text or "").strip()
    operator_psid = get_operator_psid()

    if text == SET_OPERATOR_COMMAND:
        set_operator_psid(sender_id)
        send_text_message(sender_id, "Vy ustanovleny kak operator.")
        logger.info("Operator PSID set to %s", sender_id)
        return True

    if operator_psid and sender_id == operator_psid:
        parts = text.split(maxsplit=1)
        command = parts[0] if parts else ""

        if command in RESET_TO_BOT_COMMANDS:
            if len(parts) != 2:
                send_text_message(sender_id, "Format: /bot <client_id>")
                return True

            target_user_id = parts[1].strip()
            if not target_user_id:
                send_text_message(sender_id, "Format: /bot <client_id>")
                return True

            remove_operator_requested(target_user_id)
            notify_user_back_to_bot(target_user_id)
            notify_operator_bot_restored(target_user_id)
            return True

    return False


def handle_message(sender_id: str, event: dict) -> None:
    event_id = event_id_for_message(sender_id, event)
    if is_processed(event_id):
        return
    remember_processed(event_id)

    message = event.get("message", {})

    if message.get("is_echo"):
        return

    user_text = message.get("text", "")

    if handle_operator_commands(sender_id, user_text):
        return

    if is_operator_requested(sender_id):
        return

    if not user_text:
        send_text_message(sender_id, "Pozhaluysta, otpravte tekstovoe soobshchenie.")
        send_operator_button(sender_id)
        return

    reply = generate_reply(user_text)
    send_text_message(sender_id, reply)
    send_operator_button(sender_id)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge or "", 200
    return "Invalid token", 403


@app.route("/webhook", methods=["POST"])
def webhook():
    raw_body = request.get_data() or b""
    signature = request.headers.get("X-Hub-Signature-256")

    if not is_valid_meta_signature(raw_body, signature):
        return jsonify({"error": "invalid signature"}), 403

    data = request.get_json(silent=True) or {}

    if data.get("object") != "page":
        return jsonify({"status": "ignored"}), 200

    try:
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                sender_id = event.get("sender", {}).get("id")
                if not sender_id:
                    continue

                if "postback" in event:
                    handle_postback(sender_id, event)
                elif "message" in event:
                    handle_message(sender_id, event)
    except Exception:
        logger.exception("Unhandled webhook processing error")
        return jsonify({"status": "partial_error"}), 200

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    validate_env()
    init_clients()
    app.run(host="0.0.0.0", port=PORT, debug=False)
