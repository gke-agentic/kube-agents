import os
import google.auth
from google.cloud import pubsub_v1
from chat_incoming_message import extract_pubsub_message, parse_chat_event_from_bytes

TARGET_TOPIC_1 = "platform-agent-chat-events"
TARGET_TOPIC_2 = "test-chat-app-topic-2"
TARGET_TOPIC_3 = "hermes-crd-chat-events"
TARGET_TOPIC_4 = "test-chat-app-topic"

def re_publish_message(cloud_event):
  """Extracts Pub/Sub message, checks for <bot-2> in text, and routes to correct topic."""
  print(f"re_publish_message called with event: {cloud_event}")

  # Use generic helper to extract and decode message
  data_bytes, attributes = extract_pubsub_message(cloud_event)
  if not data_bytes:
    print("Error: Failed to extract Pub/Sub message payload.")
    return "Error: Failed to extract Pub/Sub message payload.", 400

  # Determine Project ID
  try:
    _, project_id = google.auth.default()
  except Exception as e:
    print(f"Notice: Failed to get default credentials ({e}). Trying environment variable.")
    project_id = os.environ.get('GOOGLE_CLOUD_PROJECT')

  if not project_id:
    error_msg = "Could not determine Google Cloud Project ID. Set GOOGLE_CLOUD_PROJECT env var or configure ADC."
    print(f"Error: {error_msg}")
    return error_msg, 500

  # Use parse_chat_event_from_bytes to extract message text for routing (avoiding double extraction)
  chat_metadata = parse_chat_event_from_bytes(data_bytes)
  message_text = ""
  if chat_metadata:
    message_text = chat_metadata.get('received_text', '')
    print(f"Parsed Chat message text: '{message_text}'")
  else:
    print("Warning: Could not parse Chat event metadata from bytes. Defaulting to empty text for routing.")

  try:
    publisher = pubsub_v1.PublisherClient()
    
    # Route based on content and construct full topic path for the target
    if "<bot-2>" in message_text:
      target_topic_name = TARGET_TOPIC_2
    elif "<bot-3>" in message_text:
      target_topic_name = TARGET_TOPIC_3
    elif "<bot-4>" in message_text:
      target_topic_name = TARGET_TOPIC_4
    else:
      target_topic_name = TARGET_TOPIC_1

    target_topic_path = publisher.topic_path(project_id, target_topic_name)

    print(f"Routing message to {target_topic_name} (path: {target_topic_path})")
    
    # Ensure all attributes are strings
    str_attributes = {k: str(v) for k, v in attributes.items()}

    future = publisher.publish(target_topic_path, data_bytes, **str_attributes)
    msg_id = future.result()
    
    print(f"Successfully re-published to {target_topic_name}. Message ID: {msg_id}")
    return "Success", 200
  except Exception as e:
    print(f"Error during publishing: {e}")
    return f"Error during publishing: {e}", 500
