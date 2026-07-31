"""Groww API gateway — runs on the VPS static IP so autoIntraday on any WiFi can trade.

Groww credentials (GROWW_API_KEY, GROWW_TOTP_SECRET) live only on this server.
Clients authenticate with GROWW_GATEWAY_TOKEN (Bearer).
"""
from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import groww_client
from groww_client import GrowwClient, GrowwClientError

_GATEWAY_TOKEN = os.environ.get("GROWW_GATEWAY_TOKEN", "").strip()
_client_lock = threading.Lock()
_client: Optional[GrowwClient] = None


def _require_gateway_token(authorization: Optional[str] = Header(default=None)) -> None:
    if not _GATEWAY_TOKEN:
        raise HTTPException(status_code=503, detail="GROWW_GATEWAY_TOKEN is not configured on the server")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != _GATEWAY_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid gateway token")


def _get_live_client() -> GrowwClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = GrowwClient(mode="live")
        try:
            _client.ensure_ready()
        except GrowwClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return _client


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Lazy auth on first broker request — do not authenticate at startup (restarts would
    # hammer Groww TOTP and trip the rate limit while the container is crash-looping).
    yield
    global _client
    with _client_lock:
        _client = None


app = FastAPI(title="Groww Gateway", version="1.0.0", lifespan=lifespan)


@app.exception_handler(GrowwClientError)
async def _groww_error_handler(_request: Request, exc: GrowwClientError) -> JSONResponse:
    """Translate broker/SDK errors into a diagnosable HTTP 502 carrying the real message, instead
    of an opaque 500. The transport reads {"detail": ...} from the body, so cancel/margin/ltp
    failures now surface their actual cause to the client and the orchestrator's logs."""
    msg = str(exc)
    if groww_client.is_token_invalid_error(msg):    # Groww rejected the token (not a rate-limit)
        groww_client._clear_cached_token()          # stale token -> mint a fresh one next request
        global _client
        with _client_lock:
            _client = None
    return JSONResponse(status_code=502, content={"detail": msg})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/ready", dependencies=[Depends(_require_gateway_token)])
def ready() -> dict[str, str]:
    _get_live_client()
    return {"status": "ready", "mode": "live"}


class PlaceOrderBody(BaseModel):
    symbol: str
    exchange: str = "NSE"
    transaction_type: str
    quantity: int
    order_type: str
    price: Optional[float] = None
    product: str = "MIS"
    trigger_price: Optional[float] = None


class OcoLeg(BaseModel):
    trigger_price: float
    order_type: str = "LIMIT"
    price: Optional[float] = None


class PlaceOcoBody(BaseModel):
    symbol: str
    entry: dict[str, Any]
    target: dict[str, Any]
    stop_loss: dict[str, Any]


class ModifyOcoBody(BaseModel):
    target: float
    stop_loss: float


@app.get("/v1/ltp", dependencies=[Depends(_require_gateway_token)])
def get_ltp(symbols: str) -> dict[str, float]:
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="symbols query param required")
    return _get_live_client().get_ltp(symbol_list)


@app.get("/v1/quote/{symbol}", dependencies=[Depends(_require_gateway_token)])
def get_quote(symbol: str) -> dict[str, Any]:
    return _get_live_client().get_quote(symbol.upper())


@app.get("/v1/candles", dependencies=[Depends(_require_gateway_token)])
def get_candles(symbol: str, start: str, end: str, interval: int = 1) -> dict[str, Any]:
    """Historical OHLCV candles — the ONLY way to seed the Live Intraday indicators at open.

    The GrowwFeed WebSocket carries LTP/depth/order updates but no candles, so a fresh session
    has no history to compute VWAP, EMA or ATR from until it has polled for an hour. This fills
    that gap. Groww limits: 1-min <= 7 days per request and 3 months back.

    start/end are 'YYYY-MM-DD HH:mm:ss' IST. Returns {"candles": [[epoch,o,h,l,c,v], ...]}.
    """
    return _get_live_client().get_historical_candles(
        symbol.upper(), start_time=start, end_time=end, interval_minutes=interval)


@app.get("/v1/holdings", dependencies=[Depends(_require_gateway_token)])
def get_holdings() -> list[dict]:
    return _get_live_client().get_holdings()


@app.get("/v1/positions", dependencies=[Depends(_require_gateway_token)])
def get_positions() -> list[dict]:
    return _get_live_client().get_positions()


@app.get("/v1/orders/open", dependencies=[Depends(_require_gateway_token)])
def get_open_orders() -> list[dict]:
    return _get_live_client().get_open_orders()


@app.get("/v1/margin", dependencies=[Depends(_require_gateway_token)])
def get_margin() -> dict:
    return _get_live_client().get_margin()


@app.post("/v1/orders", dependencies=[Depends(_require_gateway_token)])
def place_order(body: PlaceOrderBody) -> dict:
    return _get_live_client().place_order(
        symbol=body.symbol,
        exchange=body.exchange,
        transaction_type=body.transaction_type,
        quantity=body.quantity,
        order_type=body.order_type,
        price=body.price,
        product=body.product,
        trigger_price=body.trigger_price,
    )


@app.get("/v1/orders/{order_id}/status", dependencies=[Depends(_require_gateway_token)])
def get_order_status(order_id: str) -> dict:
    return _get_live_client().get_order_status(order_id)


@app.post("/v1/orders/{order_id}/cancel", dependencies=[Depends(_require_gateway_token)])
def cancel_order(order_id: str) -> dict:
    return _get_live_client().cancel_order(order_id)


@app.post("/v1/orders/oco", dependencies=[Depends(_require_gateway_token)])
def place_oco(body: PlaceOcoBody) -> dict:
    return _get_live_client().place_oco_order(
        symbol=body.symbol,
        entry=body.entry,
        target=body.target,
        stop_loss=body.stop_loss,
    )


@app.post("/v1/orders/oco/{order_id}/modify", dependencies=[Depends(_require_gateway_token)])
def modify_oco(order_id: str, body: ModifyOcoBody) -> dict:
    return _get_live_client().modify_oco_order(order_id, body.target, body.stop_loss)


@app.post("/v1/orders/oco/{order_id}/cancel", dependencies=[Depends(_require_gateway_token)])
def cancel_oco(order_id: str) -> dict:
    return _get_live_client().cancel_oco_order(order_id)


@app.get("/v1/orders/oco/{order_id}/status", dependencies=[Depends(_require_gateway_token)])
def get_oco_status(order_id: str) -> dict:
    return _get_live_client().get_smart_order_status(order_id)
