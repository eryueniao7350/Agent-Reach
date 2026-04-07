# -*- coding: utf-8 -*-
"""Tests for FxiaokeChannel."""

import json
import unittest
from unittest.mock import MagicMock, patch


class TestFxiaokeChannelCanHandle(unittest.TestCase):
    def setUp(self):
        from agent_reach.channels.fxiaoke import FxiaokeChannel
        self.ch = FxiaokeChannel()

    def test_matches_fxiaoke_url(self):
        self.assertTrue(self.ch.can_handle("https://www.fxiaoke.com/XV/UI/Home#crm/index"))

    def test_matches_subdomain(self):
        self.assertTrue(self.ch.can_handle("https://open.fxiaoke.com/wiki.html"))

    def test_does_not_match_other(self):
        self.assertFalse(self.ch.can_handle("https://github.com/some/repo"))

    def test_does_not_match_empty(self):
        self.assertFalse(self.ch.can_handle(""))


class _FakeConfig:
    """Minimal config stub."""

    def __init__(self, data: dict):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


class TestFxiaokeChannelCheck(unittest.TestCase):
    def setUp(self):
        from agent_reach.channels import fxiaoke as mod
        mod._token_cache.clear()
        from agent_reach.channels.fxiaoke import FxiaokeChannel
        self.ch = FxiaokeChannel()
        self.mod = mod

    def test_check_missing_config_returns_warn(self):
        cfg = _FakeConfig({})
        status, msg = self.ch.check(cfg)
        self.assertEqual(status, "warn")
        self.assertIn("未配置", msg)

    def test_check_partial_config_returns_warn(self):
        cfg = _FakeConfig({"fxiaoke_app_id": "FSAID_test"})
        status, msg = self.ch.check(cfg)
        self.assertEqual(status, "warn")

    def test_check_ok_when_token_succeeds(self):
        cfg = _FakeConfig({
            "fxiaoke_app_id": "FSAID_test",
            "fxiaoke_app_secret": "secret",
            "fxiaoke_permanent_code": "code123",
            "fxiaoke_user_id": "UID001",
        })
        mock_resp = {
            "errorCode": 0,
            "accessToken": "TOKEN_ABC",
            "ea": "corp001",
            "expiresIn": 7200,
        }
        with patch.object(self.mod, "_post_json", return_value=mock_resp):
            status, msg = self.ch.check(cfg)
        self.assertEqual(status, "ok")
        self.assertIn("Open API", msg)

    def test_check_warn_when_auth_fails(self):
        cfg = _FakeConfig({
            "fxiaoke_app_id": "FSAID_bad",
            "fxiaoke_app_secret": "bad",
            "fxiaoke_permanent_code": "bad",
            "fxiaoke_user_id": "UID001",
        })
        mock_resp = {"errorCode": 20001, "errorMessage": "invalid credentials"}
        with patch.object(self.mod, "_post_json", return_value=mock_resp):
            status, msg = self.ch.check(cfg)
        self.assertEqual(status, "warn")
        self.assertIn("认证失败", msg)


class TestFxiaokeChannelData(unittest.TestCase):
    def setUp(self):
        from agent_reach.channels import fxiaoke as mod
        mod._token_cache.clear()
        from agent_reach.channels.fxiaoke import FxiaokeChannel
        self.ch = FxiaokeChannel()
        self.mod = mod
        self.cfg = _FakeConfig({
            "fxiaoke_app_id": "FSAID_test",
            "fxiaoke_app_secret": "secret",
            "fxiaoke_permanent_code": "code123",
            "fxiaoke_user_id": "UID001",
        })
        # Pre-populate token cache to skip real auth calls
        mod._token_cache.store("TOKEN_ABC", "corp001", 7200)

    def test_get_object_returns_data(self):
        mock_resp = {
            "errorCode": 0,
            "data": {"_id": "OBJ001", "name": "测试客户"},
        }
        with patch.object(self.mod, "_post_json", return_value=mock_resp):
            result = self.ch.get_object("AccountObj", "OBJ001", config=self.cfg)
        self.assertEqual(result["name"], "测试客户")

    def test_get_object_raises_on_error(self):
        mock_resp = {"errorCode": 40001, "errorMessage": "not found"}
        with patch.object(self.mod, "_post_json", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.ch.get_object("AccountObj", "MISSING", config=self.cfg)
        self.assertIn("查询失败", str(ctx.exception))

    def test_search_objects_returns_list(self):
        mock_resp = {
            "errorCode": 0,
            "data": {"dataList": [{"_id": "A1", "name": "客户A"}, {"_id": "A2", "name": "客户B"}]},
        }
        with patch.object(self.mod, "_post_json", return_value=mock_resp):
            results = self.ch.search_objects("AccountObj", config=self.cfg)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["name"], "客户A")

    def test_search_objects_empty_result(self):
        mock_resp = {"errorCode": 0, "data": {"dataList": []}}
        with patch.object(self.mod, "_post_json", return_value=mock_resp):
            results = self.ch.search_objects("AccountObj", config=self.cfg)
        self.assertEqual(results, [])

    def test_search_objects_raises_on_error(self):
        mock_resp = {"errorCode": 50000, "errorMessage": "server error"}
        with patch.object(self.mod, "_post_json", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.ch.search_objects("AccountObj", config=self.cfg)
        self.assertIn("搜索失败", str(ctx.exception))

    def test_list_customers_calls_account_obj(self):
        mock_resp = {"errorCode": 0, "data": {"dataList": [{"_id": "C1"}]}}
        with patch.object(self.mod, "_post_json", return_value=mock_resp) as mock_post:
            self.ch.list_customers(limit=5, config=self.cfg)
        call_payload = mock_post.call_args[0][1]
        self.assertEqual(call_payload["data"]["dataObjectApiName"], "AccountObj")
        self.assertEqual(call_payload["data"]["search"]["limit"], 5)

    def test_list_contacts(self):
        mock_resp = {"errorCode": 0, "data": {"dataList": []}}
        with patch.object(self.mod, "_post_json", return_value=mock_resp) as mock_post:
            self.ch.list_contacts(config=self.cfg)
        call_payload = mock_post.call_args[0][1]
        self.assertEqual(call_payload["data"]["dataObjectApiName"], "ContactObj")

    def test_list_leads(self):
        mock_resp = {"errorCode": 0, "data": {"dataList": []}}
        with patch.object(self.mod, "_post_json", return_value=mock_resp) as mock_post:
            self.ch.list_leads(config=self.cfg)
        call_payload = mock_post.call_args[0][1]
        self.assertEqual(call_payload["data"]["dataObjectApiName"], "SalesClueObj")

    def test_list_opportunities(self):
        mock_resp = {"errorCode": 0, "data": {"dataList": []}}
        with patch.object(self.mod, "_post_json", return_value=mock_resp) as mock_post:
            self.ch.list_opportunities(config=self.cfg)
        call_payload = mock_post.call_args[0][1]
        self.assertEqual(call_payload["data"]["dataObjectApiName"], "LeadsObj")


class TestTokenCache(unittest.TestCase):
    def test_cache_validity(self):
        from agent_reach.channels.fxiaoke import _TokenCache
        cache = _TokenCache()
        self.assertFalse(cache.is_valid())
        cache.store("TOKEN", "ea1", 7200)
        self.assertTrue(cache.is_valid())
        cache.clear()
        self.assertFalse(cache.is_valid())

    def test_cache_expires_at_threshold(self):
        import time
        from agent_reach.channels.fxiaoke import _TokenCache, _TOKEN_REFRESH_THRESHOLD
        cache = _TokenCache()
        cache.store("TOKEN", "ea1", 7200)
        # expires_at should be ~6600s from now (capped at threshold)
        self.assertAlmostEqual(
            cache.expires_at, time.time() + _TOKEN_REFRESH_THRESHOLD, delta=2
        )


if __name__ == "__main__":
    unittest.main()
