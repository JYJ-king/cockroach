"""会计猫同伴：同窗右侧槽位，偶尔与主宠对喷黑话。"""

from __future__ import annotations

import math
import random
import time
from typing import TYPE_CHECKING, Callable

import pygame

if TYPE_CHECKING:
    pass

# 对喷脚本：(角色, 台词)  main=主宠  buddy=同伴
# 场景不限财务：打工/编程/摸鱼/吃饭/桌宠自嘲等，台词宜短
BANTER_SCRIPTS: list[list[tuple[str, str]]] = [
    # —— 财务经典 ——
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
    # —— 打工黑话 ——
    [
        ("main", "拉个短会"),
        ("buddy", "短会从不短"),
        ("main", "就五分钟"),
        ("buddy", "五分钟起步价"),
    ],
    [
        ("main", "owner是谁?"),
        ("buddy", "又是我吗"),
        ("main", "大家一起扛"),
        ("buddy", "大家=你"),
    ],
    [
        ("main", "先沉淀一波"),
        ("buddy", "沉淀成文档了吗"),
        ("main", "在脑子里"),
        ("buddy", "那叫蒸发"),
    ],
    [
        ("main", "同步一下"),
        ("buddy", "同步成群发了?"),
        ("main", "关键干系人"),
        ("buddy", "全公司都知道了"),
    ],
    [
        ("main", "这个很紧急"),
        ("buddy", "排期呢?"),
        ("main", "昨天就要"),
        ("buddy", "时间机器呢"),
    ],
    [
        ("main", "加班加点干"),
        ("buddy", "调休写进合同了?"),
        ("main", "...精神加班"),
        ("buddy", "精神不发薪"),
    ],
    [
        ("main", "周报写好了?"),
        ("buddy", "复制上周的"),
        ("main", "也是一种复用"),
        ("buddy", "也是一种摆烂"),
    ],
    # —— 程序员 ——
    [
        ("main", "本地能跑"),
        ("buddy", "线上呢?"),
        ("main", "环境问题"),
        ("buddy", "经典甩锅"),
    ],
    [
        ("main", "先复现再说"),
        ("buddy", "我这复现不了"),
        ("main", "那就是你的环境"),
        ("buddy", "那就是你的bug"),
    ],
    [
        ("main", "是不是缓存?"),
        ("buddy", "清了三次了"),
        ("main", "重启试试"),
        ("buddy", "重启治百病?"),
    ],
    [
        ("main", "日志呢日志!"),
        ("buddy", "打太多了"),
        ("main", "关键的呢?"),
        ("buddy", "恰好没打"),
    ],
    [
        ("main", "这PR太大了"),
        ("buddy", "拆不动了"),
        ("main", "单测呢?"),
        ("buddy", "信心单测"),
    ],
    [
        ("main", "命名能看懂吗"),
        ("buddy", "tmp2_final_真的"),
        ("main", "别硬编码"),
        ("buddy", "魔法数字护体"),
    ],
    [
        ("main", "周末别发版"),
        ("buddy", "已经发了"),
        ("main", "...回滚方案呢"),
        ("buddy", "祈祷方案"),
    ],
    # —— 摸鱼与日常 ——
    [
        ("main", "喝水!"),
        ("buddy", "你先喝"),
        ("main", "我看着你喝"),
        ("buddy", "那谁都别喝"),
    ],
    [
        ("main", "中午吃啥"),
        ("buddy", "你问我我问谁"),
        ("main", "随便"),
        ("buddy", "随便最难选"),
    ],
    [
        ("main", "困了..."),
        ("buddy", "咖啡续命"),
        ("main", "续不动了"),
        ("buddy", "那躺平吧"),
    ],
    [
        ("main", "要下雨了"),
        ("buddy", "记得收衣服"),
        ("main", "我是小猫"),
        ("buddy", "也怕湿"),
    ],
    [
        ("main", "周末有空吗"),
        ("buddy", "没空加班"),
        ("main", "那一起加班?"),
        ("buddy", "你想得美"),
    ],
    [
        ("main", "减肥!"),
        ("buddy", "从下一顿开始"),
        ("main", "下一顿奶茶"),
        ("buddy", "逻辑自洽"),
    ],
    [
        ("main", "运动了吗"),
        ("buddy", "眼睛在运动"),
        ("main", "刷手机算吗"),
        ("buddy", "算手指操"),
    ],
    # —— 桌宠自嘲 / 互怼身份 ——
    [
        ("main", "你怎么又来"),
        ("buddy", "你先开口的"),
        ("main", "我是主宠"),
        ("buddy", "我是主见"),
    ],
    [
        ("main", "别挡屏幕"),
        ("buddy", "你先挪开"),
        ("main", "我在角落"),
        ("buddy", "角落也很挤"),
    ],
    [
        ("main", "被看见了!"),
        ("buddy", "装睡"),
        ("main", "太晚了"),
        ("buddy", "那就对喷"),
    ],
    [
        ("main", "谁更可爱"),
        ("buddy", "当然是我"),
        ("main", "投票呢"),
        ("buddy", "我投我自己"),
    ],
    [
        ("main", "你影子好丑"),
        ("buddy", "你本体一般"),
        ("main", "握手?"),
        ("buddy", "碰鼻头一下"),
    ],
    [
        ("main", "今晚睡哪"),
        ("buddy", "键盘旁边"),
        ("main", "那位置是我的"),
        ("buddy", "拼桌吗"),
    ],
    [
        ("main", "铲屎官来了"),
        ("buddy", "散!"),
        ("main", "假装屏保"),
        ("buddy", "假装鼠标垫"),
    ],
    # —— 互联网 / 游戏感 ——
    [
        ("main", "今天玄学"),
        ("buddy", "改个注释就好了"),
        ("main", "真的假的"),
        ("buddy", "玄学不解释"),
    ],
    [
        ("main", "报错好看吗"),
        ("buddy", "红的像鞭炮"),
        ("main", "过年了?"),
        ("buddy", "年年有bug"),
    ],
    [
        ("main", "再试一次"),
        ("buddy", "定义疯狂"),
        ("main", "也许这次行"),
        ("buddy", "也许你做梦"),
    ],
    [
        ("main", "网卡了"),
        ("buddy", "锅在运营商"),
        ("main", "那我重连"),
        ("buddy", "信仰重连"),
    ],
    [
        ("main", "打游戏吗"),
        ("buddy", "打工人呢"),
        ("main", "打工人也要玩"),
        ("buddy", "老板在看吗"),
    ],
    [
        ("main", "上分!"),
        ("buddy", "上什么分"),
        ("main", "人生经验分"),
        ("buddy", "扣光了吧"),
    ],
    # —— 抬杠哲学 ——
    [
        ("main", "你说得对"),
        ("buddy", "那我反驳呢"),
        ("main", "...你说得也对"),
        ("buddy", "抬杠成功"),
    ],
    [
        ("main", "听我一句"),
        ("buddy", "我听十句"),
        ("main", "然后呢"),
        ("buddy", "一句不听"),
    ],
    [
        ("main", "讲道理"),
        ("buddy", "讲感觉"),
        ("main", "感觉不准"),
        ("buddy", "道理没人听"),
    ],
    [
        ("main", "你赢了"),
        ("buddy", "赢什么"),
        ("main", "抬杠锦标赛"),
        ("buddy", "金鱼干奖给我"),
    ],
    [
        ("main", "消停会儿"),
        ("buddy", "你先消停"),
        ("main", "那我们都静音"),
        ("buddy", "三...二...又吵"),
    ],
    [
        ("main", "今天和好吧"),
        ("buddy", "条件呢"),
        ("main", "请你喝水"),
        ("buddy", "加奶茶"),
    ],
]

# 月结/高压日：鼓励型对白（不调侃加班、不催票、不阴阳）
SUPPORT_BANTER_SCRIPTS: list[list[tuple[str, str]]] = [
    [
        ("main", "好累..."),
        ("buddy", "你已经很棒"),
        ("main", "真的吗"),
        ("buddy", "真的,歇口气"),
    ],
    [
        ("main", "表还没平"),
        ("buddy", "慢慢来,能平"),
        ("main", "怕来不及"),
        ("buddy", "一步一步来"),
    ],
    [
        ("main", "月结好烦"),
        ("buddy", "我陪着你"),
        ("main", "谢谢猫"),
        ("buddy", "喝口水再战"),
    ],
    [
        ("main", "眼睛花了"),
        ("buddy", "看远处20秒"),
        ("main", "好"),
        ("buddy", "护眼也是生产力"),
    ],
    [
        ("main", "好想躺平"),
        ("buddy", "合法摸鱼两分钟"),
        ("main", "可以吗"),
        ("buddy", "必须可以"),
    ],
    [
        ("main", "又改数了"),
        ("buddy", "深呼吸,再改"),
        ("main", "心态崩了"),
        ("buddy", "崩完还能站起来"),
    ],
    [
        ("main", "今晚能睡吗"),
        ("buddy", "能,留点力气"),
        ("main", "还有一堆"),
        ("buddy", "明天也有你"),
    ],
    [
        ("main", "我行吗"),
        ("buddy", "你一直很行"),
        ("main", "夸我"),
        ("buddy", "月结战士喵"),
    ],
    [
        ("main", "咖啡续命"),
        ("buddy", "也记得喝水"),
        ("main", "嗯"),
        ("buddy", "腰也要直一点"),
    ],
    [
        ("main", "审计来了"),
        ("buddy", "材料齐就稳"),
        ("main", "紧张"),
        ("buddy", "我给你加油"),
    ],
    [
        ("main", "差一分"),
        ("buddy", "找得到的"),
        ("main", "头大"),
        ("buddy", "摸摸头再找"),
    ],
    [
        ("main", "别催我"),
        ("buddy", "不催,只陪"),
        ("main", "真好"),
        ("buddy", "你先保存一下"),
    ],
]


def pick_banter_script(support: bool = False) -> list[tuple[str, str]]:
    """按情境抽取对喷脚本。"""
    pool = SUPPORT_BANTER_SCRIPTS if support else BANTER_SCRIPTS
    if not pool:
        pool = BANTER_SCRIPTS
    return list(random.choice(pool))


class AccountantBuddy:
    """会计猫同伴：画在主窗口右侧槽，自己排队说话。"""

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
        script = list(script or pick_banter_script(support=False))
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

        # 对喷时现身挥爪；平时完全隐去
        if self.bantering:
            self.roach.target_alpha = 255
            self.roach.target_scale = 1.05 + 0.03 * math.sin(pygame.time.get_ticks() * 0.01)
        else:
            self.roach.target_alpha = 0
            self.roach.target_scale = 0.95

        self.roach.set_facing(-1, 0)
        self.roach.tick(
            False, 0.2, False, False,
            dancing=self.bantering,
            anim="waving" if self.bantering else "idle",
        )

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
