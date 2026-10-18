import json
import pytest
from openagent.protocol import parse_tool_envelope


def test_single_call_envelope():
    msg='{"v":1,"id":"x","tool":"shell.run","args":{"cmd":"echo ok"}}'
    batch=parse_tool_envelope(msg, allowed_tools={"shell.run"})
    assert batch.calls[0].id == "x"
    assert batch.mode == "sequential"


def test_batch_and_no_execution():
    msg=json.dumps({"v":1,"calls":[
        {"id":"1","tool":"file.read","args":{"path":"test.txt"}},
        {"id":"2","tool":"shell.run","args":{"cmd":"ls"}},
    ]})
    batch=parse_tool_envelope(msg,allowed_tools={"file.read","shell.run"})
    assert len(batch.calls) == 2


def test_unknown_tool_fails_closed():
    with pytest.raises(ValueError,match="undeclared"):
        parse_tool_envelope('{"v":1,"id":"1","tool":"danger","args":{}}',allowed_tools={"shell.run"})


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError,match="duplicate"):
        parse_tool_envelope('{"v":1,"calls":[{"id":"1","tool":"x"},{"id":"1","tool":"x"}]}',allowed_tools={"x"})


def test_oversized_payload_rejected():
    with pytest.raises(ValueError,match="1 MiB"):
        parse_tool_envelope("x"*1_048_577, allowed_tools=set())
