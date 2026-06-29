from __future__ import annotations

import json
import logging
import smtplib
from email.mime.text import MIMEText
from typing import Any

import requests

from .config import AlertConfig
from .types import ClosedTrade, Signal

LOG = logging.getLogger(__name__)


class AlertManager:
    def __init__(self, cfg: AlertConfig) -> None:
        self.cfg = cfg

    def notify(self, event: str, payload: dict[str, Any]) -> None:
        LOG.info("[ALERT] %s %s", event, json.dumps(payload, separators=(",", ":")))
        self._send_webhook(event, payload)
        self._send_telegram(event, payload)
        self._send_email(event, payload)

    def notify_signal(self, signal: Signal) -> None:
        payload = {
            "timestamp_utc": signal.timestamp_utc,
            "asset": signal.symbol,
            "market": signal.market.value,
            "direction": signal.direction.value,
            "entry": round(signal.entry, 8),
            "stop": round(signal.stop, 8),
            "target_1": round(signal.target_1, 8),
            "target_2": round(signal.target_2, 8),
            "regime": signal.regime.value,
            "strategy_module": signal.strategy_module.value,
            "risk_r": signal.risk_multiple,
            "notes": signal.rationale,
        }
        self.notify("ENTRY_SIGNAL", payload)

    def notify_closed_trade(self, trade: ClosedTrade, extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "asset": trade.symbol,
            "market": trade.market.value,
            "direction": trade.direction.value,
            "strategy_module": trade.strategy_module.value,
            "regime": trade.regime.value,
            "entry": round(trade.entry, 8),
            "exit": round(trade.exit_price, 8),
            "qty": round(trade.quantity, 8),
            "pnl": round(trade.pnl, 8),
            "fee": round(trade.fee_paid, 8),
            "funding": round(trade.funding_paid, 8),
            "close_reason": trade.close_reason,
            "partial_exit": trade.partial_exit,
            "opened_at_utc": trade.opened_at_utc,
            "closed_at_utc": trade.closed_at_utc,
        }
        if extra:
            payload.update(extra)
        if trade.partial_exit or trade.close_reason == "TAKE_PROFIT_1_PARTIAL":
            event = "PARTIAL_EXIT"
        elif trade.close_reason == "STOP_LOSS":
            event = "STOP_LOSS_HIT"
        else:
            event = "EXIT"
        self.notify(event, payload)

    def _send_webhook(self, event: str, payload: dict[str, Any]) -> None:
        if not self.cfg.webhook_url:
            return
        try:
            requests.post(
                self.cfg.webhook_url,
                json={"event": event, "payload": payload},
                timeout=6,
            )
        except Exception as exc:  # noqa: BLE001
            LOG.warning("webhook alert failed: %s", exc)

    def _send_telegram(self, event: str, payload: dict[str, Any]) -> None:
        if not self.cfg.telegram_bot_token or not self.cfg.telegram_chat_id:
            return
        try:
            text = f"{event}\n{json.dumps(payload, indent=2)}"
            url = f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}/sendMessage"
            requests.post(
                url,
                data={"chat_id": self.cfg.telegram_chat_id, "text": text},
                timeout=6,
            )
        except Exception as exc:  # noqa: BLE001
            LOG.warning("telegram alert failed: %s", exc)

    def _send_email(self, event: str, payload: dict[str, Any]) -> None:
        if (
            not self.cfg.email_smtp_host
            or not self.cfg.email_user
            or not self.cfg.email_password
            or not self.cfg.email_from
            or not self.cfg.email_to
        ):
            return
        try:
            body = json.dumps({"event": event, "payload": payload}, indent=2)
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = f"[Bot Alert] {event}"
            msg["From"] = self.cfg.email_from
            msg["To"] = self.cfg.email_to

            with smtplib.SMTP(self.cfg.email_smtp_host, self.cfg.email_smtp_port, timeout=10) as smtp:
                smtp.starttls()
                smtp.login(self.cfg.email_user, self.cfg.email_password)
                smtp.sendmail(self.cfg.email_from, [self.cfg.email_to], msg.as_string())
        except Exception as exc:  # noqa: BLE001
            LOG.warning("email alert failed: %s", exc)
