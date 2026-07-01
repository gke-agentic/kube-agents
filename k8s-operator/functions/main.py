from datetime import datetime
from zoneinfo import ZoneInfo
import functions_framework
from chat_incoming_message import parse_pubsub_chat_event
from chat_send_message import send_chat_message
from pub_sub_re_publish import re_publish_message as re_publish_message_impl

VERSION = "0.0.2"

@functions_framework.http
def hello_get(request):
  """HTTP Cloud Function.
  Args:
    request (flask.Request): The request object.
    <https://flask.palletsprojects.com/en/1.1.x/api/#incoming-request-data>
  Returns:
    The response text, or any set of values that can be turned into a
    Response object using `make_response`
    <https://flask.palletsprojects.com/en/1.1.x/api/#flask.make_response>.
  Note:
    For more information on how Flask integrates with Cloud
    Functions, see the `Writing HTTP functions` page.
    <https://cloud.google.com/functions/docs/writing/http#http_frameworks>
  """
  return "Hello World!"


@functions_framework.http
def send_chat(request):
  """HTTP Cloud Function that sends a chat message with a Warsaw timestamp."""
  chat_space_id = "spaces/AAQAXAJBI5g"
  current_time = datetime.now(ZoneInfo("Europe/Warsaw")).isoformat()
  message = f"Hello from my cloud function! Timestamp: {current_time}"
  response = send_chat_message(chat_space_id, message)
  if response:
    return f"Message successfully sent to space {chat_space_id}! Message Name: {response.get('name')}"
  return "Failed to send chat message. See logs for details.", 500


@functions_framework.cloud_event
def handle_chat_message(cloud_event):
  """Background Cloud Function entry point to handle messages from a Pub/Sub topic securely."""
  metadata = parse_pubsub_chat_event(cloud_event)
  if not metadata:
    # Parsing failed or warning occurred, helper has already print-logged it
    return

  space_name = metadata.get('space_name')
  thread_name = metadata.get('thread_name')
  sender_name = metadata.get('sender_name', 'User')
  received_text = metadata.get('received_text', '')

  if not space_name or not thread_name:
    print(f"Notice: Missing space name ({space_name}) or thread name ({thread_name}) in the Chat event JSON. Cannot reply in-thread.")
    print(f"Payload details: Space: {space_name}, Thread: {thread_name}, Text: '{received_text}'")
    return

  # Prepare the response message, repeating the received text with the version tag
  reply_text = f"[Ver {VERSION}] Received message from user: {received_text}"
  
  print(f"Replying to user '{sender_name}' in space '{space_name}' (thread: '{thread_name}')...")
  
  # Send the response back in the same thread
  send_chat_message(space_name, reply_text, thread_name=thread_name)


@functions_framework.cloud_event
def re_publish_message(cloud_event):
  """Background Cloud Function to re-publish incoming Pub/Sub messages to two other topics."""
  return re_publish_message_impl(cloud_event)
