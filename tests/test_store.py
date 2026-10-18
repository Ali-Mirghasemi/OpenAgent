import pytest
from openagent.models import Message, ProviderOutput
from openagent.store import SessionBusy, SessionStore


def test_pending_turn_survives_restart(tmp_path):
    path=tmp_path/"sessions.db"
    db=SessionStore(path)
    sid=db.create("mock/echo")["id"]
    assert db.claim_turn(sid,"req1",Message(role="user",content="hello")) is None
    db.close()
    restarted=SessionStore(path)
    with pytest.raises(SessionBusy,match="manual reconciliation"):
        restarted.claim_turn(sid,"req1",Message(role="user",content="hello"))
    with pytest.raises(SessionBusy,match="unresolved"):
        restarted.claim_turn(sid,"req2",Message(role="user",content="hello"))
    restarted.close()


def test_cached_turn(store):
    sid=store.create("mock/echo")["id"]
    store.claim_turn(sid,"r1",Message(role="user",content="hi"))
    store.complete_turn(sid,"r1",Message(role="user",content="hi"),ProviderOutput(content="hey"))
    cached=store.claim_turn(sid,"r1",Message(role="user",content="hi"))
    assert cached["content"]=="hey"
    assert len(store.get(sid)["messages"])==2


def test_request_id_cannot_be_reused_for_different_content(store):
    sid=store.create("mock/echo")["id"]
    store.claim_turn(sid,"same-id",Message(role="user",content="first"))
    store.complete_turn(sid,"same-id",Message(role="user",content="first"),ProviderOutput(content="ok"))
    with pytest.raises(SessionBusy,match="different content"):
        store.claim_turn(sid,"same-id",Message(role="user",content="changed"))


def test_ambiguous_submission_blocks_new_turns(store):
    sid=store.create("mock/echo")["id"]
    store.claim_turn(sid,"old",Message(role="user",content="first"))
    store.fail_turn(sid,"old")
    with pytest.raises(SessionBusy,match="unresolved"):
        store.claim_turn(sid,"next",Message(role="user",content="continue"))
