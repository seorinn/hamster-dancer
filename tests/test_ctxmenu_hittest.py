"""
tests/test_ctxmenu_hittest.py

_CtxMenu._on_global_click 히트 테스트 통합 테스트.
- GUI/Tk 초기화 없이 실행 가능.
- _CtxMenu.__new__(_CtxMenu)로 Tk 없이 인스턴스를 만들고
  _top_menu/_root/_all/winfo_* 등을 mock 주입해 실제 메서드를 호출한다.
- winfo_* / _physical_to_logical 는 mock으로 대체.
- 연속 클릭·라이프사이클 경합(destroy 후 winfo 호출) 시나리오 포함.
"""

import sys
import types
import ctypes
import ctypes.wintypes
import unittest
from unittest.mock import patch, MagicMock


# ── 의존성 스텁 ───────────────────────────────────────────────────────────────
# main.py 모듈 수준 임포트가 Tk() 등을 초기화하지 않도록 무거운 의존성을 차단한다.

def _stub_module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules.setdefault(name, m)


# PIL 스텁 — Image.Image 클래스 포함 (type annotation에 사용됨)
class _ImageStub:
    pass

_pil = types.ModuleType('PIL')
_pil_image = types.ModuleType('PIL.Image')
_pil_image.LANCZOS = 1
_pil_image.Image = _ImageStub   # create_tray_icon_image() -> Image.Image 어노테이션 대응
_pil_image.new = MagicMock()
_pil_image.open = MagicMock()
_pil_image.merge = MagicMock()
_pil_imagetk = types.ModuleType('PIL.ImageTk')
_pil_imagetk.PhotoImage = MagicMock()
_pil_imagedraw = types.ModuleType('PIL.ImageDraw')
_pil_imagedraw.Draw = MagicMock()
for _m in (_pil, _pil_image, _pil_imagetk, _pil_imagedraw):
    sys.modules.setdefault(_m.__name__, _m)

# pystray 스텁
_pystray = types.ModuleType('pystray')
_pystray.Icon = MagicMock()
_pystray.Menu = MagicMock()
_pystray.MenuItem = MagicMock()
sys.modules.setdefault('pystray', _pystray)

# pynput 스텁
_pynput = types.ModuleType('pynput')
_pynput_kb = types.ModuleType('pynput.keyboard')
_pynput_kb.Listener = MagicMock()
_pynput_kb.Key = MagicMock()
_pynput_m = types.ModuleType('pynput.mouse')
_pynput_m.Listener = MagicMock()
for _m in (_pynput, _pynput_kb, _pynput_m):
    sys.modules.setdefault(_m.__name__, _m)

# tkinter 스텁 — Toplevel을 일반 object 기반 클래스로 스텁해 __new__ 가 동작하게 한다.
class _ToplevelStub:
    pass

_tk_stub = types.ModuleType('tkinter')
_tk_stub.Tk = MagicMock()
_tk_stub.Toplevel = _ToplevelStub
_tk_stub.Frame = MagicMock()
_tk_stub.Label = MagicMock()
_tk_stub.Canvas = MagicMock()
_tk_stub.StringVar = MagicMock()
_tk_stub.IntVar = MagicMock()
_tk_stub.BooleanVar = MagicMock()
_tk_stub.filedialog = MagicMock()
_tk_stub.messagebox = MagicMock()
sys.modules['tkinter'] = _tk_stub

# ── main.py 임포트 및 _CtxMenu 취득 ─────────────────────────────────────────
import importlib
import sys as _sys

# 캐시된 main 모듈이 있더라도, 그 모듈이 _ToplevelStub이 아닌 Toplevel(예: MagicMock)
# 기반으로 로드된 경우(test_physical_to_logical이 먼저 실행된 경우) _CtxMenu.__new__가
# MagicMock 인스턴스를 반환해 AttributeError가 발생한다.
# sys.modules['tkinter']를 이미 덮어썼으므로, 캐시된 main이 있어도 Toplevel 확인 후
# 필요 시 강제 재로드한다.
def _need_reload():
    if 'main' not in sys.modules:
        return True
    try:
        import inspect
        _cached = sys.modules['main']
        # _CtxMenu의 기반 클래스가 _ToplevelStub인지 확인
        return not issubclass(_cached._CtxMenu, _ToplevelStub)
    except Exception:
        return True

if _need_reload():
    import importlib.util, pathlib
    # 기존 캐시 제거 후 재로드
    sys.modules.pop('main', None)
    _spec = importlib.util.spec_from_file_location(
        'main',
        str(pathlib.Path(__file__).parent.parent / 'main.py'),
    )
    _main_mod = importlib.util.module_from_spec(_spec)
    sys.modules['main'] = _main_mod
    _spec.loader.exec_module(_main_mod)

_main = sys.modules['main']
_CtxMenu = _main._CtxMenu
_physical_to_logical = _main._physical_to_logical


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _make_win(rx, ry, rw, rh, winfo_id_val=1234):
    """지정한 사각형(rootx, rooty, width, height)을 반환하는 mock 창."""
    w = MagicMock()
    w.winfo_rootx.return_value = rx
    w.winfo_rooty.return_value = ry
    w.winfo_width.return_value = rw
    w.winfo_height.return_value = rh
    w.winfo_id.return_value = winfo_id_val
    return w


def _make_ctx_instance(wins):
    """
    _CtxMenu.__new__(_CtxMenu)로 Tk 없이 인스턴스를 만들고
    _on_global_click 동작에 필요한 속성만 mock 주입하여 반환한다.

    wins : list[MagicMock]  — _all() 이 반환할 창 목록
    """
    menu = _CtxMenu.__new__(_CtxMenu)
    menu._root = MagicMock()
    menu._closed = False
    menu._top_menu = menu

    # _all() 은 wins 목록을 그대로 반환
    menu._all = lambda: wins

    # _close() 는 실제 구현이 없으니 mock 으로 대체
    menu._close = MagicMock()

    return menu


def _identity_ptl_patches():
    """PhysicalToLogicalPointForPerMonitorDPI 를 항등 함수로 패치하는 컨텍스트 쌍."""
    # 항등 변환 전제 검증: ctypes.wintypes.POINT(x, y) 생성자가 x/y 인자를 실제 필드에 저장해야 한다.
    # 이 단언이 실패하면 fake_ptl이 항등이 아닌 (0,0) 변환이 되어 모든 관련 테스트 결과가 뒤집힌다.
    _pt_check = ctypes.wintypes.POINT(42, 99)
    assert _pt_check.x == 42 and _pt_check.y == 99, (
        f"ctypes.wintypes.POINT 생성자 가정 위반: expected (42, 99), got ({_pt_check.x}, {_pt_check.y}). "
        "fake_ptl 항등 변환 전제가 깨졌으므로 이 환경에서는 _identity_ptl_patches를 사용할 수 없다."
    )

    def fake_get_ancestor(hwnd, flag):
        return hwnd

    def fake_ptl(hwnd, byref_pt):
        # POINT 필드를 수정하지 않고 성공(1)을 반환한다.
        # _physical_to_logical은 ok=True 시 pt.x, pt.y를 읽으므로,
        # ctypes.wintypes.POINT(x, y) 초기화 값이 그대로 반환 → 항등 변환.
        # 전제: 위 _pt_check 단언으로 런타임에 검증됨.
        #
        # 한계: fake_ptl은 byref_pt를 전혀 읽거나 쓰지 않으므로,
        # main.py가 ctypes.byref(pt)에 올바른 POINT 인스턴스를 전달하는지
        # (즉 변환 함수에 올바른 포인터가 넘어가는지)는 이 mock으로는 검증되지 않는다.
        # 해당 경로(byref → cast → 필드 수정)는 test_physical_to_logical.py의
        # test_normal_conversion이 커버하므로 전체적으로 빈틈은 없다.
        return 1

    return (
        patch.object(ctypes.windll.user32, 'GetAncestor', fake_get_ancestor),
        patch.object(ctypes.windll.user32, 'PhysicalToLogicalPointForPerMonitorDPI', fake_ptl),
    )


def _run_click(menu, x, y, pressed=True):
    """클릭을 실행하고 _root.after 가 호출됐는지(= 닫기 예약 여부) 반환."""
    button = MagicMock()
    menu._root.after.reset_mock()
    menu._on_global_click(x, y, button, pressed)
    return menu._root.after.called


# ── 테스트 ────────────────────────────────────────────────────────────────────

class TestOnGlobalClickHitTest(unittest.TestCase):
    """_CtxMenu._on_global_click 히트 판정 정확성 테스트 (실제 메서드 호출)."""

    # ── 기본 히트 판정 ─────────────────────────────────────────────────────────

    def test_click_inside_single_window_does_not_close(self):
        """창 내부 클릭 시 _close 가 예약되지 않아야 한다."""
        win = _make_win(100, 100, 200, 150)
        menu = _make_ctx_instance([win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            triggered = _run_click(menu, 150, 130)  # 내부

        self.assertFalse(triggered, "내부 클릭이므로 after(0, _close)가 호출되면 안 된다.")

    def test_click_outside_single_window_triggers_close(self):
        """창 외부 클릭 시 after(0, _close) 가 호출되어야 한다."""
        win = _make_win(100, 100, 200, 150)
        menu = _make_ctx_instance([win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            triggered = _run_click(menu, 50, 50)  # 외부

        self.assertTrue(triggered, "외부 클릭이므로 after(0, _close)가 호출되어야 한다.")

    def test_click_on_window_border_inside(self):
        """창 경계(왼쪽 상단 모서리)는 내부로 판정되어야 한다."""
        win = _make_win(100, 100, 200, 150)  # right=300, bottom=250
        menu = _make_ctx_instance([win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            # 정확히 (100, 100) — 경계값
            triggered = _run_click(menu, 100, 100)

        self.assertFalse(triggered, "경계값 (100,100)은 내부이므로 닫히면 안 된다.")

    def test_release_event_ignored(self):
        """pressed=False(마우스 버튼 뗌) 이벤트는 아무 동작도 하지 않아야 한다."""
        win = _make_win(100, 100, 200, 150)
        menu = _make_ctx_instance([win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            triggered = _run_click(menu, 50, 50, pressed=False)

        self.assertFalse(triggered, "pressed=False 이벤트는 무시되어야 한다.")

    # ── 서브메뉴(다중 창) 히트 판정 ───────────────────────────────────────────

    def test_click_inside_submenu_does_not_close(self):
        """서브메뉴 내부 클릭 시에도 닫히지 않아야 한다."""
        main_win = _make_win(100, 100, 200, 150, winfo_id_val=1111)
        sub_win  = _make_win(302, 110, 180, 120, winfo_id_val=2222)
        menu = _make_ctx_instance([main_win, sub_win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            # 서브메뉴 내부
            triggered = _run_click(menu, 380, 140)

        self.assertFalse(triggered, "서브메뉴 내부 클릭이므로 닫히면 안 된다.")

    def test_click_between_windows_triggers_close(self):
        """메인 창과 서브메뉴 사이 빈 공간 클릭 시 닫혀야 한다."""
        main_win = _make_win(100, 100, 200, 150)
        sub_win  = _make_win(302, 110, 180, 120)
        menu = _make_ctx_instance([main_win, sub_win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            # 두 창 사이 빈 공간 (x=301 은 main_win 오른쪽 바깥, sub_win 왼쪽 바깥)
            triggered = _run_click(menu, 301, 140)

        self.assertTrue(triggered, "두 창 사이 빈 공간 클릭이므로 닫혀야 한다.")

    # ── 좌표 변환 적용 확인 ────────────────────────────────────────────────────

    def test_dpi_scaled_click_inside_after_conversion(self):
        """150% 배율 시뮬레이션: 물리 좌표 → 논리 좌표 변환 후 내부로 판정되어야 한다.

        창 논리 좌표: (100, 100, w=200, h=200).  논리 범위: 100~300.
        물리 좌표 (375, 375) ÷ 1.5 = 논리 (250, 250) → 내부.
        변환 없이 물리 좌표 직접 사용 시: 375 > 300 → 외부.
        → 변환 경로가 실제로 적용되지 않으면 이 테스트는 실패한다.
        """
        win = _make_win(100, 100, 200, 200, winfo_id_val=9999)
        menu = _make_ctx_instance([win])

        SCALE = 1.5

        def fake_get_ancestor(hwnd, flag):
            return hwnd

        def fake_ptl(hwnd, byref_pt):
            pt = ctypes.cast(byref_pt, ctypes.POINTER(ctypes.wintypes.POINT)).contents
            # 물리 → 논리: 배율로 나누기
            pt.x = int(round(pt.x / SCALE))
            pt.y = int(round(pt.y / SCALE))
            return 1

        with patch.object(ctypes.windll.user32, 'GetAncestor', fake_get_ancestor), \
             patch.object(ctypes.windll.user32, 'PhysicalToLogicalPointForPerMonitorDPI', fake_ptl):
            # 물리 (375, 375) → 논리 (250, 250) → 창(100,100,w200,h200) 내부 ✓
            # 변환 없이 (375, 375) → 창 외부(x 범위 100~300) ✗
            triggered = _run_click(menu, 375, 375)

        self.assertFalse(triggered,
                         "변환 후 내부로 판정되므로 닫히면 안 된다 (고DPI 버그 수정 검증).")

    def test_dpi_scaled_click_outside_after_conversion(self):
        """150% 배율 시뮬레이션: 변환 후에도 외부인 클릭은 닫혀야 한다.

        물리 (60, 60) ÷ 1.5 = 논리 (40, 40) → 창 범위(100~300) 밖 → 외부.
        """
        win = _make_win(100, 100, 200, 200, winfo_id_val=9999)
        menu = _make_ctx_instance([win])

        SCALE = 1.5

        def fake_get_ancestor(hwnd, flag):
            return hwnd

        def fake_ptl(hwnd, byref_pt):
            pt = ctypes.cast(byref_pt, ctypes.POINTER(ctypes.wintypes.POINT)).contents
            pt.x = int(round(pt.x / SCALE))
            pt.y = int(round(pt.y / SCALE))
            return 1

        with patch.object(ctypes.windll.user32, 'GetAncestor', fake_get_ancestor), \
             patch.object(ctypes.windll.user32, 'PhysicalToLogicalPointForPerMonitorDPI', fake_ptl):
            # 물리 (60, 60) → 논리 (40, 40) → 외부
            triggered = _run_click(menu, 60, 60)

        self.assertTrue(triggered, "변환 후에도 외부이므로 닫혀야 한다.")

    # ── winfo 폴백 경로 (창 파괴 중 경합) ────────────────────────────────────

    def test_winfo_raises_during_destroy_treated_as_miss(self):
        """창 파괴 중 winfo_rootx 등이 예외를 던지면 해당 창은 miss로 처리한다.
        (현행 정책: 파괴 중인 창은 어차피 닫히는 중이므로 miss로 계속 진행)
        """
        # 정상 창 1개 + 파괴 중인 창 1개
        good_win = _make_win(100, 100, 200, 150)
        dying_win = MagicMock()
        dying_win.winfo_id.return_value = 5555
        dying_win.winfo_rootx.side_effect = Exception('TclError: window destroyed')
        # winfo_rooty는 설정하지 않는다.
        # winfo_rootx 예외가 try/except(L233)에서 즉시 catch되어 winfo_rooty는 호출되지 않는다.

        # 파괴 중인 창이 _all() 결과에 포함된 상태
        menu = _make_ctx_instance([good_win, dying_win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            # good_win 내부 클릭 — dying_win은 예외 → miss로 pass, good_win에서 hit
            triggered = _run_click(menu, 150, 130)

        self.assertFalse(triggered, "good_win 내부 클릭이므로 닫히면 안 된다.")

    def test_winfo_id_raises_during_destroy_treated_as_miss(self):
        """창 파괴가 더 일찍 진행되어 winfo_id() 자체가 TclError를 던지면
        dying_win 이 miss 로 처리되어야 한다.

        실행 경로:
          1. _physical_to_logical 내부에서 winfo_id() 예외 → 내부 except 가 잡고 (x,y) 폴백 반환.
          2. _on_global_click 루프에서 이후 winfo_rootx() 도 예외 → 외부 try/except(L234) 가 잡고
             해당 창을 miss 로 처리 후 계속 진행.
          3. dying_win 이 리스트 첫 번째에 있어도 good_win 내부 클릭이면 닫히지 않아야 한다.

        dying_win 은 winfo_id 와 winfo_rootx 모두 예외를 던지도록 설정하여
        outer try/except 경로가 실제로 타지는지를 검증한다.
        """
        good_win = _make_win(100, 100, 200, 150)
        dying_win = MagicMock()
        dying_win.winfo_id.side_effect = Exception('TclError: window destroyed')
        # _physical_to_logical 이 폴백 후 winfo_rootx 도 파괴 중 예외를 던짐 →
        # _on_global_click 외부 try/except 가 dying_win 을 miss 로 처리한다.
        dying_win.winfo_rootx.side_effect = Exception('TclError: window destroyed')

        # dying_win 이 첫 번째: miss 처리 → good_win 에서 hit 확인
        menu = _make_ctx_instance([dying_win, good_win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            triggered = _run_click(menu, 150, 130)

        self.assertFalse(triggered, "dying_win 은 miss, good_win 내부 클릭이므로 닫히면 안 된다.")

    def test_all_windows_raise_triggers_close(self):
        """모든 창이 winfo 예외를 던지면(모두 miss) 외부 클릭으로 판정되어 닫혀야 한다."""
        dying1 = MagicMock()
        dying1.winfo_id.return_value = 1
        dying1.winfo_rootx.side_effect = Exception('TclError')

        dying2 = MagicMock()
        dying2.winfo_id.return_value = 2
        dying2.winfo_rootx.side_effect = Exception('TclError')

        menu = _make_ctx_instance([dying1, dying2])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            triggered = _run_click(menu, 150, 130)

        self.assertTrue(triggered, "모든 창이 miss이면 닫혀야 한다.")

    # ── 연속 클릭 경합 ────────────────────────────────────────────────────────

    def test_consecutive_outside_clicks_schedules_close_each_time(self):
        """_closed=False 상태에서 외부 클릭이 연속으로 오면 매번 after(0, _close)가 예약된다."""
        win = _make_win(100, 100, 200, 150)
        menu = _make_ctx_instance([win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            menu._on_global_click(50, 50, MagicMock(), True)
            menu._on_global_click(60, 60, MagicMock(), True)

        # after(0, _close)가 2번 예약되어야 한다
        self.assertEqual(menu._root.after.call_count, 2)

    def test_rapid_open_close_no_crash(self):
        """빠른 열기→닫기 반복 시 예외 없이 동작하고, 외부 클릭마다 after(0, _close)가 예약되어야 한다."""
        for _ in range(10):
            win = _make_win(100, 100, 200, 150)
            menu = _make_ctx_instance([win])
            # 외부 클릭 → after(0, _close) 예약 확인
            p1, p2 = _identity_ptl_patches()
            with p1, p2:
                menu._on_global_click(50, 50, MagicMock(), True)
            # 각 이터레이션에서 외부 클릭이므로 after()가 반드시 호출되어야 한다
            self.assertTrue(menu._root.after.called,
                            f"이터레이션 {_}: 외부 클릭에서 after(0, _close)가 호출되지 않았다.")

    # ── _physical_to_logical 변환 실패 폴백 경로 ──────────────────────────────

    def test_conversion_failure_falls_back_to_physical_coords(self):
        """PhysicalToLogicalPointForPerMonitorDPI 실패 시 원본 물리 좌표로 비교한다.
        창이 논리 좌표(100~300, 100~250) 기준이고 물리 좌표가 같은 범위에 있으면
        폴백에서도 hit 로 판정된다 (배율 100% 등가 상황).
        """
        win = _make_win(100, 100, 200, 150)
        menu = _make_ctx_instance([win])

        def fake_get_ancestor(hwnd, flag):
            return hwnd

        def fake_ptl_fail(hwnd, byref_pt):
            return 0  # 실패

        with patch.object(ctypes.windll.user32, 'GetAncestor', fake_get_ancestor), \
             patch.object(ctypes.windll.user32, 'PhysicalToLogicalPointForPerMonitorDPI',
                          fake_ptl_fail):
            # 물리 좌표가 논리 창 범위 내 → 폴백에서 hit
            triggered = _run_click(menu, 150, 130)

        self.assertFalse(triggered, "폴백에서도 좌표가 창 안이면 닫히면 안 된다.")

    # ── 우측/하단 경계값 (inclusive) 시맨틱 명문화 ───────────────────────────────

    def test_click_on_right_bottom_border_inside(self):
        """우측/하단 경계(rx+width, ry+height)는 내부(inclusive)로 판정되어야 한다.

        main.py L231: rx <= lx <= rx + win.winfo_width() (<=, inclusive)
        좌표 (300, 250) = (rx+width, ry+height) 가 내부로 판정되는지 확인한다.
        이 테스트는 현행 <= 시맨틱을 고정하여 후속 수정 시 회귀를 감지한다.
        """
        win = _make_win(100, 100, 200, 150)  # right=300, bottom=250
        menu = _make_ctx_instance([win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            triggered = _run_click(menu, 300, 250)

        self.assertFalse(triggered, "경계값 (300,250)=우하단 모서리는 내부(inclusive)이므로 닫히면 안 된다.")

    def test_click_just_outside_right_bottom_border(self):
        """우측/하단 경계 바로 바깥(rx+width+1, ry+height+1)은 외부로 판정되어야 한다."""
        win = _make_win(100, 100, 200, 150)  # right=300, bottom=250
        menu = _make_ctx_instance([win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            triggered = _run_click(menu, 301, 251)

        self.assertTrue(triggered, "경계 바깥 (301,251)은 외부이므로 닫혀야 한다.")

    # ── _physical_to_logical 성공 후 winfo_rootx 실패 경로 ───────────────────

    def test_ptl_success_then_winfo_rootx_raises_treated_as_miss(self):
        """_physical_to_logical 변환 성공(lx,ly 확정) 후 winfo_rootx()가 예외를 던지면
        해당 창은 miss로 처리되어야 한다(outer try/except 정책).

        경로: ptl 성공 → lx,ly 설정됨 → winfo_rootx() 예외 → outer except → miss → 계속 진행.
        다른 창이 없으면 전체 miss → 닫기 예약.
        """
        dying_win = MagicMock()
        dying_win.winfo_id.return_value = 7777
        # _physical_to_logical은 성공(winfo_id 정상) → outer 루프로 lx,ly 반환
        # 이후 winfo_rootx()가 예외 → outer except가 catch
        dying_win.winfo_rootx.side_effect = Exception('TclError: window destroyed after ptl')

        menu = _make_ctx_instance([dying_win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            triggered = _run_click(menu, 150, 130)

        self.assertTrue(triggered,
                        "_physical_to_logical 성공 후 winfo_rootx 실패 시 해당 창은 miss → 닫혀야 한다.")

    def test_ptl_success_then_winfo_rootx_raises_other_window_hit(self):
        """_physical_to_logical 성공 후 winfo_rootx 실패인 창이 있어도 다른 창 hit 시 닫히지 않아야 한다."""
        dying_win = MagicMock()
        dying_win.winfo_id.return_value = 7778
        dying_win.winfo_rootx.side_effect = Exception('TclError: destroyed')

        good_win = _make_win(100, 100, 200, 150)
        menu = _make_ctx_instance([dying_win, good_win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            triggered = _run_click(menu, 150, 130)  # good_win 내부

        self.assertFalse(triggered, "dying_win은 miss이나 good_win 내부 클릭이므로 닫히면 안 된다.")

    # ── winfo_id 예외 폴백 → winfo_rootx 정상 → 히트 판정 경로 ─────────────────

    def test_winfo_id_raises_fallback_then_winfo_rootx_normal_hit(self):
        """winfo_id() 예외 → _physical_to_logical 원본 좌표 폴백 → winfo_rootx 정상 → hit.

        경로: winfo_id() 예외 → _physical_to_logical 내부 except → (x,y) 반환(폴백)
             → _on_global_click 루프에서 winfo_rootx/y 정상 → 좌표 비교 → hit → 닫히지 않음.
        폴백 좌표가 올바르게 히트 판정에 사용되는지 hittest 수준에서 검증한다.
        """
        fallback_win = MagicMock()
        fallback_win.winfo_id.side_effect = Exception('TclError: winfo_id failed')
        # 폴백 후 물리 좌표(150, 130)로 비교 → (100,100,w=200,h=150) 안이므로 hit
        fallback_win.winfo_rootx.return_value = 100
        fallback_win.winfo_rooty.return_value = 100
        fallback_win.winfo_width.return_value = 200
        fallback_win.winfo_height.return_value = 150

        menu = _make_ctx_instance([fallback_win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            # winfo_id 예외 → 폴백 물리 좌표 (150,130) → 창 내부 → hit
            triggered = _run_click(menu, 150, 130)

        self.assertFalse(triggered,
                         "winfo_id 예외 폴백 후 물리 좌표가 창 안이면 hit → 닫히면 안 된다.")

    def test_winfo_id_raises_fallback_then_winfo_rootx_normal_miss(self):
        """winfo_id() 예외 → 폴백 → winfo_rootx 정상 → 폴백 좌표가 창 밖 → miss → 닫혀야 한다."""
        fallback_win = MagicMock()
        fallback_win.winfo_id.side_effect = Exception('TclError: winfo_id failed')
        fallback_win.winfo_rootx.return_value = 100
        fallback_win.winfo_rooty.return_value = 100
        fallback_win.winfo_width.return_value = 200
        fallback_win.winfo_height.return_value = 150

        menu = _make_ctx_instance([fallback_win])

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            # 폴백 물리 좌표 (50, 50) → 창 밖(100~300, 100~250) → miss
            triggered = _run_click(menu, 50, 50)

        self.assertTrue(triggered,
                        "winfo_id 예외 폴백 후 물리 좌표가 창 밖이면 miss → 닫혀야 한다.")

    # ── after() 자체 예외 방어 ────────────────────────────────────────────────

    def test_after_raises_does_not_crash(self):
        """메뉴 밖 클릭 시 _root.after()가 예외를 던져도 크래시가 없어야 한다.
        (pynput 스레드에서 root가 이미 파괴된 경우 방어 — L236 try/except 검증)
        """
        win = _make_win(100, 100, 200, 150)
        menu = _make_ctx_instance([win])
        menu._root.after.side_effect = Exception('application has been destroyed')

        p1, p2 = _identity_ptl_patches()
        with p1, p2:
            # 외부 클릭 — after()가 예외를 던져도 예외가 전파되면 안 된다.
            try:
                menu._on_global_click(50, 50, MagicMock(), True)
            except Exception as e:
                self.fail(f"_on_global_click이 예외를 전파했다: {e}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
