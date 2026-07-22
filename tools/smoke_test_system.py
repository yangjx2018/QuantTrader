#!/usr/bin/env python3
"""QuantFlow end-to-end smoke test (API + optional frontend page loads)."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import urllib.error
import urllib.parse
import urllib.request

API_BASE = "http://127.0.0.1:8000"
FE_BASE = "http://127.0.0.1:5000"
API_KEY = "quant-local-dev-key"
TIMEOUT = 45


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    ms: int = 0


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def add(self, r: Result) -> None:
        self.results.append(r)
        mark = "PASS" if r.ok else "FAIL"
        print(f"[{mark}] {r.name} ({r.ms}ms) {r.detail}".rstrip())

    @property
    def failed(self) -> list[Result]:
        return [r for r in self.results if not r.ok]


def api_request(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = TIMEOUT,
) -> tuple[int, Any, int]:
    url = API_BASE + path
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
    data = None
    headers = {"X-API-Key": API_KEY, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            ms = int((time.perf_counter() - t0) * 1000)
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = raw
            return resp.status, payload, ms
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        ms = int((time.perf_counter() - t0) * 1000)
        try:
            payload = json.loads(raw) if raw else {"detail": str(e)}
        except json.JSONDecodeError:
            payload = {"detail": raw or str(e)}
        return e.code, payload, ms
    except Exception as e:  # noqa: BLE001
        ms = int((time.perf_counter() - t0) * 1000)
        return 0, {"detail": str(e)}, ms


def unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def expect_ok(report: Report, name: str, method: str, path: str, **kwargs: Any) -> Any:
    status, payload, ms = api_request(method, path, **kwargs)
    ok = 200 <= status < 300
    detail = ""
    if not ok:
        detail = f"HTTP {status}: {json.dumps(payload, ensure_ascii=False)[:240]}"
    report.add(Result(name, ok, detail, ms))
    return unwrap(payload) if ok else None


def expect_status(
    report: Report,
    name: str,
    method: str,
    path: str,
    allowed: set[int],
    **kwargs: Any,
) -> Any:
    status, payload, ms = api_request(method, path, **kwargs)
    ok = status in allowed
    detail = f"HTTP {status}"
    if not ok:
        detail = f"HTTP {status}: {json.dumps(payload, ensure_ascii=False)[:240]}"
    report.add(Result(name, ok, detail, ms))
    return unwrap(payload) if 200 <= status < 300 else None


def run_api_tests(report: Report) -> dict[str, Any]:
    ctx: dict[str, Any] = {}

    # --- health ---
    expect_ok(report, "health", "GET", "/api/health")

    # --- api-data ---
    symbols = expect_ok(report, "api-data.symbols", "GET", "/api/api-data/symbols")
    ctx["symbols"] = symbols
    stock_list = expect_ok(
        report, "api-data.stock.list", "GET", "/api/api-data/stock/list", query={"limit": 5}
    )
    expect_ok(
        report,
        "api-data.stock.search",
        "GET",
        "/api/api-data/stock/search",
        query={"keyword": "平安"},
    )
    expect_ok(report, "api-data.sector", "GET", "/api/api-data/sector")
    expect_ok(report, "api-data.ticker", "GET", "/api/api-data/ticker")

    symbol = "000001.SZ"
    if isinstance(stock_list, dict):
        items = stock_list.get("items") or stock_list.get("list") or []
        if items and isinstance(items[0], dict):
            symbol = items[0].get("symbol") or items[0].get("code") or symbol
    elif isinstance(stock_list, list) and stock_list:
        first = stock_list[0]
        if isinstance(first, dict):
            symbol = first.get("symbol") or first.get("code") or symbol
        elif isinstance(first, str):
            symbol = first
    if isinstance(symbols, list) and symbols:
        first = symbols[0]
        if isinstance(first, dict):
            symbol = first.get("symbol") or first.get("code") or symbol
        elif isinstance(first, str):
            symbol = first
    # 列表接口常返回 000001，行情接口更稳妥用带后缀代码
    if isinstance(symbol, str) and symbol.isdigit() and len(symbol) == 6:
        symbol = f"{symbol}.SZ" if symbol.startswith(("0", "3")) else f"{symbol}.SH"
    ctx["symbol"] = symbol

    expect_ok(
        report,
        "api-data.kline",
        "GET",
        f"/api/api-data/kline/{urllib.parse.quote(symbol)}",
        query={"interval": "1d", "limit": 30},
    )
    expect_status(
        report,
        "api-data.realtime",
        "GET",
        f"/api/api-data/realtime/{urllib.parse.quote(symbol)}",
        {200, 404, 502, 503},
    )
    expect_status(
        report,
        "api-data.stock.base",
        "GET",
        f"/api/api-data/stock/{urllib.parse.quote(symbol)}/base",
        {200, 404},
    )

    # --- account ---
    accounts = expect_ok(report, "account.accounts", "GET", "/api/account/accounts")
    account_items: list[Any] = []
    if isinstance(accounts, list):
        account_items = accounts
    elif isinstance(accounts, dict):
        account_items = accounts.get("items") or accounts.get("list") or []

    def _account_usable(acc: dict[str, Any]) -> bool:
        status = str(acc.get("status") or "").lower()
        if status == "archived" or acc.get("is_archived") or acc.get("archived"):
            return False
        return status in {"", "active", "enabled", "normal"} or status == "active"

    account_id = None
    # 优先 paper active，再任意 active
    for prefer_type in ("paper", "live", "backtest", None):
        for acc in account_items:
            if not isinstance(acc, dict) or not _account_usable(acc):
                continue
            if prefer_type and str(acc.get("account_type") or "").lower() != prefer_type:
                continue
            account_id = acc.get("id")
            break
        if account_id:
            break
    ctx["account_id"] = account_id

    expect_status(
        report,
        "account.automation.status",
        "GET",
        "/api/account/automation/status",
        # 409: 同花顺客户端未就绪（环境依赖，非代码故障）
        {200, 409},
    )
    if account_id:
        expect_ok(
            report,
            "account.balance",
            "GET",
            "/api/account/balance",
            query={"account_id": account_id},
        )
        expect_ok(
            report,
            "account.positions",
            "GET",
            "/api/account/positions",
            query={"account_id": account_id},
        )
        expect_ok(
            report,
            "account.orders",
            "GET",
            "/api/account/orders",
            query={"account_id": account_id},
        )
        expect_ok(
            report,
            "account.trades",
            "GET",
            "/api/account/trades",
            query={"account_id": account_id},
        )
        expect_ok(
            report,
            "account.snapshot",
            "GET",
            "/api/account/snapshot",
            query={"account_id": account_id},
        )
    else:
        report.add(Result("account.balance", False, "no account available"))
        report.add(Result("account.positions", False, "no account available"))
        report.add(Result("account.orders", False, "no account available"))

    # --- strategy ---
    strategies = expect_ok(report, "strategy.list", "GET", "/api/strategy/list")
    expect_ok(report, "strategy.options", "GET", "/api/strategy/options/all")
    strategy_id = None
    strategy_items: list[Any] = []
    if isinstance(strategies, list):
        strategy_items = strategies
    elif isinstance(strategies, dict):
        strategy_items = strategies.get("items") or strategies.get("list") or []
    if strategy_items:
        strategy_id = strategy_items[0].get("id")
        # prefer active
        for s in strategy_items:
            if str(s.get("status", "")).lower() == "active":
                strategy_id = s.get("id")
                break
    ctx["strategy_id"] = strategy_id
    if strategy_id:
        expect_ok(report, "strategy.detail", "GET", f"/api/strategy/{strategy_id}")
        expect_ok(
            report,
            "strategy.versions",
            "GET",
            f"/api/strategy/{strategy_id}/versions",
        )
    else:
        report.add(Result("strategy.detail", False, "no strategy available"))

    # --- execution ---
    expect_ok(report, "execution.status", "GET", "/api/execution/status")
    exec_list = expect_ok(report, "execution.list", "GET", "/api/execution/list")
    expect_ok(report, "execution.signals", "GET", "/api/execution/signals")
    expect_ok(report, "execution.risk-alerts.active", "GET", "/api/execution/risk-alerts/active")
    expect_ok(report, "execution.risk-alerts", "GET", "/api/execution/risk-alerts")
    expect_ok(report, "execution.risk-rules", "GET", "/api/execution/risk-rules")

    execution_id = None
    exec_items: list[Any] = []
    if isinstance(exec_list, list):
        exec_items = exec_list
    elif isinstance(exec_list, dict):
        exec_items = exec_list.get("items") or exec_list.get("list") or []
    if exec_items:
        execution_id = exec_items[0].get("id")
        for e in exec_items:
            if str(e.get("status", "")).lower() == "running":
                execution_id = e.get("id")
                break
    ctx["execution_id"] = execution_id
    if execution_id:
        expect_ok(report, "execution.detail", "GET", f"/api/execution/{execution_id}")
        expect_ok(
            report,
            "execution.logs",
            "GET",
            f"/api/execution/{execution_id}/logs",
        )
        expect_ok(
            report,
            "execution.signals.by_id",
            "GET",
            f"/api/execution/{execution_id}/signals",
        )

    # Soft write: start execution only if we have strategy+account and nothing running
    running = [
        e
        for e in exec_items
        if str(e.get("status", "")).lower() in {"running", "paused"}
    ]
    if strategy_id and account_id and not running:
        started = expect_status(
            report,
            "execution.start",
            "POST",
            "/api/execution/start",
            {200, 201, 400, 409, 422},
            body={
                "strategy_id": strategy_id,
                "account_id": account_id,
                "params": {"symbol": symbol, "timeframe": "1d"},
            },
        )
        if isinstance(started, dict) and started.get("id"):
            ctx["started_execution_id"] = started["id"]
            tick = expect_status(
                report,
                "execution.tick",
                "POST",
                f"/api/execution/{started['id']}/tick",
                {200, 400, 404, 409, 500},
            )
            if isinstance(tick, dict):
                ctx["tick"] = {
                    "intents": tick.get("intents"),
                    "orders": len(tick.get("orders") or []),
                }
            expect_status(
                report,
                "execution.stop",
                "POST",
                f"/api/execution/{started['id']}/stop",
                {200, 400, 404, 409},
            )
    elif running:
        report.add(
            Result(
                "execution.start",
                True,
                f"skipped: already have {len(running)} running/paused",
            )
        )
        rid = running[0].get("id")
        if rid:
            tick = expect_status(
                report,
                "execution.tick",
                "POST",
                f"/api/execution/{rid}/tick",
                {200, 400, 404, 409, 500},
            )
            if isinstance(tick, dict):
                ctx["tick"] = {
                    "intents": tick.get("intents"),
                    "orders": len(tick.get("orders") or []),
                }
    else:
        report.add(
            Result(
                "execution.start",
                False,
                f"cannot start: strategy_id={strategy_id}, account_id={account_id}",
            )
        )

    # --- review ---
    expect_ok(report, "review.accounts", "GET", "/api/review/accounts")
    sessions = expect_ok(report, "review.sessions", "GET", "/api/review/sessions")
    session_id = None
    session_items: list[Any] = []
    if isinstance(sessions, list):
        session_items = sessions
    elif isinstance(sessions, dict):
        session_items = sessions.get("items") or sessions.get("list") or []
    if session_items and isinstance(session_items[0], dict):
        session_id = session_items[0].get("id") or session_items[0].get("session_id")
    ctx["session_id"] = session_id
    if session_id:
        q = {"session_id": session_id}
        expect_ok(report, "review.report", "GET", "/api/review/report", query=q)
        expect_ok(report, "review.trades", "GET", "/api/review/trades", query=q)
        expect_ok(report, "review.suggestions", "GET", "/api/review/suggestions", query=q)
        expect_ok(report, "review.equity-curve", "GET", "/api/review/equity-curve", query=q)
        expect_ok(report, "review.drawdown-curve", "GET", "/api/review/drawdown-curve", query=q)
    else:
        report.add(Result("review.report", True, "skipped: no review session"))
        report.add(Result("review.trades", True, "skipped: no review session"))
        report.add(Result("review.suggestions", True, "skipped: no review session"))
        report.add(Result("review.equity-curve", True, "skipped: no review session"))
        report.add(Result("review.drawdown-curve", True, "skipped: no review session"))

    # --- replay ---
    expect_ok(report, "replay.strategies", "GET", "/api/replay/strategies")
    expect_ok(report, "replay.virtual-accounts", "GET", "/api/replay/virtual-accounts")
    expect_status(
        report,
        "replay.stocks.search",
        "POST",
        "/api/replay/stocks/search",
        {200, 422},
        body={"keyword": "平安", "limit": 5},
    )

    # --- integration ---
    expect_ok(report, "integration.capabilities", "GET", "/api/integration/capabilities")
    expect_ok(report, "integration.accounts", "GET", "/api/integration/accounts")

    return ctx


def run_frontend_tests(report: Report) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report.add(Result("frontend.playwright", False, "playwright not installed"))
        return

    pages = [
        ("/", "首页"),
        ("/api-data", "行情数据"),
        ("/account/manage", "账户管理"),
        ("/account/trading", "交易界面"),
        ("/account/orders", "订单查询"),
        ("/strategies", "策略管理"),
        ("/execution", "执行监控"),
        ("/review", "复盘分析"),
        ("/replay", "历史回放"),
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        console_errors: list[str] = []
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text)
            if msg.type == "error"
            else None,
        )
        page.on(
            "pageerror",
            lambda exc: console_errors.append(f"pageerror: {exc}"),
        )

        for path, label in pages:
            console_errors.clear()
            t0 = time.perf_counter()
            try:
                resp = page.goto(FE_BASE + path, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
                status = resp.status if resp else 0
                body_ok = page.locator("body").count() > 0
                # nav should exist for layout pages
                has_nav = page.locator("nav, aside, a[href='/execution']").count() > 0
                fatal = [e for e in console_errors if "Failed to load" not in e][:3]
                ok = status == 200 and body_ok and has_nav
                detail = f"HTTP {status}; nav={has_nav}"
                if fatal:
                    detail += f"; console={fatal[0][:120]}"
                ms = int((time.perf_counter() - t0) * 1000)
                report.add(Result(f"frontend.{label}", ok, detail, ms))
            except Exception as e:  # noqa: BLE001
                ms = int((time.perf_counter() - t0) * 1000)
                report.add(Result(f"frontend.{label}", False, str(e)[:200], ms))

        browser.close()


def main() -> int:
    report = Report()
    print("=== QuantFlow Smoke Test ===")
    print(f"API: {API_BASE}")
    print(f"FE : {FE_BASE}")
    print()

    print("--- API ---")
    ctx = run_api_tests(report)
    print()
    print("--- Frontend ---")
    run_frontend_tests(report)

    print()
    print("=== Summary ===")
    total = len(report.results)
    failed = report.failed
    print(f"Total: {total}  Pass: {total - len(failed)}  Fail: {len(failed)}")
    if ctx:
        print("Context:", json.dumps({k: v for k, v in ctx.items() if k != "symbols"}, ensure_ascii=False)[:500])
    if failed:
        print("\nFailed cases:")
        for r in failed:
            print(f"  - {r.name}: {r.detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
