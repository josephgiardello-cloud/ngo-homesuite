from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from ngo_homesuite.db.audit_log import log_event

# Service layer for bank reconciliation operations

class BankReconciliationService:
    def reconcile(self, bank_statement, ledger, actor=None):
        log_event('ngo_data.db', actor or 'system', 'reconcile', 'bank_reconciliation', {'bank_statement': str(bank_statement), 'ledger': str(ledger)})
        if isinstance(bank_statement, str) or isinstance(ledger, str):
            return {
                "mode": "reference_only",
                "status": "queued",
                "bank_statement_ref": str(bank_statement),
                "ledger_ref": str(ledger),
                "matched_count": 0,
                "unmatched_bank_count": 0,
                "unmatched_ledger_count": 0,
            }

        bank_entries = self._normalize_entries(bank_statement)
        ledger_entries = self._normalize_entries(ledger)

        by_txid_matched, remaining_bank, remaining_ledger = self._match_by_txid(bank_entries, ledger_entries)
        by_amount_matched, unmatched_bank, unmatched_ledger = self._match_by_amount(remaining_bank, remaining_ledger)

        matched_entries = by_txid_matched + by_amount_matched
        matched_total = self._sum_amounts(matched_entries)
        unmatched_bank_total = self._sum_amounts(unmatched_bank)
        unmatched_ledger_total = self._sum_amounts(unmatched_ledger)

        return {
            "mode": "structured",
            "status": "balanced" if not unmatched_bank and not unmatched_ledger else "mismatch",
            "matched_count": len(matched_entries),
            "matched_total": matched_total,
            "unmatched_bank_count": len(unmatched_bank),
            "unmatched_bank_total": unmatched_bank_total,
            "unmatched_ledger_count": len(unmatched_ledger),
            "unmatched_ledger_total": unmatched_ledger_total,
            "unmatched_bank": [self._render_entry(entry) for entry in unmatched_bank],
            "unmatched_ledger": [self._render_entry(entry) for entry in unmatched_ledger],
        }

    @staticmethod
    def _to_amount(value: Any) -> Decimal:
        try:
            amount = Decimal(str(value))
            if not amount.is_finite() or amount != amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):
                raise ValueError("Amount must be finite with at most two decimals")
            return amount.quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid reconciliation amount: {value!r}") from exc

    def _normalize_entries(self, values: Any) -> list[dict[str, Any]]:
        if not isinstance(values, list):
            raise ValueError("Structured reconciliation expects list inputs for bank_statement and ledger")

        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(values):
            if isinstance(raw, dict):
                amount_raw = raw.get("amount")
                raw_tx_id = raw.get("tx_id")
                tx_id = str(raw_tx_id).strip() if raw_tx_id is not None else None
                tx_id = tx_id or None
                source = raw.get("source")
            else:
                amount_raw = raw
                tx_id = None
                source = None

            amount = self._to_amount(amount_raw)
            normalized.append({"amount": amount, "tx_id": tx_id, "source": source, "index": idx})

        return normalized

    @staticmethod
    def _match_by_txid(
        bank_entries: list[dict[str, Any]],
        ledger_entries: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        ledger_by_txid: dict[str, list[dict[str, Any]]] = {}
        for entry in ledger_entries:
            tx_id = entry.get("tx_id")
            if tx_id:
                ledger_by_txid.setdefault(tx_id, []).append(entry)

        matched: list[dict[str, Any]] = []
        remaining_bank: list[dict[str, Any]] = []
        consumed_ledger_ids: set[int] = set()

        for bank_entry in bank_entries:
            tx_id = bank_entry.get("tx_id")
            if not tx_id:
                remaining_bank.append(bank_entry)
                continue

            candidates = ledger_by_txid.get(tx_id, [])
            hit = next((item for item in candidates if id(item) not in consumed_ledger_ids and item["amount"] == bank_entry["amount"]), None)
            if hit is None:
                remaining_bank.append(bank_entry)
                continue

            consumed_ledger_ids.add(id(hit))
            matched.append(bank_entry)

        remaining_ledger = [entry for entry in ledger_entries if id(entry) not in consumed_ledger_ids]
        return matched, remaining_bank, remaining_ledger

    @staticmethod
    def _match_by_amount(
        bank_entries: list[dict[str, Any]],
        ledger_entries: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        remaining_ledger = list(ledger_entries)
        disputed_ids = (
            {entry["tx_id"] for entry in bank_entries if entry["tx_id"]}
            & {entry["tx_id"] for entry in ledger_entries if entry["tx_id"]}
        )
        matched: list[dict[str, Any]] = []
        unmatched_bank: list[dict[str, Any]] = []

        # Identified bank entries have fewer eligible partners; match them first.
        for bank_entry in sorted(bank_entries, key=lambda entry: not bool(entry["tx_id"])):
            if bank_entry["tx_id"] in disputed_ids:
                unmatched_bank.append(bank_entry)
                continue
            hit = next((index for index, entry in enumerate(remaining_ledger)
                        if entry["amount"] == bank_entry["amount"]
                        and entry["tx_id"] not in disputed_ids
                        and not (entry["tx_id"] and bank_entry["tx_id"])), None)
            if hit is not None:
                remaining_ledger.pop(hit)
                matched.append(bank_entry)
            else:
                unmatched_bank.append(bank_entry)

        return matched, unmatched_bank, remaining_ledger

    @staticmethod
    def _sum_amounts(entries: list[dict[str, Any]]) -> float:
        total = sum((entry["amount"] for entry in entries), start=Decimal("0.00"))
        return float(total)

    @staticmethod
    def _render_entry(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "amount": float(entry["amount"]),
            "tx_id": entry.get("tx_id"),
            "index": entry.get("index"),
            "source": entry.get("source"),
        }
