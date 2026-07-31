"""HTTP client for the VPS Groww gateway (static-IP broker relay)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from groww_client import GrowwClientError


class GrowwGatewayTransport:
    """Talks to groww_gateway.app on the VPS instead of the growwapi SDK locally."""

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None):
        self.base_url = (base_url or os.environ.get("GROWW_GATEWAY_URL", "")).rstrip("/")
        self.token = (token or os.environ.get("GROWW_GATEWAY_TOKEN", "")).strip()
        if not self.base_url:
            raise GrowwClientError("GROWW_GATEWAY_URL must be set for gateway mode")
        if not self.token:
            raise GrowwClientError("GROWW_GATEWAY_TOKEN must be set for gateway mode")

    def ensure_session(self) -> None:
        self._request("GET", "/v1/ready")

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        query = urllib.parse.urlencode({"symbols": ",".join(symbols)})
        return self._request("GET", f"/v1/ltp?{query}")

    def get_quote(self, symbol: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/quote/{urllib.parse.quote(symbol)}")

    def get_historical_candles(self, symbol: str, start_time: str, end_time: str,
                               interval_minutes: int = 1) -> dict[str, Any]:
        query = urllib.parse.urlencode({"symbol": symbol, "start": start_time,
                                        "end": end_time, "interval": interval_minutes})
        return self._request("GET", f"/v1/candles?{query}")

    def get_holdings(self) -> list[dict]:
        return self._request("GET", "/v1/holdings")

    def get_positions(self) -> list[dict]:
        return self._request("GET", "/v1/positions")

    def get_open_orders(self) -> list[dict]:
        return self._request("GET", "/v1/orders/open")

    def get_margin(self) -> dict:
        return self._request("GET", "/v1/margin")

    def place_order(
        self,
        symbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        order_type: str,
        price: Optional[float] = None,
        product: str = "MIS",
        trigger_price: Optional[float] = None,
    ) -> dict:
        return self._request(
            "POST",
            "/v1/orders",
            {
                "symbol": symbol,
                "exchange": exchange,
                "transaction_type": transaction_type,
                "quantity": quantity,
                "order_type": order_type,
                "price": price,
                "product": product,
                "trigger_price": trigger_price,
            },
        )

    def get_order_status(self, order_id: str) -> dict:
        return self._request("GET", f"/v1/orders/{urllib.parse.quote(order_id)}/status")

    def cancel_order(self, order_id: str) -> dict:
        return self._request("POST", f"/v1/orders/{urllib.parse.quote(order_id)}/cancel")

    def place_oco_order(self, symbol: str, entry: dict, target: dict, stop_loss: dict) -> dict:
        return self._request(
            "POST",
            "/v1/orders/oco",
            {"symbol": symbol, "entry": entry, "target": target, "stop_loss": stop_loss},
        )

    def modify_oco_order(self, order_id: str, target: float, stop_loss: float) -> dict:
        return self._request(
            "POST",
            f"/v1/orders/oco/{urllib.parse.quote(order_id)}/modify",
            {"target": target, "stop_loss": stop_loss},
        )

    def cancel_oco_order(self, order_id: str) -> dict:
        return self._request("POST", f"/v1/orders/oco/{urllib.parse.quote(order_id)}/cancel")

    def get_smart_order_status(self, order_id: str) -> dict:
        return self._request("GET", f"/v1/orders/oco/{urllib.parse.quote(order_id)}/status")

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{path}"
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(detail)
                detail = parsed.get("detail", detail)
            except json.JSONDecodeError:
                pass
            raise GrowwClientError(f"gateway {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise GrowwClientError(f"gateway unreachable: {exc.reason}") from exc
