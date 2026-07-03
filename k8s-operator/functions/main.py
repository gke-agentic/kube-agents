import os
import json
from flask import Flask, request, jsonify
from google.cloud import firestore
from google.cloud import pubsub_v1
import google.auth
from googleapiclient.discovery import build

app = Flask(__name__)

# Hardcoded Agent Configuration matching dispatcher manifest
AGENTS_CONFIG = [
    {"id": "platform-agent", "name": "Platform Agent", "topic": "platform-agent-chat-events"},
    {"id": "test-agent", "name": "Test Agent", "topic": "test-chat-app-topic"}
]

# Initialize GCP clients
db = firestore.Client()
publisher = pubsub_v1.PublisherClient()
project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "YOUR_PROJECT_ID") # TODO DBG: replace this with dynamic value

# Initialize Chat client with proper scopes
credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/chat.bot"])
chat_service = build("chat", "v1", credentials=credentials)

@app.route("/", methods=["POST"])
def handle_chat_event():
    event_data = request.get_json()
    if not event_data:
        return jsonify({}), 400

    print(f"RAW EVENT: {json.dumps(event_data)}")

    common_obj = event_data.get("commonEventObject", {})
    chat_obj = event_data.get("chat", {})

    event_type = None
    if chat_obj.get("buttonClickedPayload"):
        event_type = "CARD_CLICKED"
    elif chat_obj.get("messagePayload"):
        event_type = "MESSAGE"

    # CASE 1: Incoming message in a thread
    if event_type == "MESSAGE":
        print(f"[DBG] MESSAGE")
        msg_payload = chat_obj.get("messagePayload", {})
        thread_name = msg_payload.get("message", {}).get("thread", {}).get("name")
        space_name = msg_payload.get("space", {}).get("name")
        
        if not thread_name or not space_name:
            return jsonify({}), 200

        # Check if this thread is already bound to an agent in Firestore
        safe_thread_id = thread_name.replace("/", "_")
        doc_ref = db.collection("chat_threads").document(safe_thread_id)
        doc = doc_ref.get()
        
        if doc.exists:
            # Route message event directly to the bound agent's Pub/Sub topic
            data = doc.to_dict()
            target_topic = data.get("target_topic")
            agent_id = data.get("agent_id")
            print(f"Routing thread {thread_name} message to {agent_id} on topic {target_topic}")
            
            topic_path = publisher.topic_path(project_id, target_topic)
            publisher.publish(topic_path, json.dumps(event_data).encode("utf-8"))
            
            # Return empty response to indicate success (asynchronous execution)
            return jsonify({}), 200
        else:
            # Thread is unbound, ask user to select an agent
            print(f"Unknown thread {thread_name}, sending agent selection card asynchronously via Chat API")
            function_url = request.host_url.replace("http://", "https://").rstrip('/')
            card_payload = create_card_with_buttons(function_url, thread_name)
            
            # Post asynchronously using Chat API
            chat_service.spaces().messages().create(
                parent=space_name,
                body=card_payload,
                messageReplyOption="REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
            ).execute()
            
            return jsonify({}), 200

    # CASE 2: User clicked an agent selection button on the card
    elif event_type == "CARD_CLICKED":
        print(f"[DBG] CARD_CLICKED")
        parameters = common_obj.get("parameters", {})
        action_method = parameters.get("action_method")
        
        if action_method == "select_agent":
            agent_id = parameters.get("agent_id")
            target_topic = parameters.get("target_topic")
            
            btn_payload = chat_obj.get("buttonClickedPayload", {})
            thread_name = btn_payload.get("message", {}).get("thread", {}).get("name")
            
            if thread_name and agent_id and target_topic:
                safe_thread_id = thread_name.replace("/", "_")
                print(f"Saving selection to Firestore: {thread_name} -> {agent_id} ({target_topic})")
                doc_ref = db.collection("chat_threads").document(safe_thread_id)
                doc_ref.set({
                    "agent_id": agent_id,
                    "target_topic": target_topic
                })
                
                # Return confirmation card update
                resp = create_confirmation_card(agent_id)
                print(f"sending response for CARD_CLICKED (select_agent): {resp}")
                return jsonify(resp)
        
        # Fallback if parameters are incomplete
        return jsonify({}), 200

    # Fallback for unsupported event types
    return jsonify({}), 200



def create_card_with_buttons(function_url, thread_name):
    """Generates the card containing two action buttons as a Chat Message resource."""
    buttons = []
    for agent in AGENTS_CONFIG:
        buttons.append({
            "text": agent["name"],
            "onClick": {
                "action": {
                    "function": function_url,
                    "parameters": [
                        {"key": "agent_id", "value": agent["id"]},
                        {"key": "target_topic", "value": agent["topic"]},
                        {"key": "action_method", "value": "select_agent"}
                    ]
                }
            }
        })

    return {
        "cardsV2": [
            {
                "cardId": "interactive-test-card",
                "card": {
                    "header": {
                        "title": "Interactive Card Test",
                        "subtitle": "Workspace Add-on Protocol"
                    },
                    "sections": [
                        {
                            "widgets": [
                                {
                                    "textParagraph": {
                                        "text": "Please choose an agent to connect to this thread:"
                                    }
                                },
                                {
                                    "buttonList": {
                                        "buttons": buttons
                                    }
                                }
                            ]
                        }
                    ]
                }
            }
        ],
        "thread": {
            "name": thread_name
        }
    }


def create_confirmation_card(button_name):
    """Generates the card that replaces the old one, providing synchronous UI feedback."""
    return {
        "hostAppDataAction": {
            "chatDataAction": {
                "updateMessageAction": {
                    "message": {
                        "cardsV2": [
                            {
                                "cardId": "confirmation-card",
                                "card": {
                                    "header": {
                                        "title": "Selection Confirmed",
                                    },
                                    "sections": [
                                        {
                                            "widgets": [
                                                {
                                                    "textParagraph": {
                                                        "text": f"✅ You clicked: <b>Option {button_name}</b>!"
                                                    }
                                                }
                                            ]
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                }
            }
        }
    }

if __name__ == "__main__":
    # Runs locally on port 8080
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
