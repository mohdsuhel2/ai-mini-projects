import json
from unittest.mock import MagicMock, patch

import pytest

from groww_client import GrowwClient, GrowwClientError
from groww_gateway_transport import GrowwGatewayTransport


def test_gateway_transport_requires_url(monkeypatch):
    monkeypatch.delenv("GROWW_GATEWAY_URL", raising=False)
    monkeypatch.delenv("GROWW_GATEWAY_TOKEN", raising=False)
    with pytest.raises(GrowwClientError, match="GROWW_GATEWAY_URL"):
        GrowwGatewayTransport(base_url="", token="tok")


def test_gateway_transport_parses_holdings(monkeypatch):
    payload = [{"symbol": "RELIANCE", "quantity": 1, "avg_price": 100.0}]
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__ = lambda s: s
    response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=response):
        client = GrowwGatewayTransport(base_url="http://gw.test", token="secret")
        assert client.get_holdings() == payload


def test_groww_client_uses_gateway_when_configured(monkeypatch):
    monkeypatch.setenv("GROWW_GATEWAY_URL", "http://gw.test")
    monkeypatch.setenv("GROWW_GATEWAY_TOKEN", "secret")

    with patch("groww_gateway_transport.GrowwGatewayTransport") as mock_cls:
        mock_cls.return_value.ensure_session.return_value = None
        mock_cls.return_value.get_positions.return_value = []
        client = GrowwClient(mode="live")
        client.ensure_ready()
        client.get_positions()
        mock_cls.return_value.ensure_session.assert_called_once()
        mock_cls.return_value.get_positions.assert_called_once()
