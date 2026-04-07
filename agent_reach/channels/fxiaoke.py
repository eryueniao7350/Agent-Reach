# -*- coding: utf-8 -*-
"""Fxiaoke (纷享销客) CRM — read and search CRM data via Open API.

Authentication: client-credentials mode (app_secret grant).
Required config keys (in ~/.agent-reach/config.yaml or env vars):
  fxiaoke_app_id       — AppId  (FSAID_xxxxx)
  fxiaoke_app_secret   — AppSecret
  fxiaoke_permanent_code — 永久授权码
  fxiaoke_user_id      — 员工ID (x-fs-userid)
  fxiaoke_domain       — optional, defaults to www.fxiaoke.com
"""

import json
import time
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .base import Channel

_TOKEN_REFRESH_THRESHOLD = 6600  # seconds — refresh before expiry


class _TokenCache:
    """Module-level token cache to avoid redundant auth requests."""

    def __init__(self):
        self.access_token: Optional[str] = None
        self.ea: Optional[str] = None
        self.expires_at: float = 0.0

    def is_valid(self) -> bool:
        return (
            self.access_token is not None
            and time.time() < self.expires_at
        )

    def store(self, access_token: str, ea: str, expires_in: int) -> None:
        self.access_token = access_token
        self.ea = ea
        self.expires_at = time.time() + min(expires_in, _TOKEN_REFRESH_THRESHOLD)

    def clear(self) -> None:
        self.access_token = None
        self.ea = None
        self.expires_at = 0.0


_token_cache = _TokenCache()


def _trace_id() -> str:
    return str(uuid.uuid4())


def _post_json(url: str, payload: dict, headers: Optional[Dict[str, str]] = None) -> Any:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            **(headers or {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_token(app_id: str, app_secret: str, permanent_code: str, domain: str) -> Tuple[str, str]:
    """Fetch a fresh access token. Returns (access_token, ea)."""
    url = f"https://{domain}/oauth2.0/token?thirdTraceId={_trace_id()}"
    data = _post_json(url, {
        "appId": app_id,
        "appSecret": app_secret,
        "permanentCode": permanent_code,
        "grantType": "app_secret",
    })
    if data.get("errorCode") != 0:
        raise RuntimeError(
            f"纷享销客获取 token 失败：{data.get('errorMessage')} (code {data.get('errorCode')})"
        )
    return data["accessToken"], data["ea"]


def _ensure_token(config) -> Tuple[str, str]:
    """Return (access_token, ea), refreshing if necessary."""
    if _token_cache.is_valid():
        return _token_cache.access_token, _token_cache.ea  # type: ignore[return-value]

    app_id = config.get("fxiaoke_app_id")
    app_secret = config.get("fxiaoke_app_secret")
    permanent_code = config.get("fxiaoke_permanent_code")
    domain = config.get("fxiaoke_domain") or "www.fxiaoke.com"

    if not all([app_id, app_secret, permanent_code]):
        raise RuntimeError(
            "缺少纷享销客配置（fxiaoke_app_id / fxiaoke_app_secret / fxiaoke_permanent_code）"
        )

    access_token, ea = _get_token(app_id, app_secret, permanent_code, domain)
    _token_cache.store(access_token, ea, 7200)
    return access_token, ea


def _api_headers(config) -> Dict[str, str]:
    access_token, ea = _ensure_token(config)
    user_id = config.get("fxiaoke_user_id", "")
    return {
        "Authorization": f"Bearer {access_token}",
        "x-fs-ea": ea,
        "x-fs-userid": user_id,
    }


def _api_url(config, path: str) -> str:
    domain = config.get("fxiaoke_domain") or "www.fxiaoke.com"
    return f"https://{domain}{path}?thirdTraceId={_trace_id()}"


class FxiaokeChannel(Channel):
    """纷享销客 CRM — 客户、联系人、线索、商机等对象的读取与搜索。"""

    name = "fxiaoke"
    description = "纷享销客 CRM（客户/联系人/商机）"
    backends = ["Fxiaoke Open API"]
    tier = 2

    def can_handle(self, url: str) -> bool:
        return "fxiaoke.com" in urllib.parse.urlparse(url).netloc.lower()

    # ------------------------------------------------------------------ #
    # Health check
    # ------------------------------------------------------------------ #

    def check(self, config=None) -> Tuple[str, str]:
        if config is None:
            from ..config import Config
            config = Config()

        app_id = config.get("fxiaoke_app_id")
        app_secret = config.get("fxiaoke_app_secret")
        permanent_code = config.get("fxiaoke_permanent_code")

        if not all([app_id, app_secret, permanent_code]):
            return (
                "warn",
                "未配置。在纷享销客管理后台创建企业自建应用后，运行：\n"
                "  agent-reach configure fxiaoke_app_id=FSAID_xxx "
                "fxiaoke_app_secret=xxx fxiaoke_permanent_code=xxx fxiaoke_user_id=xxx",
            )

        try:
            _token_cache.clear()
            _ensure_token(config)
            return "ok", "Open API 已就绪（客户、联系人、线索、商机等对象可读写）"
        except Exception as exc:
            return "warn", f"认证失败：{exc}"

    # ------------------------------------------------------------------ #
    # Data access helpers
    # ------------------------------------------------------------------ #

    def get_object(self, object_api_name: str, object_data_id: str, config=None) -> dict:
        """查询单条 CRM 对象记录。

        Args:
            object_api_name: 对象 API 名，如 AccountObj（客户）、ContactObj（联系人）
            object_data_id:  记录 ID

        Returns:
            dict — 记录字段
        """
        if config is None:
            from ..config import Config
            config = Config()

        url = _api_url(config, "/cgi/crm/v2/data/get")
        resp = _post_json(
            url,
            {"data": {"dataObjectApiName": object_api_name, "objectDataId": object_data_id}},
            headers=_api_headers(config),
        )
        if resp.get("errorCode") != 0:
            raise RuntimeError(
                f"查询失败：{resp.get('errorMessage')} (code {resp.get('errorCode')})"
            )
        return resp.get("data") or {}

    def search_objects(
        self,
        object_api_name: str,
        filters: Optional[List[dict]] = None,
        limit: int = 20,
        offset: int = 0,
        config=None,
    ) -> List[dict]:
        """搜索 CRM 对象列表。

        Args:
            object_api_name: 对象 API 名，如 AccountObj、ContactObj、LeadsObj
            filters: 过滤条件列表，每项格式：
                     {"fieldName": "name", "filterType": "LIKE", "fieldValues": ["张三"]}
            limit:   每页条数（最大 100）
            offset:  偏移量

        Returns:
            list of dicts — 记录列表
        """
        if config is None:
            from ..config import Config
            config = Config()

        url = _api_url(config, "/cgi/crm/v2/data/query")
        payload: Dict[str, Any] = {
            "data": {
                "dataObjectApiName": object_api_name,
                "search": {
                    "filters": filters or [],
                    "offset": offset,
                    "limit": limit,
                },
            }
        }
        resp = _post_json(url, payload, headers=_api_headers(config))
        if resp.get("errorCode") != 0:
            raise RuntimeError(
                f"搜索失败：{resp.get('errorMessage')} (code {resp.get('errorCode')})"
            )
        data = resp.get("data") or {}
        return data.get("dataList") or []

    def list_customers(self, limit: int = 20, config=None) -> List[dict]:
        """列出 CRM 客户（AccountObj）。"""
        return self.search_objects("AccountObj", limit=limit, config=config)

    def list_contacts(self, limit: int = 20, config=None) -> List[dict]:
        """列出 CRM 联系人（ContactObj）。"""
        return self.search_objects("ContactObj", limit=limit, config=config)

    def list_leads(self, limit: int = 20, config=None) -> List[dict]:
        """列出 CRM 线索（SalesClueObj）。"""
        return self.search_objects("SalesClueObj", limit=limit, config=config)

    def list_opportunities(self, limit: int = 20, config=None) -> List[dict]:
        """列出 CRM 商机（LeadsObj）。"""
        return self.search_objects("LeadsObj", limit=limit, config=config)
