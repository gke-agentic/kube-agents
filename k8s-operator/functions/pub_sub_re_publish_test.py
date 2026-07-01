import unittest
from unittest.mock import MagicMock, patch
import os

from pub_sub_re_publish import re_publish_message, TARGET_TOPIC_1, TARGET_TOPIC_2

class TestPubSubRePublish(unittest.TestCase):

  def setUp(self):
    # Patch google.auth.default to return (None, 'test-project')
    self.auth_patcher = patch('google.auth.default', return_value=(None, 'test-project'))
    self.mock_auth = self.auth_patcher.start()
    
    # Also patch env var just in case fallback is triggered
    self.env_patcher = patch.dict(os.environ, {'GOOGLE_CLOUD_PROJECT': 'test-project'})
    self.env_patcher.start()

  def tearDown(self):
    self.auth_patcher.stop()
    self.env_patcher.stop()

  @patch('pub_sub_re_publish.parse_chat_event_from_bytes')
  @patch('pub_sub_re_publish.pubsub_v1.PublisherClient')
  def test_route_to_topic_1_default(self, mock_publisher_client, mock_parse_chat):
    # Setup mock parser to return message without <bot-2>
    mock_parse_chat.return_value = {
        'received_text': 'Hello world message'
    }

    # Setup mock publisher
    mock_publisher = MagicMock()
    mock_publisher_client.return_value = mock_publisher
    mock_publisher.topic_path.side_effect = lambda proj, topic: f"projects/{proj}/topics/{topic}"
    
    mock_future = MagicMock()
    mock_future.result.return_value = 'msg-id-1'
    mock_publisher.publish.return_value = mock_future

    event_data = {
        "message": {
            "data": "SGVsbG8gV29ybGQ=",  # "Hello World" in base64
            "attributes": {"key1": "value1"}
        }
    }
    class MockCloudEvent:
      def __init__(self, data):
        self.data = data
    cloud_event = MockCloudEvent(event_data)

    # Call the function
    result_msg, status_code = re_publish_message(cloud_event)

    # Assertions
    self.assertEqual(status_code, 200)
    self.assertEqual(result_msg, "Success")
    
    # Verify published ONLY to Topic 1
    expected_data = b"Hello World"
    mock_publisher.publish.assert_called_once_with(
        'projects/test-project/topics/test-chat-app-topic',
        expected_data,
        key1='value1'
    )
    
    # Verify parser was called with decoded data_bytes
    mock_parse_chat.assert_called_once_with(expected_data)
    
    # Verify topic_path was called ONLY for Topic 1
    mock_publisher.topic_path.assert_called_once_with('test-project', TARGET_TOPIC_1)

  @patch('pub_sub_re_publish.parse_chat_event_from_bytes')
  @patch('pub_sub_re_publish.pubsub_v1.PublisherClient')
  def test_route_to_topic_2_with_bot2(self, mock_publisher_client, mock_parse_chat):
    # Setup mock parser to return message with <bot-2>
    mock_parse_chat.return_value = {
        'received_text': 'Hello <bot-2> how are you'
    }

    # Setup mock publisher
    mock_publisher = MagicMock()
    mock_publisher_client.return_value = mock_publisher
    mock_publisher.topic_path.side_effect = lambda proj, topic: f"projects/{proj}/topics/{topic}"
    
    mock_future = MagicMock()
    mock_future.result.return_value = 'msg-id-2'
    mock_publisher.publish.return_value = mock_future

    event_data = {
        "message": {
            "data": "SGVsbG8gV29ybGQ=",
            "attributes": {"key1": "value1"}
        }
    }
    class MockCloudEvent:
      def __init__(self, data):
        self.data = data
    cloud_event = MockCloudEvent(event_data)

    # Call the function
    result_msg, status_code = re_publish_message(cloud_event)

    # Assertions
    self.assertEqual(status_code, 200)
    self.assertEqual(result_msg, "Success")
    
    # Verify published ONLY to Topic 2
    expected_data = b"Hello World"
    mock_publisher.publish.assert_called_once_with(
        'projects/test-project/topics/test-chat-app-topic-2',
        expected_data,
        key1='value1'
    )
    
    # Verify parser was called with decoded data_bytes
    mock_parse_chat.assert_called_once_with(expected_data)
    
    # Verify topic_path was called ONLY for Topic 2
    mock_publisher.topic_path.assert_called_once_with('test-project', TARGET_TOPIC_2)

  @patch('pub_sub_re_publish.parse_chat_event_from_bytes')
  @patch('pub_sub_re_publish.pubsub_v1.PublisherClient')
  def test_route_to_topic_1_when_parsing_fails(self, mock_publisher_client, mock_parse_chat):
    # Setup mock parser to return None (parsing failed)
    mock_parse_chat.return_value = None

    # Setup mock publisher
    mock_publisher = MagicMock()
    mock_publisher_client.return_value = mock_publisher
    mock_publisher.topic_path.side_effect = lambda proj, topic: f"projects/{proj}/topics/{topic}"
    
    mock_future = MagicMock()
    mock_future.result.return_value = 'msg-id-1'
    mock_publisher.publish.return_value = mock_future

    event_data = {
        "message": {
            "data": "SGVsbG8gV29ybGQ="
        }
    }
    class MockCloudEvent:
      def __init__(self, data):
        self.data = data
    cloud_event = MockCloudEvent(event_data)

    # Call the function
    result_msg, status_code = re_publish_message(cloud_event)

    # Assertions
    self.assertEqual(status_code, 200)
    
    # Should default to Topic 1
    mock_publisher.publish.assert_called_once_with(
        'projects/test-project/topics/test-chat-app-topic',
        b"Hello World"
    )
    
    # Verify parser was called with decoded data_bytes
    mock_parse_chat.assert_called_once_with(b"Hello World")
    
    # Should default to Topic 1
    mock_publisher.topic_path.assert_called_once_with('test-project', TARGET_TOPIC_1)

  @patch('pub_sub_re_publish.pubsub_v1.PublisherClient')
  def test_missing_project_id(self, mock_publisher_client):
    # Force google.auth.default to raise exception and clear env var
    self.mock_auth.side_effect = Exception("Auth error")
    
    with patch.dict(os.environ, {}, clear=True):
      class MockCloudEvent:
        def __init__(self, data):
          self.data = data
      
      event_data = {
          "message": {
              "data": "SGVsbG8gV29ybGQ="
          }
      }
      cloud_event = MockCloudEvent(event_data)
      
      result_msg, status_code = re_publish_message(cloud_event)
      self.assertEqual(status_code, 500)
      self.assertIn("Could not determine Google Cloud Project ID", result_msg)

  @patch('pub_sub_re_publish.pubsub_v1.PublisherClient')
  def test_missing_message_field(self, mock_publisher_client):
    class MockCloudEvent:
      def __init__(self, data):
        self.data = data

    # Missing 'message'
    cloud_event = MockCloudEvent({"invalid": "data"})
    result_msg, status_code = re_publish_message(cloud_event)
    self.assertEqual(status_code, 400)
    self.assertIn("Error: Failed to extract Pub/Sub message payload.", result_msg)

  @patch('pub_sub_re_publish.pubsub_v1.PublisherClient')
  def test_message_field_not_a_dict(self, mock_publisher_client):
    class MockCloudEvent:
      def __init__(self, data):
        self.data = data

    # 'message' is not a dict
    cloud_event = MockCloudEvent({"message": "not-a-dict"})
    result_msg, status_code = re_publish_message(cloud_event)
    self.assertEqual(status_code, 400)
    self.assertIn("Error: Failed to extract Pub/Sub message payload.", result_msg)

if __name__ == '__main__':
  unittest.main()
