import pytest

from src.tls import http_client


def test_no_certificate_uses_default_trust():
    assert http_client("") is None


def test_missing_certificate_file_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="CA certificate"):
        http_client(str(tmp_path / "nope.crt"))


class _Busy(Exception):
    status_code = 429


def _reply():
    from types import SimpleNamespace as N
    return N(usage=N(prompt_tokens=3, completion_tokens=2), choices=[N(message=N(content='{"answer": "ok"}'))])


def test_busy_endpoint_is_retried_then_succeeds(monkeypatch):
    from src import config
    from src.llm import LLM

    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def create(**kw):
        calls["n"] += 1
        assert kw["max_tokens"] <= config.LLM_MAX_OUTPUT_TOKENS
        if calls["n"] < 3:
            raise _Busy("429 too many requests")
        return _reply()

    from types import SimpleNamespace as N
    llm = LLM(use_cache=False)
    llm._client = N(chat=N(completions=N(create=create)))
    text, _, _ = llm._raw([{"role": "user", "content": "x"}], {}, 5000)
    assert calls["n"] == 3 and "ok" in text


def test_persistent_busy_endpoint_is_reported(monkeypatch):
    from types import SimpleNamespace as N

    import pytest as _pt

    from src.llm import LLM, ToolCallError

    monkeypatch.setattr("time.sleep", lambda s: None)

    def create(**kw):
        raise _Busy("429 too many requests")

    llm = LLM(use_cache=False)
    llm._client = N(chat=N(completions=N(create=create)))
    with _pt.raises(ToolCallError):
        llm._raw([{"role": "user", "content": "x"}], {}, 100)


def test_rate_window_waits_when_full():
    from src.llm import RateWindow

    now = {"t": 0.0}
    slept = []

    def sleep(s):
        slept.append(s)
        now["t"] += s

    w = RateWindow(3, clock=lambda: now["t"], sleep=sleep)
    for _ in range(3):
        assert w.wait() == 0
        now["t"] += 1
    assert w.wait() > 55          # fourth call in the same minute waits for the first slot to age out
    assert len(slept) == 1


def test_rate_window_off_when_zero():
    from src.llm import RateWindow

    assert all(RateWindow(0).wait() == 0 for _ in range(100))


def test_context_length_400_is_reported_clearly_without_changing_mode():
    from types import SimpleNamespace as N

    import pytest as _pt

    from src.llm import LLM, ToolCallError

    class Bad(Exception):
        status_code = 400

    calls = {"n": 0}

    def create(**kw):
        calls["n"] += 1
        raise Bad("400 This model's maximum context length is 8192 tokens")

    llm = LLM(use_cache=False)
    mode = llm.mode
    llm._client = N(chat=N(completions=N(create=create)))
    with _pt.raises(ToolCallError, match="too large"):
        llm._raw([{"role": "user", "content": "x"}], {}, 100)
    assert calls["n"] == 1 and llm.mode == mode
