import io
import json
from types import SimpleNamespace

from src.monitoring.telegram_topic_notifier import TelegramTopicNotifier


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def test_send_message_includes_message_thread_id(monkeypatch):
    captured = {}

    def _fake_urlopen(request, timeout=10):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(json.dumps({"ok": True}).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    notifier = TelegramTopicNotifier(
        bot_token="token",
        chat_id="123",
        message_thread_id="456",
    )

    result = notifier.send_message("hello world")

    assert result.ok is True
    assert captured["payload"]["chat_id"] == "123"
    assert captured["payload"]["message_thread_id"] == 456


def test_send_message_uses_reply_to_message_id_when_thread_missing(monkeypatch):
    captured = {}

    def _fake_urlopen(request, timeout=10):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(json.dumps({"ok": True}).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    notifier = TelegramTopicNotifier(
        bot_token="token",
        chat_id="123",
        reply_to_message_id="789",
    )

    result = notifier.send_message("hello world", require_topic_target=True)

    assert result.ok is True
    assert captured["payload"]["reply_to_message_id"] == 789
    assert "message_thread_id" not in captured["payload"]
    assert captured["payload"]["disable_notification"] is False


def test_send_message_can_disable_notification(monkeypatch):
    captured = {}

    def _fake_urlopen(request, timeout=10):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(json.dumps({"ok": True}).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    notifier = TelegramTopicNotifier(
        bot_token="token",
        chat_id="123",
        reply_to_message_id="789",
        disable_notification=True,
    )

    result = notifier.send_message("quiet update", require_topic_target=True)

    assert result.ok is True
    assert captured["payload"]["disable_notification"] is True


def test_send_message_explicit_args_ignore_env_disable_notification(monkeypatch):
    captured = {}

    def _fake_urlopen(request, timeout=10):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(json.dumps({"ok": True}).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    monkeypatch.setenv("TELEGRAM_ROLE_UPDATES_DISABLE_NOTIFICATION", "true")
    notifier = TelegramTopicNotifier(
        bot_token="token",
        chat_id="123",
        reply_to_message_id="789",
    )

    result = notifier.send_message("explicit routing", require_topic_target=True)

    assert result.ok is True
    assert captured["payload"]["disable_notification"] is False


def test_send_message_returns_missing_config_when_token_absent():
    notifier = TelegramTopicNotifier(bot_token="", chat_id="")
    result = notifier.send_message("hello")
    assert result.ok is False
    assert result.reason == "missing_token_or_chat_id"


def test_send_message_requires_topic_target_when_requested():
    notifier = TelegramTopicNotifier(bot_token="token", chat_id="123", message_thread_id="")
    result = notifier.send_message("hello", require_topic_target=True)
    assert result.ok is False
    assert result.reason == "missing_topic_target"
