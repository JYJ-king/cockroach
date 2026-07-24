"""会计蟑螂同伴：同窗右侧槽位，偶尔与主宠对喷黑话。"""

from __future__ import annotations

import math
import random
import time
from typing import TYPE_CHECKING, Callable

import pygame

if TYPE_CHECKING:
    pass

# 对喷脚本：(角色, 台词)  main=打工蟑螂  buddy=会计蟑螂
BANTER_SCRIPTS: list[list[tuple[str, str]]] = [
    [
        ("main", "对齐一下?"),
        ("buddy", "先对齐科目"),
        ("main", "颗粒度再细"),
        ("buddy", "细到分录行"),
    ],
    [
        ("main", "闭环了吗?"),
        ("buddy", "账没平别闭环"),
        ("main", "先上线再说"),
        ("buddy", "先过账再说"),
    ],
    [
        ("main", "需求又改了"),
        ("buddy", "预算也改了?"),
        ("main", "老板画饼"),
        ("buddy", "饼不能抵税"),
    ],
    [
        ("main", "摸鱼两分钟"),
        ("buddy", "发票贴了吗"),
        ("main", "...贴了"),
        ("buddy", "专票还是普票"),
    ],
    [
        ("main", "站会别超时"),
        ("buddy", "月结别添乱"),
        ("main", "EOD前给到"),
        ("buddy", "关账前别提需求"),
    ],
    [
        ("main", "赋能业务!"),
        ("buddy", "先把账赋能平"),
        ("main", "底层逻辑呢"),
        ("buddy", "借贷逻辑呢"),
    ],
    [
        ("main", "回款催了吗"),
        ("buddy", "我催三回了"),
        ("main", "客户说下周"),
        ("buddy", "下周是哪周"),
    ],
    [
        ("main", "报销走OA"),
        ("buddy", "票呢?票呢!"),
        ("main", "电子票在邮箱"),
        ("buddy", "转发财务共享"),
    ],
    [
        ("main", "发薪日快乐"),
        ("buddy", "个税算清了吗"),
        ("main", "快乐减半"),
        ("buddy", "这叫合规快乐"),
    ],
    [
        ("main", "周末别发版"),
        ("buddy", "周末别甩凭证"),
        ("main", "握手言和?"),
        ("buddy", "对完账再说"),
    ],
]


class AccountantBuddy:
    """会计蟑螂同伴：画在主窗口右侧槽，自己排队说话。"""

    def __init__(
        self,
        roach,
        font: pygame.font.Font,
        font_sm: pygame.font.Font,
        slot_x: int,
        say_main: Callable[..., None],
        fx_main: Callable[..., None],
        pad_x: int = 0,
        roach_y: int = 0,
        pet_w: int = 160,
        pet_h: int = 180,
    ):
        self.roach = roach
        self.font = font
        self.font_sm = font_sm
        self.slot_x = slot_x
        self.say_main = say_main
        self.fx_main = fx_main
        self._pad_x = pad_x
        self._roach_y = roach_y
        self._pet_w = pet_w
        self._pet_h = pet_h
        self.bubbles = _MiniBubbleQueue()
        self.bubble = None
        self.active = True
        self._script: list[tuple[str, str]] = []
        self._busy_until = 0.0
        # 平时完全隐藏，仅对喷时现身
        self.roach.target_alpha = 0
        self.roach.alpha = 0
        self.roach.heading = -45.0  # 大致朝向左侧主宠
        self.roach.set_facing(-1, 0)

    @property
    def bantering(self) -> bool:
        return bool(self._script) or bool(self.bubbles.current) or bool(self.bubbles._q)

    @property
    def visible(self) -> bool:
        """对喷中，或淡出尚未结束时需要占位。"""
        return self.active and (self.bantering or self.roach.alpha > 4)

    def start_banter(self, script: list[tuple[str, str]] | None = None) -> None:
        script = list(script or random.choice(BANTER_SCRIPTS))
        self._script = script
        self.bubbles.clear()
        self.roach.target_alpha = 255
        self.roach.alpha = max(self.roach.alpha, 40)  # 立刻看得见，再淡入满不透明
        self.roach.target_scale = 1.08
        self.fx_main("star", 3)
        # 立刻抛第一句
        self._feed_next_line()

    def _feed_next_line(self) -> None:
        if not self._script:
            return
        if self.bubbles.current or self.bubbles._q:
            return
        who, text = self._script[0]
        if who == "main":
            self._script.pop(0)
            self.say_main(text, life=100, urgent=True)
            self._busy_until = time.time() + 1.15
        else:
            self._script.pop(0)
            self.bubbles.push(text, life=100)
            self.roach.burst(
                self.slot_x + self._pad_x + self._pet_w / 2,
                self._roach_y + self._pet_h / 2,
                "crumb",
                3,
            )
            self._busy_until = time.time() + 1.15

    def tick(self, main_busy: bool) -> None:
        if not self.active:
            self.roach.target_alpha = 0
            self.roach.tick(False, 0, False, False)
            self.bubble = None
            return

        self.bubble = self.bubbles.tick()
        now = time.time()
        if self._script and now >= self._busy_until:
            if not (self.bubbles.current or self.bubbles._q):
                if self._script[0][0] == "main" and main_busy:
                    pass
                else:
                    self._feed_next_line()

        # 对喷时现身跳舞；不用 happy 黄光，保持普通棕色
        if self.bantering:
            self.roach.target_alpha = 255
            self.roach.target_scale = 1.05 + 0.03 * math.sin(pygame.time.get_ticks() * 0.01)
        else:
            self.roach.target_alpha = 0
            self.roach.target_scale = 0.95

        self.roach.set_facing(-1, 0)
        self.roach.tick(False, 0.2, False, False, dancing=self.bantering)

    def draw(self, canvas: pygame.Surface, pad_x: int, roach_y: int, pet_w: int) -> None:
        if not self.active or self.roach.alpha < 1:
            return
        ox = self.slot_x + pad_x
        # 允许淡出到完全透明（主宠 draw 默认保底 alpha=40）
        self.roach.draw(canvas, ox, roach_y, False, False, min_alpha=0)
        self._draw_bubble(canvas, ox, pet_w)

    def _draw_bubble(self, canvas: pygame.Surface, ox: int, pet_w: int) -> None:
        if not self.bubble:
            return
        text = self.bubble.text
        # 会计气泡带前缀感，偏短
        font = self.font if len(text) <= 12 else self.font_sm
        rendered = font.render(text, True, (70, 40, 10))
        tw, th = rendered.get_size()
        pad = 7
        bw, bh = tw + pad * 2, th + pad * 2
        alpha = min(255, self.bubble.life * 4)
        total_h = bh + 10
        b = pygame.Surface((bw + 4, total_h), pygame.SRCALPHA)
        r = pygame.Rect(0, 0, bw, bh)
        # 奶油金气泡，区别于主宠白泡
        pygame.draw.rect(b, (255, 236, 180, alpha), r, border_radius=8)
        pygame.draw.rect(b, (200, 150, 60, alpha), r, 1, border_radius=8)
        cx = bw // 2
        pygame.draw.polygon(
            b, (255, 236, 180, alpha),
            [(cx - 6, bh), (cx + 6, bh), (cx, bh + 9)],
        )
        b.blit(rendered, (pad, pad))
        bx = ox + max(0, (pet_w - b.get_width()) // 2)
        bx = max(self.slot_x + 2, min(canvas.get_width() - b.get_width() - 2, bx))
        canvas.blit(b, (bx, 4))


class _MiniBubbleQueue:
    def __init__(self):
        self._q: list[tuple[str, int]] = []
        self.current = None

    def clear(self):
        self._q.clear()
        self.current = None

    def push(self, text: str, life: int = 160):
        self._q.append((text, life))

    def tick(self):
        if self.current:
            self.current.life -= 1
            if self.current.life <= 0:
                self.current = None
        if self.current is None and self._q:
            text, life = self._q.pop(0)

            class _B:
                pass

            b = _B()
            b.text = text
            b.life = life
            self.current = b
        return self.current
