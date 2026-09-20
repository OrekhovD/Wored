"""Tests for webui.ui_presenters — UI-01 acceptance."""
import math
import pytest
from webui.ui_presenters import (
    fmt_usdt, fmt_price, fmt_pct, fmt_fraction_as_pct, fmt_pnl, fmt_qty,
    fmt_time, fmt_time_ago, state_label, present_forecast_summary, present_health,
    is_terminal, is_valid_forecast, present_deck_ui, present_preview_ui,
)


class TestFmtUSDT:
    def test_none(self):
        assert fmt_usdt(None) == '—'

    def test_nan(self):
        assert fmt_usdt(float('nan')) == '—'

    def test_inf(self):
        assert fmt_usdt(float('inf')) == '—'

    def test_zero(self):
        assert fmt_usdt(0) == '0,00 USDT'

    def test_large(self):
        assert fmt_usdt(64321.5) == '64 321,50 USDT'

    def test_small_positive(self):
        assert fmt_usdt(0.005) == '0,005 USDT'

    def test_one_cent(self):
        assert fmt_usdt(0.01) == '0,01 USDT'

    def test_tiny(self):
        r = fmt_usdt(0.000000001)
        assert r == '<0,00000001 USDT'

    def test_negative(self):
        r = fmt_usdt(-150.25)
        assert r == '-150,25 USDT'


class TestFmtPrice:
    def test_none(self):
        assert fmt_price(None) == '—'

    def test_normal(self):
        assert fmt_price(64500.5) == '64 500,50'

    def test_zero(self):
        assert fmt_price(0) == '0'


class TestFmtPct:
    def test_none(self):
        assert fmt_pct(None) == '—'

    def test_normal(self):
        assert fmt_pct(2.5) == '2,50 %'


class TestFmtFractionAsPct:
    def test_none(self):
        assert fmt_fraction_as_pct(None) == '—'

    def test_normal(self):
        assert fmt_fraction_as_pct(0.025) == '2,50 %'


class TestFmtPnL:
    def test_positive(self):
        assert fmt_pnl(125.5) == '+125,50 USDT'

    def test_negative(self):
        assert fmt_pnl(-50.0) == '-50,00 USDT'

    def test_zero(self):
        assert fmt_pnl(0) == '0,00 USDT'

    def test_none(self):
        assert fmt_pnl(None) == '—'


class TestFmtQty:
    def test_none(self):
        assert fmt_qty(None) == '—'

    def test_btc(self):
        assert fmt_qty(0.000155642023, 'BTC') == '0,00015564 BTC'


class TestFmtTime:
    def test_none(self):
        assert fmt_time(None) == '—'

    def test_empty(self):
        assert fmt_time('') == '—'

    def test_iso(self):
        # b083857: fmt_time displays Asia/Bangkok (UTC+7); 16:30Z -> 23:30
        assert fmt_time('2026-09-09T16:30:00Z') == '09.09 23:30'


class TestStateLabel:
    def test_known(self):
        r = state_label('completed')
        assert r['label'] == 'Расчёт завершён'
        assert r['css_class'] == 'ui-status-completed'

    def test_legacy_pending(self):
        r = state_label(legacy_status='pending')
        assert r['css_class'] == 'ui-status-queued'

    def test_unknown(self):
        r = state_label('something_else')
        assert r['label'] == 'Состояние уточняется'


class TestIsTerminal:
    def test_completed(self):
        assert is_terminal('completed') is True

    def test_failed(self):
        assert is_terminal('failed') is True

    def test_expired(self):
        assert is_terminal('expired') is True

    def test_queued(self):
        assert is_terminal('queued') is False

    def test_running(self):
        assert is_terminal('running') is False

    def test_legacy_failed(self):
        assert is_terminal(None, legacy_status='failed') is True

    def test_none(self):
        assert is_terminal(None) is False


class TestIsValidForecast:
    def test_no_valid_until(self):
        assert is_valid_forecast(None) is False

    def test_future(self):
        assert is_valid_forecast('2026-12-31T23:59:59Z', now_iso='2026-09-09T12:00:00Z') is True

    def test_past(self):
        assert is_valid_forecast('2026-01-01T00:00:00Z', now_iso='2026-09-09T12:00:00Z') is False

    def test_invalid_string(self):
        assert is_valid_forecast('not-a-date') is False


class TestPresentForecastSummary:
    def test_basic(self):
        f = {'run_id': 1, 'execution_state': 'completed',
             'predicted_price': 64500.5, 'target_price': 64600.0,
             'target_time': '2026-09-09T16:30:00Z'}
        r = present_forecast_summary(f)
        assert r['state_label'] == 'Расчёт завершён'
        assert r['predicted_price_fmt'] == '64 500,50'


class TestPresentHealth:
    def test_all_ok(self):
        r = present_health({'postgres': True, 'redis': True, 'collector_feed': True})
        assert r['all_ok'] is True

    def test_broken(self):
        r = present_health({'postgres': True, 'redis': False, 'collector_feed': True})
        assert r['all_ok'] is False


class TestPresentDeckUI:
    def test_empty(self):
        r = present_deck_ui([], {}, [], {}, {}, {})
        assert r['ui_schema_version'] == 1
        assert r['ui']['forecast'] is None
        assert r['ui']['market']['symbol'] == 'btcusdt'

    def test_with_data(self):
        r = present_deck_ui(
            market=[{'symbol': 'btcusdt', 'fetched_at': '2026-09-09T12:00:00Z', 'fresh': True}],
            consensus={'request_id': 9001, 'created_at': '2026-09-09T11:00:00Z',
                       'roles': [{'role': 'bull'}], 'candles': []},
            positions=[{'id': 1, 'symbol': 'btcusdt', 'direction': 'long'}],
            session={'id': 's1'},
            accuracy={'model_a': {'total': 50}},
            health={'postgres': True, 'redis': True, 'forecast_worker': True},
        )
        assert r['ui']['market']['symbol'] == 'btcusdt'
        assert r['ui']['forecast']['request_id'] == 9001
        assert r['ui']['actions']['forecast']['allowed'] is True
        assert r['ui']['metrics']['available'] is True

    def test_insufficient_samples(self):
        r = present_deck_ui([], {}, [], {}, {'model_a': {'total': 10}}, {})
        assert r['ui']['metrics']['available'] is False
        assert r['ui']['metrics']['reason_code'] == 'insufficient_independent_samples'

    def test_no_forecast_worker(self):
        r = present_deck_ui([], {}, [], {}, {}, {'postgres': True, 'redis': True, 'forecast_worker': False})
        assert r['ui']['actions']['forecast']['allowed'] is False


class TestPresentPreviewUI:
    def test_basic(self):
        r = present_preview_ui({'taker_fee': 0.5, 'scenarios': {}})
        assert r['ui']['preview']['entry_fee'] == 0.5
        assert r['ui']['preview']['scenarios'] == []

    def test_with_scenarios(self):
        r = present_preview_ui({
            'scenarios': {'-2%': {'price': 60000, 'net_pnl': -10, 'liquidation_crossed': True}},
        })
        assert len(r['ui']['preview']['scenarios']) == 1
        assert r['ui']['preview']['scenarios'][0]['label'] == '-2%'
        assert r['ui']['preview']['scenarios'][0]['liquidation_crossed'] is True