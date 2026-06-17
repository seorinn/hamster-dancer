"""
tests/test_physical_to_logical.py

_physical_to_logical 헬퍼 단위 테스트.
- GUI 없이 실행 가능 (tkinter 창 불필요).
- ctypes / pynput 의존 동작은 mock으로 대체.
"""
import sys
import types
import ctypes
import ctypes.wintypes
import unittest
from unittest.mock import patch, MagicMock


# ── main.py 임포트 준비 ────────────────────────────────────────────────────────
# main.py는 모듈 수준에서 tkinter·pystray·pynput·PIL 등을 임포트하고
# GifPet() 인스턴스를 생성하지 않지만 Tk() 호출 없이 함수 정의 구역까지만
# 로드하면 충분하므로, 무거운 의존성은 sys.modules 스텁으로 차단한다.

def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules.setdefault(name, m)


# --- PIL 스텁 ---
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

# --- pystray 스텁 ---
_pystray = types.ModuleType('pystray')
_pystray.Icon = MagicMock()
_pystray.Menu = MagicMock()
_pystray.MenuItem = MagicMock()
sys.modules.setdefault('pystray', _pystray)

# --- pynput 스텁 ---
_pynput = types.ModuleType('pynput')
_pynput_kb = types.ModuleType('pynput.keyboard')
_pynput_kb.Listener = MagicMock()
_pynput_kb.Key = MagicMock()
_pynput_m = types.ModuleType('pynput.mouse')
_pynput_m.Listener = MagicMock()
for _m in (_pynput, _pynput_kb, _pynput_m):
    sys.modules.setdefault(_m.__name__, _m)

# --- tkinter 스텁 ---
# tkinter 자체는 Windows에서 사용 가능하지만 Tk() 초기화를 막기 위해
# 클래스·함수만 가볍게 모킹한다.
_tk_stub = types.ModuleType('tkinter')
_tk_stub.Tk = MagicMock()
_tk_stub.Toplevel = MagicMock()
_tk_stub.Frame = MagicMock()
_tk_stub.Label = MagicMock()
_tk_stub.Canvas = MagicMock()
_tk_stub.StringVar = MagicMock()
_tk_stub.IntVar = MagicMock()
_tk_stub.BooleanVar = MagicMock()
_tk_stub.filedialog = MagicMock()
_tk_stub.messagebox = MagicMock()
sys.modules['tkinter'] = _tk_stub

# ── _physical_to_logical 함수 직접 추출 ───────────────────────────────────────
# main.py 전체를 exec 하지 않고, 해당 함수 정의만 소스에서 잘라내어
# 독립적으로 컴파일·실행한다.  이렇게 하면 Tk() 초기화 오류를 완전히 우회한다.

import pathlib, textwrap

_main_src = pathlib.Path(__file__).parent.parent.joinpath('main.py').read_text(encoding='utf-8')

# 함수 정의 구간 추출: 'def _physical_to_logical(' 부터 다음 'def ' 또는 EOF 까지
_lines = _main_src.splitlines()
_start = next(i for i, l in enumerate(_lines) if l.startswith('def _physical_to_logical('))
_end = next(
    (i for i, l in enumerate(_lines) if i > _start and l.startswith('def ')),
    len(_lines)
)
_func_src = textwrap.dedent('\n'.join(_lines[_start:_end]))

# ctypes / ctypes.wintypes は既にロード済み
_ns = {'ctypes': ctypes}
exec(compile(_func_src, '<extracted>', 'exec'), _ns)
_physical_to_logical = _ns['_physical_to_logical']


# ── テスト ─────────────────────────────────────────────────────────────────────

class TestPhysicalToLogical(unittest.TestCase):

    def _make_widget(self, winfo_id_val=12345):
        """winfo_id() 를 지정한 값으로 반환하는 mock widget."""
        w = MagicMock()
        w.winfo_id.return_value = winfo_id_val
        return w

    # ── 정상 변환 경로 ─────────────────────────────────────────────────────────

    def test_normal_conversion(self):
        """API 호출이 성공하면 변환된 논리 좌표를 반환한다."""
        widget = self._make_widget(1000)

        def fake_get_ancestor(hwnd, flag):
            return hwnd  # 최상위 hwnd를 그대로 반환

        def fake_ptl(hwnd, byref_pt):
            # POINT 구조체의 x, y를 변환값으로 덮어쓴다.
            # ctypes.cast를 통해 CArgObject → POINTER(POINT) → contents 접근 (안정적).
            pt = ctypes.cast(byref_pt, ctypes.POINTER(ctypes.wintypes.POINT)).contents
            pt.x = 100
            pt.y = 80
            return 1  # 성공

        with patch.object(ctypes.windll.user32, 'GetAncestor', fake_get_ancestor), \
             patch.object(ctypes.windll.user32, 'PhysicalToLogicalPointForPerMonitorDPI',
                          fake_ptl):
            result = _physical_to_logical(widget, 125, 100)

        self.assertEqual(result, (100, 80))

    # ── 폴백 경로 ─────────────────────────────────────────────────────────────

    def test_fallback_when_winfo_id_returns_zero(self):
        """winfo_id()가 0을 반환하면 원본 좌표를 그대로 반환한다."""
        widget = self._make_widget(0)
        result = _physical_to_logical(widget, 200, 150)
        self.assertEqual(result, (200, 150))

    def test_fallback_when_winfo_id_raises(self):
        """winfo_id()가 예외를 발생시키면 원본 좌표를 반환한다."""
        widget = MagicMock()
        widget.winfo_id.side_effect = Exception('TclError')
        result = _physical_to_logical(widget, 300, 250)
        self.assertEqual(result, (300, 250))

    def test_fallback_when_get_ancestor_returns_zero_uses_original_hwnd(self):
        """GetAncestor가 0(NULL)을 반환하면 원본 hwnd로 API를 시도한다."""
        widget = self._make_widget(9999)
        called_with_hwnd = []

        def fake_get_ancestor(hwnd, flag):
            return 0  # NULL 반환

        def fake_ptl(hwnd, byref_pt):
            called_with_hwnd.append(hwnd)
            pt = ctypes.cast(byref_pt, ctypes.POINTER(ctypes.wintypes.POINT)).contents
            pt.x = 50
            pt.y = 40
            return 1

        with patch.object(ctypes.windll.user32, 'GetAncestor', fake_get_ancestor), \
             patch.object(ctypes.windll.user32, 'PhysicalToLogicalPointForPerMonitorDPI',
                          fake_ptl):
            result = _physical_to_logical(widget, 62, 50)

        # GetAncestor가 0이면 원본 hwnd(9999)로 폴백하여 API를 호출해야 한다.
        self.assertEqual(called_with_hwnd[0], 9999)
        self.assertEqual(result, (50, 40))

    def test_fallback_when_api_returns_failure(self):
        """PhysicalToLogicalPointForPerMonitorDPI가 0(실패)을 반환하면 원본 좌표."""
        widget = self._make_widget(5000)

        def fake_get_ancestor(hwnd, flag):
            return hwnd

        def fake_ptl(hwnd, byref_pt):
            return 0  # 실패

        with patch.object(ctypes.windll.user32, 'GetAncestor', fake_get_ancestor), \
             patch.object(ctypes.windll.user32, 'PhysicalToLogicalPointForPerMonitorDPI',
                          fake_ptl):
            result = _physical_to_logical(widget, 400, 300)

        self.assertEqual(result, (400, 300))

    def test_fallback_when_api_raises(self):
        """API 자체가 없는 경우(AttributeError) 원본 좌표를 반환한다."""
        widget = self._make_widget(7777)

        def fake_get_ancestor(hwnd, flag):
            return hwnd

        with patch.object(ctypes.windll.user32, 'GetAncestor', fake_get_ancestor), \
             patch.object(ctypes.windll.user32, 'PhysicalToLogicalPointForPerMonitorDPI',
                          side_effect=AttributeError('no such function')):
            result = _physical_to_logical(widget, 500, 400)

        self.assertEqual(result, (500, 400))

    # ── 반환 타입 ─────────────────────────────────────────────────────────────

    def test_returns_tuple(self):
        """반환 타입이 항상 tuple 이어야 한다."""
        widget = self._make_widget(0)  # 폴백 경로
        result = _physical_to_logical(widget, 1, 2)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_returns_tuple_on_success(self):
        """성공 경로에서도 반환 타입이 tuple 이어야 한다."""
        widget = self._make_widget(111)

        def fake_get_ancestor(hwnd, flag):
            return hwnd

        def fake_ptl(hwnd, byref_pt):
            pt = ctypes.cast(byref_pt, ctypes.POINTER(ctypes.wintypes.POINT)).contents
            pt.x = 10
            pt.y = 20
            return 1

        with patch.object(ctypes.windll.user32, 'GetAncestor', fake_get_ancestor), \
             patch.object(ctypes.windll.user32, 'PhysicalToLogicalPointForPerMonitorDPI',
                          fake_ptl):
            result = _physical_to_logical(widget, 12, 25)

        self.assertIsInstance(result, tuple)
        self.assertEqual(result, (10, 20))


if __name__ == '__main__':
    unittest.main(verbosity=2)
