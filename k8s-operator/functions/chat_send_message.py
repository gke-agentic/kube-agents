import google.auth
from googleapiclient.discovery import build
from google.auth.exceptions import DefaultCredentialsError

def send_chat_message(space_id, message_text, thread_name=None):
  try:
    # Automatically uses Application Default Credentials (e.g., from gcloud auth)
    credentials, project_id = google.auth.default(
      scopes=['https://www.googleapis.com/auth/chat.messages']
    )
    print(f"Using credentials for project: {project_id}")

    chat_service = build('chat', 'v1', credentials=credentials)

    message = {
      'text': message_text
    }
    if thread_name:
      message['thread'] = {'name': thread_name}

    # The 'parent' is the space ID
    request = chat_service.spaces().messages().create(
      parent=space_id,
      body=message,
      messageReplyOption='REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD'
    )
    response = request.execute()
    print(f"Successfully sent message: {response.get('name')}")
    return response

  except DefaultCredentialsError as e:
    print(f"Authentication error: {e}")
    print("Please ensure you have run 'gcloud auth application-default login'.")
    print("Also, check if GOOGLE_CLOUD_PROJECT is set or gcloud config has a project.")
  except Exception as e:
    print(f"An error occurred: {e}")
    # Check if the error is due to the app/service account not being in the space
    if "Permission denied" in str(e) or "not a member" in str(e):
      print(f"Error: The authenticated user/service account is likely not a member of space '{space_id}'.")
      print("Please add the service account email or your user account to the Chat space.")
    return None
