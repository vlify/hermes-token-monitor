"""Opt-in alerts use real ledger transitions, task context and host event-loop scheduling."""
import asyncio
import threading
from contextvars import Context, copy_context
from types import SimpleNamespace

import pytest
from gateway.session_context import set_session_vars
from token_monitor import notifications
from token_monitor.config import MonitorConfig, profile_home
from token_monitor.hooks import TokenMonitorHooks


def bound(call, *, sid="A", platform="matrix", chat="!room:example.org", thread="$root:example.org"):
    def invoke():
        set_session_vars(session_id=sid, platform=platform, chat_id=chat, thread_id=thread,
                         message_id="42", user_id="owner", scope_id="workspace")
        return call()
    return copy_context().run(invoke)


@pytest.fixture
def gateway_loop():
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run)
    thread.start()
    assert ready.wait(2)
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2)
    assert not thread.is_alive()
    loop.close()


@pytest.fixture
def transport(monkeypatch, gateway_loop):
    from gateway import warning_notifications
    from tools import send_message_senders
    sent = []

    async def send(**kwargs):
        sent.append((kwargs, profile_home(), asyncio.get_running_loop()))
        return SimpleNamespace(success=True)

    adapter = SimpleNamespace(send=send)
    monkeypatch.setattr(send_message_senders, "_live_adapter", lambda platform: (
        SimpleNamespace(_gateway_loop=gateway_loop), adapter))
    monkeypatch.setattr(warning_notifications, "warning_notifications_enabled", lambda platform: True)
    futures = []
    original = notifications.send_alert

    def tracked(*args):
        future = original(*args)
        if future is not None:
            futures.append(future)
        return future

    monkeypatch.setattr(notifications, "send_alert", tracked)
    return SimpleNamespace(adapter=adapter, sent=sent, futures=futures)


def hooks(tmp_path, **kwargs):
    return TokenMonitorHooks(MonitorConfig(state_file=str(tmp_path / "ledger.json"),
        limit_total_tokens=100, warning_levels=[0.5], notify_console=False,
        notify_gateway=True, **kwargs))


@pytest.mark.parametrize("platform,chat,thread", [
    ("matrix", "!room:example.org", "$root:example.org"),
    ("telegram", "12345", "77"),
    ("telegram", "-10012345", "88"),
    ("discord", "123456789", "987654321"),
    ("slack", "D123456789", "172345.123"),
    ("email", "user@example.org", "<original@example.org>"),
    ("feishu", "oc_example", "thread_example"),
])
def test_host_adapter_receives_exact_chat_thread_and_reply(tmp_path, transport, gateway_loop,
                                                         platform, chat, thread):
    monitor = hooks(tmp_path)
    bound(lambda: monitor.on_post_api_request(session_id="A", platform=platform,
          api_request_id="one", usage={"input_tokens": 60}), platform=platform, chat=chat, thread=thread)
    assert transport.futures[0].result(timeout=2) is True
    assert len(transport.sent) == 1
    args, home, loop = transport.sent[0]
    assert args["chat_id"] == chat
    assert args["reply_to"] == "42"
    assert args["metadata"]["thread_id"] == thread
    assert args["metadata"]["notification_category"] == "diagnostic"
    if platform == "telegram":
        assert args["metadata"]["telegram_reply_to_message_id"] == "42"
    assert "60/100" in args["content"]
    assert loop is gateway_loop
    assert home == profile_home()
    assert monitor.ledger.get_session_stats("A").total_tokens == 60


def test_environment_fallback_never_routes_an_alert(monkeypatch):
    for name, value in {"ID": "A", "PLATFORM": "matrix", "CHAT_ID": "!stale:example.org"}.items():
        monkeypatch.setenv("HERMES_SESSION_" + name, value)
    assert Context().run(notifications.current_route, "A", "matrix") is None


@pytest.mark.parametrize("sid,platform", [("B", "matrix"), ("A", "telegram"), ("A", "cli")])
def test_missing_or_mismatched_context_does_not_send(tmp_path, transport, sid, platform):
    monitor = hooks(tmp_path)
    bound(lambda: monitor.on_post_api_request(session_id=sid, platform=platform,
          usage={"input_tokens": 60}))
    assert not transport.sent
    assert not transport.futures
    assert monitor.ledger.get_session_stats(sid).total_tokens == 60


def test_concurrent_global_threshold_has_only_one_send_attempt(tmp_path, transport):
    monitor = hooks(tmp_path, scope="all_time")
    workers = []
    for index in range(10):
        sid = f"S{index}"
        def invoke(sid=sid):
            bound(lambda: monitor.on_post_api_request(session_id=sid, platform="matrix",
                  api_request_id=sid, usage={"input_tokens": 10}), sid=sid, chat=f"!{sid}:example.org")
        workers.append(threading.Thread(target=invoke))
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3)
        assert not worker.is_alive()
    for future in transport.futures:
        assert future.result(timeout=2) is True
    assert len(transport.sent) == 1
    assert monitor.ledger.get_all_time_stats().total_tokens == 100
    # Recreating hooks (e.g. reload) cannot re-send a persisted threshold.
    replacement = hooks(tmp_path, scope="all_time")
    bound(lambda: replacement.on_post_api_request(session_id="A", usage={"input_tokens": 1}))
    assert len(transport.futures) == 1


def test_warning_and_freeze_merge_into_one_alert(tmp_path, transport):
    monitor = hooks(tmp_path, freeze_on_limit=True)
    bound(lambda: monitor.on_post_api_request(session_id="A", usage={"input_tokens": 100}))
    assert transport.futures[0].result(timeout=2) is True
    assert len(transport.sent) == 1
    assert "blocked" in transport.sent[0][0]["content"]
    assert monitor.on_pre_tool_call(session_id="A")["action"] == "block"


@pytest.mark.parametrize("failure", ["receipt", "exception"])
def test_failed_delivery_keeps_usage_and_is_not_retried(tmp_path, transport, caplog, failure):
    calls = []
    async def fail(**kwargs):
        calls.append(kwargs)
        if failure == "exception":
            raise RuntimeError("secret transport detail must not be logged")
        return SimpleNamespace(success=False, error="secret transport detail")
    transport.adapter.send = fail
    monitor = hooks(tmp_path)
    bound(lambda: monitor.on_post_api_request(session_id="A", api_request_id="one",
                                            usage={"input_tokens": 60}))
    future = transport.futures[0]
    if failure == "exception":
        with pytest.raises(RuntimeError):
            future.result(timeout=2)
    else:
        assert future.result(timeout=2) is False
    bound(lambda: monitor.on_post_api_request(session_id="A", api_request_id="one",
                                            usage={"input_tokens": 60}))
    bound(lambda: monitor.on_post_api_request(session_id="A", api_request_id="two",
                                            usage={"input_tokens": 1}))
    assert len(calls) == 1
    assert monitor.ledger.get_session_stats("A").total_tokens == 61
    assert "secret transport detail" not in caplog.text
    assert "Token:" in monitor.on_transform_llm_output(response_text="Done", session_id="A")


def test_muted_diagnostics_do_not_send(tmp_path, transport, monkeypatch):
    from gateway import warning_notifications
    monkeypatch.setattr(warning_notifications, "warning_notifications_enabled", lambda platform: False)
    monitor = hooks(tmp_path)
    bound(lambda: monitor.on_post_api_request(session_id="A", usage={"input_tokens": 60}))
    assert transport.futures[0].result(timeout=2) is False
    assert not transport.sent


def test_alert_disabled_by_default_and_language(tmp_path, transport):
    monitor = TokenMonitorHooks(MonitorConfig(state_file=str(tmp_path / "off.json"), limit_total_tokens=100))
    bound(lambda: monitor.on_post_api_request(session_id="A", usage={"input_tokens": 60}))
    assert not transport.futures
    monitor = hooks(tmp_path, language="zh")
    bound(lambda: monitor.on_post_api_request(session_id="A", usage={"input_tokens": 60}))
    assert transport.futures[0].result(timeout=2) is True
    assert "额度提醒" in transport.sent[0][0]["content"]


def test_owning_profile_and_concurrent_routes_are_captured_before_scheduling(tmp_path, transport):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    monitor = hooks(tmp_path)
    expected = {}
    for sid in ("A", "B"):
        home = tmp_path / sid
        expected[f"!{sid}:example.org"] = home
        token = set_hermes_home_override(home)
        try:
            bound(lambda sid=sid: monitor.on_post_api_request(session_id=sid, usage={"input_tokens": 60}),
                  sid=sid, chat=f"!{sid}:example.org", thread=f"${sid}:example.org")
        finally:
            reset_hermes_home_override(token)
    for future in transport.futures:
        assert future.result(timeout=2) is True
    assert len(transport.sent) == 2
    for args, home, loop in transport.sent:
        assert home == expected[args["chat_id"]]
        assert args["metadata"]["thread_id"] == "$" + home.name + ":example.org"


def test_no_live_adapter_never_falls_back_to_a_home_channel(tmp_path, transport, monkeypatch):
    from tools import send_message_senders
    monkeypatch.setattr(send_message_senders, "_live_adapter", lambda platform: (None, None))
    monitor = hooks(tmp_path)
    bound(lambda: monitor.on_post_api_request(session_id="A", usage={"input_tokens": 60}))
    assert not transport.futures
    assert not transport.sent
    assert monitor.ledger.get_session_stats("A").total_tokens == 60
