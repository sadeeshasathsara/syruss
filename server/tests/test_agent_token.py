import pytest
from app.core.agent_token import mint_agent_token, verify_agent_token


def test_round_trip():
    scope = verify_agent_token(mint_agent_token("t1", "tpl1", "e1"))
    assert (scope.tenant_id, scope.template_id, scope.event_id) == ("t1", "tpl1", "e1")


def test_tampered_scope_is_rejected():
    token = mint_agent_token("t1", "tpl1", "e1")
    other = mint_agent_token("t1", "tpl2", "e1")
    forged = other.split(".")[0] + "." + token.split(".")[1]
    with pytest.raises(ValueError):
        verify_agent_token(forged)


def test_expired_token_is_rejected():
    with pytest.raises(ValueError, match="expired"):
        verify_agent_token(mint_agent_token("t1", "tpl1", "e1", ttl_seconds=-1))
