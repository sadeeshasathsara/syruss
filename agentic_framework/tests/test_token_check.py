"""An event is only worked on if its token is genuine and is for exactly that event."""
import pytest
from agentic_framework import token_check
from tests.conftest import make_event, sign_token


def test_a_genuine_token_for_its_own_event_passes():
    token_check.verify(make_event("document.submitted", {}))
    token_check.verify(make_event("human.message", {}, actor={"type": "user", "id": "usr_1"}))


def test_a_token_for_another_workspace_event_or_person_is_refused():
    event = make_event("document.submitted", {})
    for forged in (sign_token("t2", "tpl1", "evt1"), sign_token("t1", "tpl2", "evt1"), sign_token("t1", "tpl1", "evt_other"),
                   sign_token("t1", "tpl1", "evt1", actor_id="usr_someone_else")):
        with pytest.raises(token_check.BadToken, match="different"):
            token_check.verify(event.model_copy(update={"agent_token": forged}))


def test_expired_unsigned_and_foreign_signed_tokens_are_refused():
    event = make_event("document.submitted", {})
    for bad in (sign_token("t1", "tpl1", "evt1", ttl=-10), "garbage", sign_token("t1", "tpl1", "evt1", secret="attacker-secret")):
        with pytest.raises(token_check.BadToken):
            token_check.verify(event.model_copy(update={"agent_token": bad}))
