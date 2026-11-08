"""
Тесты DQ-правил.

Проверяют:
  - price > 0
  - RUB (643): price > 1000
  - distance > 0
  - total_weight >= 0
  - total_volume >= 0
"""

import pytest
import pandas as pd

from etl.core import apply_dq


class TestDQRules:
    """Тестирование правил качества данных."""

    def test_price_not_positive(self, raw_prices_for_dq):
        """Строки с price <= 0 должны попасть в карантин."""
        valid, quarantine = apply_dq(raw_prices_for_dq)

        # price=0 (id=1) и price=-100 (id=3) должны быть в карантине
        quarantined_ids = set(quarantine["price_id"])
        assert 1 in quarantined_ids, "Строка с price=0 должна быть в карантине"
        assert 3 in quarantined_ids, "Строка с price<0 должна быть в карантине"

        # Проверка причины
        price_q = quarantine[quarantine["price_id"].isin([1, 3])]
        assert all(price_q["reason_code"] == "price_not_positive")

    def test_rub_price_below_threshold(self, raw_prices_for_dq):
        """Строки с RUB и price <= 1000 должны попасть в карантин."""
        valid, quarantine = apply_dq(raw_prices_for_dq)

        # id=4: price=500, currency=643 -> карантин
        # id=10: price=1000, currency=643 -> карантин
        quarantined_ids = set(quarantine["price_id"])
        assert 4 in quarantined_ids, "RUB price=500 должна быть в карантине"
        assert 10 in quarantined_ids, "RUB price=1000 должна быть в карантине"

        rub_q = quarantine[quarantine["price_id"].isin([4, 10])]
        assert all(rub_q["reason_code"] == "rub_price_below_threshold")

    def test_distance_not_positive(self, raw_prices_for_dq):
        """Строки с distance <= 0 должны попасть в карантин."""
        valid, quarantine = apply_dq(raw_prices_for_dq)

        # id=3: distance=0
        quarantined_ids = set(quarantine["price_id"])
        assert 3 in quarantined_ids

        dist_q = quarantine[quarantine["price_id"] == 3]
        assert all(dist_q["reason_code"] == "distance_not_positive")

    def test_negative_weight(self, raw_prices_for_dq):
        """Строки с total_weight < 0 должны попасть в карантин."""
        valid, quarantine = apply_dq(raw_prices_for_dq)

        # id=4: total_weight=-1
        quarantined_ids = set(quarantine["price_id"])
        assert 4 in quarantined_ids

        weight_q = quarantine[quarantine["price_id"] == 4]
        assert all(weight_q["reason_code"] == "negative_weight")

    def test_negative_volume(self, raw_prices_for_dq):
        """Строки с total_volume < 0 должны попасть в карантин."""
        valid, quarantine = apply_dq(raw_prices_for_dq)

        # id=5: total_volume=-5
        quarantined_ids = set(quarantine["price_id"])
        assert 5 in quarantined_ids

        vol_q = quarantine[quarantine["price_id"] == 5]
        assert all(vol_q["reason_code"] == "negative_volume")

    def test_valid_rows_pass_dq(self, valid_prices):
        """Валидные строки должны пройти DQ без карантина."""
        valid, quarantine = apply_dq(valid_prices)

        assert len(valid) == len(valid_prices), "Все валидные строки должны пройти"
        assert len(quarantine) == 0, "Карантин должен быть пустым"

    def test_quarantine_has_reason_codes(self, raw_prices_for_dq):
        """Карантинные строки должны иметь reason_code и reason_detail."""
        valid, quarantine = apply_dq(raw_prices_for_dq)

        assert not quarantine.empty, "Карантин не должен быть пустым"
        assert "reason_code" in quarantine.columns
        assert "reason_detail" in quarantine.columns
        assert quarantine["reason_code"].notna().all()
        assert quarantine["reason_detail"].notna().all()

    def test_no_duplicate_in_quarantine(self, raw_prices_for_dq):
        """Одна строка не должна попадать в карантин несколько раз."""
        valid, quarantine = apply_dq(raw_prices_for_dq)

        if not quarantine.empty:
            price_ids = quarantine["price_id"]
            assert price_ids.is_unique, "price_id в карантине должны быть уникальны"
