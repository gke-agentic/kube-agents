import base64
import json

def parse_pubsub_chat_event(cloud_event):
  """Decodes and parses a Google Chat event from a Pub/Sub CloudEvent.
  
  Args:
    cloud_event: The raw CloudEvent received by the trigger.
      
  Returns:
    dict/None: Chat metadata or None.
  """
  print(f"Function parse_pubsub_chat_event called with params: {cloud_event}")
  try:
    data_bytes, _ = extract_pubsub_message(cloud_event)
    if not data_bytes:
      return None
    return parse_chat_event_from_bytes(data_bytes)
  except Exception as e:
    print(f"CRITICAL ERROR: Unhandled exception in parse_pubsub_chat_event: {e}")
    import traceback
    traceback.print_exc()
    return None


def extract_pubsub_message(cloud_event):
  """Extracts and decodes raw Pub/Sub message data and attributes from a CloudEvent.
  
  Args:
    cloud_event: The raw CloudEvent.
    
  Returns:
    tuple: (data_bytes, attributes) or (None, None) on error.
  """
  if not hasattr(cloud_event, 'data') or cloud_event.data is None:
    print("Error: CloudEvent has no data payload.")
    return None, None

  data = cloud_event.data
  if isinstance(data, (str, bytes)):
    try:
      data = json.loads(data)
    except Exception as e:
      print(f"Error: Failed to parse raw CloudEvent data as JSON: {e}")
      return None, None

  if not isinstance(data, dict):
    print(f"Error: CloudEvent data is not a dictionary (type: {type(data)}).")
    return None, None

  if "message" not in data:
    print("Error: 'message' field is missing.")
    return None, None

  pubsub_message = data.get("message")
  if not isinstance(pubsub_message, dict):
    print(f"Error: 'message' field is not a dictionary (type: {type(pubsub_message)}).")
    return None, None

  raw_data = pubsub_message.get("data")
  if not raw_data:
    print("Warning: Pub/Sub message 'data' field is missing or empty.")
    return None, None

  print(f"Debug: raw_data type from pubsub_message: {type(raw_data)}")

  try:
    data_bytes = base64.b64decode(raw_data)
    attributes = pubsub_message.get('attributes', {}) or {}
    return data_bytes, attributes
  except Exception as e:
    print(f"Error: Failed to decode base64 data: {e}")
    return None, None


def parse_chat_event_from_bytes(data_bytes):
  """Parses a decoded Pub/Sub payload as a Google Chat event.
  
  Args:
    data_bytes (bytes): The decoded message payload.
    
  Returns:
    dict/None: Chat metadata or None if not valid Chat event JSON.
  """
  try:
    data_str = data_bytes.decode("utf-8")
  except Exception as e:
    print(f"Error: Failed to decode bytes as utf-8: {e}")
    return None

  try:
    event_data = json.loads(data_str)
  except Exception as e:
    print(f"Notice: Message data is not valid JSON ({e}). Treating as a simple raw test message.")
    print(f"Received raw test message text: '{data_str}'")
    return None

  if not isinstance(event_data, dict):
    print(f"Error: Parsed JSON event data is not a dictionary.")
    return None

  return _parse_chat_event(event_data)


def _parse_chat_event(event_data):
  """Parses a Google Chat event (both direct and Workspace Add-on styles) and extracts metadata.
  
  Args:
    event_data (dict): The parsed JSON payload of the event.
      
  Returns:
    dict: A dictionary containing extracted fields:
          - 'space_name' (str/None)
          - 'thread_name' (str/None)
          - 'sender_name' (str)
          - 'received_text' (str)
  """
  chat_payload = event_data.get('chat', {}) if isinstance(event_data, dict) else {}
  message_payload = chat_payload.get('messagePayload', {}) if isinstance(chat_payload, dict) else {}

  # 1. Extract message data
  message_data = {}
  if isinstance(message_payload, dict) and 'message' in message_payload:
    message_data = message_payload.get('message', {})
  elif isinstance(event_data, dict) and 'message' in event_data:
    message_data = event_data.get('message', {})

  if not isinstance(message_data, dict):
    message_data = {}

  # 2. Extract space data
  space_data = {}
  if isinstance(message_payload, dict) and 'space' in message_payload:
    space_data = message_payload.get('space', {})
  elif isinstance(message_data, dict) and 'space' in message_data:
    space_data = message_data.get('space', {})
  elif isinstance(event_data, dict) and 'space' in event_data:
    space_data = event_data.get('space', {})

  if not isinstance(space_data, dict):
    space_data = {}

  # 3. Extract final fields safely
  space_name = space_data.get('name')
  
  thread_data = message_data.get('thread', {}) if isinstance(message_data, dict) else {}
  thread_name = thread_data.get('name') if isinstance(thread_data, dict) else None
  
  sender_data = message_data.get('sender', {}) if isinstance(message_data, dict) else {}
  sender_name = sender_data.get('displayName', 'User') if isinstance(sender_data, dict) else 'User'
  
  received_text = message_data.get('text', '') if isinstance(message_data, dict) else ''

  return {
    'space_name': space_name,
    'thread_name': thread_name,
    'sender_name': sender_name,
    'received_text': received_text
  }
