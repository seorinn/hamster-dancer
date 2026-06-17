#!/usr/bin/env python3
"""
GifPet - 타이핑할수록 신나게 춤추는 오버레이 펫
트레이 아이콘 우클릭 → 보이기/숨기기, 속도, 크기, 펫 변경, GIF 등록, 종료
"""

import tkinter as tk
import threading
import time
import math
import json
import random
import ctypes
import ctypes.wintypes
import os
import sys
from pathlib import Path

from PIL import Image, ImageTk, ImageDraw
import pystray
from pynput import keyboard as kb

# ── 고정 설정 ─────────────────────────────────────────────────────────────────
CHROMA     = '#fe01fe'
CHROMA_RGB = (254, 1, 254)

SIDE_OFFSET   = 10
BOTTOM_OFFSET = 4
IDLE_DELAY    = 1000          # ms (숨김 상태 폴링 주기)
DECAY_HALF_LIFE = 1.2         # 초: 키 입력 후 속도가 절반이 되는 시간
SPEED_RANGE   = 150           # ms: 기본속도 ~ 최고속도 범위 (고정)

# 속도 옵션 (label, base_delay ms) — 0은 정지 모드 센티넬
SPEED_OPTIONS = [
    ('정지 (타이핑 시 반응)',  0),
    ('매우 느리게', 380),
    ('느리게',      280),
    ('보통',        200),
    ('빠르게',      130),
    ('매우 빠르게',  70),
]
# 크기 옵션 (px)
SIZE_OPTIONS = [30, 55, 80, 120, 170]

DEFAULT_BASE_DELAY = 200
DEFAULT_SIZE       = 80

# ── 액션 시스템 ───────────────────────────────────────────────────────────────
ACTION_PRIORITY = {
    'waving':        5,
    'jumping':       4,
    'running-right': 3,
    'running-left':  3,
    'failed':        3,
    'running':       2,
    'review':        1,
    'idle':          0,
    'waiting':       0,
}
IDLE_TIMEOUT_MS    = 5_000    # 5초 무입력 → idle
WAITING_TIMEOUT_MS = 30_000   # 30초 무입력 → waiting
MOVE_STEP_PX       = 12       # 방향키 1회 이동 거리(px)


# ── 경로 헬퍼 ─────────────────────────────────────────────────────────────────
def get_app_dir() -> Path:
    path = Path(os.environ.get('APPDATA', Path.home())) / 'GifPet'
    path.mkdir(exist_ok=True)
    return path


def get_bundled_dir(subdir: str) -> Path:
    """번들(exe) 또는 소스 실행 시 모두 동작하는 리소스 디렉터리 경로."""
    base = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).parent
    return base / subdir


def get_workarea() -> tuple:
    rc = ctypes.wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rc), 0)
    return rc.left, rc.top, rc.right, rc.bottom


# ── 설정 저장/불러오기 ────────────────────────────────────────────────────────
def load_config() -> dict:
    p = get_app_dir() / 'config.json'
    if p.exists():
        try:
            with open(p, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


_config_lock = threading.Lock()


def save_config(cfg: dict):
    p = get_app_dir() / 'config.json'
    try:
        tmp = p.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f)
        os.replace(tmp, p)   # atomic write
    except Exception:
        pass


def update_config(**kwargs):
    """스레드 안전하게 config 일부 키를 업데이트"""
    with _config_lock:
        cfg = load_config()
        cfg.update(kwargs)
        save_config(cfg)




# ── 멀티 모니터 헬퍼 ─────────────────────────────────────────────────────────
def _physical_to_logical(widget, x: int, y: int) -> tuple:
    """pynput 물리 픽셀 좌표 (x, y)를 widget이 속한 모니터 기준 논리 좌표로 변환.
    winfo_id() 실패·GetAncestor 실패·API 부재/실패 등 모든 예외 시 원본 (x, y) 반환.
    변환 정확성 보장: 점(x, y)이 widget과 같은 모니터에 있을 때.
    """
    try:
        hwnd = widget.winfo_id()
        if not hwnd:
            return x, y
        # winfo_id()가 child HWND를 반환할 경우를 대비해 GA_ROOT(2)로 최상위 핸들 취득.
        # 이미 최상위 HWND라면 GetAncestor는 자기 자신을 반환하므로 무해하다.
        root_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2)
        if not root_hwnd:
            root_hwnd = hwnd

        pt = ctypes.wintypes.POINT(x, y)
        ok = ctypes.windll.user32.PhysicalToLogicalPointForPerMonitorDPI(
            root_hwnd, ctypes.byref(pt)
        )
        if not ok:
            return x, y
        return pt.x, pt.y
    except Exception:
        return x, y


def _monitor_work_area(x: int, y: int) -> tuple:
    """(x, y) 좌표가 속한 모니터의 작업 영역 (left, top, right, bottom) 반환."""
    class _RECT(ctypes.Structure):
        _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                    ('right', ctypes.c_long), ('bottom', ctypes.c_long)]
    class _MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_ulong), ('rcMonitor', _RECT),
                    ('rcWork', _RECT), ('dwFlags', ctypes.c_ulong)]

    pt   = ctypes.wintypes.POINT(x, y)
    hmon = ctypes.windll.user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
    mi   = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(_MONITORINFO)
    ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    r = mi.rcWork
    return r.left, r.top, r.right, r.bottom


# ── 커스텀 컨텍스트 메뉴 ─────────────────────────────────────────────────────
class _CtxMenu(tk.Toplevel):
    """그림자 없는 컨텍스트 메뉴. overrideredirect + WS_POPUP 으로 OS 그림자 제거."""

    BG    = '#ffffff'
    HOVER = '#eef'
    SEP   = '#e8e8e8'
    TEXT  = '#111111'
    MUTED = '#999999'
    ACC   = '#6366f1'
    F     = ('Segoe UI', 8)

    def __init__(self, root_win: tk.Tk, items: list, x: int, y: int,
                 _on_close=None, _top_menu=None):
        super().__init__(root_win)
        self.overrideredirect(True)
        self.wm_attributes('-topmost', True)
        self.configure(bg='#cccccc')          # 외곽 1px 테두리색

        self._root      = root_win
        self._sub       = None
        self._on_close  = _on_close
        self._top_menu  = _top_menu or self   # 최상위 메뉴 참조
        self._closed    = False
        self._mouse_lst = None

        # 최상위 메뉴만 전역 마우스 리스너 등록
        if _top_menu is None:
            import pynput.mouse as _m
            self._mouse_lst = _m.Listener(on_click=self._on_global_click)
            self._mouse_lst.daemon = True
            self._mouse_lst.start()

        # 화면 밖에 먼저 렌더링하여 크기 확정 후 정확한 위치로 이동
        self.geometry('+9999+9999')
        self._target_x = x
        self._target_y = y

        body = tk.Frame(self, bg=self.BG, pady=3)
        body.pack(fill='both', expand=True, padx=1, pady=1)

        for it in items:
            if it is None:
                tk.Frame(body, bg=self.SEP, height=1).pack(fill='x', padx=6, pady=2)
            else:
                self._row(body, it)

        self.update_idletasks()
        # 1ms 후 실제 렌더된 크기로 위치 확정 (update_idletasks만으론 부족할 때 대비)
        self.after(1, self._reposition)

        self._bid = root_win.bind('<Button-1>', self._outside, add='+')
        self.bind('<Escape>', lambda _: self._close())

    def _on_global_click(self, x, y, button, pressed):
        if not pressed:
            return
        if self._closed:
            return
        # 메뉴 영역 안 클릭이면 tkinter에 맡기고 무시.
        # pynput은 물리 픽셀 좌표를 전달하고 tkinter winfo_*는 논리 좌표를 반환하므로
        # 고DPI(125~150%) 환경에서 좌표계 불일치가 발생한다.
        # 창별로 PhysicalToLogicalPointForPerMonitorDPI 변환 후 비교한다.
        # (변환 실패 시 _physical_to_logical이 원본 물리 좌표를 반환 → 현재와 동일 동작)
        for win in self._top_menu._all():
            try:
                lx, ly = _physical_to_logical(win, x, y)
                # winfo_* 값을 한 번씩 읽어 지역 변수에 저장: 두 호출 사이 창 이동 시 rx/ry 불일치를
                # 최소화한다(완전한 원자성 보장은 아니나, 비교식에서 재호출보다 낫다).
                rx = win.winfo_rootx()
                ry = win.winfo_rooty()
                rw = win.winfo_width()
                rh = win.winfo_height()
                if rx <= lx <= rx + rw and ry <= ly <= ry + rh:
                    return
            except Exception:
                pass
        # 메뉴 밖 클릭이면 닫기 (pynput 스레드 → 메인 루프 큐에 등록; after 호출 자체도 방어)
        try:
            self._root.after(0, self._top_menu._close)
        except Exception:
            pass

    # ── 위치 확정 ─────────────────────────────────────────────────────────────
    def _reposition(self):
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        x, y = self._target_x, self._target_y

        # 클릭한 좌표가 속한 모니터의 작업 영역을 구해 클리핑
        mx0, my0, mx1, my1 = _monitor_work_area(x, y)
        fx = min(x, mx1 - w - 2)
        fy = min(y, my1 - h - 2)
        self.geometry(f'{w}x{h}+{max(mx0, fx)}+{max(my0, fy)}')
        self.lift()

    # ── 항목 ──────────────────────────────────────────────────────────────────
    def _row(self, parent: tk.Frame, it: dict):
        has_sub = it.get('sub') is not None
        checked = it.get('checked', False)
        cmd     = it.get('cmd')

        row = tk.Frame(parent, bg=self.BG, cursor='hand2')
        row.pack(fill='x', padx=2)

        ck = tk.Label(row, text='·' if checked else ' ',
                      bg=self.BG, fg=self.ACC if checked else self.BG,
                      font=('Segoe UI', 9), width=2, anchor='center')
        ck.pack(side='left')

        lbl = tk.Label(row, text=it['label'], bg=self.BG, fg=self.TEXT,
                       font=self.F, anchor='w', padx=4, pady=2)
        lbl.pack(side='left', fill='x', expand=True)

        arr = None
        if has_sub:
            arr = tk.Label(row, text='›', bg=self.BG, fg=self.MUTED,
                           font=('Segoe UI', 10), padx=6, pady=2)
            arr.pack(side='right')

        parts = [w for w in (row, ck, lbl, arr) if w]

        def enter(_):
            for w in parts: w.config(bg=self.HOVER)
            if has_sub:
                self._open_sub(it['sub'], row)
            elif self._sub:
                self._close_sub()

        def leave(_):
            for w in parts: w.config(bg=self.BG)

        def click(_):
            if cmd:
                self._top_menu._close()   # 최상위부터 전체 닫기
                cmd()

        for w in parts:
            w.bind('<Enter>', enter)
            w.bind('<Leave>', leave)
            w.bind('<Button-1>', click)

    # ── 서브메뉴 ──────────────────────────────────────────────────────────────
    def _open_sub(self, items, anchor):
        self._close_sub()
        ax = anchor.winfo_rootx() + anchor.winfo_width() + 2
        ay = anchor.winfo_rooty() - 3
        self._sub = _CtxMenu(self._root, items, ax, ay,
                             _on_close=lambda: setattr(self, '_sub', None),
                             _top_menu=self._top_menu)

    def _close_sub(self):
        if self._sub:
            try: self._sub._close()
            except Exception: pass
            self._sub = None

    # ── 닫기 ──────────────────────────────────────────────────────────────────
    def _close(self):
        if self._closed:
            return
        self._closed = True
        self._close_sub()
        if self._mouse_lst:
            try: self._mouse_lst.stop()
            except Exception: pass
        try: self._root.unbind('<Button-1>', self._bid)
        except Exception: pass
        if self._on_close:
            try: self._on_close()
            except Exception: pass
        try: self.destroy()
        except Exception: pass

    def _outside(self, event):
        for win in self._all():
            try:
                if (win.winfo_rootx() <= event.x_root <= win.winfo_rootx() + win.winfo_width()
                        and win.winfo_rooty() <= event.y_root <= win.winfo_rooty() + win.winfo_height()):
                    return
            except Exception:
                pass
        self._close()

    def _all(self):
        sub = self._sub  # 단일 읽기: pynput 스레드 경합 시 _sub가 중간에 None으로 바뀌는 경우를 방지
        return [self] + (sub._all() if sub else [])


def create_tray_icon_image() -> Image.Image:
    """🐹 이모지를 Segoe UI Emoji 폰트로 렌더링. 실패 시 손그림 폴백."""
    size = 64
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    try:
        from PIL import ImageFont
        font_path = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'Fonts' / 'seguiemj.ttf'
        font = ImageFont.truetype(str(font_path), 52)
        bbox = d.textbbox((0, 0), '🐹', font=font, embedded_color=True)
        x = (size - (bbox[2] - bbox[0])) // 2 - bbox[0]
        y = (size - (bbox[3] - bbox[1])) // 2 - bbox[1]
        d.text((x, y), '🐹', font=font, embedded_color=True)
    except Exception:
        # 폴백: 손그림 햄스터 얼굴
        d.ellipse([ 2,  2, 62, 62], fill=(180, 130,  80, 255))
        d.ellipse([ 8, 10, 56, 55], fill=(240, 210, 170, 255))
        d.ellipse([ 2,  0, 18, 16], fill=(180, 130,  80, 255))
        d.ellipse([46,  0, 62, 16], fill=(180, 130,  80, 255))
        d.ellipse([16, 22, 25, 31], fill=( 20,  10,   5, 255))
        d.ellipse([39, 22, 48, 31], fill=( 20,  10,   5, 255))
        d.ellipse([28, 36, 36, 43], fill=(240, 150, 150, 255))
    return img


# ── GIF 프레임 로더 ───────────────────────────────────────────────────────────
def _rgba_to_chroma(img: Image.Image, size: tuple) -> ImageTk.PhotoImage:
    """
    RGBA → 크로마키 배경 합성 PhotoImage.
    알파 임계처리(128)로 반투명 픽셀을 완전 투명으로 처리 → 핑크 테두리 방지.
    """
    if img.size != size:
        img = img.resize(size, Image.LANCZOS)
    r, g, b, a = img.split()
    # 반투명 픽셀 → 완전 투명(0) or 불투명(255) 으로 이분화
    a_clean = a.point(lambda v: 0 if v < 128 else 255)
    bg = Image.new('RGB', size, CHROMA_RGB)
    bg.paste(Image.merge('RGB', (r, g, b)), mask=a_clean)
    return ImageTk.PhotoImage(bg)



def load_gif_frames(gif_path: Path, size: tuple) -> list:
    frames = []
    with Image.open(str(gif_path)) as gif:
        n = getattr(gif, 'n_frames', 1)
        for i in range(n):
            gif.seek(i)
            frames.append(_rgba_to_chroma(gif.copy().convert('RGBA'), size))
    return frames


# ── 메인 앱 ──────────────────────────────────────────────────────────────────
class GifPet:
    def __init__(self):
        cfg = load_config()
        self._base_delay   = cfg.get('base_delay', DEFAULT_BASE_DELAY)
        self._display_size = cfg.get('size', DEFAULT_SIZE)

        self._last_key_time: float = 0.0
        self.running       = True
        self.visible       = True
        self.current_frame = 0
        self.frames: list  = []
        self.action_frames: dict = {}
        self.current_action: str = 'idle'
        self._current_pet_id: str | None = None

        self._drag_x = 0
        self._drag_y = 0
        self._last_heart_time: float = 0.0

        self._action_hold_until: float   = 0.0
        self._is_hovering:       bool    = False
        self._idle_after_id:     str | None = None
        self._wait_after_id:     str | None = None
        self._last_char:         str | None = None
        self._last_char_time:    float   = 0.0
        self._held_arrows:       set     = set()
        self._arrow_loop_active: bool    = False
        self._is_jumping:        bool    = False
        self._jump_return_id:    str | None = None

        self._init_window()
        self._init_tray()
        self._init_keyboard()
        self._load_pet_safe()

    # ── 펫 로드 ──────────────────────────────────────────────────────────────
    def _load_pet_safe(self):
        """초기 로드 – 등록된 펫 없으면 기본 펫(claude) 자동 등록 후 로드."""
        from pet_registry import load_registry
        reg = load_registry(get_app_dir())
        if not reg.get('pets'):
            try:
                self._register_default_pet()
            except Exception:
                pass
        self._load_pet()

    def _register_default_pet(self):
        """번들된 default_pet/ GIF를 AppData에 복사하고 레지스트리에 등록."""
        import shutil
        from pet_registry import load_registry, save_registry, _pets_dir, ACTIONS

        src_dir = get_bundled_dir('default_pet')
        pet_id  = 'claude-default'
        dst_dir = _pets_dir(get_app_dir()) / pet_id
        dst_dir.mkdir(parents=True, exist_ok=True)

        # 파일명 패턴: claude-pixel-{action}.gif → {action}.gif
        actions: dict[str, str] = {}
        for action in ACTIONS:
            src = src_dir / f'claude-pixel-{action}.gif'
            if src.exists():
                dst = dst_dir / f'{action}.gif'
                shutil.copy2(src, dst)
                actions[action] = str(dst)

        if not actions:
            return

        reg = load_registry(get_app_dir())
        # 이미 있으면 스킵 (재등록 방지)
        if any(p['id'] == pet_id for p in reg['pets']):
            return
        reg['pets'].insert(0, {
            'name': 'Default', 'id': pet_id,
            'source': 'default', 'actions': actions,
        })
        if reg['active'] is None:
            reg['active'] = pet_id
        save_registry(get_app_dir(), reg)

    def _load_pet(self):
        """레지스트리의 활성 펫을 로드."""
        from pet_registry import get_active_pet
        size = (self._display_size, self._display_size)

        pet = get_active_pet(get_app_dir())
        if pet:
            loaded: dict[str, list] = {}
            for action, gif_path in pet['actions'].items():
                p = Path(gif_path)
                if p.exists():
                    try:
                        frames = load_gif_frames(p, size)
                        if frames:
                            loaded[action] = frames
                    except Exception:
                        pass
            if loaded:
                self.action_frames = loaded
                self._current_pet_id = pet['id']
                self._set_action('idle')
                return

        raise RuntimeError(
            '표시할 GIF가 없습니다.\n트레이 메뉴 > GIF 등록... 으로 펫을 추가해 주세요.'
        )

    def _set_action(self, action: str):
        """현재 액션 설정. 없으면 첫 번째로 폴백. 동일 액션이면 프레임 유지."""
        prev = self.current_action
        if action in self.action_frames:
            self.current_action = action
        elif self.action_frames:
            self.current_action = next(iter(self.action_frames))
        else:
            self.current_action = 'idle'
        self.frames = self.action_frames.get(self.current_action, [])
        if self.current_action != prev:
            self.current_frame = 0

    # ── 윈도우 초기화 ─────────────────────────────────────────────────────────
    def _init_window(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.wm_attributes('-topmost', True)
        self.root.wm_attributes('-transparentcolor', CHROMA)
        self.root.configure(bg=CHROMA)
        self.root.resizable(False, False)

        self._reposition()

        self.canvas = tk.Canvas(
            self.root, width=self._display_size, height=self._display_size,
            bg=CHROMA, highlightthickness=0, bd=0,
        )
        self.canvas.pack()
        self._canvas_img_id = None

        def _reset_canvas_img():
            if self._canvas_img_id is not None:
                self.canvas.delete(self._canvas_img_id)
            self._canvas_img_id = None
        self._reset_canvas_img = _reset_canvas_img

        # 드래그로 위치 이동 + 클릭 시 하트 + 우클릭 메뉴
        self.canvas.bind('<ButtonPress-1>',   self._drag_start)
        self.canvas.bind('<B1-Motion>',       self._drag_move)
        self.canvas.bind('<ButtonRelease-1>', self._on_click_release)
        self.canvas.bind('<Button-3>',        self._show_context_menu)

        self._heart_pts_cache: dict = {}  # size → 정규화된 하트 좌표 캐시

        # 호버 시 포인터 커서
        self.canvas.bind('<Enter>', lambda e: self.canvas.config(cursor='hand2'))
        self.canvas.bind('<Leave>', lambda e: self.canvas.config(cursor=''))

        self.root.after(50,  self._animate)
        self.root.after(200, self._check_hover)
        self.root.after(500, self._reschedule_idle)  # 초기 idle 타이머

    def _reposition(self):
        """저장된 위치가 있으면 복원, 없으면 기본값(우측 하단)"""
        s   = self._display_size
        cfg = load_config()
        if 'x' in cfg and 'y' in cfg:
            x, y = cfg['x'], cfg['y']
        else:
            _, _, wa_right, wa_bottom = get_workarea()
            x = wa_right  - s - SIDE_OFFSET
            y = wa_bottom - s - BOTTOM_OFFSET
        self.root.geometry(f'{s}x{s}+{x}+{y}')

    def _drag_start(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _drag_move(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f'+{x}+{y}')

    def _drag_end(self, event):
        update_config(x=self.root.winfo_x(), y=self.root.winfo_y())

    def _on_click_release(self, event):
        dx = abs(event.x_root - (self.root.winfo_x() + self._drag_x))
        dy = abs(event.y_root - (self.root.winfo_y() + self._drag_y))
        if dx < 5 and dy < 5:   # 드래그 아닌 클릭
            self._spawn_heart(event.x, event.y)
            self._try_action('waving', 600)
        else:
            self._drag_end(event)

    def _heart_polygon(self, cx: int, cy: int, size: int) -> list:
        """파라메트릭 하트 곡선 좌표 반환 (캔버스 절대 좌표). 크기별 캐시."""
        if size not in self._heart_pts_cache:
            steps = 80
            pad   = size * 0.05
            raw   = []
            for i in range(steps):
                a = 2 * math.pi * i / steps
                raw.append((
                    16 * math.sin(a) ** 3,
                    -(13 * math.cos(a) - 5 * math.cos(2*a)
                      - 2 * math.cos(3*a) - math.cos(4*a)),
                ))
            xs = [p[0] for p in raw]; ys = [p[1] for p in raw]
            mn_x, mx_x = min(xs), max(xs)
            mn_y, mx_y = min(ys), max(ys)
            sc = min((size - pad*2) / (mx_x - mn_x),
                     (size - pad*2) / (mx_y - mn_y))
            ox = pad + ((size - pad*2) - (mx_x - mn_x) * sc) / 2
            oy = pad + ((size - pad*2) - (mx_y - mn_y) * sc) / 2
            # 원점 기준 정규화 좌표 저장
            self._heart_pts_cache[size] = [
                (ox + (px - mn_x) * sc - size/2,
                 oy + (py - mn_y) * sc - size/2)
                for px, py in raw
            ]
        return [(cx + dx, cy + dy)
                for dx, dy in self._heart_pts_cache[size]]

    def _spawn_heart(self, cx: int, cy: int):
        """클릭 위치에 하트 폴리곤을 띄우고 위로 떠오르며 페이드아웃"""
        FRAMES = 18
        size   = max(10, self._display_size // 6)
        pos    = [cx, cy - size // 2]

        pts     = self._heart_polygon(pos[0], pos[1], size)
        now = time.time()
        if now - self._last_heart_time < 0.2:   # 200ms 쿨다운
            return
        self._last_heart_time = now

        brightness = random.randint(4, 10) / 10   # 40~100% 밝기 랜덤 (10% 단위)
        item    = self.canvas.create_polygon(pts, fill='#dc1432', outline='', smooth=False)

        step = 0

        def _tick():
            nonlocal step
            if step >= FRAMES:
                self.canvas.delete(item)
                return
            t      = brightness * (1.0 - step / FRAMES)
            r_val  = int(220 * t + CHROMA_RGB[0] * (1 - t))
            g_val  = int(20  * t + CHROMA_RGB[1] * (1 - t))
            b_val  = int(50  * t + CHROMA_RGB[2] * (1 - t))
            color  = f'#{r_val:02x}{g_val:02x}{b_val:02x}'
            pos[1] -= 2
            new_pts = self._heart_polygon(pos[0], pos[1], size)
            self.canvas.coords(item, [c for pt in new_pts for c in pt])
            self.canvas.itemconfigure(item, fill=color)
            step += 1
            self.root.after(30, _tick)

        _tick()

    # ── 애니메이션 루프 ───────────────────────────────────────────────────────
    def _animate(self):
        if not self.running:
            return
        if self.visible and self.frames:
            # 항상 현재 프레임 렌더 (정지 모드에서도 gif가 보이도록)
            frame = self.frames[self.current_frame]
            if self._canvas_img_id is None:
                self._canvas_img_id = self.canvas.create_image(0, 0, anchor='nw', image=frame)
            else:
                self.canvas.itemconfigure(self._canvas_img_id, image=frame)

            # 정지 모드: 타이핑 없으면 프레임 고정, 100ms 간격으로 폴링
            if self._base_delay == 0:
                elapsed = time.time() - self._last_key_time
                if elapsed > 0.3:
                    self.root.after(100, self._animate)
                    return

            self.current_frame = (self.current_frame + 1) % len(self.frames)
            self.root.after(self._frame_delay(), self._animate)
        else:
            self.root.after(IDLE_DELAY, self._animate)

    def _frame_delay(self) -> int:
        """running 액션일 때만 타이핑에 반응해 빨라지고, 나머지는 기본 속도 고정.
        정지 모드(base_delay=0)일 때는 최고 속도(20ms) 고정."""
        if self._base_delay == 0:
            elapsed = time.time() - self._last_key_time
            speed   = math.exp(-elapsed * math.log(2) / DECAY_HALF_LIFE)
            return int(80 + (1 - speed) * 60)
        if self.current_action != 'running':
            return self._base_delay
        elapsed = time.time() - self._last_key_time
        speed   = math.exp(-elapsed * math.log(2) / DECAY_HALF_LIFE)
        min_d   = max(20, self._base_delay - SPEED_RANGE)
        return int(self._base_delay - speed * (self._base_delay - min_d))

    # ── 액션 제어 ─────────────────────────────────────────────────────────────
    def _try_action(self, action: str, hold_ms: int = 0):
        """우선순위·hold 기반 액션 전환.
        hold 중이면 현재보다 낮은 우선순위는 무시, 같거나 높으면 교체."""
        now     = time.time()
        new_pri = ACTION_PRIORITY.get(action, 0)
        cur_pri = ACTION_PRIORITY.get(self.current_action, 0)
        if now < self._action_hold_until and new_pri < cur_pri:
            return
        self._set_action(action)
        self._action_hold_until = now + hold_ms / 1000.0 if hold_ms else 0.0
        if action == 'jumping':
            self._do_jump_anim()

    def _do_jump_anim(self):
        """점프 애니메이션: 착지 완료 후에만 다음 점프 허용.
        _is_jumping=True 구간(점프+쿨다운 400ms) 동안은 진입 불가이므로
        이 시점의 winfo_y()는 반드시 착지 상태 위치를 반환한다."""
        if self._is_jumping:
            return

        gx = self.root.winfo_x()   # 점프 시작 위치를 X·Y 모두 고정
        gy = self.root.winfo_y()
        self._is_jumping = True
        self.root.geometry(f'+{gx}+{gy - 20}')

        def _land():
            self.root.geometry(f'+{gx}+{gy}')
            self._jump_return_id = self.root.after(150, _allow)

        def _allow():
            self._is_jumping     = False
            self._jump_return_id = None

        self._jump_return_id = self.root.after(250, _land)

    def _reschedule_idle(self):
        """키 입력마다 idle/waiting 타이머 재설정."""
        for attr in ('_idle_after_id', '_wait_after_id'):
            aid = getattr(self, attr, None)
            if aid:
                try: self.root.after_cancel(aid)
                except Exception: pass
        self._idle_after_id = self.root.after(IDLE_TIMEOUT_MS,    self._force_idle)
        self._wait_after_id = self.root.after(WAITING_TIMEOUT_MS,  self._force_waiting)

    def _force_idle(self):
        self._action_hold_until = 0.0
        self._set_action('idle')

    def _force_waiting(self):
        self._action_hold_until = 0.0
        self._set_action('waiting')

    def _check_hover(self):
        """150ms마다 마우스 위치 확인 → review 액션 전환."""
        if not self.running:
            return
        try:
            px = self.root.winfo_pointerx()
            py = self.root.winfo_pointery()
            wx = self.root.winfo_x()
            wy = self.root.winfo_y()
            s  = self._display_size
            margin = 20
            hovering = (wx - margin <= px <= wx + s + margin and
                        wy - margin <= py <= wy + s + margin)
            if hovering and not self._is_hovering:
                self._is_hovering = True
                self._try_action('review', 0)
            elif not hovering and self._is_hovering:
                self._is_hovering = False
                if self.current_action == 'review':
                    self._action_hold_until = 0.0
                    self._set_action('idle')
        except Exception:
            pass
        self.root.after(150, self._check_hover)

    def _arrow_move(self, direction: str):
        """방향키: 해당 방향 액션 + 위치 이동 (루프에서 호출)."""
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        s = self._display_size

        if direction in ('right', 'left'):
            action = 'running-right' if direction == 'right' else 'running-left'
            self._try_action(action, 0)
            step  = MOVE_STEP_PX if direction == 'right' else -MOVE_STEP_PX
            new_x = x + step
            cx    = new_x + s // 2
            mx0, _, mx1, _ = _monitor_work_area(cx, y + s // 2)
            new_x = max(mx0, min(new_x, mx1 - s))
            self.root.geometry(f'+{new_x}+{y}')
        else:  # up / down
            step  = -MOVE_STEP_PX if direction == 'up' else MOVE_STEP_PX
            new_y = y + step
            _, my0, _, my1 = _monitor_work_area(x + s // 2, new_y + s // 2)
            new_y = max(my0, min(new_y, my1 - s))
            self.root.geometry(f'+{x}+{new_y}')

    def _start_arrow(self, direction: str):
        """방향키 누름: 방향 추가 후 루프 시작."""
        self._held_arrows.add(direction)
        if not self._arrow_loop_active:
            self._arrow_loop_active = True
            self._arrow_loop()

    def _stop_arrow(self, direction: str):
        """방향키 뗌: 방향 제거, 모두 떼면 idle로."""
        self._held_arrows.discard(direction)
        if not self._held_arrows:
            self._arrow_loop_active = False
            self._action_hold_until = 0.0
            self._set_action('idle')
            update_config(x=self.root.winfo_x(), y=self.root.winfo_y())

    def _arrow_loop(self):
        """꾹 누르는 동안 30ms마다 이동."""
        if not self._arrow_loop_active or not self.running:
            return
        for d in list(self._held_arrows):
            self._arrow_move(d)
        self.root.after(200, self._arrow_loop)

    # ── 키보드 리스너 ─────────────────────────────────────────────────────────
    def _on_key_press(self, key):
        now = time.time()

        def dispatch():
            self._last_key_time = now   # 메인 스레드에서 안전하게 쓰기
            if key == kb.Key.right:
                self._start_arrow('right')
            elif key == kb.Key.left:
                self._start_arrow('left')
            elif key == kb.Key.up:
                self._start_arrow('up')
            elif key == kb.Key.down:
                self._start_arrow('down')
            elif key in (kb.Key.space, kb.Key.enter, kb.Key.tab):
                self._try_action('jumping', 400)
            elif key == kb.Key.backspace:
                self._try_action('failed', 700)
            else:
                ch  = getattr(key, 'char', None)
                vk  = getattr(key, 'vk', None)
                # ㅠ = 'b'(VK 66), ㅜ = 'n'(VK 78) — Korean IME는 char 대신 VK로 판별
                is_cry = (ch in ('ㅠ', 'ㅜ') or
                          vk in (66, 78) or
                          (ch and ch.lower() in ('b', 'n')))
                key_id = vk or ch   # 연속 감지용 식별자
                if is_cry:
                    if (key_id == self._last_char and
                            now - self._last_char_time < 0.5):
                        self._try_action('failed', 700)
                    else:
                        self._try_action('running', 300)
                else:
                    self._try_action('running', 300)
                self._last_char      = key_id
                self._last_char_time = now
            self._reschedule_idle()

        self.root.after(0, dispatch)

    def _on_key_release(self, key):
        _ARROW_MAP = {
            kb.Key.right: 'right', kb.Key.left: 'left',
            kb.Key.up:    'up',    kb.Key.down: 'down',
        }
        if key in _ARROW_MAP:
            direction = _ARROW_MAP[key]
            self.root.after(0, lambda d=direction: self._stop_arrow(d))

    def _init_keyboard(self):
        try:
            self.listener = kb.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release,
            )
            self.listener.daemon = True
            self.listener.start()
        except Exception:
            pass

    # ── 시스템 트레이 ─────────────────────────────────────────────────────────
    def _init_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem('보이기 / 숨기기', self._toggle, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('기본 속도', self._speed_menu()),
            pystray.MenuItem('크기',     self._size_menu()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('펫 변경', pystray.Menu(self._iter_pet_menu_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('GIF 등록...',      self._open_register_dialog),
            pystray.MenuItem('등록 목록 관리...', self._open_manage_dialog),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('종료', self._quit),
        )
        self.tray = pystray.Icon(
            'GifPet', create_tray_icon_image(), 'GifPet', menu
        )
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _iter_pet_menu_items(self):
        """펫 변경 서브메뉴 – 열릴 때마다 레지스트리에서 동적으로 생성"""
        from pet_registry import load_registry
        reg = load_registry(get_app_dir())
        pets = reg.get('pets', [])
        if not pets:
            yield pystray.MenuItem('(등록된 펫 없음)', None, enabled=False)
            return
        for pet in pets:
            pid = pet['id']
            yield pystray.MenuItem(
                pet['name'],
                lambda *_, pid=pid: self._switch_pet(pid),
                checked=lambda *_, pid=pid: self._current_pet_id == pid,
                radio=True,
            )

    def _switch_pet(self, pet_id: str):
        from pet_registry import set_active_pet
        set_active_pet(get_app_dir(), pet_id)
        def _do():
            import tkinter.messagebox as mb
            try:
                self._reset_canvas_img()
                self._load_pet()
            except Exception as e:
                mb.showerror('GifPet', f'펫 전환 실패: {e}')
        self.root.after(0, _do)

    def _open_register_dialog(self, *_):
        from register_dialog import RegisterDialog
        def _on_done(pet):
            self._switch_pet(pet['id'])
        self.root.after(0, lambda: RegisterDialog(
            self.root, get_app_dir(), on_complete=_on_done))

    def _open_manage_dialog(self, *_):
        from register_dialog import ManagePetsDialog
        def _on_change():
            self.root.after(0, self._reload)
        self.root.after(0, lambda: ManagePetsDialog(
            self.root, get_app_dir(), on_change=_on_change))

    def _speed_menu(self):
        items = []
        for label, delay in SPEED_OPTIONS:
            d = delay
            items.append(pystray.MenuItem(
                label,
                lambda *args, d=d: self._set_speed(d),
                checked=lambda *args, d=d: self._base_delay == d,
                radio=True,
            ))
        return pystray.Menu(*items)

    def _size_menu(self):
        items = []
        for s in SIZE_OPTIONS:
            items.append(pystray.MenuItem(
                f'{s}px',
                lambda *args, s=s: self._set_size(s),
                checked=lambda *args, s=s: self._display_size == s,
                radio=True,
            ))
        return pystray.Menu(*items)

    def _set_speed(self, base_delay: int):
        # pystray 스레드 → 메인 스레드에서 상태 변경
        def _apply():
            self._base_delay = base_delay
        self.root.after(0, _apply)
        update_config(base_delay=base_delay)

    def _set_size(self, size: int):
        update_config(size=size)
        self.root.after(0, lambda: self._apply_size(size))

    def _apply_size(self, size: int):
        """크기 변경 (메인 스레드에서 실행)"""
        import tkinter.messagebox as mb
        prev_size = self._display_size
        self._display_size = size
        try:
            self._load_pet()
        except Exception as e:
            self._display_size = prev_size
            mb.showerror('GifPet', f'GIF 로드 실패: {e}')
            return
        self.canvas.config(width=size, height=size)
        self._reset_canvas_img()
        self._reposition()

    def _toggle(self, *_):
        self.visible = not self.visible
        if self.visible:
            self.root.after(0, self.root.deiconify)   # _animate는 IDLE 루프에서 자동 재개
        else:
            self.root.after(0, self.root.withdraw)

    def _reload(self, *_):
        def _do_reload():
            import tkinter.messagebox as mb
            try:
                self._reset_canvas_img()
                self._load_pet()
                self._heart_pts_cache.clear()
            except Exception as e:
                mb.showerror('GifPet', f'GIF 로드 실패: {e}')
        self.root.after(0, _do_reload)

    def _show_context_menu(self, event):
        """펫 위 우클릭 → 커스텀 메뉴 (그림자 없음)"""
        from pet_registry import load_registry

        speed_sub = [
            {'label': lbl, 'cmd': lambda d=d: self._set_speed(d),
             'checked': self._base_delay == d}
            for lbl, d in SPEED_OPTIONS
        ]
        size_sub = [
            {'label': f'{s}px', 'cmd': lambda s=s: self._set_size(s),
             'checked': self._display_size == s}
            for s in SIZE_OPTIONS
        ]

        items: list = [
            {'label': '숨기기' if self.visible else '보이기', 'cmd': self._toggle},
            None,
            {'label': '기본 속도', 'sub': speed_sub},
            {'label': '크기',      'sub': size_sub},
        ]

        reg  = load_registry(get_app_dir())
        pets = reg.get('pets', [])
        if pets:
            pet_sub = [
                {'label': p['name'],
                 'cmd': lambda pid=p['id']: self._switch_pet(pid),
                 'checked': p['id'] == self._current_pet_id}
                for p in pets
            ]
            items += [None, {'label': '펫 변경', 'sub': pet_sub}]

        items += [
            None,
            {'label': 'GIF 등록...',       'cmd': self._open_register_dialog},
            {'label': '등록 목록 관리...', 'cmd': self._open_manage_dialog},
            None,
            {'label': '종료', 'cmd': self._quit},
        ]

        _CtxMenu(self.root, items, event.x_root, event.y_root)

    def _quit(self, *_):
        self.running = False
        def _do_quit():
            self.tray.stop()
            self.root.destroy()
        self.root.after(0, _do_quit)

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    app = GifPet()
    app.run()
