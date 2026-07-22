from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from ..ths_settings import dialog_poll_seconds, grid_cache_seconds, light_verify, status_probe_timeout_seconds

# Windows 专用模块 - Linux 上条件导入
if sys.platform == "win32":
    import pywinauto
    import pywinauto.clipboard
    import pywinauto.keyboard
    import win32clipboard
    import win32con
    import win32gui
    import win32process
    from pywinauto import Application, Desktop
    from pywinauto.application import ProcessNotFoundError
    from pywinauto.findwindows import ElementNotFoundError
    from PIL import Image, ImageEnhance, ImageFilter, ImageGrab
else:
    # Linux 占位，避免导入错误
    pywinauto = None
    Application = None
    Desktop = None
    ProcessNotFoundError = Exception
    ElementNotFoundError = Exception
    Image = None


OrderSide = Literal["buy", "sell"]


class CaptchaRequiredError(RuntimeError):
    def __init__(self, message: str = "检测到验证码弹窗，请在同花顺中人工处理后继续。") -> None:
        super().__init__(message)


class TradingClientNotReadyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ThsOrderRequest:
    symbol: str
    side: OrderSide
    price: Decimal
    quantity: int


class ThsDesktopAdapter:
    """同花顺桌面自动化适配器，不依赖 easytrader。

    已根据本机实测行为实现：
    - 使用 pywinauto 连接或启动 xiadan.exe。
    - 使用 TreeView 菜单切换功能页。
    - 使用 WM_CHAR 填写买入/卖出表单。
    - 自行处理委托确认、提示、撤单确认等弹窗。
    - 读取余额使用控件 ID。
    - 表格读取使用 Ctrl+A/C 复制，验证码优先 OCR 自动识别，失败再人工兜底。
    - 撤单支持直接点击撤单页按钮，绕开原 easytrader 的 OCR 依赖。
    """

    LEFT_MENU_CONTROL_ID = 129
    COMMON_GRID_CONTROL_ID = 1047
    CANCEL_SELECTED_BUTTON_ID = 1099
    CANCEL_ALL_BUTTON_ID = 30001
    TRADE_SECURITY_CONTROL_ID = 1032
    TRADE_PRICE_CONTROL_ID = 1033
    TRADE_AMOUNT_CONTROL_ID = 1034
    TRADE_SUBMIT_CONTROL_ID = 1006
    POP_DIALOG_TITLE_CONTROL_ID = 1365

    BALANCE_CONTROL_IDS = {
        "资金余额": 1012,
        "可用金额": 1016,
        "可取金额": 1017,
        "股票市值": 1014,
        "总资产": 1015,
    }

    GRID_DTYPE = {
        "操作日期": str,
        "委托编号": str,
        "申请编号": str,
        "合同编号": str,
        "证券代码": str,
        "股东代码": str,
        "资金帐号": str,
        "资金帐户": str,
        "发生日期": str,
    }
    def __init__(
        self,
        client_path: str | Path,
        captcha_code: str | None = None,
        wait_manual_captcha: bool = False,
        manual_captcha_timeout: int = 120,
    ) -> None:
        self.client_path = Path(client_path)
        self.captcha_code = captcha_code
        self.wait_manual_captcha = wait_manual_captcha
        self.manual_captcha_timeout = manual_captcha_timeout
        self.auto_captcha = os.getenv("THS_AUTO_CAPTCHA", "1").strip().lower() not in {"0", "false", "no"}
        self.app: Application | None = None
        self.main: Any | None = None
        self._grid_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._last_menu_path: tuple[str, ...] | None = None

    def connect(self, *, auto_start: bool = True, wait_timeout: int | None = None) -> None:
        self.client_path = self._normalize_client_path(self.client_path)
        if not self.client_path.exists():
            raise FileNotFoundError(f"同花顺交易客户端不存在: {self.client_path}")

        timeout = 30 if wait_timeout is None else max(1, int(wait_timeout))
        self.app = Application(backend="win32")
        try:
            self.app.connect(path=str(self.client_path), timeout=5)
        except ProcessNotFoundError:
            if not auto_start:
                raise TradingClientNotReadyError(
                    "未检测到同花顺交易客户端进程。请先手动打开并登录交易端，或点击「连接」启动。"
                )
            self.app = Application(backend="win32").start(str(self.client_path))
        except Exception:
            if not auto_start:
                raise TradingClientNotReadyError(
                    "无法连接到同花顺交易客户端。请确认已登录网上股票交易系统后再试。"
                )
            self.app = Application(backend="win32").start(str(self.client_path))

        self.main = self._wait_for_main_window(timeout_seconds=timeout)
        self._activate_main_window()

    def connect_probe(self) -> None:
        """状态探测：不自动启动客户端，短超时失败快返回。"""
        self.connect(auto_start=False, wait_timeout=status_probe_timeout_seconds())

    def _normalize_client_path(self, client_path: Path) -> Path:
        """The trading UI is xiadan.exe even when the user points at the main launcher."""
        if client_path.name.lower() in {"hexinlauncher.exe", "hexin.exe"}:
            xiadan_path = client_path.with_name("xiadan.exe")
            if xiadan_path.exists():
                return xiadan_path
        return client_path

    def ensure_connected(self) -> tuple[Application, Any]:
        if self.app is None or self.main is None:
            self.connect()
        assert self.app is not None
        assert self.main is not None
        return self.app, self.main

    def _find_main_window(self):
        assert self.app is not None
        process_id = self.app.process
        for window in Desktop(backend="win32").windows():
            try:
                _, window_pid = win32process.GetWindowThreadProcessId(window.handle)
            except Exception:
                continue
            if window_pid != process_id:
                continue
            if "网上股票交易系统" in (window.window_text() or ""):
                return self.app.window(handle=window.handle)
        return self.app.top_window()

    def _wait_for_main_window(self, timeout_seconds: int):
        start = time.monotonic()
        last_error: Exception | None = None
        while time.monotonic() - start <= timeout_seconds:
            try:
                main = self._find_main_window()
                if main.exists(timeout=1):
                    try:
                        main.wait("visible", timeout=2)
                    except Exception:
                        pass
                    return main
            except Exception as exc:
                last_error = exc
            time.sleep(0.8)
        raise TradingClientNotReadyError(
            f"同花顺交易客户端已连接或已尝试启动，但 {timeout_seconds} 秒内未发现主窗口。"
            "请确认已登录网上股票交易系统后再试。"
        ) from last_error

    def _activate_main_window(self) -> None:
        if self.main is None:
            return
        try:
            if self.main.is_minimized():
                self.main.restore()
        except Exception:
            pass
        try:
            self.main.set_focus()
        except Exception:
            try:
                self._set_foreground(self.main)
            except Exception:
                pass
        time.sleep(0.2)

    def status(self) -> dict[str, Any]:
        _, main = self.ensure_connected()
        return {
            "connected": True,
            "ready": self._is_trade_page_ready(),
            "window_title": main.window_text(),
            "window_class": main.class_name(),
            "window_rect": str(main.rectangle()),
            "account": self.get_account_info(),
        }

    def prepare_trading_workspace(self) -> dict[str, Any]:
        """启动/连接交易端，并验证下单和查询页面都可用。"""
        _, main = self.ensure_connected()
        self._activate_main_window()
        self.close_pop_dialogs()
        self._try_open_trading_session()
        if self._is_login_page_visible():
            raise TradingClientNotReadyError(
                "同花顺交易程序已自动启动并置前，但当前停留在交易登录页。"
                "请先在同花顺交易窗口完成交易密码/验证码登录；登录成功后再点击 Web 端连接。"
            )

        checks: dict[str, Any] = {
            "buy_form_ready": False,
            "balance_page_ready": False,
        }

        self.switch_menu(["买入[F1]"], sleep_seconds=0.5)
        checks["buy_form_ready"] = self._has_trade_form_controls()
        if not checks["buy_form_ready"]:
            raise TradingClientNotReadyError(
                "同花顺交易端已连接，但无法打开买入委托页面或未找到证券代码/价格/数量输入框。"
                "请确认交易端已登录并进入网上股票交易系统。"
            )

        self.switch_menu(["查询[F4]", "资金股票"], sleep_seconds=0.5)
        checks["balance_page_ready"] = self._has_balance_controls()
        if not checks["balance_page_ready"]:
            raise TradingClientNotReadyError(
                "同花顺买入委托页面可用，但无法打开 查询[F4]/资金股票。"
                "请确认交易端已登录、资金账号可见并允许查询。"
            )

        return {
            "connected": True,
            "ready": True,
            "window_title": main.window_text(),
            "window_class": main.class_name(),
            "window_rect": str(main.rectangle()),
            "account": self.get_account_info(),
            "checks": checks,
        }

    def get_account_info(self) -> dict[str, Any]:
        _, main = self.ensure_connected()
        texts: list[dict[str, Any]] = []
        for child in main.children():
            try:
                text = (child.window_text() or "").strip()
                if not text:
                    continue
                texts.append(
                    {
                        "control_id": child.control_id(),
                        "class_name": child.class_name(),
                        "text": text,
                    }
                )
            except Exception:
                continue

        account_name = self._first_control_text(texts, [2322, 2380])
        market = self._first_control_text(texts, [1003])
        shareholder_account = self._first_control_text(texts, [1004])
        capital_account = self._first_control_text(texts, [1711])

        account_text = " ".join(item["text"] for item in texts)
        is_paper = "模拟" in account_text
        is_live = bool(capital_account) and not is_paper

        return {
            "account_name": account_name,
            "account_type": "paper" if is_paper else "live" if is_live else "unknown",
            "account_type_label": "模拟盘" if is_paper else "实盘" if is_live else "未知",
            "market": market,
            "shareholder_account": shareholder_account,
            "capital_account": capital_account,
            "raw_controls": [
                item
                for item in texts
                if item["control_id"] in (1003, 1004, 1711, 2322, 2380)
                or "账户" in item["text"]
                or "帐户" in item["text"]
                or "模拟" in item["text"]
            ],
        }

    def _first_control_text(self, controls: list[dict[str, Any]], control_ids: list[int]) -> str:
        for control_id in control_ids:
            for item in controls:
                if item["control_id"] == control_id and item["text"]:
                    return str(item["text"])
        return ""

    def get_balance(self) -> dict[str, float]:
        _, main = self.ensure_connected()
        self.switch_menu(["查询[F4]", "资金股票"])
        self._assert_trade_page_ready()
        result: dict[str, float] = {}
        for key, control_id in self.BALANCE_CONTROL_IDS.items():
            try:
                result[key] = float(
                    main.child_window(
                        control_id=control_id,
                        class_name="Static",
                    ).window_text()
                )
            except ElementNotFoundError as exc:
                raise TradingClientNotReadyError(
                    "未找到资金余额控件，通常表示同花顺交易端尚未登录、连接已断开，"
                    "或当前未进入“查询-资金股票”页面。"
                ) from exc
        return result

    def get_positions(self) -> list[dict[str, Any]]:
        self.switch_menu(["查询[F4]", "资金股票"])
        try:
            return self.read_grid()
        except RuntimeError as exc:
            if "未找到同花顺表格控件" in str(exc):
                return []
            raise

    def get_today_orders(self) -> list[dict[str, Any]]:
        self.switch_menu(["查询[F4]", "当日委托"])
        try:
            return self.read_grid()
        except RuntimeError as exc:
            if "未找到同花顺表格控件" in str(exc):
                return []
            raise

    def get_today_trades(self) -> list[dict[str, Any]]:
        self.switch_menu(["查询[F4]", "当日成交"])
        try:
            return self.read_grid()
        except RuntimeError as exc:
            if "未找到同花顺表格控件" in str(exc):
                return []
            raise

    def get_history_orders(self) -> list[dict[str, Any]]:
        self.switch_menu(["查询[F4]", "历史委托"])
        try:
            return self.read_grid()
        except RuntimeError as exc:
            if "未找到同花顺表格控件" in str(exc):
                return []
            raise

    def get_history_trades(self) -> list[dict[str, Any]]:
        self.switch_menu(["查询[F4]", "历史成交"])
        try:
            return self.read_grid()
        except RuntimeError as exc:
            if "未找到同花顺表格控件" in str(exc):
                return []
            raise

    def buy(self, symbol: str, price: str | float | Decimal, quantity: int) -> dict[str, Any]:
        return self.place_order(
            ThsOrderRequest(
                symbol=symbol,
                side="buy",
                price=Decimal(str(price)),
                quantity=quantity,
            )
        )

    def sell(self, symbol: str, price: str | float | Decimal, quantity: int) -> dict[str, Any]:
        return self.place_order(
            ThsOrderRequest(
                symbol=symbol,
                side="sell",
                price=Decimal(str(price)),
                quantity=quantity,
            )
        )

    def place_order(self, request: ThsOrderRequest) -> dict[str, Any]:
        self._grid_cache.clear()
        self.switch_menu(["买入[F1]"] if request.side == "buy" else ["卖出[F2]"])
        self.fill_trade_form(request)
        _, main = self.ensure_connected()
        main.child_window(
            control_id=self.TRADE_SUBMIT_CONTROL_ID,
            class_name="Button",
        ).wrapper_object().click()
        time.sleep(0.8)

        action_result = self.handle_pop_dialogs()
        verification = self.verify_order(request, action_result, light=light_verify())
        return verification

    def cancel_order(self, entrust_no: str | None = None) -> dict[str, Any]:
        if not str(entrust_no or "").strip():
            raise ValueError("撤单必须提供合同编号，禁止不带合同编号执行默认撤单。")
        entrust_no = str(entrust_no).strip()
        try:
            return self.cancel_order_from_today_orders(entrust_no)
        except Exception as today_exc:
            fallback_error = f"{type(today_exc).__name__}: {today_exc}"

        self.switch_menu(["撤单[F3]"])
        selected = self.select_cancel_row_by_entrust_no(entrust_no)
        if not selected:
            raise TradingClientNotReadyError(
                f"当日委托双击撤单失败，且撤单列表中未找到合同编号 {entrust_no}，或该委托当前不可撤。"
                f" 当日委托路径错误: {fallback_error}"
            )
        result = self.click_direct_cancel_button(entrust_no)
        result["entrust_no"] = entrust_no
        result["fallback_from_today_orders_error"] = fallback_error
        return result

    def cancel_order_from_today_orders(self, entrust_no: str) -> dict[str, Any]:
        self.switch_menu(["查询[F4]", "当日委托"])
        row_count, row_index = self._find_grid_row_index_by_entrust_no(entrust_no)
        self.switch_menu(["查询[F4]", "当日委托"], sleep_seconds=0.5)

        trigger_method = self._trigger_cancel_from_grid_row(row_index, row_count)
        if not trigger_method:
            raise TradingClientNotReadyError(f"当日委托中已找到合同编号 {entrust_no}，但无法触发该行撤单。")

        dialog_result = self.handle_pop_dialogs(
            expected_cancel_entrust_no=entrust_no,
            allow_unknown_cancel_dialog=True,
        )
        status_result = self._wait_cancel_status_confirmed(entrust_no)
        return {
            "status": status_result["status"],
            "method": f"today_orders_{trigger_method}",
            "target_entrust_no": entrust_no,
            "dialog_result": dialog_result,
            "status_result": status_result,
            "balance_after": self.safe_balance(),
            "entrust_no": entrust_no,
        }

    def cancel_all_orders(self) -> dict[str, Any]:
        self.switch_menu(["撤单[F3]"])
        target = self._find_cancel_button(self.CANCEL_ALL_BUTTON_ID)
        if target is None:
            raise TradingClientNotReadyError("未找到可见的全部撤单按钮。")
        try:
            if not target.is_enabled():
                raise TradingClientNotReadyError("同花顺全部撤单按钮当前不可点击。")
            _, main = self.ensure_connected()
            self._set_foreground(main)
            target.click_input()
        except TradingClientNotReadyError:
            raise
        except Exception as exc:
            raise TradingClientNotReadyError("同花顺全部撤单按钮点击失败。") from exc
        time.sleep(1)
        dialog_result = self.handle_pop_dialogs()
        time.sleep(1)
        return {
            "status": "submitted",
            "method": "cancel_all_button",
            "dialog_result": dialog_result,
            "balance_after": self.safe_balance(),
        }

    def switch_menu(self, path: list[str], sleep_seconds: float = 0.2) -> None:
        _, main = self.ensure_connected()
        self._activate_main_window()
        self.close_pop_dialogs()
        tree = main.child_window(
            control_id=self.LEFT_MENU_CONTROL_ID,
            class_name="SysTreeView32",
        )
        try:
            tree.wait("ready", timeout=2)
            tree.get_item(path).select()
        except Exception as exc:
            if self._is_login_page_visible():
                raise TradingClientNotReadyError(
                    "同花顺交易程序已自动启动并置前，但当前停留在交易登录页。"
                    "请先在同花顺交易窗口完成交易密码/验证码登录；登录成功后再点击 Web 端连接。"
                ) from exc
            if self._switch_by_hotkey(path, sleep_seconds):
                self._last_menu_path = tuple(path)
                return
            if self._is_page_ready_for_path(path):
                time.sleep(sleep_seconds)
                self._last_menu_path = tuple(path)
                return
            raise TradingClientNotReadyError(
                f"无法切换同花顺菜单 {'/'.join(path)}。交易端可能未登录或仍在启动页。"
            ) from exc
        try:
            main.type_keys("{F5}")
        except Exception:
            pass
        time.sleep(sleep_seconds)
        self._last_menu_path = tuple(path)

    def _switch_by_hotkey(self, path: list[str], sleep_seconds: float) -> bool:
        target = "/".join(path)
        key: str | None = None
        if "撤单" in target and self._switch_cancel_by_toolbar(sleep_seconds):
            return True
        if "买入" in target:
            key = "{F1}"
        elif "卖出" in target:
            key = "{F2}"
        elif "撤单" in target:
            key = "{F3}"
        elif "查询" in target:
            key = "{F4}"
        if not key:
            return False
        try:
            _, main = self.ensure_connected()
            self._activate_main_window()
            main.type_keys(key)
            time.sleep(max(sleep_seconds, 0.8))
            return self._is_page_ready_for_path(path)
        except Exception:
            return False

    def _switch_cancel_by_toolbar(self, sleep_seconds: float) -> bool:
        try:
            _, main = self.ensure_connected()
            self._activate_main_window()
            for child in main.descendants():
                try:
                    if child.control_id() != 32817 or child.class_name() != "Button":
                        continue
                    rect = child.rectangle()
                    if rect.width() <= 10 or rect.height() <= 10:
                        continue
                    pywinauto.mouse.click(
                        button="left",
                        coords=(int((rect.left + rect.right) / 2), int((rect.top + rect.bottom) / 2)),
                    )
                    time.sleep(max(sleep_seconds, 0.8))
                    return self._has_cancel_page_controls()
                except Exception:
                    continue
        except Exception:
            return False
        return False

    def _try_open_trading_session(self) -> None:
        if self._has_balance_controls() or self._has_trade_form_controls():
            return
        try:
            _, main = self.ensure_connected()
            login_button = main.child_window(control_id=1709, class_name="Button")
            if login_button.exists(timeout=0.5) and login_button.is_visible() and login_button.is_enabled():
                login_button.click_input()
                time.sleep(3)
                self.close_pop_dialogs()
        except Exception:
            pass

    def _is_login_page_visible(self) -> bool:
        try:
            _, main = self.ensure_connected()
            visible_controls: list[tuple[int, str]] = []
            for child in main.descendants():
                try:
                    if child.is_visible():
                        visible_controls.append((child.control_id(), child.class_name()))
                except Exception:
                    continue
            has_login_button = (1006, "Button") in visible_controls
            has_password_edit = (1012, "Edit") in visible_controls
            has_account_selector = any(
                control_id in {1011, 2351, 2353} and class_name == "ComboBox"
                for control_id, class_name in visible_controls
            )
            has_toolbar_login = (1709, "Button") in visible_controls
            return (
                ((has_login_button and has_password_edit and has_account_selector) or has_toolbar_login)
                and not self._has_trade_form_controls()
                and not self._has_balance_controls()
            )
        except Exception:
            return False

    def _is_page_ready_for_path(self, path: list[str]) -> bool:
        target = "/".join(path)
        if "资金股票" in target:
            return self._has_balance_controls()
        if "买入" in target or "卖出" in target:
            return self._has_trade_form_controls()
        if "撤单" in target:
            return self._has_cancel_page_controls()
        if any(name in target for name in ("当日委托", "当日成交", "历史委托", "历史成交")):
            try:
                self._find_grid()
                return True
            except Exception:
                return False
        return False

    def _is_trade_page_ready(self) -> bool:
        try:
            _, main = self.ensure_connected()
            texts = " ".join(child.window_text() or "" for child in main.children())
            if ("登录" in texts or "资金帐户" in texts or "断开" in texts) and not self._has_balance_controls():
                return False
            if self._has_balance_controls() or self._has_trade_form_controls():
                return True
            try:
                main.child_window(
                    control_id=self.LEFT_MENU_CONTROL_ID,
                    class_name="SysTreeView32",
                ).wait("exists", timeout=1)
                return True
            except Exception:
                return False
        except Exception:
            return False

    def _has_balance_controls(self) -> bool:
        try:
            _, main = self.ensure_connected()
            found_ids = set()
            for child in main.children():
                try:
                    if child.class_name() == "Static":
                        found_ids.add(child.control_id())
                except Exception:
                    continue
            required_ids = set(self.BALANCE_CONTROL_IDS.values())
            return required_ids.issubset(found_ids)
        except Exception:
            return False

    def _has_trade_form_controls(self) -> bool:
        try:
            _, main = self.ensure_connected()
            for control_id in (
                self.TRADE_SECURITY_CONTROL_ID,
                self.TRADE_PRICE_CONTROL_ID,
                self.TRADE_AMOUNT_CONTROL_ID,
            ):
                if not main.child_window(control_id=control_id, class_name="Edit").exists(timeout=0.2):
                    return False
            return True
        except Exception:
            return False

    def _has_cancel_page_controls(self) -> bool:
        try:
            _, main = self.ensure_connected()
            for child in main.descendants():
                try:
                    if child.control_id() != self.CANCEL_SELECTED_BUTTON_ID or child.class_name() != "Button":
                        continue
                    text = child.window_text() or ""
                    if "撤单" not in text:
                        continue
                    rect = child.rectangle()
                    if rect.width() > 20 and rect.height() > 10 and child.is_visible():
                        return True
                except Exception:
                    continue
            return False
        except Exception:
            return False

    def _assert_trade_page_ready(self) -> None:
        if self._is_trade_page_ready():
            return
        raise TradingClientNotReadyError(
            "同花顺交易客户端已启动，但还没有进入已登录的交易界面。"
            "请先在同花顺中完成登录/连接后重试。"
        )

    def fill_trade_form(self, request: ThsOrderRequest) -> None:
        self.type_by_wm_char(self.TRADE_SECURITY_CONTROL_ID, request.symbol[-6:])
        time.sleep(0.6)
        self.type_by_wm_char(self.TRADE_PRICE_CONTROL_ID, f"{request.price:.2f}")
        self.type_by_wm_char(self.TRADE_AMOUNT_CONTROL_ID, str(int(request.quantity)))
        time.sleep(0.5)

    def type_by_wm_char(self, control_id: int, text: str, clear_first: bool = True) -> None:
        _, main = self.ensure_connected()
        wrapper = main.child_window(
            control_id=control_id,
            class_name="Edit",
        ).wrapper_object()
        hwnd = wrapper.handle
        if clear_first:
            for _ in range(24):
                win32gui.SendMessage(hwnd, win32con.WM_CHAR, 8, 0)
                time.sleep(0.01)
        for ch in text:
            win32gui.SendMessage(hwnd, win32con.WM_CHAR, ord(ch), 0)
            time.sleep(0.03)

    def read_grid(self) -> list[dict[str, Any]]:
        cache_key = "/".join(self._last_menu_path) if self._last_menu_path else "_default"
        ttl = grid_cache_seconds()
        if ttl > 0:
            cached = self._grid_cache.get(cache_key)
            if cached and (time.monotonic() - cached[0]) <= ttl:
                return [dict(row) for row in cached[1]]

        _, main = self.ensure_connected()
        self._handle_captcha_dialog_if_present()
        grid = self._find_grid()
        self._set_foreground(grid)
        rect = grid.rectangle()
        pywinauto.mouse.click(
            button="left",
            coords=(rect.left + 20, rect.top + 20),
        )
        time.sleep(0.1)
        self._handle_captcha_dialog_if_present()
        pywinauto.keyboard.send_keys("^a^c")
        time.sleep(0.3)
        if self._handle_captcha_dialog_if_present():
            self._set_foreground(grid)
            pywinauto.mouse.click(
                button="left",
                coords=(rect.left + 20, rect.top + 20),
            )
            time.sleep(0.1)
            pywinauto.keyboard.send_keys("^a^c")
            time.sleep(0.3)
        content = self._get_clipboard_table_text()
        if not content.strip():
            rows: list[dict[str, Any]] = []
        else:
            df = pd.read_csv(
                io.StringIO(content),
                delimiter="\t",
                dtype=self.GRID_DTYPE,
                na_filter=False,
            )
            rows = df.to_dict("records")
        if ttl > 0:
            self._grid_cache[cache_key] = (time.monotonic(), [dict(row) for row in rows])
        return rows

    def _get_clipboard_table_text(self) -> str:
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                    data = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                    if isinstance(data, bytes):
                        for encoding in ("gbk", "cp936", "mbcs"):
                            try:
                                return data.decode(encoding)
                            except UnicodeDecodeError:
                                continue
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                    if isinstance(data, str):
                        return data
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass
        return pywinauto.clipboard.GetData()

    def select_cancel_row_by_entrust_no(self, entrust_no: str) -> bool:
        entrust_no = str(entrust_no or "").strip()
        if not entrust_no:
            return False
        try:
            row_count, row_index = self._find_grid_row_index_by_entrust_no(entrust_no)
            self.switch_menu(["撤单[F3]"], sleep_seconds=0.5)
            return self._select_only_cancel_checkbox(row_index, row_count)
        except Exception:
            return False

    def _find_grid_row_index_by_entrust_no(self, entrust_no: str) -> tuple[int, int]:
        target = str(entrust_no or "").strip()
        rows = self.read_grid()
        for index, row in enumerate(rows):
            if self._row_contains_entrust_no(row, target):
                return len(rows), index
        raise TradingClientNotReadyError(f"当前表格中未找到合同编号 {target}。")

    def _row_contains_entrust_no(self, row: dict[str, Any], entrust_no: str) -> bool:
        target = str(entrust_no or "").strip()
        if not target:
            return False
        for value in row.values():
            text = str(value or "").strip()
            if text == target:
                return True
            if text.endswith(".0") and text[:-2] == target:
                return True
        return False

    def _order_cancel_status(self, row: dict[str, Any]) -> str:
        status_text = ""
        for key in ("委托状态", "状态", "状态说明", "委托状态说明", "处理结果", "备注", "说明"):
            value = row.get(key)
            if value not in (None, ""):
                status_text = str(value).strip()
                break
        compact = status_text.replace(" ", "")
        if any(token in compact for token in ("全部撤单", "已撤单", "已撤")):
            return "canceled"
        if any(token in compact for token in ("部分撤单", "部撤")):
            return "partial_canceled"
        if "撤单中" in compact:
            return "cancel_pending"
        if any(token in compact for token in ("全部成交", "已成")):
            return "filled"
        if any(token in compact for token in ("部分成交", "部成")):
            return "partial_filled"
        if any(token in compact for token in ("未成交", "已报", "已提交", "正报", "待报")):
            return "submitted"
        if any(token in compact for token in ("废单", "失败", "拒绝")):
            return "rejected"
        return compact or "unknown"

    def _wait_cancel_status_confirmed(self, entrust_no: str, timeout_seconds: float = 8) -> dict[str, Any]:
        target = str(entrust_no or "").strip()
        deadline = time.monotonic() + timeout_seconds
        last_row: dict[str, Any] | None = None
        last_status = "unknown"

        while time.monotonic() <= deadline:
            time.sleep(0.8)
            try:
                rows = self.get_today_orders()
            except Exception:
                continue
            for row in rows:
                if not self._row_contains_entrust_no(row, target):
                    continue
                last_row = row
                last_status = self._order_cancel_status(row)
                if last_status in {"cancel_pending", "partial_canceled", "canceled"}:
                    return {"confirmed": True, "status": last_status, "matched_order": row}
                break

        raise TradingClientNotReadyError(
            f"撤单动作执行后回查同花顺，当日委托合同编号 {target} 状态仍未变为撤单中/部分撤单/全部撤单。"
            f" 当前状态: {last_status}；匹配记录: {last_row or {}}"
        )

    def click_direct_cancel_button(self, entrust_no: str) -> dict[str, Any]:
        _, main = self.ensure_connected()
        target = self._find_cancel_button(self.CANCEL_SELECTED_BUTTON_ID)
        trigger_method = "single_cancel_button"
        if target is None:
            self._set_foreground(main)
            pywinauto.keyboard.send_keys("{DELETE}")
            if not self._wait_for_any_dialog(timeout_seconds=2):
                raise TradingClientNotReadyError("未找到可见可点击的单笔撤单按钮，且按 Del 未触发撤单确认弹窗。")
            trigger_method = "selected_row_delete"
        else:
            try:
                self._set_foreground(main)
                self._click_control_center(target)
            except Exception as exc:
                raise TradingClientNotReadyError(
                    "同花顺单笔撤单按钮点击失败。通常是委托不可撤、未选中撤单记录，或交易端弹窗/验证码阻塞。"
                ) from exc
        time.sleep(1)
        dialog_result = self.handle_pop_dialogs(
            expected_cancel_entrust_no=entrust_no,
            allow_unknown_cancel_dialog=True,
        )
        status_result = self._wait_cancel_status_confirmed(entrust_no)
        return {
            "status": status_result["status"],
            "method": trigger_method,
            "target_entrust_no": entrust_no,
            "dialog_result": dialog_result,
            "status_result": status_result,
            "balance_after": self.safe_balance(),
        }

    def _find_cancel_button(self, control_id: int):
        _, main = self.ensure_connected()
        candidates = []
        for child in main.descendants():
            try:
                if child.control_id() != control_id or child.class_name() != "Button":
                    continue
                text = child.window_text() or ""
                if control_id == self.CANCEL_SELECTED_BUTTON_ID:
                    if "撤单" not in text or "全撤" in text or "全部" in text:
                        continue
                if not child.is_enabled():
                    continue
                rect = child.rectangle()
                if rect.width() <= 20 or rect.height() <= 10:
                    continue
                if not child.is_visible() and control_id != self.CANCEL_SELECTED_BUTTON_ID:
                    continue
                candidates.append(child)
            except Exception:
                continue
        if not candidates:
            return None
        return min(candidates, key=lambda item: (0 if item.is_visible() else 1, item.rectangle().top, item.rectangle().left))

    def _click_control_center(self, control: Any) -> None:
        try:
            control.click_input()
            return
        except Exception:
            pass
        rect = control.rectangle()
        pywinauto.mouse.click(
            button="left",
            coords=(int((rect.left + rect.right) / 2), int((rect.top + rect.bottom) / 2)),
        )

    def handle_pop_dialogs(
        self,
        expected_cancel_entrust_no: str | None = None,
        allow_unknown_cancel_dialog: bool = False,
    ) -> dict[str, Any]:
        app, main = self.ensure_connected()
        handled_dialogs: list[dict[str, str]] = []
        while self.is_exist_pop_dialog():
            dialog = app.top_window()
            title = self._get_dialog_title(dialog)
            content = self._extract_dialog_content(dialog)

            if self._is_captcha_dialog(dialog):
                self._handle_captcha_dialog_if_present()
                time.sleep(0.5)
                continue

            if title == "委托确认" or title == "撤单确认":
                if title == "撤单确认":
                    self._assert_cancel_dialog_matches(content, expected_cancel_entrust_no)
                self._submit_dialog_by_shortcut(dialog)
                handled_dialogs.append({"title": title, "content": content})
                time.sleep(0.5)
                continue

            if title == "提示信息":
                if expected_cancel_entrust_no and self._looks_like_bulk_cancel(content):
                    raise TradingClientNotReadyError(
                        f"同花顺弹窗疑似全部/批量撤单确认，已拒绝自动确认。目标合同编号: {expected_cancel_entrust_no}；弹窗内容: {content}"
                    )
                if expected_cancel_entrust_no:
                    self._assert_cancel_dialog_matches(content, expected_cancel_entrust_no)
                self._submit_dialog_by_shortcut(dialog)
                handled_dialogs.append({"title": title, "content": content})
                time.sleep(0.5)
                continue

            if "提示" in title:
                if "成功" in content:
                    entrust_no = self._extract_entrust_no(content)
                    self._click_confirm(dialog)
                    handled_dialogs.append({"title": title, "content": content})
                    return {"entrust_no": entrust_no, "message": content, "handled_dialogs": handled_dialogs}
                if expected_cancel_entrust_no:
                    self._assert_cancel_dialog_matches(content, expected_cancel_entrust_no)
                self._click_confirm(dialog)
                handled_dialogs.append({"title": title, "content": content})
                return {"message": content, "handled_dialogs": handled_dialogs}

            if expected_cancel_entrust_no and allow_unknown_cancel_dialog and not title.strip() and not content.strip():
                self._submit_dialog_by_shortcut(dialog)
                handled_dialogs.append({"title": title, "content": content})
                time.sleep(0.8)
                continue

            if expected_cancel_entrust_no:
                raise TradingClientNotReadyError(
                    f"触发撤单后出现无法识别的同花顺弹窗，已停止自动确认，避免误撤。"
                    f"目标合同编号: {expected_cancel_entrust_no}；弹窗标题: {title}；弹窗内容: {content}"
                )

            try:
                dialog.close()
            except Exception:
                pass
            return {"message": f"unknown dialog: {title} {content}", "handled_dialogs": handled_dialogs}

        if expected_cancel_entrust_no and not handled_dialogs:
            return {"message": "no cancel dialog detected", "handled_dialogs": handled_dialogs}
        return {"message": "success", "handled_dialogs": handled_dialogs}

    def is_exist_pop_dialog(self) -> bool:
        app, main = self.ensure_connected()
        time.sleep(dialog_poll_seconds())
        try:
            return main.wrapper_object() != app.top_window().wrapper_object()
        except Exception:
            return False

    def close_pop_dialogs(self) -> None:
        app, main = self.ensure_connected()
        for _ in range(3):
            if not self.is_exist_pop_dialog():
                return
            try:
                app.top_window().close()
            except Exception:
                return

    def verify_order(
        self,
        request: ThsOrderRequest,
        action_result: dict[str, Any],
        *,
        light: bool | None = None,
    ) -> dict[str, Any]:
        errors: list[str] = []
        entrust_no = action_result.get("entrust_no")
        orders: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        balance: dict[str, Any] = {}
        use_light = light_verify() if light is None else light

        try:
            orders = self.get_today_orders()
        except Exception as exc:
            errors.append(f"orders: {type(exc).__name__}: {exc}")

        if not use_light:
            try:
                trades = self.get_today_trades()
            except Exception as exc:
                errors.append(f"trades: {type(exc).__name__}: {exc}")
            try:
                balance = self.get_balance()
            except Exception as exc:
                errors.append(f"balance: {type(exc).__name__}: {exc}")

        matched_orders = self._match_rows(orders, request)
        matched_trades = self._match_rows(trades, request)
        confirmed = bool(entrust_no or matched_orders or matched_trades)
        return {
            "status": "confirmed" if confirmed else "unconfirmed",
            "action_result": action_result,
            "entrust_no": entrust_no,
            "matched_orders": matched_orders,
            "matched_trades": matched_trades,
            "balance_after": balance,
            "errors": errors,
            "light_verify": use_light,
        }

    def safe_balance(self) -> dict[str, Any]:
        try:
            return self.get_balance()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def _find_grid(self):
        _, main = self.ensure_connected()
        candidates = []
        for child in main.children():
            try:
                if child.control_id() == self.COMMON_GRID_CONTROL_ID:
                    rect = child.rectangle()
                    if (
                        rect.width() > 100
                        and rect.height() > 80
                        and child.is_visible()
                    ):
                        candidates.append(child)
            except Exception:
                continue
        if not candidates:
            raise RuntimeError("未找到同花顺表格控件")
        return max(candidates, key=lambda item: item.rectangle().width() * item.rectangle().height())

    def _click_grid_row(self, index: int) -> None:
        grid = self._find_grid()
        rect = grid.rectangle()
        x = rect.left + 20
        y = rect.top + 30 + index * 18
        pywinauto.mouse.click(button="left", coords=(x, y))
        time.sleep(0.2)

    def _grid_row_point(self, index: int, row_count: int | None = None) -> tuple[int, int]:
        grid = self._find_grid()
        rect = grid.rectangle()
        usable_height = max(18, rect.height() - 28)
        row_height = 18
        if row_count and row_count > 0:
            row_height = max(16, min(28, usable_height / row_count))
        row_y = int(rect.top + 28 + index * row_height + row_height / 2)
        row_y = min(max(row_y, rect.top + 18), rect.bottom - 8)
        row_x = rect.left + max(140, rect.width() // 3)
        return row_x, row_y

    def _double_click_grid_row(self, index: int, row_count: int | None = None) -> bool:
        try:
            grid = self._find_grid()
            self._set_foreground(grid)
            pywinauto.keyboard.send_keys("{ESC}")
            time.sleep(0.1)
            pywinauto.keyboard.send_keys("{VK_CONTROL up}{VK_SHIFT up}")
            time.sleep(0.1)
            x, y = self._grid_row_point(index, row_count)
            pywinauto.mouse.double_click(button="left", coords=(x, y))
            time.sleep(0.5)
            return True
        except Exception:
            return False

    def _trigger_cancel_from_grid_row(self, index: int, row_count: int | None = None) -> str | None:
        if self._select_single_grid_row(index, row_count):
            pywinauto.keyboard.send_keys("{DELETE}")
            if self._wait_for_any_dialog(timeout_seconds=2):
                return "selected_row_delete"

        if self._double_click_grid_row(index, row_count):
            if self._wait_for_any_dialog(timeout_seconds=2):
                return "double_click"

        if self._select_single_grid_row(index, row_count):
            pywinauto.keyboard.send_keys("{ENTER}")
            if self._wait_for_any_dialog(timeout_seconds=2):
                return "selected_row_enter"

        return None

    def _select_single_grid_row(self, index: int, row_count: int | None = None) -> bool:
        """Clear clipboard-read multi-selection and select exactly one cancel row."""
        try:
            grid = self._find_grid()
            self._set_foreground(grid)
            rect = grid.rectangle()
            pywinauto.keyboard.send_keys("{ESC}")
            time.sleep(0.1)
            pywinauto.keyboard.send_keys("{VK_CONTROL up}{VK_SHIFT up}")
            time.sleep(0.1)
            x, row_y = self._grid_row_point(index, row_count)
            for click_x in (rect.left + max(80, rect.width() // 5), x):
                pywinauto.mouse.click(button="left", coords=(click_x, row_y))
                time.sleep(0.15)
            return True
        except Exception:
            return False

    def _select_only_cancel_checkbox(self, index: int, row_count: int | None = None) -> bool:
        try:
            grid = self._find_grid()
            self._set_foreground(grid)
            rect = grid.rectangle()
            rows = max(1, int(row_count or 1))
            row_height = max(16, min(28, (rect.height() - 28) / rows))
            checkbox_x = rect.left + 10

            header_y = rect.top + 14
            pywinauto.mouse.click(button="left", coords=(checkbox_x, header_y))
            time.sleep(0.12)
            pywinauto.mouse.click(button="left", coords=(checkbox_x, header_y))
            time.sleep(0.12)

            # The THS cancel grid copies rows in the reverse order of the visible table.
            visual_index = max(0, min(rows - 1, rows - 1 - index))
            target_y = int(rect.top + 28 + visual_index * row_height + row_height / 2)
            pywinauto.mouse.click(button="left", coords=(checkbox_x, target_y))
            time.sleep(0.2)
            pywinauto.mouse.click(button="left", coords=(rect.left + 80, target_y))
            time.sleep(0.2)
            return True
        except Exception:
            return False

    def _wait_for_any_dialog(self, timeout_seconds: float = 2) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() <= deadline:
            if self._handle_captcha_dialog_if_present():
                deadline = time.monotonic() + timeout_seconds
                continue
            if self.is_exist_pop_dialog():
                return True
            time.sleep(0.2)
        return False

    def _set_foreground(self, window: Any) -> None:
        try:
            window.set_focus()
        except Exception:
            try:
                win32gui.SetForegroundWindow(window.handle)
            except Exception:
                pass

    def _handle_captcha_dialog_if_present(self) -> bool:
        dialog = self._find_captcha_dialog()
        if dialog is None:
            return False

        self._set_foreground(dialog)
        if self.captcha_code:
            self._submit_captcha_dialog(dialog, self.captcha_code)
            self._wait_for_dialog_closed(dialog, 10)
        elif self.auto_captcha:
            if self._auto_submit_captcha_dialog(dialog):
                time.sleep(0.8)
                return True
            if self.wait_manual_captcha:
                print(
                    f"验证码自动识别失败，请在同花顺中手动输入验证码并点击确定，"
                    f"脚本将在最多 {self.manual_captcha_timeout} 秒内等待弹窗关闭后继续。",
                    file=sys.stderr,
                    flush=True,
                )
                self._wait_for_dialog_closed(dialog, self.manual_captcha_timeout)
            else:
                raise CaptchaRequiredError("检测到验证码弹窗，但自动识别失败。")
        elif self.wait_manual_captcha:
            print(
                f"检测到验证码弹窗，请在同花顺中手动输入验证码并点击确定，"
                f"脚本将在最多 {self.manual_captcha_timeout} 秒内等待弹窗关闭后继续。",
                file=sys.stderr,
                flush=True,
            )
            self._wait_for_dialog_closed(dialog, self.manual_captcha_timeout)
        else:
            raise CaptchaRequiredError()
        time.sleep(0.8)
        return True

    def _auto_submit_captcha_dialog(self, dialog: Any) -> bool:
        tried_codes: set[str] = set()
        for _ in range(4):
            time.sleep(0.5)
            if not self._is_dialog_still_captcha(dialog):
                return True
            captcha_code = self._recognize_captcha_dialog(dialog)
            if not captcha_code or captcha_code in tried_codes:
                continue
            tried_codes.add(captcha_code)
            self._submit_captcha_dialog(dialog, captcha_code)
            try:
                self._wait_for_dialog_closed(dialog, 4)
                return True
            except TimeoutError:
                continue
        return not self._is_dialog_still_captcha(dialog)

    def _is_dialog_still_captcha(self, dialog: Any) -> bool:
        try:
            return dialog.exists(timeout=0.2) and self._is_captcha_dialog(dialog)
        except Exception:
            return False

    def _find_captcha_dialog(self) -> Any | None:
        app, _ = self.ensure_connected()
        windows: list[Any] = []
        try:
            windows.append(app.top_window())
        except Exception:
            pass
        try:
            windows.extend(app.windows())
        except Exception:
            pass
        try:
            process_id = app.process
            for window in Desktop(backend="win32").windows():
                try:
                    _, window_pid = win32process.GetWindowThreadProcessId(window.handle)
                except Exception:
                    continue
                if window_pid == process_id:
                    windows.append(window)
        except Exception:
            pass

        seen: set[int] = set()
        for window in windows:
            try:
                handle = int(window.handle)
                if handle in seen:
                    continue
                seen.add(handle)
                if window.is_visible() and self._is_captcha_dialog(window):
                    return window
            except Exception:
                continue
        return None

    def _recognize_captcha_dialog(self, dialog: Any) -> str | None:
        try:
            image = self._capture_captcha_image(dialog)
            return self._ocr_captcha_image(image)
        except Exception:
            return None

    def _capture_captcha_image(self, dialog: Any) -> Image.Image:
        edit_rect = None
        candidates: list[Any] = []
        dialog_rect = dialog.rectangle()

        for child in dialog.children():
            try:
                class_name = child.class_name()
                rect = child.rectangle()
                if class_name == "Edit":
                    edit_rect = rect
                    continue
                if class_name == "Button" or not child.is_visible():
                    continue
                width = rect.width()
                height = rect.height()
                text = child.window_text() or ""
                if class_name == "Static" and 30 <= width <= 220 and 14 <= height <= 90 and "验证码" not in text:
                    candidates.append(child)
            except Exception:
                continue

        if edit_rect is not None and candidates:
            nearby_candidates = []
            for child in candidates:
                rect = child.rectangle()
                horizontal_gap = min(abs(rect.left - edit_rect.right), abs(edit_rect.left - rect.right))
                vertical_overlap = min(rect.bottom, edit_rect.bottom) - max(rect.top, edit_rect.top)
                if horizontal_gap <= 18 and vertical_overlap > 0:
                    nearby_candidates.append(child)
            if nearby_candidates:
                target = max(nearby_candidates, key=lambda item: item.rectangle().width() * item.rectangle().height())
                rect = target.rectangle()
                return ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))

        if edit_rect is not None:
            left = max(dialog_rect.left, edit_rect.left - 180)
            top = max(dialog_rect.top, edit_rect.top - 8)
            right = min(dialog_rect.right, edit_rect.left - 4)
            bottom = min(dialog_rect.bottom, edit_rect.bottom + 8)
            if right - left >= 30 and bottom - top >= 14:
                return ImageGrab.grab(bbox=(left, top, right, bottom))

        return ImageGrab.grab(
            bbox=(
                dialog_rect.left + 20,
                dialog_rect.top + 45,
                dialog_rect.right - 20,
                min(dialog_rect.bottom - 45, dialog_rect.top + 145),
            )
        )

    def _ocr_captcha_image(self, image: Image.Image) -> str | None:
        pytesseract = self._load_pytesseract()
        if pytesseract is None:
            return None

        candidates = self._preprocess_captcha_images(image)
        configs = [
            "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789",
            "--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789",
            "--oem 3 --psm 13 -c tessedit_char_whitelist=0123456789",
        ]
        for candidate in candidates:
            for config in configs:
                try:
                    text = pytesseract.image_to_string(candidate, config=config)
                except Exception:
                    continue
                code = re.sub(r"\D", "", text or "")
                if 4 <= len(code) <= 6:
                    return code
        return None

    def _load_pytesseract(self) -> Any | None:
        try:
            import pytesseract
        except Exception:
            return None

        tesseract_cmd = os.getenv("TESSERACT_CMD") or os.getenv("TESSERACT_OCR_PATH")
        if not tesseract_cmd:
            default_tesseract = Path(r"E:\Tesseract-OCR\tesseract.exe")
            if default_tesseract.exists():
                tesseract_cmd = str(default_tesseract)
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        return pytesseract

    def _preprocess_captcha_images(self, image: Image.Image) -> list[Image.Image]:
        base = image.convert("L")
        base = ImageEnhance.Contrast(base).enhance(2.4)
        base = base.resize((base.width * 3, base.height * 3))
        denoised = base.filter(ImageFilter.MedianFilter(size=3))
        variants = [denoised]

        for threshold in (120, 145, 170, 195):
            variants.append(denoised.point(lambda pixel, limit=threshold: 255 if pixel > limit else 0))
            variants.append(denoised.point(lambda pixel, limit=threshold: 0 if pixel > limit else 255))

        try:
            import cv2
            import numpy as np

            array = np.array(base)
            array = cv2.GaussianBlur(array, (3, 3), 0)
            _, otsu = cv2.threshold(array, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            adaptive = cv2.adaptiveThreshold(
                array,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31,
                8,
            )
            kernel = np.ones((2, 2), np.uint8)
            variants.extend(
                [
                    Image.fromarray(otsu),
                    Image.fromarray(cv2.bitwise_not(otsu)),
                    Image.fromarray(cv2.morphologyEx(otsu, cv2.MORPH_OPEN, kernel)),
                    Image.fromarray(adaptive),
                    Image.fromarray(cv2.bitwise_not(adaptive)),
                ]
            )
        except Exception:
            pass

        return variants

    def _is_captcha_dialog(self, dialog: Any) -> bool:
        try:
            text = " ".join(
                [dialog.window_text() or ""]
                + [child.window_text() or "" for child in dialog.children()]
            )
        except Exception:
            return False
        return "验证码" in text or "账号数据安全" in text

    def _wait_for_dialog_closed(self, dialog: Any, timeout_seconds: int) -> None:
        start = time.monotonic()
        while time.monotonic() - start <= timeout_seconds:
            try:
                if not dialog.exists(timeout=0.2) or not self._is_captcha_dialog(dialog):
                    return
            except Exception:
                return
            time.sleep(0.5)
        raise TimeoutError(f"等待人工输入验证码超时: {timeout_seconds} 秒")

    def _submit_captcha_dialog(self, dialog: Any, captcha_code: str) -> None:
        edit = None
        for child in dialog.children():
            try:
                if child.class_name() == "Edit":
                    edit = child
                    break
            except Exception:
                continue
        if edit is None:
            raise RuntimeError("检测到验证码弹窗，但未找到验证码输入框")

        hwnd = edit.handle
        for _ in range(8):
            win32gui.SendMessage(hwnd, win32con.WM_CHAR, 8, 0)
            time.sleep(0.01)
        for ch in captcha_code.strip():
            win32gui.SendMessage(hwnd, win32con.WM_CHAR, ord(ch), 0)
            time.sleep(0.03)

        for child in dialog.children():
            try:
                if child.class_name() == "Button" and "确定" in (child.window_text() or ""):
                    child.click()
                    return
            except Exception:
                continue
        raise RuntimeError("检测到验证码弹窗，但未找到确定按钮")

    def _get_dialog_title(self, dialog: Any) -> str:
        try:
            return dialog.child_window(
                control_id=self.POP_DIALOG_TITLE_CONTROL_ID
            ).window_text()
        except Exception:
            return dialog.window_text() or ""

    def _extract_dialog_content(self, dialog: Any) -> str:
        texts: list[str] = []
        for child in dialog.children():
            try:
                if child.class_name() == "Static":
                    text = child.window_text()
                    if text:
                        texts.append(text)
            except Exception:
                continue
        return " ".join(texts)

    def _assert_cancel_dialog_matches(self, content: str, expected_entrust_no: str | None) -> None:
        if not expected_entrust_no:
            return
        if self._looks_like_bulk_cancel(content):
            raise TradingClientNotReadyError(
                f"同花顺弹窗疑似全部/批量撤单确认，已拒绝自动确认。目标合同编号: {expected_entrust_no}；弹窗内容: {content}"
            )
        numbers = re.findall(r"\d{8,}", content or "")
        if numbers and expected_entrust_no not in numbers:
            raise TradingClientNotReadyError(
                f"同花顺撤单确认弹窗合同编号与目标不一致，已拒绝自动确认。目标合同编号: {expected_entrust_no}；弹窗内容: {content}"
            )

    def _looks_like_bulk_cancel(self, content: str) -> bool:
        text = str(content or "")
        bulk_keywords = ("全部撤", "全撤", "批量", "所有", "全部委托", "全部可撤")
        return any(keyword in text for keyword in bulk_keywords)

    def _submit_dialog_by_shortcut(self, dialog: Any) -> None:
        self._set_foreground(dialog)
        try:
            dialog.type_keys("%Y", set_foreground=False)
            time.sleep(0.2)
            if not self._dialog_exists(dialog):
                return
        except Exception:
            pass
        try:
            dialog.type_keys("{ENTER}", set_foreground=False)
            time.sleep(0.2)
            if not self._dialog_exists(dialog):
                return
        except Exception:
            pass
        self._click_confirm(dialog)

    def _dialog_exists(self, dialog: Any) -> bool:
        try:
            return dialog.exists(timeout=0.2)
        except Exception:
            return False

    def _click_confirm(self, dialog: Any) -> None:
        for child in dialog.children():
            try:
                text = child.window_text() or ""
                if child.class_name() == "Button" and any(label in text for label in ("确定", "是", "确认", "OK")):
                    child.click_input()
                    return
            except Exception:
                continue
        try:
            dialog["确定"].click_input()
        except Exception:
            pass

    def _extract_entrust_no(self, content: str) -> str | None:
        match = re.search(r"[\da-zA-Z]+", content)
        return match.group() if match else None

    def _match_rows(self, rows: Any, request: ThsOrderRequest) -> list[dict[str, Any]]:
        if not isinstance(rows, list):
            return []
        symbol = request.symbol[-6:]
        matched: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_symbol = str(row.get("证券代码", "")).strip()
            row_quantity = int(float(row.get("委托数量", 0) or 0))
            row_price = Decimal(str(row.get("委托价格", 0) or 0))
            if row_symbol and row_symbol != symbol:
                continue
            if row_quantity and row_quantity != request.quantity:
                continue
            if row_price and abs(row_price - request.price) > Decimal("0.001"):
                continue
            if row_symbol or row_quantity or row_price:
                matched.append(row)
        return matched


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="THS desktop adapter CLI")
    parser.add_argument("--client-path", required=True)
    parser.add_argument(
        "--operation",
        required=True,
        choices=[
            "status",
            "balance",
            "positions",
            "orders",
            "trades",
            "history-orders",
            "history-trades",
            "buy",
            "sell",
            "cancel",
        ],
    )
    parser.add_argument("--symbol")
    parser.add_argument("--price")
    parser.add_argument("--quantity", type=int)
    parser.add_argument("--entrust-no")
    parser.add_argument("--captcha-code", help="人工识别后的验证码")
    parser.add_argument(
        "--wait-manual-captcha",
        action="store_true",
        help="检测到验证码弹窗时等待人工在同花顺中输入并确认，然后继续流程",
    )
    parser.add_argument(
        "--manual-captcha-timeout",
        type=int,
        default=120,
        help="等待人工处理验证码弹窗的最长秒数，默认 120",
    )
    args = parser.parse_args()

    adapter = ThsDesktopAdapter(
        args.client_path,
        captcha_code=args.captcha_code,
        wait_manual_captcha=args.wait_manual_captcha,
        manual_captcha_timeout=args.manual_captcha_timeout,
    )
    adapter.connect()

    if args.operation == "status":
        result = adapter.status()
    elif args.operation == "balance":
        result = adapter.get_balance()
    elif args.operation == "positions":
        result = adapter.get_positions()
    elif args.operation == "orders":
        result = adapter.get_today_orders()
    elif args.operation == "trades":
        result = adapter.get_today_trades()
    elif args.operation == "history-orders":
        result = adapter.get_history_orders()
    elif args.operation == "history-trades":
        result = adapter.get_history_trades()
    elif args.operation in {"buy", "sell"}:
        if not args.symbol or not args.price or args.quantity is None:
            raise SystemExit("buy/sell requires --symbol --price --quantity")
        if args.operation == "buy":
            result = adapter.buy(args.symbol, args.price, args.quantity)
        else:
            result = adapter.sell(args.symbol, args.price, args.quantity)
    elif args.operation == "cancel":
        result = adapter.cancel_order(args.entrust_no)
    else:
        raise SystemExit(f"unsupported operation: {args.operation}")

    print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
