# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Example demonstrating background triggers in Google Antigravity SDK.

Triggers are long-lived async functions that run in the background alongside
an active agent session. They react to external events (such as timers, file
changes, or webhooks) and push automated trigger notifications back to the
agent connection.

This example demonstrates:
1. Periodic Triggers (using the `every` helper) - Simulating SRE Ticket Queues.
2. Custom Triggers (using `@triggers.trigger` decorator) - Simulating CI/CD
   Webhook listeners.

To run:
  python triggers.py

Criteria for correct script performance:
  1. The script exits cleanly with return code 0 (no unhandled exceptions).
  2. The periodic trigger fires and sends a system alert to the agent.
  3. The custom CI/CD trigger fires and sends a build failure alert.
  4. The agent acknowledges the trigger notifications in its responses.
"""

import asyncio

from google.antigravity import Agent, LocalAgentConfig
from google.antigravity.triggers import every, trigger, TriggerContext

import google.auth
from googleapiclient.discovery import build
from google.auth.exceptions import DefaultCredentialsError

# ==============================================================================
# 1. PERIODIC TRIGGER EXAMPLE: Customer Support Ticket Queue
# ==============================================================================

# Stateful checks to simulate background ticket arrivals.
_ticket_counter = 0
_standby_active = False


# Define a callback function for the periodic trigger.
# The callback must be an async function that accepts a TriggerContext.
async def _poll_queue_callback(ctx: TriggerContext) -> None:
  """Polls the support ticket queue periodically for new tickets."""
  global _ticket_counter

  # Avoid polling before Turn 1 completes to prevent latency race conditions.
  if not _standby_active:
    return

  _ticket_counter += 1

  # On the second tick after standby starts (2 seconds in), simulate arrival.
  if _ticket_counter == 2:
    # We use explicit print statements with flush=True so that output appears
    # in real-time in the terminal even while the main thread is sleeping.
    print(
        "\n  [TRIGGER EVENT] Alert! New ticket detected in the queue...",
        flush=True,
    )

    # ctx.send pushes an automated trigger notification into the connection.
    # The agent's model will see this message in its conversation history.
    await ctx.send(
        "[SYSTEM ALERT] New critical ticket assigned: b/98765. Title: "
        "Database Connection Leak in Prod."
    )


async def _run_periodic_trigger_example() -> None:
  """Demonstrates a periodic trigger polling an SRE support queue."""
  print("  === Support Queue Trigger Demo ===")
  print("  Creating agent and starting session...")

  global _ticket_counter
  global _standby_active
  _ticket_counter = 0
  _standby_active = False

  # Configure a trigger that checks every 1 second for demonstration.
  my_trigger = every(1, _poll_queue_callback)

  config = LocalAgentConfig(
      system_instructions=(
          "You are a system operations and support assistant. You monitor a "
          "queue of incoming support tickets. When the user asks for updates, "
          "you must check and report any tickets that came in from the "
          "background system alert trigger."
      ),
      triggers=[my_trigger],
  )

  # Triggers are active only while inside the 'async with' session block.
  async with Agent(config) as my_agent:
    # Turn 1: Instruct the agent to watch.
    prompt1 = (
        "Your task will be to standby and simply let me know if there are any "
        "critical tickets received."
    )
    print(f"\n  User: {prompt1}")
    response1 = await my_agent.chat(prompt1)
    print(f"  Agent: {await response1.text()}")

    # Turn 1 is resolved. We now enable the standby trigger.
    _standby_active = True

    # Sleep to let the background task execute.
    # The TriggerRunner runs the callback concurrently in an asyncio task.
    print(
        "\n  Sleeping for 5 seconds. A new ticket will be simulated "
        "in the background..."
    )
    await asyncio.sleep(5)

    # Turn 2: Ask for updates.
    # Because the trigger sent an event, it was appended to the conversation
    # history. The agent will recall it reactively.
    prompt2 = "I'm back. Did anything critical come in while I was working?"
    print(f"\n  User: {prompt2}")
    response2 = await my_agent.chat(prompt2)
    print(f"  Agent: {await response2.text()}")

    print("\n  Ending session. Background triggers will stop automatically.")


# ==============================================================================
# 2. CUSTOM TRIGGER EXAMPLE: CI/CD Webhook Alert Listener
# ==============================================================================

# Stateful checks to simulate background webhook notifications.
_webhook_active = False


# A custom trigger is any async function decorated with @triggers.trigger
# that accepts a single TriggerContext argument.
@trigger
async def _webhook_listener(ctx: TriggerContext) -> None:
  """Simulates a background push-based CI/CD webhook listener."""

  print("\n  [WEBHOOK TRIGGER] Custom Webhook listener started...", flush=True)

  # The developer is responsible for their own loop and interval/compaction
  # logic.
  tick = 0
  while True:
    await asyncio.sleep(1)  # Poll simulated webhook port every 1s

    # Avoid processing before Turn 1 resolves.
    if not _webhook_active:
      continue

    tick += 1
    # On the third tick inside standby, push a simulated build failure alert.
    if tick == 3:
      print(
          "\n  [WEBHOOK TRIGGER] Event received: 'AppBuild-42' status FAILED.",
          flush=True,
      )
      await ctx.send(
          "[WEBHOOK ALERT] CI/CD Build Pipeline 'AppBuild-42' FAILED on "
          "branch 'main'. Reason: Lint errors in routes.py."
      )


async def _run_custom_trigger_example() -> None:
  """Demonstrates a custom trigger simulating a background webhook listener."""
  print("  === Custom Webhook Trigger Demo ===")
  print("  Creating agent and starting session...")

  global _webhook_active
  _webhook_active = False

  # Register our custom webhook listener trigger directly.
  config = LocalAgentConfig(
      system_instructions=(
          "You are a CI/CD operations assistant. You monitor pipeline status "
          "via an external webhook trigger. When the user asks for updates, "
          "you must check and report any failures that came in from the "
          "webhook alert trigger."
      ),
      triggers=[_webhook_listener],
  )

  async with Agent(config) as my_agent:
    # Turn 1: Set standby monitoring.
    prompt1 = (
        "Your task will be to standby and simply let me know if there are any "
        "critical pipeline webhook alerts received."
    )
    print(f"\n  User: {prompt1}")
    response1 = await my_agent.chat(prompt1)
    print(f"  Agent: {await response1.text()}")

    # Turn 1 is resolved. We now enable the webhook trigger.
    _webhook_active = True

    print(
        "\n  Sleeping for 5 seconds. A pipeline failure will be simulated "
        "in the background..."
    )
    await asyncio.sleep(5)

    # Turn 2: Ask for updates.
    prompt2 = "I'm back. Any updates on my builds?"
    print(f"\n  User: {prompt2}")
    response2 = await my_agent.chat(prompt2)
    print(f"  Agent: {await response2.text()}")

    print("\n  Ending session. Background triggers will stop automatically.")


# ==============================================================================
# 4. MY TEST CODE FOR CHAT
# ==============================================================================

# Replace with your Chat Space ID (e.g., 'spaces/AAAAAAAAAAA')
# You can get this from the Chat URL when in the space.

# CHAT_SPACE_ID = "spaces/7Yj6RyAAAAE"
CHAT_SPACE_ID = "spaces/AAQAXAJBI5g"
MESSAGE_TEXT = "Hello from my local Python script!"

def send_chat_message_local(space_id, message_text):
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

    # The 'parent' is the space ID
    request = chat_service.spaces().messages().create(
        parent=space_id,
        body=message
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

# ==============================================================================
# 4. MAIN EXECUTION ENTRYPOINT
# ==============================================================================


async def main() -> None:
  """Runs both the periodic and custom trigger Getting Started examples.

  First demonstrates a Periodic Trigger (using the `every` helper) monitoring
  a support ticket queue. Second demonstrates a Custom Trigger (using the
  `@triggers.trigger` decorator) simulating a push-based CI/CD webhook alert
  listener.
  """
#   await _run_periodic_trigger_example()
#   print("\n" + "=" * 60 + "\n")
#   await _run_custom_trigger_example()
  
  send_chat_message_local(CHAT_SPACE_ID, MESSAGE_TEXT)

if __name__ == "__main__":
  asyncio.run(main())
