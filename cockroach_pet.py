"""桌面小猫宠物 — macOS AppKit / Windows 透明置顶窗口 + pygame 离屏绘制。"""

import math
import os
import json
import queue
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from enum import Enum, auto

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

# Windows：在创建窗口前声明 DPI 感知，避免 125%/150% 缩放下点击错位、窗体偏移
if IS_WIN:
    try:
        import ctypes as _ctypes_dpi

        try:
            _ctypes_dpi.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        except Exception:
            _ctypes_dpi.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# macOS 用 AppKit 窗口时，禁止 SDL 抢窗口；Windows 需要真实 pygame 窗口
if IS_MAC:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame

try:
    import psutil
    PSUTIL_OK = True
except ImportError:
    psutil = None  # type: ignore
    PSUTIL_OK = False

from phrase_packs import PACKS
from roach_settings import (
    apply_interaction_interval,
    evaluate_achievements,
    load_progress,
    load_settings,
    save_progress,
    save_provider_api_key,
    save_settings,
)
from desktop_chrome import DesktopChrome
from accountant_buddy import AccountantBuddy, pick_banter_script
from story_mode import (
    CARE_EYE_PHRASES,
    CARE_STRETCH_PHRASES,
    CARE_WATER_PHRASES,
    CLICK_BANTER_PHRASES,
    POKE_BANTER_PHRASES,
    REST_PHRASES,
    pick_rest_line,
    pick_showcase_line,
    pick_story,
)
from presence_guard import (
    detect_presence,
    routine_mode as _presence_routine_mode,
    system_focus_active,
)
from llm_client import (
    PROVIDER_ORDER,
    LLMError,
    current_provider_name,
    generate_banter_script,
    generate_line,
    generate_story_lines,
    provider_ready,
)

OBJC_OK = False
if IS_MAC:
    try:
        import objc
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyAccessory,
            NSBackingStoreBuffered,
            NSCompositeSourceOver,
            NSColor,
            NSEvent,
            NSFloatingWindowLevel,
            NSImage,
            NSScreen,
            NSView,
            NSWindow,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorIgnoresCycle,
            NSWindowCollectionBehaviorStationary,
        )
        from Foundation import NSData, NSDate, NSDefaultRunLoopMode, NSMakeRect
        OBJC_OK = True
    except ImportError:
        OBJC_OK = False

CAPTION = "CatPet"
FPS = 60


def resource_dir() -> str:
    """开发目录，或 PyInstaller 解包目录（含贴图）。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def app_dir() -> str:
    """可写/工作目录：开发时为脚本目录，打包后为 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


_BASE_DIR = resource_dir()
IMAGE_PATH = os.path.join(_BASE_DIR, "cockroach.png")
CAT_IMAGE_DIR = os.path.join(_BASE_DIR, "image")
CODEX_PETS_DIR = os.path.join(_BASE_DIR, "codex_pets")
# 小猫 GIF：Idle / Waving / Running / Waiting / Review
CAT_ANIM_FILES = {
    "idle": "Idle.gif",
    "waving": "Waving.gif",
    "running": "Running.gif",
    "waiting": "Waiting.gif",
    "review": "Review.gif",
}
# Codex spritesheet（awesome-codex-pet）动作行 → 本项目动画名
# 参考: https://github.com/legeling/awesome-codex-pet
CODEX_CELL_W, CODEX_CELL_H = 192, 208
CODEX_COLUMNS = 8
CODEX_ANIM_ROWS = {
    # name: (row, frame_durations_ms)
    "idle": (0, [280, 110, 110, 140, 140, 320]),
    "running": (1, [120, 120, 120, 120, 120, 120, 120, 220]),  # running-right
    "waving": (3, [140, 140, 140, 280]),
    "waiting": (6, [150, 150, 150, 150, 150, 260]),
    "review": (8, [150, 150, 150, 150, 150, 280]),
}
CLASSIC_APPEARANCE = {
    "slug": "_classic",
    "name": "Classic Cat",
    "name_zh": "经典小猫",
    "author": "local",
    "source": "image/",
}
# Running 贴图默认头朝右；其它多为正面坐姿
CAT_SIDE_ANIMS = frozenset({"running"})
SKIN_TINTS = {
    "gold": (1.25, 1.05, 0.55),
    "ghost": (0.75, 0.95, 1.2),
}
SPRITE_W = 140
PET_W, PET_H = 168, 190
BUBBLE_ZONE = 48
WIN_W = PET_W + 70
WIN_H = BUBBLE_ZONE + PET_H + 28
PAD_X = (WIN_W - PET_W) // 2
ROACH_Y = BUBBLE_ZONE + 6
BUDDY_SLOT_W = PET_W + 36
DUAL_WIN_W = WIN_W + BUDDY_SLOT_W

WALK_SPEED = 1.8
RUN_SPEED = 3.6
BOB_AMPLITUDE = 2.0
# 蟑螂静态贴图仅作 GIF 缺失时的回退资源
SPRITE_ROTATE_DEG = -45
SPRITE_UPRIGHT_HEADING = -90.0
LEG_COLOR = (138, 72, 32)
LEG_COLOR_DARK = (100, 48, 20)
ANTENNA_COLOR = (90, 55, 30)
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# Windows 色键透明（品红底会被抠掉）
_WIN_COLORKEY = (255, 0, 255)

# 养生提醒节奏预设（秒）：护眼 / 喝水 / 伸展
CARE_PRESETS: dict[str, dict[str, int]] = {
    "gentle": {"eye": 1800, "water": 2400, "stretch": 3600},
    "standard": {"eye": 1200, "water": 1800, "stretch": 2700},
    "strict": {"eye": 900, "water": 1200, "stretch": 1800},
}

# macOS / Windows 中文字体（dummy SDL 下 SysFont 无法加载 CJK）
_CJK_FONT_PATHS = (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\msyhl.ttc",
)


def load_cjk_font(size: int) -> pygame.font.Font:
    for path in _CJK_FONT_PATHS:
        if os.path.exists(path):
            return pygame.font.Font(path, size)
    return pygame.font.Font(None, size)


def period_of_day(hour: int | None = None) -> str:
    h = datetime.now().hour if hour is None else hour
    if 5 <= h < 11:
        return "morning"
    if 11 <= h < 14:
        return "noon"
    if 14 <= h < 18:
        return "afternoon"
    if 18 <= h < 23:
        return "evening"
    return "night"


def greeting_by_period() -> str:
    return {
        "morning": "早上好喵!",
        "noon": "中午好喵!",
        "afternoon": "下午好喵!",
        "evening": "晚上好喵!",
        "night": "夜深了,该踩奶了~",
    }[period_of_day()]


def routine_mode(respect_focus: bool = True, meeting_level: str = "") -> str:
    """作息模式: active | quiet | sleepish（含跨平台专注 + 会议）。"""
    return _presence_routine_mode(respect_focus=respect_focus, meeting_level=meeting_level)


def date_phrase() -> str:
    now = datetime.now()
    return f"今天{now.month}月{now.day}日 {_WEEKDAYS[now.weekday()]}"


def time_phrase() -> str:
    now = datetime.now()
    return f"现在 {now.hour:02d}:{now.minute:02d}"


# 打工人日程提醒：(小时, 分钟) -> 文案；每个槽位每天只触发一次
WORKER_SCHEDULE: list[tuple[int, int, str]] = [
    (8, 50, "快上班啦,别迟到"),
    (9, 0, "打卡了吗?对齐今天"),
    (9, 15, "财务:今日资金头寸看一眼"),
    (9, 30, "站会别开太久"),
    (9, 45, "财务:待付款清单过一遍"),
    (10, 0, "先把核心链路跑通"),
    (10, 20, "财务:银行流水拉取了吗"),
    (10, 30, "起来倒杯水吧"),
    (11, 0, "眺望远方护眼"),
    (11, 20, "财务:应收账龄盯一下"),
    (11, 30, "需求颗粒度够细吗"),
    (11, 40, "财务:进项发票收齐没"),
    (11, 50, "午饭时间将近~"),
    (12, 0, "记得好好吃饭"),
    (12, 40, "午饭后走走更好"),
    (13, 10, "财务:午后别忘暂估清理"),
    (13, 30, "午后困了?升维思考下"),
    (14, 0, "犯困?站起来晃晃"),
    (14, 20, "财务:费用报销催一波"),
    (14, 30, "下午同步一下进度"),
    (15, 0, "下午茶时间到"),
    (15, 10, "财务:报销别积压"),
    (15, 20, "财务:增值税底稿核对"),
    (15, 30, "喝水提醒!保持闭环"),
    (16, 0, "别只开会,要有抓手"),
    (16, 15, "财务:银企对账了吗"),
    (16, 30, "整理待办,别堆着"),
    (16, 45, "财务:未达账项清一清"),
    (17, 0, "收尾前复盘三分钟"),
    (17, 10, "财务:今日凭证审完没"),
    (17, 20, "财务:对账收尾了吗"),
    (17, 40, "财务:回单归档别隔夜"),
    (17, 50, "准备收工了吗"),
    (18, 0, "下班记得打卡"),
    (18, 30, "加班也要吃饭"),
    (19, 0, "财务:月结周别熬太晚"),
    (19, 30, "别把今天的锅留明天"),
    (20, 0, "别久坐,起来转转"),
    (21, 0, "还不走?注意身体"),
    (22, 0, "打工人也该休息了"),
    (23, 0, "再忙也早点睡"),
]

# 互联网黑话 / 打工人口头禅（气泡宜短）
WORKER_BUZZ = [
    "对齐一下?",
    "拉通一下",
    "闭环了吗?",
    "抓手在哪?",
    "颗粒度再细",
    "沉淀一波",
    "赋能业务!",
    "底层逻辑呢",
    "顶层设计先",
    "链路跑通没",
    "同步一下",
    "复盘三分钟",
    "owner是谁?",
    "EOD前给到",
    "ASAP跟进",
    "ping你一下",
    "拉个短会",
    "follow up",
    "先灰度再放",
    "别阻塞主线",
    "升维思考",
    "降维打击?",
    "心智占领!",
    "打通任督二脉",
    "形成方法论",
    "打造生态",
    "资源倾斜一下",
    "重点突破口",
    "可复制可规模",
    "价值主张呢",
    "用户心智!",
    "增长飞轮转起来",
    "漏斗要优化",
    "埋点看转化",
    "私域做起来",
    "破圈破局!",
    "这件事要闭环",
    "别只讲故事",
    "给个可落地的",
    "颗粒度不够",
    "链路断了",
    "先对齐目标",
    "站会一分钟",
    "今天站会!",
    "OKR对上没",
    "KPI别虚高",
    "需求又改版",
    "这个要迭代",
    "发版别翻车",
    "回滚预案呢",
    "阻塞升级!",
    "风险拉通下",
    "依赖对齐了吗",
    "排期给到我",
    "优先级重排",
    "砍需求保交付",
    "最小闭环先上",
    "MVP就行",
    "别过度设计",
    "先跑通再优化",
    "数据说话",
    "用结果说话",
    "过程也要透明",
    "同步到群里",
    "纪要谁写?",
    "Action Item!",
    "下次会前对齐",
    "这个我来owner",
    "你跟进一下",
    "我这边阻塞了",
    "卡在依赖上",
    "先解耦再推进",
    "技术债要还",
    "别只堆功能",
    "体验也要抓",
    "口碑种草一波",
    "破冰一下?",
    "脑暴十分钟",
    "团建不能当饭",
    "画饼我不吃",
    "狼性先喝水",
    "奋斗也要吃饭",
    "996不如996杯水",
    "牛马也要站立",
    "内卷停一下",
    "合法摸鱼中",
    "润之前先保存",
    "背锅侠拒绝",
    "甩锅检测中",
    "今天也在搬砖",
    "打工人续命中",
    "咖啡续命+1",
    "会议能短则短",
    "能异步就异步",
    "文档沉淀一下",
    "知识库更新了吗",
    "SOP有没有",
    "可观测性呢",
    "告警别麻木",
    "线上稳住!",
    "别带着bug睡觉",
]

# 财务人员黑话 / 行话（气泡宜短）
FINANCE_BUZZ = [
    "对账了吗?",
    "账对不平!",
    "先做凭证",
    "科目选错了",
    "借贷不平衡",
    "差额在哪?",
    "挂账先别挂",
    "暂估入账?",
    "发票来了吗",
    "专票还是普票",
    "税点多少?",
    "进项抵扣了吗",
    "销项别漏",
    "开票信息对吗",
    "抬头错了重开",
    "报销贴票!",
    "费用归属哪?",
    "成本中心选对",
    "预算超了!",
    "预算卡死了",
    "走OA审批",
    "付款申请呢",
    "回款到账没",
    "应收账款催一催",
    "坏账准备提了吗",
    "存货盘点了吗",
    "固定资产折旧",
    "摊销别忘了",
    "计提了吗?",
    "冲销一笔",
    "红冲重开",
    "结转损益",
    "月结锁账了",
    "关账前别动!",
    "年结要加班",
    "审计要底稿",
    "底稿给审计",
    "内控过不了",
    "合规风险高",
    "税务稽查来了?",
    "汇算清缴准备",
    "增值税申报",
    "个税代扣了吗",
    "社保公积金对一下",
    "工资表锁定",
    "发薪日注意现金流",
    "现金流告急",
    "头寸够不够",
    "银企对账",
    "未达账项清理",
    "银行回单归档",
    "三单匹配了吗",
    "采购订单对发票",
    "入库单呢",
    "暂收款别乱挂",
    "预付核销了吗",
    "保证金退了吗",
    "往来清账",
    "串户了!",
    "重分类一下",
    "调账要说明",
    "差错更正",
    "重要性水平呢",
    "实质重于形式",
    "谨慎性原则",
    "权责发生制",
    "收付实现制?",
    "配比原则别忘",
    "收入确认时点",
    "履约义务拆了吗",
    "新收入准则",
    "租赁准则头疼",
    "公允价值怎么估",
    "减值测试了吗",
    "商誉别乱摊",
    "合并抵消分录",
    "内部往来对平",
    "少数股东权益",
    "递延所得税",
    "税会差异调整",
    "纳税调增调减",
    "研发加计扣除",
    "高新认定材料",
    "费用资本化?",
    "别费用化乱入",
    "资本性支出分清",
    "损益别粉饰",
    "别调节利润!",
    "毛利率异常",
    "费用率飙了",
    "资产负债率高",
    "周转天数拉长",
    "账龄分析一下",
    "逾期应收跟进",
    "信用账期缩短",
    "付款账期拉长?",
    "供应商对账函",
    "询证函回函了吗",
    "函证控制好",
    "盘点监盘到场",
    "抽凭抽到你了",
    "科目余额表导出",
    "序时账查一下",
    "辅助核算补全",
    "项目核算挂上",
    "部门分摊合理吗",
    "分摊规则更新",
    "结账检查清单",
    "未记账报账单?",
    "跨期费用调整",
    "预提费用够不够",
    "待摊费用摊完",
    "材料成本差异",
    "产成品结转",
    "完工百分比法",
    "合同负债确认",
    "合同资产呢",
    "质保金挂着",
    "或有事项披露",
    "关联交易披露",
    "大额异常交易",
    "资金流水异常",
    "公私账户别混",
    "备用金报销清",
    "借款利息资本化",
    "汇率损益入账",
    "外币折算差额",
    "票据贴现了吗",
    "承兑到期注意",
    "信用证单据齐吗",
    "保理融资记账",
    "融资租赁分类",
    "经营租赁费用",
    "财务BP对齐业务",
    "经营分析会准备",
    "管理报表先出",
    "管报和财报差异",
    "滚动预测更新",
    "Forecast校准",
    "Variance分析",
    "差异归因说清楚",
    "降本增效抓手",
    "费用削减清单",
    "人效算一下",
    "单位经济模型",
    "回本周期多久",
    "ROI算过没",
    "IRR过门槛吗",
    "NPV别只看漂亮",
    "敏感性分析",
    "情景假设别太乐观",
    "资金计划报一下",
    "付款日历排好",
    "大额支付双人复核",
    "印章证照借用登记",
    "合同章别乱盖",
    "开票限额够吗",
    "红字信息表申请",
    "作废发票别乱点",
    "电子税局卡住了",
    "申报成功截图留存",
    "完税证明下载",
    "银行回单别丢",
    "档案装订归档",
    "凭证号连续吗",
    "断号查原因",
    "反结账慎用!",
    "别反结账啊",
    "财务章在谁那",
    "出纳和会计分离",
    "不相容岗位分离",
    "今日对完现金账",
    # 增补行话
    "借方贷方分清",
    "余额方向对吗",
    "试算平衡了吗",
    "明细账对总账",
    "总账对报表",
    "报表勾稽关系",
    "表表核对",
    "账实核对",
    "账证核对",
    "账账核对",
    "平行记账了吗",
    "辅助余额清零",
    "往来重分类",
    "一年内到期重分类",
    "流动性列报对吗",
    "现金流量表难产",
    "间接法调节项",
    "经营/投资/筹资分清",
    "非现金重大事项",
    "附注披露齐全吗",
    "会计估计变更",
    "会计政策变更",
    "前期差错更正",
    "追溯调整小心",
    "未来适用法?",
    "资产负债表日后事项",
    "持续经营假设",
    "合并范围变了吗",
    "VIE别漏",
    "同一控制判断",
    "非同一控制并购",
    "商誉减值迹象",
    "可收回金额测算",
    "使用价值折扣率",
    "公允价值层级",
    "金融工具分类",
    "FVTPL还是FVOCI",
    "预期信用损失",
    "ECL模型更新",
    "合同资产减值",
    "存货跌价准备",
    "可变现净值",
    "成本与市价孰低",
    "发出计价方法",
    "加权平均还是先进先出",
    "在产品约当产量",
    "制造费用分配",
    "标准成本差异",
    "量差价差分析",
    "作业成本法?",
    "本量利分析",
    "盈亏平衡点",
    "边际贡献够吗",
    "固定成本高企",
    "沉没成本别纠结",
    "机会成本算了吗",
    "预算编制启动",
    "滚动预算更新",
    "零基预算太狠",
    "预算执行率",
    "预算追加走流程",
    "费用包干超了",
    "专项资金专款专用",
    "三公经费严控",
    "业务招待费限额",
    "福利费税务风险",
    "工会经费计提",
    "教育经费结余",
    "年终奖计税方式",
    "汇缴补退税",
    "滞纳金别惹",
    "发票作废期限",
    "认证抵扣期限",
    "失控发票处理",
    "异常凭证核查",
    "税负率预警了",
    "进销项匹配度",
    "农产品抵扣注意",
    "出口退税单据",
    "免抵退测算",
    "印花税别漏缴",
    "房产土地税申报",
    "残保金年审",
    "财务报表报送",
    "工商年报别忘",
    "银行授信材料",
    "贷款合同条款",
    "担保披露完整",
    "或有负债评估",
    "售后回租分类",
    "售后服务质保",
    "客户押金核算",
    "供应商质保金",
    "长期待摊摊销",
    "开办费一次性?",
    "研发费用归集",
    "资本化时点判断",
    "利息资本化暂停",
    "专门借款一般借款",
    "汇兑损益资本化",
    "债务重组损益",
    "非货币交换",
    "政府补助分类",
    "与资产相关补助",
    "递延收益摊销",
    "股份支付费用",
    "期权估值模型",
    "限制性股票回购",
    "少数股东损益",
    "其他综合收益",
    "OCI重分类进损益",
    "所有者权益变动表",
    "资本公积明细",
    "盈余公积计提",
    "未分配利润结转",
    "分红决议执行",
    "减资程序合规",
    "增资验资报告",
    "实收资本到位",
    "抽逃出资红线",
]

# 财务人员常用口头禅（吐槽/自嘲/口头禅）
FINANCE_CATCHPHRASES = [
    "发票呢?发票呢!",
    "没有发票不能报",
    "先把票开对",
    "这个我要凭证",
    "口头说了不算",
    "系统里走一遭",
    "审批流走完再说",
    "钱不是大风刮来的",
    "预算里有吗?",
    "超预算免谈",
    "这个科目不对",
    "我给你调个账",
    "别让我反结账",
    "月结周别添乱",
    "关账了别再提需求",
    "审计要问你的",
    "底稿你自己补",
    "函证请配合",
    "现金盘点到场",
    "公私款分开!",
    "备用金先核销",
    "报销单填完整",
    "附件缺三件套",
    "三单不全不付",
    "对不上别找我急",
    "差额你自己查",
    "串户了自己改",
    "我只做合规的",
    "税务风险我背不起",
    "这个要税局口径",
    "税负率别乱蹦",
    "别教我做假账",
    "利润不是调出来的",
    "报表要经得起查",
    "重要性水平在这",
    "实质重于形式懂吗",
    "谨慎一点没坏处",
    "权责发生制记牢",
    "今天头寸紧",
    "付款排期已满",
    "大额要双签",
    "章不能外借",
    "回单今晚归档",
    "凭证别积压",
    "明细对不平睡不着",
    "试算不平衡心慌",
    "现金流量表劝退",
    "合并抵消头大",
    "递延所得税玄学",
    "新准则又改了",
    "估值得有依据",
    "模型别拍脑袋",
    "差异要能解释",
    "Variance讲清楚",
    "管报别当财报",
    "BP先对齐口径",
    "口径不统一别吵",
    "数出一门",
    "一个数一个源",
    "别口头改数",
    "改数留痕迹",
    "版本锁死了",
    "以系统为准",
    "以银行回单为准",
    "以发票原件为准",
    "电子档案也要全",
    "截图不能当回单",
    "聊天记录不算凭证",
    "老板签字也要附件",
    "先合规后速度",
    "快可以,乱不行",
    "月底见真章",
    "年结人要疯",
    "汇缴季别请假",
    "申报日别断网",
    "税局系统又挂了",
    "电子税局转圈中",
    "开票额度不够了",
    "红字信息表走起",
    "专票认证别超期",
    "进项转出算了吗",
    "视同销售别漏",
    "福利费税会差",
    "年终奖计税想好",
    "个税APP对一下",
    "社保基数调了吗",
    "公积金比例变了?",
    "发薪日我最忙",
    "工资条发出去了",
    "银行代发成功没",
    "退票重发跟进",
    "对账函请盖章回",
    "询证函当天寄",
    "回函率太低了",
    "盘点表签字确认",
    "监盘我到场了",
    "抽凭抽到笑不出来",
    "内控缺陷整改中",
    "不相容岗位真香",
    "出纳会计不能兼",
    "印鉴分管记牢",
    "网银U盾谁保管",
    "支付指令双人",
    "异常流水立刻查",
    "大额现金慎用",
    "坐支现金违规",
    "白条顶库绝对不行",
    "账外账想都别想",
    "两套账是红线",
    "我是会计不是金主",
    "费用请走预算",
    "这个我拒单了",
    "退回请看意见",
    "补材料再提交",
    "下次注意附件",
    "同类问题第三次了",
    "培训纪要发群里",
    "财务制度请先看",
    "SOP在知识库",
    "别催我临时急付",
    "急付也要合规",
    "特批也要留痕",
    "特批不能常态化",
    "我理解你急",
    "但规则不能破",
    "对公对私分清",
    "个人卡收款风险大",
    "公转私要有理由",
    "关联交易披露好",
    "价格要公允",
    "合同先审再付",
    "无合同不付款",
    "验收单呢?",
    "入库单呢?",
    "物流单呢?",
    "签收单呢?",
    "会议纪要当附件",
    "邮件确认也行",
    "微信转账截图不够",
    "支付宝流水要清晰",
    "境外支付备案了吗",
    "外汇申报别忘",
    "出口退税材料齐",
    "报关单核对",
    "我先把账做平",
    "平了再睡觉",
    "不平不下班",
    "对平了才舒服",
    "借贷平衡万岁",
    "会计人的浪漫是平衡",
    "数字洁癖发作中",
    "差一分钱也要查",
    "一分钱难倒英雄汉",
    "尾差调到哪里?",
    "汇兑尾差处理",
    "四舍五入别累积",
    "精度设置检查",
    "本位币别弄错",
    "汇率取哪天的",
    "即期还是平均",
    "我去对银行了",
    "回单打印机又卡",
    "U盾过期续办",
    "网银额度调高",
    "今日付款截止点",
    "过点等明天",
    "资金计划已锁定",
    "临时加付困难",
]

# 财务场景互动台词
FINANCE_CLOSE = [
    "月结开始,别乱动账",
    "关账倒计时!",
    "结转损益中...",
    "科目余额表过一遍",
    "月结检查清单打钩",
    "关账完成,解放!",
    "试算平衡通过!",
    "往来重分类完成",
    "跨期调整入账了",
    "报表勾稽OK",
    "附注先别动版",
    "锁账啦,求别提需求",
]

FINANCE_AUDIT = [
    "审计抽凭了",
    "底稿补一下",
    "函证跟催!",
    "盘点请到场",
    "内控问题整改",
    "审计问询回复中",
    "抽样清单来了",
    "穿行测试配合",
    "控制测试抽样",
    "截止性测试注意",
    "期后回款说明",
    "律师函催一下",
]

FINANCE_REIMBURSE = [
    "报销贴票对齐",
    "发票真伪查一下",
    "超标准打回!",
    "缺附件不通过",
    "费用科目选对",
    "老板先批再付款",
    "出差补助算清楚",
    "交通住宿分开报",
    "连号发票注意",
    "连号太多要说明",
    "电子发票查重",
    "重复报销拦截!",
]

FINANCE_TAX = [
    "申报日前自查",
    "税负率看一眼",
    "进销项匹配吗",
    "进项认证别超期",
    "视同销售别漏",
    "汇缴资料归档",
    "滞纳金零容忍",
    "税局通知别已读不回",
]

FINANCE_PAYROLL = [
    "工资表锁定了吗",
    "个税算一遍",
    "社保公积金对平",
    "代发成功确认",
    "退票名单跟进",
    "工资条已发送",
    "年终奖计税方式?",
    "发薪日头寸预留",
]

FINANCE_TIPS = [
    "差一分也要查到底",
    "先合规,再谈快",
    "月结周少开会",
    "回单当天归档最省事",
    "预算外先问财务",
    "口头承诺换不了凭证",
    "数出一门少吵架",
    "改数请留痕",
    "公私款永远分开",
    "U盾别随手放",
    "大额支付记得双人",
    "对账不平先别慌,逐步缩小范围",
]

WORKER_TIPS_WEEKDAY = [
    "工资虽薄,身体要厚",
    "需求又改?先深呼吸",
    "会议能短则短",
    "邮件回完喝口水",
    "保存!保存!保存!",
    "摸鱼也要护颈椎",
    "别对屏幕较劲",
    "今天也辛苦了",
    "Deadline 会过,你还在",
    "站起来伸个懒腰",
    "对齐之前先喝水",
    "闭环之前先吃饭",
    "颗粒度细,颈椎别细",
    "别用黑话替代思考",
    "拉通不如先把活干完",
    "异步沟通能救命",
    "站会超过15分钟就跑",
    "需求变更请走流程",
    "今天也拒绝无效会议",
    "搬砖也要有节奏",
]

WORKER_TIPS_MONDAY = [
    "周一加油,打工人!",
    "新的一周,先开电脑",
    "周一别内耗,一步步来",
    "周一站会:活着就好",
    "本周目标先对齐自己",
    "别周一就把电量花光",
]

WORKER_TIPS_FRIDAY = [
    "周五了!稳住别崩",
    "周五少开会多推进",
    "周末将近,收好尾巴",
    "周五别上大改动",
    "发版?周五慎之",
    "复盘完就润",
]

WORKER_TIPS_WEEKEND = [
    "周末也要好好休息",
    "工作邮件可以明天看",
    "出去走走晒晒太阳",
    "周末拒绝对齐",
    "Slack可以已读不回",
    "牛马周末也是人",
]

# 场景化打工人互动台词
WORKER_STANDUP = [
    "昨日:活着 今日:继续活",
    "阻塞:老板的临时需求",
    "我这边没有阻塞(假的)",
    "昨天对齐了,今天再对齐",
    "站会结束!散会!",
    "同步完毕,各自闭环",
]

WORKER_ALIGN = [
    "我们对齐一下预期",
    "先对齐目标再开战",
    "这个认知要拉齐",
    "颗粒度对齐了吗?",
    "上下游对齐一下",
    "对齐完再拉会",
]

WORKER_REVIEW = [
    "复盘:过程很曲折",
    "结论:下次早点睡觉",
    "亮点:没有出事",
    "改进:少开会",
    "沉淀:喝水很重要",
    "复盘完毕,继续搬砖",
]

WORKER_FISH = [
    "合法摸鱼中...",
    "我在深度思考(摸鱼)",
    "异步回复中,勿扰",
    "状态:脑暴(发呆)",
    "正在沉淀方法论(划水)",
    "关闭摄像头开会中",
]

WORKER_PUA_RESIST = [
    "画饼充不了饥",
    "狼性不能当饭吃",
    "拒绝情绪PUA",
    "我的边界感在线",
    "加班换不来热爱",
    "身体是1,别归零",
]


def is_workday(dt: datetime | None = None) -> bool:
    d = dt or datetime.now()
    return d.weekday() < 5  # 周一到周五


def worker_startup_tip() -> str:
    now = datetime.now()
    if not is_workday(now):
        return PACKS.pick("worker_tips_weekend", WORKER_TIPS_WEEKEND)
    if now.weekday() == 0:
        return PACKS.pick("worker_tips_monday", WORKER_TIPS_MONDAY)
    if now.weekday() == 4:
        return PACKS.pick("worker_tips_friday", WORKER_TIPS_FRIDAY)
    period = period_of_day(now.hour)
    if period == "morning":
        return random.choice(["打工人早上好", "今天也要稳住", "先喝口水开工", "先摸摸猫再开工"])
    if period == "noon":
        return random.choice(["中午要记得吃饭哦", "午休十分钟也好", "闭环之前先干饭", "给我开个罐头?"])
    if period == "afternoon":
        return random.choice(["下午继续干!", "摸鱼合法但护眼", "下午茶续命", "激光笔呢?"])
    if period == "evening":
        return random.choice(["别卷太晚", "下班路上注意安全", "收工别忘打卡", "该陪猫玩了"])
    return random.choice(["夜班也辛苦了", "早点睡吧打工人", "线上稳住,人先睡", "深夜跑酷预告"])


def worker_random_tip() -> str:
    now = datetime.now()
    if not is_workday(now):
        return PACKS.pick("worker_tips_weekend", WORKER_TIPS_WEEKEND)
    r = random.random()
    if now.weekday() == 0 and r < 0.3:
        return PACKS.pick("worker_tips_monday", WORKER_TIPS_MONDAY)
    if now.weekday() == 4 and r < 0.3:
        return PACKS.pick("worker_tips_friday", WORKER_TIPS_FRIDAY)
    if r < 0.22:
        return PACKS.pick("worker_buzz", WORKER_BUZZ)
    if r < 0.32:
        return PACKS.pick("programmer_buzz", WORKER_BUZZ)
    if r < 0.45:
        return PACKS.pick("finance_catchphrases", FINANCE_CATCHPHRASES)
    if r < 0.6:
        return PACKS.pick("finance_buzz", FINANCE_BUZZ)
    if r < 0.7:
        return PACKS.pick("finance_tips", FINANCE_TIPS)
    fest = PACKS.extras("festival")
    if fest and r < 0.78:
        return random.choice(fest)
    return PACKS.pick("worker_tips_weekday", WORKER_TIPS_WEEKDAY)


def finance_buzzword() -> str:
    """财务行话 / 口头禅混合。"""
    if random.random() < 0.55:
        return PACKS.pick("finance_catchphrases", FINANCE_CATCHPHRASES)
    return PACKS.pick("finance_buzz", FINANCE_BUZZ)


def finance_random_tip() -> str:
    """财务专属提醒（含口头禅）。"""
    r = random.random()
    if r < 0.4:
        return PACKS.pick("finance_catchphrases", FINANCE_CATCHPHRASES)
    if r < 0.7:
        return PACKS.pick("finance_tips", FINANCE_TIPS)
    if r < 0.85:
        return PACKS.pick("finance_buzz", FINANCE_BUZZ)
    day = datetime.now().day
    if day <= 5:
        return random.choice(["月初对账黄金期", "上月往来清一清", PACKS.pick("finance_close", FINANCE_CLOSE)])
    if day >= 25:
        return random.choice(["月底将近,准备月结", "别临时甩大额付款", PACKS.pick("finance_close", FINANCE_CLOSE)])
    if day in (7, 8, 9, 10, 11, 12, 13, 14, 15):
        return random.choice(
            PACKS.pool("finance_tax", FINANCE_TAX) + PACKS.pool("finance_payroll", FINANCE_PAYROLL)
        )
    return PACKS.pick("finance_tips", FINANCE_TIPS)


def worker_buzzword() -> str:
    """互联网黑话 + 财务黑话混合。"""
    r = random.random()
    if r < 0.2:
        return PACKS.pick("finance_catchphrases", FINANCE_CATCHPHRASES)
    if r < 0.4:
        return PACKS.pick("finance_buzz", FINANCE_BUZZ)
    if r < 0.55:
        return PACKS.pick("programmer_buzz", WORKER_BUZZ)
    return PACKS.pick("worker_buzz", WORKER_BUZZ)


def worker_schedule_due(fired: set[str], now: datetime | None = None) -> str | None:
    """检查是否到了日程提醒点；返回文案或 None。"""
    now = now or datetime.now()
    if not is_workday(now):
        return None
    for hour, minute, text in WORKER_SCHEDULE:
        key = f"{now.date()}-{hour:02d}:{minute:02d}"
        if key in fired:
            continue
        # 在目标分钟内触发（容错 2 分钟）
        if now.hour == hour and minute <= now.minute <= minute + 1:
            fired.add(key)
            return text
    return None


# ── 系统监控（macOS / Windows，依赖 psutil）────────────────

_net_sample: tuple[float, int, int] | None = None  # t, sent, recv


def _fmt_bytes(n: float) -> str:
    n = float(max(0, n))
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024), ("B", 1)):
        if n >= div or unit == "B":
            val = n / div
            return f"{val:.1f}{unit}" if unit != "B" else f"{int(val)}B"
    return "0B"


def _fmt_rate(bps: float) -> str:
    return f"{_fmt_bytes(bps)}/s"


def _disk_root() -> str:
    if IS_WIN:
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


def _net_counters() -> tuple[int, int]:
    if not PSUTIL_OK:
        return 0, 0
    io = psutil.net_io_counters()
    if io is None:
        return 0, 0
    return int(io.bytes_sent), int(io.bytes_recv)


def refresh_net_sample() -> tuple[float, float]:
    """更新网速采样，返回 (上行B/s, 下行B/s)。"""
    global _net_sample
    now = time.time()
    sent, recv = _net_counters()
    up = down = 0.0
    if _net_sample is not None:
        t0, s0, r0 = _net_sample
        dt = max(0.05, now - t0)
        up = max(0.0, (sent - s0) / dt)
        down = max(0.0, (recv - r0) / dt)
    _net_sample = (now, sent, recv)
    return up, down


def format_cpu_line(s: dict | None = None) -> str:
    s = s or sample_system()
    if not s:
        return "CPU: 需要安装 psutil"
    return f"CPU {s['cpu']:.0f}% · {s['cpu_count']}核"


def format_mem_line(s: dict | None = None) -> str:
    s = s or sample_system(0.0)
    if not s:
        return "内存: 需要安装 psutil"
    return f"内存 {s['mem_pct']:.0f}% {_fmt_bytes(s['mem_used'])}/{_fmt_bytes(s['mem_total'])}"


def format_disk_line(s: dict | None = None) -> str:
    s = s or sample_system(0.0)
    if not s:
        return "磁盘: 需要安装 psutil"
    root = s["disk_path"].rstrip("\\/") or s["disk_path"]
    return f"磁盘{root} {s['disk_pct']:.0f}% {_fmt_bytes(s['disk_used'])}/{_fmt_bytes(s['disk_total'])}"


def format_net_line(s: dict | None = None) -> str:
    s = s or sample_system(0.0)
    if not s:
        return "网络: 需要安装 psutil"
    return f"网 ↑{_fmt_rate(s['net_up'])} ↓{_fmt_rate(s['net_down'])}"


def format_sys_overview(s: dict | None = None) -> list[str]:
    s = s or sample_system()
    if not s:
        return ["监控不可用", "pip install psutil"]
    return [
        format_cpu_line(s),
        format_mem_line(s),
        format_disk_line(s),
        format_net_line(s),
    ]


def sys_alert_messages(s: dict | None = None) -> list[str]:
    """资源告警文案（小猫吐槽）。"""
    s = s or sample_system(0.05)
    if not s:
        return []
    msgs = []
    if s["cpu"] >= 85:
        msgs.append(f"CPU发烧{s['cpu']:.0f}%!少开点标签喵")
    elif s["cpu"] >= 70:
        msgs.append(f"CPU有点忙{s['cpu']:.0f}%")
    if s["mem_pct"] >= 90:
        msgs.append(f"内存告急{s['mem_pct']:.0f}%!快清理")
    elif s["mem_pct"] >= 80:
        msgs.append(f"内存紧张{s['mem_pct']:.0f}%")
    if s["disk_pct"] >= 92:
        msgs.append(f"磁盘快满{s['disk_pct']:.0f}%!清垃圾")
    elif s["disk_pct"] >= 85:
        msgs.append(f"磁盘偏满{s['disk_pct']:.0f}%")
    if s["net_down"] >= 8 * 1024 * 1024:
        msgs.append("下行好猛,在下片?")
    return msgs


def fetch_weather_sync(timeout: float = 4.0) -> str | None:
    """用 wttr.in 拉取简短天气（无需 API Key）。失败返回 None。"""
    url = "https://wttr.in/?format=%C+%t&lang=zh&m"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="ignore").strip()
        if not text or "Unknown" in text:
            return None
        # 去掉多余空格，限制长度
        text = " ".join(text.split())
        if len(text) > 18:
            text = text[:17] + "…"
        return text
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def canvas_to_nsimage(surface: pygame.Surface):
    if not OBJC_OK:
        return None
    return surface_to_nsimage(surface)


_DESKTOP_SIZE_CACHE: tuple[float, tuple[int, int]] = (0.0, (1280, 800))
_LOAD_SAMPLE_CACHE: tuple[float, dict | None] = (0.0, None)


def get_desktop_size() -> tuple[int, int]:
    """屏幕尺寸：缓存约 1 秒，避免每帧打 AppKit/WinAPI。"""
    global _DESKTOP_SIZE_CACHE
    now = time.time()
    ts, cached = _DESKTOP_SIZE_CACHE
    if now - ts < 1.0 and cached[0] > 0 and cached[1] > 0:
        return cached

    size = (1280, 800)
    if IS_MAC and OBJC_OK:
        try:
            f = NSScreen.mainScreen().frame()
            size = (int(f.size.width), int(f.size.height))
        except Exception:
            size = cached if cached[0] > 0 else size
    elif IS_WIN:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            SM_CXVIRTUALSCREEN = 78
            SM_CYVIRTUALSCREEN = 79
            vw = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
            vh = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
            if vw > 0 and vh > 0:
                size = (vw, vh)
            else:
                size = (int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1)))
        except Exception:
            size = cached if cached[0] > 0 else size
    else:
        try:
            if pygame.display.get_init():
                info = pygame.display.Info()
                if info.current_w > 0 and info.current_h > 0:
                    size = (int(info.current_w), int(info.current_h))
        except Exception:
            pass
    _DESKTOP_SIZE_CACHE = (now, size)
    return size


def sample_load_light(max_age: float = 2.5) -> dict | None:
    """轻量 CPU/内存采样（带缓存），供应援门控用，绝不 sleep。"""
    global _LOAD_SAMPLE_CACHE
    now = time.time()
    ts, cached = _LOAD_SAMPLE_CACHE
    if cached is not None and now - ts < max_age:
        return cached
    if not PSUTIL_OK:
        return None
    try:
        info = {
            "cpu": float(psutil.cpu_percent(interval=0.0)),
            "mem_pct": float(psutil.virtual_memory().percent),
        }
        _LOAD_SAMPLE_CACHE = (now, info)
        return info
    except Exception:
        return cached


def sample_system(interval_cpu: float = 0.15) -> dict | None:
    """采集 CPU / 内存 / 磁盘 / 网络。失败返回 None。主循环勿用 interval>0。"""
    if not PSUTIL_OK:
        return None
    try:
        # 短间隔采样；主线程请传 interval=0，避免卡住动画
        cpu = float(psutil.cpu_percent(interval=interval_cpu))
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage(_disk_root())
        up, down = refresh_net_sample()
        # 首次网速为 0 时不再 sleep：会冻住桌宠主循环
        return {
            "cpu": cpu,
            "mem_pct": float(vm.percent),
            "mem_used": int(vm.used),
            "mem_total": int(vm.total),
            "disk_pct": float(disk.percent),
            "disk_used": int(disk.used),
            "disk_total": int(disk.total),
            "disk_path": _disk_root(),
            "net_up": up,
            "net_down": down,
            "cpu_count": int(psutil.cpu_count() or 0),
        }
    except Exception:
        return None


def surface_to_nsimage(surface: pygame.Surface):
    """pygame Surface → NSImage。优先原始像素，避免每帧 PNG 编码卡顿。"""
    try:
        import ctypes
        from AppKit import NSBitmapImageRep, NSImage, NSDeviceRGBColorSpace

        src = surface.convert_alpha()
        w, h = src.get_size()
        tobytes = getattr(pygame.image, "tobytes", None) or pygame.image.tostring
        raw = tobytes(src, "RGBA")
        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None,
            w,
            h,
            8,
            4,
            True,
            False,
            NSDeviceRGBColorSpace,
            w * 4,
            32,
        )
        dest = rep.bitmapData()
        if dest is not None:
            try:
                ctypes.memmove(dest, raw, min(len(raw), w * h * 4))
            except TypeError:
                ctypes.memmove(int(dest), raw, min(len(raw), w * h * 4))
            img = NSImage.alloc().initWithSize_((float(w), float(h)))
            img.addRepresentation_(rep)
            if img is not None:
                return img
    except Exception:
        pass
    # 回退：PNG（慢，仅应急）
    import io

    buf = io.BytesIO()
    pygame.image.save(surface, buf, "png")
    png = buf.getvalue()
    data = NSData.dataWithBytes_length_(png, len(png))
    img = NSImage.alloc().initWithData_(data)
    if img is None:
        raise RuntimeError("NSImage.initWithData_ failed")
    return img


# ── 状态 ──────────────────────────────────────────────────

class State(Enum):
    IDLE = auto()
    WALK = auto()
    RUN = auto()
    SLEEP = auto()
    HAPPY = auto()
    CURIOUS = auto()
    DRAGGED = auto()
    SCARED = auto()
    GREET = auto()
    DANCE = auto()
    FOLLOW = auto()
    SPIN = auto()
    HIDE = auto()
    BELLY = auto()
    LASER = auto()
    PEEK = auto()      # 观鸟 / 伸脖子张望
    FORAGE = auto()    # 沿边打猎 / 追毛线
    ZOOMIE = auto()    # 短暂疯跑
    PANIC = auto()     # 炸毛乱窜
    POSE = auto()      # 摆拍定格 / 发呆
    CALL = auto()      # 被召唤靠近
    CLIMB = auto()     # 自主攀爬到屏幕上沿
    HANG = auto()      # 倒挂在屏幕边缘


class Bubble:
    PHRASES = [
        "喵~", "呼噜呼噜", "嘿嘿", "我是小猫!",
        "想吃小鱼干", "晒会儿太阳", "纸箱呢...", "窗台最舒服",
    ]
    CLICK_PHRASES = CLICK_BANTER_PHRASES
    POKE_PHRASES = POKE_BANTER_PHRASES
    FEED_PHRASES = ["好吃!", "嚼嚼", "谢谢喵~", "还有吗?", "香!", "小鱼干!", "罐头呢?"]
    CHAT_PHRASES = [
        "今天键盘很暖", "你又坐很久了", "我在窗台看着你",
        "别踩尾巴~", "听说猫有九条命", "阳光真舒服",
        "要不要投喂?", "工作加油哦", "我去巡房了",
        "激光笔呢?", "屏幕好亮啊",
        "对齐了吗打工人", "闭环了再摸鱼", "颗粒度再细点",
        "站会别超时", "需求又改了?", "保存了吗老板",
        "账对平了吗", "发票贴好没", "别反结账啊",
        "月结别熬夜", "报销我帮你盯",
        "该摸摸猫了", "纸箱借我钻钻", "陪我玩毛线?",
    ]
    FORAGE_PHRASES = ["打猎中...", "这边有味道", "发现毛线?", "虫子呢?", "扑!"]
    PANIC_PHRASES = ["吸尘器!!", "黄瓜!!", "逃逃逃!", "炸毛!", "救命喵!"]
    POSE_PHRASES = ["茄子~", "拍好看点", "我帅吗?", "定格!", "今日份猫片"]
    CALL_PHRASES = ["来了来了", "叫我?", "马上到", "干嘛呀", "喵!"]
    ZOOMIE_PHRASES = ["疯了!", "Zoom!", "半夜跑酷!", "停不下来", "电光猫!"]
    STROLL_PHRASES = ["溜达溜达", "巡房中", "去那边看看", "走走停停", "晒爪爪"]
    DAYDREAM_PHRASES = ["发呆中...", "在想小鱼干", "放空~", "盯着空气", "喵?"]
    NAP_PHRASES = ["眯一会儿", "Zzz", "别吵我睡", "窗台好困", "再睡五分钟"]
    CLIMB_PHRASES = ["往上爬!", "天花板呢?", "攀岩喵", "到顶了吗", "抓抓边缘"]
    HANG_PHRASES = ["倒挂快乐", "我是蝙蝠猫", "掉不下去!", "边缘挂机", "头朝下~"]
    PEEK_PHRASES = ["谁在那?", "张望一下", "安全吗?", "伸脖子~", "鸟呢?"]
    KNEAD_PHRASES = ["踩奶中...", "软软的", "呼噜大作", "幸福!", "面团启动"]
    LOAF_PHRASES = ["面团模式", "收起爪子", "烤猫面包", "能量填充中", "别吵"]
    # 月结应援窗口结束后，第一次回家/睡觉的收工庆祝
    SUPPORT_CLOSE_PHRASES = [
        "月结收工啦!",
        "账平了·伸个懒腰",
        "辛苦了·今晚可以睡",
        "关账快乐喵",
        "这一轮过完了!",
        "礼花给你·好好歇",
        "应援下岗·你超棒",
        "收工仪式启动!",
    ]
    MEETING_END_PHRASES = [
        "辛苦啦,开完了?",
        "会开完啦·伸个懒腰",
        "散会快乐喵",
        "开完了?喝口水",
        "会议下岗·你真棒",
    ]
    FOCUS_DONE_PHRASES = [
        "番茄到啦·起来晃晃",
        "专注收工·伸个懒腰",
        "25分到了,陪你歇歇",
        "蹲守结束·该休息了",
        "深度工作收工喵",
    ]
    FOCUS_DONE_QUIET_PHRASES = [
        "番茄到了·你忙完再歇",
        "专注结束·我不打扰",
        "时间到了·继续开会也行",
        "蹲守到点·先轻声提醒",
        "收工提示·不抢你焦点",
    ]
    FOCUS_DONE_NEUTRAL_PHRASES = [
        "番茄钟结束啦",
        "专注时间到了",
        "番茄收工",
        "蹲守结束",
    ]
    GROOM_PHRASES = ["理毛中...", "舔舔", "仪表很重要", "顺一顺", "光洁如新"]
    POUNCE_PHRASES = ["扑击!", "锁定目标", "起飞!", "逮到你!", "暗杀猫"]
    BOX_PHRASES = ["纸箱是家", "钻进去!", "外面消失了", "喵窝+1", "别拆快递"]
    YARN_PHRASES = ["毛线球!", "缠住了", "再滚一下", "玩不够", "线头在哪"]
    MEOW_PHRASES = ["喵!", "喵喵!", "喵呜~", "miao~", "大声点喵!"]
    SUN_PHRASES = ["晒太阳...", "暖乎乎", "禁止打扰", "光斑是我的", "融化中"]
    SCRATCH_PHRASES = ["抓抓抓!", "沙发危!", "磨爪子", "屏幕边缘香", "咔咔~"]
    GIFT_PHRASES = ["给你的!", "我叼来了", "神秘礼物", "别嫌弃哦", "爱心投喂"]
    STARE_PHRASES = ["盯...", "看你干嘛", "眨眼挑战", "你先看破", "灵魂锁定"]
    KNOCK_PHRASES = ["推下去!", "手滑喵", "桌面清理", "重力实验", "杯杯再见"]
    HEADBUTT_PHRASES = ["蹭蹭!", "头槌爱意", "气味标记", "你是我的", "呼噜蹭"]
    CHIRP_PHRASES = ["叽!?", "鸟!!", "窗户那边", "颤叫中", "猎手本能"]
    IGNORE_PHRASES = ["看不见你", "傲娇中", "再叫也不理", "面壁思考", "冷漠.jpg"]

    def __init__(self, text: str | None = None, life: int = 140):
        self.text = text or random.choice(self.PHRASES)
        self.life = life

    def tick(self) -> bool:
        self.life -= 1
        return self.life > 0


class BubbleQueue:
    """排队显示多条气泡，避免互相覆盖。"""

    def __init__(self):
        self._q: list[tuple[str, int]] = []
        self.current: Bubble | None = None

    def clear(self):
        self._q.clear()
        self.current = None

    def push(self, text: str, life: int = 160):
        self._q.append((text, life))

    def push_many(self, texts: list[str], life: int = 160):
        for t in texts:
            self.push(t, life)

    def interrupt(self, text: str, life: int = 160):
        self._q.clear()
        self.current = Bubble(text, life)

    def tick(self) -> Bubble | None:
        if self.current:
            if not self.current.tick():
                self.current = None
        if self.current is None and self._q:
            text, life = self._q.pop(0)
            self.current = Bubble(text, life)
        return self.current


class FxParticle:
    """短促视觉反馈：爱心 / 碎屑 / 星星 / 礼花，替代频繁说话。"""

    _CONFETTI_COLORS = (
        (255, 90, 120),
        (255, 200, 60),
        (120, 200, 255),
        (160, 255, 140),
        (255, 150, 255),
        (255, 140, 80),
    )

    def __init__(self, x, y, kind: str = "heart"):
        self.x, self.y = float(x), float(y)
        self.kind = kind
        if kind == "confetti":
            self.vx = random.uniform(-2.8, 2.8)
            self.vy = random.uniform(-4.2, -1.2)
            self.life = random.randint(42, 72)
            self.size = random.randint(2, 5)
            self.color = random.choice(self._CONFETTI_COLORS)
        else:
            self.vx = random.uniform(-1.2, 1.2)
            self.vy = random.uniform(-2.5, -0.8)
            self.life = random.randint(28, 48)
            self.size = random.randint(3, 6)
            self.color = (255, 220, 80)

    def tick(self) -> bool:
        self.x += self.vx
        self.y += self.vy
        self.vy += 0.08 if self.kind != "confetti" else 0.11
        self.life -= 1
        return self.life > 0


def _rebuild_alpha_from_black_bg(surf: pygame.Surface, thresh: int = 22) -> pygame.Surface:
    """用 RGB 重建透明：近黑→透明，深棕躯干保留。忽略原图坏 alpha。"""
    src = surf.convert()  # 强制不透明 RGB
    w, h = src.get_size()
    out = pygame.Surface((w, h), pygame.SRCALPHA)
    for y in range(h):
        for x in range(w):
            r, g, b, _ = src.get_at((x, y))
            mx = max(r, g, b)
            chroma = mx - min(r, g, b)
            # 纯黑底；深棕有色相（如 34,15,21 chroma≈19）要保留
            if mx <= thresh and chroma <= 10:
                out.set_at((x, y), (0, 0, 0, 0))
            elif mx <= thresh + 16 and chroma <= 8:
                out.set_at((x, y), (r, g, b, max(0, min(255, (mx - thresh) * 14))))
            else:
                out.set_at((x, y), (r, g, b, 255))
    return out


def _crop_opaque(surf: pygame.Surface, pad: int = 4) -> pygame.Surface:
    """裁到不透明包围盒。"""
    w, h = surf.get_size()
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            if surf.get_at((x, y)).a > 40:
                xs.append(x)
                ys.append(y)
    if not xs:
        return surf
    x0, y0 = max(0, min(xs) - pad), max(0, min(ys) - pad)
    x1, y1 = min(w, max(xs) + pad + 1), min(h, max(ys) + pad + 1)
    return surf.subsurface((x0, y0, x1 - x0, y1 - y0)).copy()


def _make_cute_body(width: int, shell: tuple[int, int, int], head: tuple[int, int, int]) -> pygame.Surface:
    """无贴图时的简易后备躯干。"""
    h = int(width * 1.4)
    s = pygame.Surface((width, h), pygame.SRCALPHA)
    shell_lo = tuple(max(0, c - 28) for c in shell)
    pygame.draw.ellipse(s, shell_lo, (int(width * 0.2), int(h * 0.18), int(width * 0.6), int(h * 0.72)))
    pygame.draw.ellipse(s, shell, (int(width * 0.22), int(h * 0.2), int(width * 0.56), int(h * 0.68)))
    pygame.draw.ellipse(s, head, (int(width * 0.28), int(h * 0.04), int(width * 0.44), int(h * 0.22)))
    ey = int(h * 0.14)
    er = max(4, width // 14)
    for ex in (int(width * 0.4), int(width * 0.6)):
        pygame.draw.circle(s, (20, 15, 12), (ex, ey), er)
        pygame.draw.circle(s, (255, 255, 255), (ex - 1, ey - 2), max(2, er // 4))
    return s


def resolve_skin_path(skin: str) -> str:
    skin = (skin or "default").strip() or "default"
    if skin != "default":
        custom = os.path.join(_BASE_DIR, "skins", f"{skin}.png")
        if os.path.isfile(custom):
            return custom
    return os.path.join(_BASE_DIR, "cockroach.png")


def _apply_tint(surf: pygame.Surface, rgb_mul: tuple[float, float, float]) -> pygame.Surface:
    out = surf.copy().convert_alpha()
    r = max(0, min(255, int(255 * rgb_mul[0])))
    g = max(0, min(255, int(255 * rgb_mul[1])))
    b = max(0, min(255, int(255 * rgb_mul[2])))
    tint = pygame.Surface(out.get_size(), pygame.SRCALPHA)
    tint.fill((r, g, b, 255))
    out.blit(tint, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return out


def _pil_to_surface(img) -> pygame.Surface:
    """PIL RGBA Image → pygame Surface（带 alpha）。"""
    raw = img.convert("RGBA").tobytes()
    return pygame.image.frombuffer(raw, img.size, "RGBA").copy()


def load_gif_clip(path: str, width: int = SPRITE_W) -> tuple[list[pygame.Surface], list[int]]:
    """加载 GIF 为缩放后的帧列表与每帧时长(ms)。"""
    try:
        from PIL import Image, ImageSequence
    except ImportError as e:
        raise RuntimeError("需要安装 pillow 才能播放小猫 GIF：pip install pillow") from e

    if not pygame.get_init():
        pygame.init()
    if pygame.display.get_surface() is None:
        pygame.display.set_mode((1, 1))

    im = Image.open(path)
    frames: list[pygame.Surface] = []
    durations: list[int] = []
    for frame in ImageSequence.Iterator(im):
        rgba = frame.convert("RGBA")
        tw = width
        th = max(1, int(width * rgba.height / max(1, rgba.width)))
        if rgba.size != (tw, th):
            rgba = rgba.resize((tw, th), Image.Resampling.NEAREST)
        frames.append(_pil_to_surface(rgba))
        durations.append(max(40, int(frame.info.get("duration") or 120)))
    if not frames:
        raise RuntimeError(f"GIF 无帧: {path}")
    return frames, durations


def list_codex_cat_appearances() -> list[dict]:
    """读取 codex_pets/manifest.json 中的猫咪形象列表。"""
    path = os.path.join(CODEX_PETS_DIR, "manifest.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.loads(f.read())
    except Exception:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        slug = str(item.get("slug") or "")
        sheet = os.path.join(CODEX_PETS_DIR, slug, "spritesheet.webp")
        if slug and os.path.isfile(sheet):
            out.append(dict(item))
    return out


def list_cat_appearances() -> list[dict]:
    """可选形象：经典 GIF + Codex 猫咪精灵图。"""
    apps: list[dict] = []
    classic_ok = all(
        os.path.isfile(os.path.join(CAT_IMAGE_DIR, fname)) for fname in CAT_ANIM_FILES.values()
    )
    if classic_ok:
        apps.append(dict(CLASSIC_APPEARANCE))
    apps.extend(list_codex_cat_appearances())
    return apps


def pick_random_appearance() -> dict:
    apps = list_cat_appearances()
    if not apps:
        return dict(CLASSIC_APPEARANCE)
    return random.choice(apps)


def appearance_by_slug(slug: str | None) -> dict | None:
    """按 slug 查找形象；找不到返回 None。"""
    want = (slug or "").strip()
    if not want:
        return None
    for app in list_cat_appearances():
        if str(app.get("slug") or "") == want:
            return dict(app)
    if want == CLASSIC_APPEARANCE["slug"]:
        return dict(CLASSIC_APPEARANCE)
    return None


def resolve_startup_appearance(settings: dict) -> dict:
    """启动时选形象：锁定则用已存 slug，否则随机。"""
    locked = bool(settings.get("appearance_lock", False))
    slug = str(settings.get("appearance_slug") or "").strip()
    if locked and slug:
        found = appearance_by_slug(slug)
        if found:
            return found
        print(f"⚠️ 锁定形象未找到({slug})，改随机")
    return pick_random_appearance()


def appearance_label(app: dict | None) -> str:
    app = app or {}
    return str(app.get("name_zh") or app.get("name") or app.get("slug") or "小猫")


def load_codex_spritesheet(
    path: str,
    width: int = SPRITE_W,
    version: int = 1,
) -> dict[str, tuple[list[pygame.Surface], list[int]]]:
    """把 Codex spritesheet.webp 切成 idle/waving/running/waiting/review。"""
    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("需要安装 pillow 才能加载 Codex 精灵图：pip install pillow") from e

    if not pygame.get_init():
        pygame.init()
    if pygame.display.get_surface() is None:
        pygame.display.set_mode((1, 1))

    atlas = Image.open(path).convert("RGBA")
    expected_h = CODEX_CELL_H * (11 if int(version) >= 2 else 9)
    expected_w = CODEX_CELL_W * CODEX_COLUMNS
    if atlas.size[0] < expected_w or atlas.size[1] < CODEX_CELL_H * 9:
        raise RuntimeError(f"spritesheet 尺寸异常: {atlas.size} ({path})")

    clips: dict[str, tuple[list[pygame.Surface], list[int]]] = {}
    for name, (row, durs) in CODEX_ANIM_ROWS.items():
        frames: list[pygame.Surface] = []
        for col in range(len(durs)):
            box = (
                col * CODEX_CELL_W,
                row * CODEX_CELL_H,
                (col + 1) * CODEX_CELL_W,
                (row + 1) * CODEX_CELL_H,
            )
            cell = atlas.crop(box)
            tw = width
            th = max(1, int(width * cell.height / max(1, cell.width)))
            if cell.size != (tw, th):
                cell = cell.resize((tw, th), Image.Resampling.NEAREST)
            frames.append(_pil_to_surface(cell))
        clips[name] = (frames, [max(40, int(d)) for d in durs])
    return clips


def load_roach_sprite(width: int = SPRITE_W, skin: str = "default") -> pygame.Surface:
    """回退：加载静态贴图（无小猫 GIF 时）。"""
    if not pygame.get_init():
        pygame.init()
    if pygame.display.get_surface() is None:
        pygame.display.set_mode((1, 1))

    path = resolve_skin_path(skin)
    if not os.path.exists(path):
        print(f"⚠️ 未找到贴图 {path}，使用默认躯干")
        return _make_cute_body(width, (150, 90, 45), (100, 60, 35))

    raw = pygame.image.load(path)
    print(f"✅ 已加载贴图: {path} (skin={skin})")
    mid_w = 420
    mid = pygame.transform.smoothscale(
        raw.convert(),
        (mid_w, int(mid_w * raw.get_height() / max(1, raw.get_width()))),
    )
    cut = _rebuild_alpha_from_black_bg(mid)
    cut = _crop_opaque(cut, pad=4)
    rot = float(SPRITE_ROTATE_DEG)
    upright = pygame.transform.rotozoom(cut, rot, 1.0)
    upright = _rebuild_alpha_from_black_bg(upright, thresh=12)
    upright = _crop_opaque(upright, pad=2)
    tint = SKIN_TINTS.get(skin)
    if tint and not os.path.isfile(os.path.join(_BASE_DIR, "skins", f"{skin}.png")):
        upright = _apply_tint(upright, tint)
    tw = width
    th = max(1, int(width * upright.get_height() / max(1, upright.get_width())))
    sprite = pygame.transform.smoothscale(upright, (tw, th))
    print(f"   朝上旋转 {rot:.1f}°，尺寸 {tw}x{th}")
    return sprite


def cat_anim_for_state(state: "State", moving: bool, hide_settled: bool = False) -> str:
    """把脑状态映射到小猫 GIF 动作。移动中一律 Running。"""
    if moving:
        return "running"
    if state in (
        State.WALK, State.RUN, State.ZOOMIE, State.FOLLOW,
        State.SCARED, State.PANIC, State.CALL, State.FORAGE,
        State.LASER, State.DRAGGED, State.CLIMB,
    ):
        return "running"
    if state == State.HIDE:
        return "waiting" if hide_settled else "idle"
    if state in (State.GREET, State.HAPPY, State.DANCE):
        return "waving"
    if state in (State.CURIOUS, State.PEEK, State.BELLY, State.SLEEP, State.HANG):
        return "waiting"
    if state in (State.POSE, State.SPIN):
        return "review"
    return "idle"


# ── 小猫 GIF 渲染（Idle / Waving / Running / Waiting / Review）──

class RoachRenderer:
    """主形象：经典 GIF 或 Codex 猫咪精灵图；无资源时回退静态贴图。"""

    def __init__(self, skin: str = "default", appearance: dict | None = None):
        self.skin = skin
        self.appearance = dict(appearance or CLASSIC_APPEARANCE)
        self.clips: dict[str, tuple[list[pygame.Surface], list[int]]] = {}
        self._raw_clips: dict[str, tuple[list[pygame.Surface], list[int]]] = {}
        self.use_cat = False
        self.anim = "idle"
        self._fi = 0
        self._accum = 0
        self._last_ms = 0
        self.anim_override: tuple[str, float] | None = None
        self.facing = 1
        self.heading = 0.0
        self.phase = 0.0
        self.bob = 0.0
        self.scale = 1.0
        self.target_scale = 1.0
        self.tilt = 0.0
        self.spin = 0.0
        self.spin_vel = 0.0
        self.alpha = 255
        self.target_alpha = 255
        self.happy = False
        self.belly = False
        self.hanging = False  # 倒挂：竖直翻转
        self.gait = 0.0
        self.gait_amp = 0.0
        self.particles: list[FxParticle] = []
        self.fade = True  # False=瞬间改透明度（主宠关闭淡化）
        self.base = self._load_visuals(skin)
        self.sw, self.sh = self.base.get_size()

    def _load_visuals(self, skin: str) -> pygame.Surface:
        loaded = self._load_appearance_clips()
        if loaded and len(loaded) >= 3 and "idle" in loaded:
            self.use_cat = True
            self._raw_clips = loaded
            self.clips = self._tint_clips(loaded, skin)
            label = self.appearance.get("name_zh") or self.appearance.get("name") or self.appearance.get("slug")
            print(f"✅ 形象已加载: {label} → {', '.join(sorted(self.clips))} ({SPRITE_W}px)")
            return self.clips["idle"][0][0]
        self.use_cat = False
        print("⚠️ 小猫动画不完整，回退静态贴图")
        return load_roach_sprite(SPRITE_W, skin=skin)

    def _load_appearance_clips(self) -> dict[str, tuple[list[pygame.Surface], list[int]]]:
        slug = str(self.appearance.get("slug") or CLASSIC_APPEARANCE["slug"])
        if slug != CLASSIC_APPEARANCE["slug"]:
            sheet = os.path.join(CODEX_PETS_DIR, slug, "spritesheet.webp")
            if os.path.isfile(sheet):
                try:
                    ver = int(self.appearance.get("spriteVersionNumber") or 1)
                    return load_codex_spritesheet(sheet, SPRITE_W, version=ver)
                except Exception as e:
                    print(f"⚠️ Codex 精灵图加载失败 ({slug}): {e}，尝试经典 GIF")
        loaded: dict[str, tuple[list[pygame.Surface], list[int]]] = {}
        for key, fname in CAT_ANIM_FILES.items():
            path = os.path.join(CAT_IMAGE_DIR, fname)
            if not os.path.isfile(path):
                continue
            try:
                frames, durs = load_gif_clip(path, SPRITE_W)
                loaded[key] = (frames, durs)
            except Exception as e:
                print(f"⚠️ 加载 {fname} 失败: {e}")
        return loaded

    def _tint_clips(
        self,
        clips: dict[str, tuple[list[pygame.Surface], list[int]]],
        skin: str,
    ) -> dict[str, tuple[list[pygame.Surface], list[int]]]:
        tint = SKIN_TINTS.get(skin)
        if not tint:
            return {k: (list(v[0]), list(v[1])) for k, v in clips.items()}
        out = {}
        for k, (frames, durs) in clips.items():
            out[k] = ([_apply_tint(f, tint) for f in frames], list(durs))
        return out

    def apply_skin(self, skin: str):
        self.skin = skin
        if self.use_cat and self._raw_clips:
            self.clips = self._tint_clips(self._raw_clips, skin)
            self.base = self.clips.get(self.anim, self.clips["idle"])[0][0]
        else:
            self.base = load_roach_sprite(SPRITE_W, skin=skin)
        self.sw, self.sh = self.base.get_size()

    def apply_appearance(self, appearance: dict):
        """运行时切换猫形象（保留当前 tint 皮肤）。"""
        self.appearance = dict(appearance or CLASSIC_APPEARANCE)
        self.anim_override = None
        self._fi = 0
        self._accum = 0
        self.base = self._load_visuals(self.skin)
        self.sw, self.sh = self.base.get_size()
        if self.use_cat and "idle" in self.clips:
            self.play("idle")

    def force_anim(self, name: str, duration_sec: float = 4.0):
        """临时强制某动作（故事/对喷等）。"""
        if name not in CAT_ANIM_FILES and name not in self.clips:
            return
        self.anim_override = (name, time.time() + max(0.5, duration_sec))
        self.play(name)

    def play(self, name: str):
        if name not in self.clips:
            name = "idle" if "idle" in self.clips else (next(iter(self.clips), "idle"))
        if name != self.anim:
            self.anim = name
            self._fi = 0
            self._accum = 0
            if name in self.clips and self.clips[name][0]:
                self.base = self.clips[name][0][0]
                self.sw, self.sh = self.base.get_size()

    def set_facing(self, vx: float, vy: float = 0.0):
        # 以水平速度决定左右朝向；Running 贴图默认朝右
        if abs(vx) > 0.08:
            self.facing = 1 if vx > 0 else -1
            self.heading = math.degrees(math.atan2(vy, vx))
        elif abs(vy) > 0.08:
            # 纯上下移动时保持当前左右朝向，只更新 heading
            self.heading = math.degrees(math.atan2(vy, vx if abs(vx) > 1e-6 else 0.0))

    def burst(self, cx: float, cy: float, kind: str, n: int = 5):
        for _ in range(n):
            self.particles.append(FxParticle(cx, cy, kind))

    def _advance_frames(self):
        if not self.use_cat or self.anim not in self.clips:
            return
        frames, durs = self.clips[self.anim]
        if not frames:
            return
        now = pygame.time.get_ticks()
        if self._last_ms <= 0:
            self._last_ms = now
            return
        self._accum += max(0, now - self._last_ms)
        self._last_ms = now
        # 防卡顿一次跳太多帧
        guard = 0
        while self._accum >= durs[self._fi] and guard < 12:
            self._accum -= durs[self._fi]
            self._fi = (self._fi + 1) % len(frames)
            guard += 1
        self.base = frames[self._fi]
        self.sw, self.sh = self.base.get_size()

    def tick(self, moving: bool, speed: float, sleeping: bool, happy: bool,
             dancing: bool = False, spinning: bool = False, anim: str | None = None):
        self.happy = happy
        # 位移中一律 Running，避免 waiting/waving 被强制时像倒着滑
        if moving and self.use_cat:
            self.play("running")
        else:
            if self.anim_override:
                name, until = self.anim_override
                if time.time() < until:
                    anim = name
                else:
                    self.anim_override = None
            if anim:
                self.play(anim)

        target_amp = 0.0
        if sleeping:
            self.phase += 0.04
            self.bob = math.sin(self.phase) * 0.6
            self.tilt = 0.0
            self.gait += 0.02
        elif dancing:
            self.phase += 0.28
            self.bob = math.sin(self.phase * 2.5) * 3
            self.tilt = math.sin(self.phase * 2) * 4
            self.spin += math.sin(self.phase) * 2
            self.gait += 0.4
            target_amp = 0.6
        elif spinning:
            self.phase += 0.2
            self.bob = 1
            self.spin += self.spin_vel or 14
            self.spin_vel *= 0.985
            self.gait += 0.45
            target_amp = 0.5
        elif moving:
            self.phase += 0.22 + speed * 0.1
            self.gait += 0.2 + speed * 0.12
            target_amp = min(1.0, 0.4 + speed * 0.15)
            # 小猫 GIF 自带动画，颠簸减弱
            self.bob = math.sin(self.gait * 2) * BOB_AMPLITUDE * 0.35 * target_amp
            self.tilt = 0.0
        else:
            self.phase += 0.05
            self.gait += 0.05
            target_amp = 0.05
            self.bob = math.sin(self.phase) * 0.3
            self.tilt = 0.0
            if abs(self.spin_vel) > 0.3:
                self.spin += self.spin_vel
                self.spin_vel *= 0.92
            elif abs(self.spin) > 1 and not self.belly:
                self.spin *= 0.85

        self.gait_amp += (target_amp - self.gait_amp) * 0.2
        self.scale += (self.target_scale - self.scale) * 0.12
        # 主宠不淡化；同伴可通过 fade=False 瞬间显隐
        if getattr(self, "fade", True):
            self.alpha += (self.target_alpha - self.alpha) * 0.15
        else:
            self.alpha = float(self.target_alpha)
        self.particles = [p for p in self.particles if p.tick()]
        self._advance_frames()

    def _compose_local(self, s: float, moving: bool) -> pygame.Surface:
        bw = max(1, int(self.sw * s))
        bh = max(1, int(self.sh * s))
        img = self.base
        if self.belly or self.hanging:
            img = pygame.transform.flip(img, False, True)
        # Running 朝右；朝左时水平翻转
        if self.use_cat and self.anim in CAT_SIDE_ANIMS and self.facing < 0:
            img = pygame.transform.flip(img, True, False)
        body = pygame.transform.scale(img, (bw, bh))
        if self.happy:
            tinted = body.copy()
            glow = pygame.Surface(tinted.get_size(), pygame.SRCALPHA)
            glow.fill((255, 200, 120, 28))
            tinted.blit(glow, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
            body = tinted

        pad = 6
        local = pygame.Surface((bw + pad * 2, bh + pad * 2), pygame.SRCALPHA)
        local.blit(body, (pad, pad))
        if abs(self.tilt) > 0.5:
            local = pygame.transform.rotate(local, self.tilt * 0.25)
        return local

    def draw(
        self,
        surf: pygame.Surface,
        ox: int,
        oy: int,
        moving: bool,
        sleeping: bool,
        min_alpha: int = 40,
    ):
        s = self.scale
        cx = ox + PET_W / 2
        # 倒挂时略上移，看起来挂在屏幕边缘
        hang_bias = -10 if self.hanging else 0
        cy = oy + PET_H / 2 + self.bob + hang_bias

        if self.alpha >= 1:
            local = self._compose_local(s, moving)
            if self.use_cat:
                # 小猫不绕头尾旋转；仅 SPIN 时轻微自转
                if abs(self.spin) > 0.5:
                    local = pygame.transform.rotate(local, self.spin)
            else:
                turn = -(self.heading - SPRITE_UPRIGHT_HEADING) + self.spin
                if abs(turn) > 0.05:
                    local = pygame.transform.rotate(local, turn)

            if self.alpha < 250:
                local = local.copy()
                local.set_alpha(max(min_alpha, int(self.alpha)))

            rect = local.get_rect(center=(int(cx), int(cy)))
            sh_a = max(0, min(40, int(40 * self.alpha / 255)))
            if sh_a > 0:
                sh_w = int(min(rect.width, rect.height) * 0.55)
                shad = pygame.Surface((max(8, sh_w), 8), pygame.SRCALPHA)
                pygame.draw.ellipse(shad, (0, 0, 0, sh_a), shad.get_rect())
                surf.blit(shad, (rect.centerx - shad.get_width() // 2, rect.bottom - 4))
            surf.blit(local, rect)

            if sleeping:
                veil = pygame.Surface((rect.width, max(4, rect.height // 5)), pygame.SRCALPHA)
                veil.fill((80, 80, 120, 55))
                surf.blit(veil, (rect.left, rect.top + 4))

        for p in self.particles:
            a = max(0, min(255, p.life * 6))
            if p.kind == "heart":
                col = (255, 90, 120, a)
            elif p.kind == "crumb":
                col = (180, 120, 60, a)
            elif p.kind == "confetti":
                r, g, b = getattr(p, "color", (255, 220, 80))
                col = (r, g, b, a)
                pygame.draw.rect(
                    surf,
                    col,
                    (int(p.x), int(p.y), max(2, p.size), max(2, p.size - 1)),
                )
                continue
            elif p.kind == "star":
                col = (255, 220, 80, a)
            else:
                col = (200, 200, 200, a)
            pygame.draw.circle(surf, col, (int(p.x), int(p.y)), p.size)


# ── AI ────────────────────────────────────────────────────

class PetBrain:
    def __init__(self, sw: int, sh: int, pw: int, ph: int):
        self.sw, self.sh, self.pw, self.ph = sw, sh, pw, ph
        self.state = State.HIDE
        self.state_timer = 99999
        self.vx = self.vy = 0.0
        self.target_x: float | None = None
        self.target_y: float | None = None
        self.energy = 80
        self.hunger = 40
        self.affection = 0
        self.floor_y = sh - ph
        self.follow = False
        self.zoomie_left = 0
        self.pet_streak = 0
        self._pick_hide_spot()

    def refresh_screen(self, sw: int, sh: int):
        self.sw, self.sh = sw, sh
        self.floor_y = sh - self.ph

    def _rand_coord(self, margin: int, span: int) -> int:
        lo, hi = margin, span - margin
        if lo >= hi:
            return max(0, (span - margin) // 2)
        return random.randint(lo, hi)

    def _pick_target(self):
        m = 30
        if random.random() < 0.65:
            self.target_x = self._rand_coord(m, self.sw - self.pw)
            self.target_y = self.floor_y
        else:
            self.target_x = self._rand_coord(m, self.sw - self.pw)
            self.target_y = self._rand_coord(m, self.sh - self.ph)

    def _pick_hide_spot(self, avoid_current: bool = False):
        """选屏幕角落/窗台作为趴窝点。"""
        spots = [
            (16, 28),
            (self.sw - self.pw - 16, 28),
            (16, self.sh - self.ph - 24),
            (self.sw - self.pw - 16, self.sh - self.ph - 24),
            (16, self.sh // 2 - self.ph // 2),
            (self.sw - self.pw - 16, self.sh // 2 - self.ph // 2),
            (self._rand_coord(20, self.sw - self.pw), self.sh - self.ph - 18),
        ]
        if avoid_current and self.target_x is not None:
            spots = [
                s for s in spots
                if math.hypot(s[0] - self.target_x, s[1] - (self.target_y or 0)) > 120
            ] or spots
        self.target_x, self.target_y = random.choice(spots)

    def _set(self, state: State, dur: int):
        self.state = state
        self.state_timer = dur

    def go_hide(self, scramble: bool = False):
        """回窝趴着：平时默认态。scramble=True 时换角落跑酷。"""
        self.follow = False
        self._pick_hide_spot(avoid_current=scramble)
        self._set(State.HIDE, 99999)
        if scramble:
            self.vx = random.choice([-1, 1]) * RUN_SPEED * 0.9
            self.vy = random.uniform(-1.2, 1.2)

    def _rest(self):
        """互动结束后回窝（跟随模式除外）。"""
        if self.follow:
            self._set(State.FOLLOW, 9999)
        else:
            self.go_hide()

    def react_click(self):
        self.affection += 1
        self.pet_streak += 1
        self._set(State.HAPPY, 90)

    def react_dblclick(self):
        self.pet_streak = 0
        self._set(State.RUN, 120)
        self._pick_target()

    def react_feed(self, feast: bool = False):
        cut = 55 if feast else 35
        self.hunger = max(0, self.hunger - cut)
        self.energy = min(100, self.energy + (25 if feast else 15))
        self.affection += 4 if feast else 2
        self.pet_streak = 0
        self._set(State.HAPPY, 140 if feast else 100)

    def react_poke(self):
        self.pet_streak = 0
        self._set(State.SCARED, 50)
        self.vx = random.choice([-1, 1]) * RUN_SPEED * 1.2
        self.vy = random.uniform(-1.5, 1.5)

    def react_dance(self):
        self.pet_streak = 0
        self._set(State.DANCE, 180)
        self.vx = self.vy = 0

    def react_spin(self, strength: float = 18):
        self.pet_streak = 0
        self._set(State.SPIN, 70)
        self.vx = self.vy = 0
        return strength

    def react_belly(self):
        self.pet_streak = 0
        self._set(State.BELLY, 150)
        self.vx = self.vy = 0

    def react_hide(self):
        self.pet_streak = 0
        self.go_hide(scramble=True)

    def react_laser(self):
        self.pet_streak = 0
        self._set(State.LASER, 200)

    def react_peek(self):
        self.pet_streak = 0
        self._set(State.PEEK, random.randint(90, 140))
        self.vx = self.vy = 0

    def react_forage(self):
        self.pet_streak = 0
        self._set(State.FORAGE, random.randint(220, 320))
        self._pick_target()

    def react_zoomie(self):
        self.pet_streak = 0
        self.zoomie_left = random.randint(3, 5)
        self._set(State.ZOOMIE, 50)
        self._pick_target()

    def react_panic(self):
        self.pet_streak = 0
        self._set(State.PANIC, random.randint(140, 200))
        self.vx = random.choice([-1, 1]) * RUN_SPEED * 1.4
        self.vy = random.uniform(-2.0, 2.0)

    def react_pose(self):
        self.pet_streak = 0
        self._set(State.POSE, 150)
        self.vx = self.vy = 0

    def react_call(self):
        self.pet_streak = 0
        self._set(State.CALL, random.randint(160, 240))

    def react_stroll(self):
        """自主散步。"""
        self.pet_streak = 0
        self.follow = False
        self._pick_target()
        self._set(State.WALK, random.randint(160, 280))

    def react_daydream(self):
        """原地发呆。"""
        self.pet_streak = 0
        self.vx = self.vy = 0
        self.target_x = self.target_y = None
        self._set(State.POSE, random.randint(160, 260))

    def react_nap(self):
        """自主打瞌睡（与右键睡共用 SLEEP）。"""
        self.pet_streak = 0
        self.vx = self.vy = 0
        self.target_x = self.target_y = None
        self._set(State.SLEEP, random.randint(360, 720))

    def _pick_top_edge(self):
        """攀爬/倒挂目标：屏幕上沿。"""
        self.target_x = float(self._rand_coord(20, self.sw - self.pw))
        self.target_y = 8.0

    def _pick_hang_spot(self):
        """倒挂点：顶边或左右上角。"""
        choice = random.random()
        if choice < 0.4:
            self.target_x = 12.0
            self.target_y = 8.0
        elif choice < 0.8:
            self.target_x = float(max(12, self.sw - self.pw - 12))
            self.target_y = 8.0
        else:
            self._pick_top_edge()

    def react_climb(self):
        """向屏幕上沿攀爬。"""
        self.pet_streak = 0
        self.follow = False
        self._pick_top_edge()
        self._set(State.CLIMB, random.randint(220, 360))

    def react_hang(self):
        """爬到边缘后倒挂一阵。"""
        self.pet_streak = 0
        self.follow = False
        self._pick_hang_spot()
        self._set(State.HANG, random.randint(260, 420))

    def react_follow_toggle(self) -> bool:
        self.follow = not self.follow
        self.pet_streak = 0
        if self.follow:
            self._set(State.FOLLOW, 9999)
        else:
            self.go_hide()
        return self.follow

    def react_nudge(self, dx: float, dy: float):
        if self.state == State.SLEEP:
            self._set(State.WALK, 50)
        self.vx += dx
        self.vy += dy
        if self.state not in (State.FOLLOW, State.LASER, State.DANCE, State.SPIN, State.POSE, State.CALL):
            self._set(State.WALK, 50)

    def react_jump(self):
        self.vy -= 6
        self._set(State.HAPPY, 40)

    def react_drop(self, dvx: float, dvy: float):
        if math.hypot(dvx, dvy) > 8:
            self._set(State.SCARED, 60)
            self.vx, self.vy = dvx * 0.3, dvy * 0.3
        else:
            self.go_hide()

    def update(self, mx, my, px, py, dragging) -> Bubble | None:
        if dragging:
            self.state = State.DRAGGED
            self.vx = self.vy = 0
            return None

        self.state_timer -= 1
        self.hunger = min(100, self.hunger + 0.008)
        bubble = None

        if self.state == State.HAPPY:
            self.vx *= 0.9
            self.vy *= 0.9
            if self.state_timer <= 0:
                self._rest()

        elif self.state == State.GREET:
            self.vx *= 0.9
            self.vy *= 0.9
            if self.state_timer <= 0:
                self._rest()

        elif self.state == State.DANCE:
            self.vx = math.sin(self.state_timer * 0.2) * 0.8
            self.vy = 0
            if self.state_timer <= 0:
                self._rest()

        elif self.state == State.SPIN:
            self.vx = self.vy = 0
            if self.state_timer <= 0:
                self._rest()

        elif self.state == State.BELLY:
            self.vx = self.vy = 0
            if self.state_timer <= 0:
                self._set(State.SCARED, 40)
                self.vx = random.choice([-1, 1]) * RUN_SPEED

        elif self.state == State.HIDE:
            if self.target_x is not None and self.target_y is not None:
                dx = self.target_x - px
                dy = self.target_y - py
                d = math.hypot(dx, dy)
                if d < 8:
                    self.vx = self.vy = 0
                    # 趴好了就在窝里静止，不做微位移（避免 Idle/Waiting 时自己平移）
                    if self.state_timer < 120:
                        self.state_timer = random.randint(800, 2400)
                else:
                    sp = WALK_SPEED * 1.55
                    self.vx, self.vy = dx / d * sp, dy / d * sp
            # 不自动乱跑；只有互动才会离开回窝态

        elif self.state == State.PEEK:
            # 观鸟只播 Waiting，不产生位移（避免被当成 Running）
            self.vx = self.vy = 0
            if self.state_timer <= 0:
                self._rest()

        elif self.state == State.FORAGE:
            sp = WALK_SPEED * 0.75
            if self.target_x is not None and self.target_y is not None:
                dx = self.target_x - px
                dy = self.target_y - py
                d = math.hypot(dx, dy)
                if d < 10:
                    self._pick_target()
                else:
                    self.vx, self.vy = dx / d * sp, dy / d * sp
            if self.state_timer <= 0:
                # 找到一点吃的
                self.hunger = max(0, self.hunger - 20)
                self.energy = min(100, self.energy + 8)
                self._set(State.HAPPY, 80)
                bubble = Bubble(random.choice(["找到了!", "嚼嚼~", "有收获!"]))

        elif self.state == State.ZOOMIE:
            sp = RUN_SPEED * 1.35
            if self.target_x is not None and self.target_y is not None:
                dx = self.target_x - px
                dy = self.target_y - py
                d = math.hypot(dx, dy)
                if d < 12 or self.state_timer <= 0:
                    self.zoomie_left -= 1
                    if self.zoomie_left <= 0:
                        self._rest()
                    else:
                        self._pick_target()
                        self.state_timer = random.randint(40, 70)
                else:
                    self.vx, self.vy = dx / d * sp, dy / d * sp
            else:
                self._pick_target()

        elif self.state == State.PANIC:
            if self.state_timer % 18 == 0:
                self.vx = random.choice([-1, 1]) * RUN_SPEED * random.uniform(1.1, 1.6)
                self.vy = random.uniform(-2.2, 2.2)
            if self.state_timer <= 0:
                self.go_hide(scramble=True)

        elif self.state == State.POSE:
            self.vx = self.vy = 0
            if self.state_timer <= 0:
                self._rest()

        elif self.state == State.CALL:
            dx = mx - (px + self.pw / 2)
            dy = my - (py + self.ph / 2)
            d = math.hypot(dx, dy) or 1
            if d > 36:
                sp = RUN_SPEED * 1.05
                self.vx, self.vy = dx / d * sp, dy / d * sp
            else:
                self.vx *= 0.6
                self.vy *= 0.6
                if abs(self.vx) + abs(self.vy) < 0.2:
                    self._set(State.HAPPY, 70)
            if self.state_timer <= 0:
                self._rest()

        elif self.state == State.FOLLOW:
            dx = mx - (px + self.pw / 2)
            dy = my - (py + self.ph / 2)
            d = math.hypot(dx, dy)
            if d > 40:
                sp = WALK_SPEED * 1.1
                self.vx, self.vy = dx / d * sp, dy / d * sp
            else:
                self.vx *= 0.7
                self.vy *= 0.7

        elif self.state == State.LASER:
            dx = mx - (px + self.pw / 2)
            dy = my - (py + self.ph / 2)
            d = math.hypot(dx, dy) or 1
            sp = RUN_SPEED * 1.35
            self.vx, self.vy = dx / d * sp, dy / d * sp
            if self.state_timer <= 0:
                self._rest()

        elif self.state == State.SCARED:
            self.vx *= 0.92
            self.vy *= 0.92
            if self.state_timer <= 0:
                self.go_hide(scramble=True)

        elif self.state == State.SLEEP:
            self.vx = self.vy = 0
            self.energy = min(100, self.energy + 0.05)
            if self.state_timer <= 0 or self.energy >= 90:
                self._rest()

        elif self.state == State.CURIOUS:
            dx = mx - (px + self.pw / 2)
            dy = my - (py + self.ph / 2)
            d = math.hypot(dx, dy)
            if d > 220 or self.state_timer <= 0:
                self._rest()
            elif d > 30:
                sp = WALK_SPEED * 0.85
                self.vx, self.vy = dx / d * sp, dy / d * sp

        elif self.state in (State.WALK, State.RUN):
            sp = RUN_SPEED if self.state == State.RUN else WALK_SPEED
            if self.target_x is not None and self.target_y is not None:
                dx = self.target_x - px
                dy = self.target_y - py
                d = math.hypot(dx, dy)
                if d < 8 or self.state_timer <= 0:
                    self.vx = self.vy = 0
                    self.target_x = self.target_y = None
                    self._rest()
                else:
                    self.vx, self.vy = dx / d * sp, dy / d * sp
            elif self.state_timer <= 0:
                self.vx = self.vy = 0
                self._rest()
            self.energy = max(0, self.energy - 0.02)

        elif self.state == State.CLIMB:
            sp = WALK_SPEED * 0.95
            if self.target_x is not None and self.target_y is not None:
                dx = self.target_x - px
                dy = self.target_y - py
                d = math.hypot(dx, dy)
                if d < 10:
                    self.vx = self.vy = 0
                    # 到顶后趴一会儿再回窝
                    if self.state_timer > 90:
                        self.state_timer = random.randint(50, 90)
                    elif self.state_timer <= 0:
                        self._rest()
                else:
                    self.vx, self.vy = dx / d * sp, dy / d * sp
            elif self.state_timer <= 0:
                self._rest()
            self.energy = max(0, self.energy - 0.025)

        elif self.state == State.HANG:
            sp = WALK_SPEED * 1.05
            if self.target_x is not None and self.target_y is not None:
                dx = self.target_x - px
                dy = self.target_y - py
                d = math.hypot(dx, dy)
                if d < 10:
                    self.vx = self.vy = 0
                    if self.state_timer <= 0:
                        self._rest()
                else:
                    self.vx, self.vy = dx / d * sp, dy / d * sp
            elif self.state_timer <= 0:
                self._rest()

        elif self.state == State.IDLE:
            self.vx *= 0.85
            self.vy *= 0.85
            if self.state_timer <= 0:
                # 默认回窝趴着，极少主动乱逛
                if random.random() < 0.04:
                    self._set(State.WALK, random.randint(60, 120))
                    self._pick_target()
                else:
                    self.go_hide()

        elif self.state == State.DRAGGED:
            self.vx = self.vy = 0

        return bubble

    def clamp(self, x, y):
        x = max(0, min(x, self.sw - self.pw))
        y = max(0, min(y, self.sh - self.ph))
        hit = False
        if x <= 0 or x >= self.sw - self.pw:
            self.vx *= -1
            hit = True
        if y <= 0 or y >= self.sh - self.ph:
            self.vy *= -1
            hit = True
        if hit:
            # 目标成对失效，避免只清一个导致 None 减法崩溃
            self.target_x = self.target_y = None
            if self.state == State.HIDE:
                self._pick_hide_spot(avoid_current=True)
        return x, y


# ── AppKit 透明视图（仅 macOS）──────────────────────────────

PetView = None
if OBJC_OK:

    class PetView(NSView):
        """自定义 NSView：左上角坐标系 + respectFlipped，与 pygame 一致。"""

        def initWithPet_(self, pet):
            self = objc.super(PetView, self).initWithFrame_(NSMakeRect(0, 0, WIN_W, WIN_H))
            if self is None:
                return None
            self.pet = pet
            return self

        def isOpaque(self):
            return False

        def isFlipped(self):
            return True

        def drawRect_(self, rect):
            img = self.pet._nsimage
            if img is None:
                return
            cw, ch = self.pet.canvas.get_size()
            img.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                NSMakeRect(0, 0, cw, ch),
                NSMakeRect(0, 0, 0, 0),
                NSCompositeSourceOver,
                1.0,
                True,
                None,
            )

        def acceptsFirstMouse_(self, event):
            return True

        def mouseDown_(self, event):
            self.pet.on_mouse_down(event)

        def mouseUp_(self, event):
            self.pet.on_mouse_up(event)

        def mouseDragged_(self, event):
            self.pet.on_mouse_drag(event)

        def rightMouseDown_(self, event):
            self.pet.on_right_click(event)

        def otherMouseDown_(self, event):
            self.pet.on_middle_click(event)

        def scrollWheel_(self, event):
            self.pet.on_scroll(event)

        def rightMouseDragged_(self, event):
            self.pet.on_right_drag(event)

        def rightMouseUp_(self, event):
            self.pet.on_right_up(event)



# ── 主类 ──────────────────────────────────────────────────

class RoachPet:
    def __init__(self):
        pygame.init()
        pygame.font.init()

        self.app_root = app_dir()
        self.settings = load_settings(self.app_root)
        self.progress = load_progress(self.app_root)
        packs_dir = os.path.join(_BASE_DIR, "packs")
        if not os.path.isdir(packs_dir):
            packs_dir = os.path.join(self.app_root, "packs")
        PACKS.load(packs_dir, self.settings.get("enabled_packs"))

        sw, sh = get_desktop_size()
        self.brain = PetBrain(sw, sh, WIN_W, WIN_H)
        self.brain.affection = int(self.progress.get("affection") or 0)
        if self.settings.get("follow_default"):
            self.brain.follow = True
        skin = str(self.settings.get("skin") or "default")
        unlocked = set(self.progress.get("unlocked_skins") or ["default"])
        if skin not in unlocked and skin not in ("default", "gold", "ghost"):
            skin = "default"
        self.appearance = resolve_startup_appearance(self.settings)
        # 未锁定时也记下本次 slug，方便用户一键锁定当前
        slug = str(self.appearance.get("slug") or "")
        if slug and not self.settings.get("appearance_lock"):
            self.settings["appearance_slug"] = slug
        self.roach = RoachRenderer(skin=skin, appearance=self.appearance)
        self.roach.fade = False  # 小猫始终清晰，不做半透明淡化
        self.roach.target_alpha = 255
        self.roach.alpha = 255
        self.canvas = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        self.font = load_cjk_font(14)
        self.font_sm = load_cjk_font(12)

        self.x = float(self.brain.target_x or (sw - WIN_W) // 2)
        self.y = float(self.brain.target_y or (sh - WIN_H - 50))
        self.prev_x, self.prev_y = self.x, self.y
        self.dragging = False
        self.drag_start = (0.0, 0.0)
        self.click_time = 0
        self.click_count = 0
        self.press_time = 0.0
        self.press_pos = (0.0, 0.0)
        self.moved_while_press = False
        self.shake_accum = 0.0
        self.last_drag_dir = 0.0
        self.right_dragging = False
        self.right_drag_start = (0.0, 0.0)
        self.bubbles = BubbleQueue()
        self.bubble: Bubble | None = None
        self._nsimage = None
        self._weather: str | None = None
        self._weather_ready = False
        self._pending_say: list[tuple[str, int]] = []
        self._pending_ai: list[tuple] = []
        self._ai_busy = False
        self._cmd_q: queue.Queue[str] = queue.Queue()
        self._last_hour_chime = -1
        self._worker_fired: set[str] = set()
        iv = float(self.settings.get("interaction_interval_sec") or 300)
        self._next_proactive = time.time() + random.uniform(iv * 1.2, iv * 2.0)
        self._next_worker_idle = time.time() + 300
        lo_sc = float(self.settings.get("idle_showcase_min") or iv * 0.9)
        hi_sc = float(self.settings.get("idle_showcase_max") or iv * 1.1)
        self._next_showcase = time.time() + random.uniform(min(lo_sc, hi_sc), max(lo_sc, hi_sc))
        rest_iv = float(self.settings.get("rest_reminder_interval_sec") or 3600)
        self._next_rest = time.time() + max(60.0, rest_iv)
        self._rest_active = False
        self._guide_step = -1
        self._guide_step_at = 0.0
        self._guide_armed_at = 0.0
        self._guide_gap_until = 0.0
        self._guide_after_gap: str | None = None  # "next" | "finish"
        self._guide_choices: list[dict] = []
        self._guide_btn_rects: list[tuple[pygame.Rect, str]] = []
        self._guide_lines: list[str] = []
        self._guide_idle_skips = 0  # 连续超时跳过次数，满 3 直接收尾
        # 护眼/喝水/伸展：错开首次触发，避免扎堆
        now0 = time.time()
        self._next_care = {
            "eye": now0 + max(90.0, float(self.settings.get("care_eye_sec") or 1200) * 0.35),
            "water": now0 + max(120.0, float(self.settings.get("care_water_sec") or 1800) * 0.4),
            "stretch": now0 + max(150.0, float(self.settings.get("care_stretch_sec") or 2700) * 0.45),
        }
        # 自主行为：无人操作一段时间后自己散步/发呆/睡觉/攀爬/倒挂
        self._last_user_act = time.time()
        lo_au = float(self.settings.get("autonomy_min") or iv * 0.9)
        hi_au = float(self.settings.get("autonomy_max") or iv * 1.1)
        self._next_autonomy = time.time() + random.uniform(min(lo_au, hi_au), max(lo_au, hi_au))
        # 会议/投屏/截图静默：共享与投屏收起；截图热键即时躲闪
        self._stealth = False
        self._stealth_reason = ""
        self._meeting_level = ""
        self._casting = False
        self._shot_tool = False
        self._shot_hide_until = 0.0
        self._stealth_clear_at = 0.0
        self._next_meeting_check = 0.0
        # 会议结束彩蛋：开会/共享持续一段时间后落下
        self._meeting_busy = False
        self._meeting_since = 0.0
        self._meeting_end_egg_at = 0.0
        self._meeting_end_egg_pending = False
        # 专注番茄钟：手动倒计时，期间安静蹲守，结束跑来催休息
        self._focus_until = 0.0
        self._focus_end_pending = False
        # 安静门控：番茄钟/会议/投屏/系统专注/stealth → 压制主动打扰，解除后重新计时
        self._quiet_gate_on = False
        lo = float(self.settings.get("sys_check_interval_min") or 50)
        hi = float(self.settings.get("sys_check_interval_max") or 90)
        self._next_sys_check = time.time() + random.uniform(lo, hi)
        self._last_sys_alert = ""
        self._near_mouse = False
        self._hide_scramble_at = 0.0
        # 鼠标久闲寻访：默认 30 分钟不动就跑去找指针
        self._last_mouse_pos: tuple[float, float] | None = None
        self._mouse_still_since = time.time()
        self._next_mouse_seek = 0.0
        self._seek_act: str | None = None
        self._fx_anchor = (PAD_X + PET_W / 2, ROACH_Y + PET_H / 2)
        self._running = True
        self._started_at = time.time()
        self.screen = None
        self.hwnd = None
        self.window = None
        self.view = None
        self._win_click_through = False
        self._chrome = None
        self.buddy = None
        self._next_banter = time.time() + random.uniform(90, 160)
        # 月结应援：负载触发的短时保持 / 用户误判反馈后的当日抑制
        self._support_hold_until = 0.0
        self._support_hold_reason = ""
        self._support_dismiss_until = float(self.progress.get("support_dismiss_until") or 0)
        # 应援收工仪式：窗口结束后，等第一次回家/睡觉
        self._support_was_active = False
        self._support_worthy = False
        self._support_skip_close = False
        self._support_watch_ready = False
        self._next_support_watch = 0.0
        # Windows：快捷键不依赖窗口焦点（鼠标在猫上 / 刚点过即可）
        self._pointer_over_pet = False
        self._win_keys_until = 0.0
        self._mac_last_origin: tuple[int, int] | None = None

        if IS_MAC:
            if not OBJC_OK:
                raise RuntimeError("macOS 需要: pip3 install pygame pyobjc-framework-Cocoa")
            self._setup_mac_window()
        elif IS_WIN:
            self._setup_win_window()
        else:
            raise RuntimeError(f"暂不支持平台: {sys.platform}")

        self._init_buddy()
        self._chrome = DesktopChrome(
            self._cmd_q.put,
            self.settings,
            win_keys_active=self._win_keys_active if IS_WIN else None,
            progress=self.progress,
        )
        self._chrome.start()
        self._start_weather_fetch()
        self._queue_startup_greetings()
        # 首帧绘制留给 run()，避免 init 阶段 finishLaunching/菜单栏 干扰窗口

    def _onboarding_done(self) -> bool:
        return bool(
            self.progress.get("onboarding_completed")
            or self.progress.get("onboarding_done")
        )

    def _guide_active(self) -> bool:
        """引导进行中（含开场等待、步骤、呼吸间隔）。"""
        return self._guide_step >= -2 and self._guide_step != -1

    def _guide_steps(self) -> list[dict]:
        return [
            {
                "id": "welcome",
                "lines": [
                    "喵~我是你的新桌宠。",
                    "会陪你打工,也会在月结时站你这边。",
                    "要花20秒设置一下吗?",
                ],
                "choices": [
                    {"id": "setup", "label": "好,设置一下"},
                    {"id": "skip_all", "label": "直接开始玩"},
                ],
            },
            {
                "id": "close_window",
                "lines": [
                    "你们大概几号到几号最忙/月结?",
                    "到时候我会少吵你,多陪你。",
                ],
                "choices": [
                    {"id": "early", "label": "月初1-5"},
                    {"id": "late", "label": "月末25-31"},
                    {"id": "mid", "label": "月中10-15"},
                    {"id": "skip", "label": "不用,随缘"},
                ],
            },
            {
                "id": "care",
                "lines": [
                    "护眼喝水伸展,希望我催得多勤?",
                ],
                "choices": [
                    {"id": "gentle", "label": "温和点"},
                    {"id": "standard", "label": "标准"},
                    {"id": "strict", "label": "严格点"},
                    {"id": "off", "label": "先别催我"},
                ],
            },
            {
                "id": "interaction",
                "lines": [
                    "我多久在屏幕上动一动?",
                    "不选的话默认大概5分钟一次。",
                ],
                "choices": [
                    {"id": "5m", "label": "5分钟"},
                    {"id": "10m", "label": "10分钟"},
                    {"id": "30m", "label": "30分钟"},
                    {"id": "1h", "label": "1小时"},
                ],
            },
            {
                "id": "appearance",
                "lines": [
                    "我今天随机换了个样子。",
                    "喜欢就锁定,不然每次开机都会换。",
                ],
                "choices": [
                    {"id": "lock", "label": "就要这只"},
                    {"id": "random", "label": "随缘换"},
                ],
            },
        ]

    def _start_onboarding(self) -> None:
        """首次启动轻量气泡引导：可点按钮跳过，非强制弹窗。"""
        self._guide_step = -2  # 启动后约 2 秒再破冰
        self._guide_armed_at = time.time() + 2.0
        self._guide_gap_until = 0.0
        self._guide_after_gap = None
        self._guide_choices = []
        self._guide_btn_rects = []
        self._guide_lines = []
        self._guide_idle_skips = 0
        self._guide_step_at = time.time()
        self.brain._set(State.GREET, 360)
        self.roach.target_alpha = 255
        self.roach.force_anim("waving", 4.0)
        print("首次引导: 气泡按钮可选，8秒无操作自动跳过；可随时干活")

    def _show_guide_step(self) -> None:
        steps = self._guide_steps()
        if self._guide_step < 0:
            return
        if self._guide_step >= len(steps):
            self._begin_guide_finish()
            return
        self._guide_step_at = time.time()
        self._guide_gap_until = 0.0
        self._guide_after_gap = None
        step = steps[self._guide_step]
        self._guide_lines = list(step.get("lines") or [])
        self._guide_choices = list(step.get("choices") or [])
        self._guide_btn_rects = []
        # 主气泡队列留给答谢短句；步骤文案由引导层绘制
        self.bubbles.clear()
        self.bubble = None
        self.fx("star", 2)

    def _schedule_guide_gap(self, after: str) -> None:
        """答完一步后喘口气再问下一步。"""
        self._guide_choices = []
        self._guide_btn_rects = []
        self._guide_after_gap = after
        self._guide_gap_until = time.time() + random.uniform(4.0, 5.5)

    def _on_guide_choice(self, choice_id: str) -> None:
        if self._guide_step < 0 or self._guide_gap_until > time.time():
            return
        steps = self._guide_steps()
        if self._guide_step >= len(steps):
            return
        step_id = steps[self._guide_step]["id"]
        self._guide_idle_skips = 0
        reply = ""
        finish_all = False

        if step_id == "welcome":
            if choice_id == "skip_all":
                finish_all = True
                reply = "好,那就直接玩~"
            else:
                reply = "行,问你几个小事"
        elif step_id == "close_window":
            if choice_id == "early":
                self._set_close_window(1, 5)
                reply = "记下了,到时候我会乖一点"
            elif choice_id == "late":
                self._set_close_window(25, 31)
                reply = "记下了,到时候我会乖一点"
            elif choice_id == "mid":
                self._set_close_window(10, 15)
                reply = "记下了,到时候我会乖一点"
            else:
                reply = "那我先靠猜的,不准你再告诉我"
        elif step_id == "care":
            if choice_id in ("gentle", "standard", "strict"):
                self._apply_care_preset(choice_id, announce=False)
                names = {"gentle": "温和", "standard": "标准", "strict": "勤催"}
                reply = f"养生={names.get(choice_id, choice_id)},收到"
            else:
                self.settings["care_reminders"] = False
                self.settings["rest_reminder"] = False
                save_settings(self.app_root, self.settings)
                self._rest_active = False
                reply = "好,那我先闭嘴,想开了随时在菜单找我"
        elif step_id == "interaction":
            presets = {"5m": 300, "10m": 600, "30m": 1800, "1h": 3600}
            sec = presets.get(choice_id, 300)
            apply_interaction_interval(self.settings, sec)
            save_settings(self.app_root, self.settings)
            now = time.time()
            lo_au = float(self.settings["autonomy_min"])
            hi_au = float(self.settings["autonomy_max"])
            lo_sc = float(self.settings["idle_showcase_min"])
            hi_sc = float(self.settings["idle_showcase_max"])
            self._next_autonomy = now + random.uniform(min(lo_au, hi_au), max(lo_au, hi_au))
            self._next_showcase = now + random.uniform(min(lo_sc, hi_sc), max(lo_sc, hi_sc))
            labels = {"5m": "5分钟", "10m": "10分钟", "30m": "30分钟", "1h": "1小时"}
            reply = f"好,大概{labels.get(choice_id, '5分钟')}动一次"
        elif step_id == "appearance":
            if choice_id == "lock":
                slug = str((self.appearance or {}).get("slug") or "")
                self.settings["appearance_lock"] = True
                if slug:
                    self.settings["appearance_slug"] = slug
                save_settings(self.app_root, self.settings)
                reply = "好嘞,以后每次都是我"
            else:
                self.settings["appearance_lock"] = False
                save_settings(self.app_root, self.settings)
                reply = "那就每次给你个惊喜"

        self._guide_lines = []
        if reply:
            self.bubbles.clear()
            self.say(reply, urgent=True, life=120)
            self.fx("heart", 3)

        if finish_all:
            self._schedule_guide_gap("finish")
        elif self._guide_step >= len(steps) - 1:
            self._schedule_guide_gap("finish")
        else:
            self._schedule_guide_gap("next")

    def _skip_guide_step_idle(self) -> None:
        """8 秒无操作：按默认跳过当前步，不改用户偏好（欢迎步则直接收尾）。"""
        if self._guide_step < 0:
            return
        steps = self._guide_steps()
        if self._guide_step >= len(steps):
            self._begin_guide_finish()
            return
        step_id = steps[self._guide_step]["id"]
        self._guide_idle_skips += 1
        self._guide_lines = []
        self._guide_choices = []
        self._guide_btn_rects = []
        if step_id == "welcome" or self._guide_idle_skips >= 3:
            self.say("先玩着,设置随时在菜单里", urgent=True, life=120)
            self._schedule_guide_gap("finish")
            return
        self._schedule_guide_gap("next")

    def _set_close_window(self, start: int, end: int) -> None:
        self.settings["close_window_start_day"] = int(start)
        self.settings["close_window_end_day"] = int(end)
        # 兼容旧逻辑：用窗口中点当 close_day
        mid = (int(start) + int(end)) // 2
        self.settings["close_day"] = max(1, min(28, mid))
        save_settings(self.app_root, self.settings)

    def _begin_guide_finish(self) -> None:
        """结束语 + 财务彩蛋，然后落盘永不再问。"""
        self._guide_step = -1
        self._guide_choices = []
        self._guide_btn_rects = []
        self._guide_lines = []
        self._guide_gap_until = 0.0
        self._guide_after_gap = None
        self.progress["onboarding_completed"] = True
        self.progress["onboarding_done"] = True
        save_progress(self.app_root, self.progress)
        self.bubbles.clear()
        self.bubbles.push_many(
            [
                "设置好啦,我先趴窝了。",
                "想改随时点菜单「更多设置」,忙起来直接无视我就行~",
                "对了,月结/审计/报销找我,我懂行话(按5-0或菜单里找)。",
            ],
            life=150,
        )
        self.fx("heart", 4)
        self.brain.go_hide(scramble=False)
        self.roach.force_anim("waiting", 6.0)

    def _finish_onboarding(self) -> None:
        self._begin_guide_finish()

    def replay_onboarding(self) -> None:
        """菜单重新开始引导。"""
        self.progress["onboarding_completed"] = False
        self.progress["onboarding_done"] = False
        save_progress(self.app_root, self.progress)
        self._start_onboarding()

    def _check_onboarding_timeout(self) -> None:
        now = time.time()
        # 开场延迟破冰
        if self._guide_step == -2:
            if now >= float(self._guide_armed_at or 0):
                self._guide_step = 0
                self._show_guide_step()
            return
        if self._guide_step < 0:
            return
        # 呼吸间隔：答完后再出下一步
        if self._guide_gap_until > 0:
            if now < self._guide_gap_until:
                return
            after = self._guide_after_gap or "next"
            self._guide_gap_until = 0.0
            self._guide_after_gap = None
            if after == "finish":
                self._begin_guide_finish()
            else:
                self._guide_step += 1
                if self._guide_step >= len(self._guide_steps()):
                    self._begin_guide_finish()
                else:
                    self._show_guide_step()
            return
        # 当前步 8 秒无点 → 跳过
        if self._guide_choices and now - float(self._guide_step_at or 0) >= 8.0:
            self._skip_guide_step_idle()

    def _queue_startup_greetings(self):
        # 首次引导优先，不塞一堆日常气泡
        if not self._onboarding_done():
            self._start_onboarding()
            return
        label = appearance_label(getattr(self, "appearance", None))
        if self.settings.get("bubbles_enabled", True):
            tip = f"{greeting_by_period()} {date_phrase()}"
            self.bubbles.push(tip, life=160)
            if self.settings.get("appearance_lock"):
                self.bubbles.push(f"固定形象:{label}", life=140)
            else:
                self.bubbles.push(f"今日形象:{label}", life=140)
            if self.settings.get("simple_mode", True):
                self.bubbles.push("极简模式·菜单点互动即可", life=120)
            if self._buddy_support_active() and self.settings.get("accountant_buddy", True):
                reason = "已标记月结" if self.settings.get("buddy_support_mode") else "高压日应援"
                self.bubbles.push(f"会计猫:{reason}", life=130)
            if is_workday():
                self.bubbles.push(worker_startup_tip(), life=140)
        # 挥爪打个招呼，随后回窗台趴窝
        self.brain._set(State.GREET, 100)
        self.roach.target_alpha = 255
        self.roach.force_anim("waving", 3.0)

    def _apply_care_preset(self, name: str, announce: bool = True) -> None:
        vals = CARE_PRESETS.get(name) or CARE_PRESETS["standard"]
        self.settings["care_preset"] = name
        self.settings["care_eye_sec"] = int(vals["eye"])
        self.settings["care_water_sec"] = int(vals["water"])
        self.settings["care_stretch_sec"] = int(vals["stretch"])
        self.settings["care_reminders"] = True
        save_settings(self.app_root, self.settings)
        now = time.time()
        self._next_care = {
            "eye": now + max(60.0, vals["eye"] * 0.25),
            "water": now + max(90.0, vals["water"] * 0.3),
            "stretch": now + max(120.0, vals["stretch"] * 0.35),
        }
        if announce:
            self.say(
                f"养生节奏:{name} 眼{vals['eye']//60}分/水{vals['water']//60}分/伸{vals['stretch']//60}分",
                urgent=True,
                life=170,
            )

    def _init_buddy(self):
        """创建会计猫同伴（默认开启，可设置关闭）。"""
        if not self.settings.get("accountant_buddy", True):
            self.buddy = None
            self._sync_layout()
            return
        buddy_roach = RoachRenderer(skin="default", appearance=self.appearance)
        buddy_roach.fade = False
        self.buddy = AccountantBuddy(
            buddy_roach,
            self.font,
            self.font_sm,
            slot_x=WIN_W,
            say_main=self.say,
            fx_main=self.fx,
            pad_x=PAD_X,
            roach_y=ROACH_Y,
            pet_w=PET_W,
            pet_h=PET_H,
        )
        self.buddy.active = True
        self._sync_layout()
        lo = float(self.settings.get("buddy_banter_min") or 120)
        hi = float(self.settings.get("buddy_banter_max") or 280)
        self._next_banter = time.time() + random.uniform(min(lo, hi), max(lo, hi))

    def _canvas_size(self) -> tuple[int, int]:
        # 仅对喷（含淡出）时加宽窗口，平时单宠
        if self.buddy and self.buddy.visible and self.settings.get("accountant_buddy", True):
            return DUAL_WIN_W, WIN_H
        return WIN_W, WIN_H

    def _sync_layout(self):
        w, h = self._canvas_size()
        size_changed = self.canvas.get_width() != w or self.canvas.get_height() != h
        if size_changed:
            self.canvas = pygame.Surface((w, h), pygame.SRCALPHA)
        self.brain.pw = w
        self.brain.ph = h
        if not size_changed:
            return
        if IS_MAC and self.window is not None and self.view is not None:
            self.view.setFrame_(NSMakeRect(0, 0, w, h))
            self.window.setContentSize_((w, h))
            sx, sy = self._screen_pos()
            self.window.setFrameOrigin_((sx, sy))
        elif IS_WIN:
            self._win_resize(w, h)

    def _win_resize(self, w: int, h: int):
        if self.screen is None:
            return
        import ctypes
        old = (int(self.x), int(self.y))
        self.screen = pygame.display.set_mode((w, h), pygame.NOFRAME)
        self.hwnd = pygame.display.get_wm_info()["window"]
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_TOPMOST = 0x00000008
        LWA_COLORKEY = 0x00000001
        style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            self.hwnd, GWL_EXSTYLE,
            style | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST,
        )
        user32.SetLayeredWindowAttributes(self.hwnd, 0x00FF00FF, 0, LWA_COLORKEY)
        self.x, self.y = float(old[0]), float(old[1])
        self._win_click_through = True  # 强制刷新样式
        self._win_set_click_through(False)
        self._win_apply_pos()

    def do_banter(self):
        """手动触发一次主宠 vs 会计猫对喷（高压日自动改鼓励语气）。"""
        support = self._buddy_support_active()
        if self._ai_available() and not self._ai_skip_busy():
            def worker():
                try:
                    script = generate_banter_script(self.settings, support=support)
                    self._pending_ai.append(("banter", script))
                except LLMError:
                    self._pending_ai.append(("banter", None))

            tip = "应援想词中..." if support else "对喷想词中..."
            if self._run_ai("banter", worker, tip):
                return
        self._start_banter_with_script(None)

    def toggle_buddy(self):
        on = not self.settings.get("accountant_buddy", True)
        self.settings["accountant_buddy"] = on
        save_settings(self.app_root, self.settings)
        if on:
            if self.buddy is None:
                self._init_buddy()
            else:
                self.buddy.active = True
                self.buddy.roach.target_alpha = 0
                self.buddy.roach.alpha = 0
                self._sync_layout()
            self.say("会计猫待命", urgent=True)
        else:
            if self.buddy:
                self.buddy.active = False
                self.buddy.bubbles.clear()
                self.buddy._script.clear()
            self._sync_layout()
            self.say("会计去对账了喵", urgent=True)

    def toggle_buddy_support(self):
        """用户标记「月结中」→ 强制应援模式。"""
        on = not self.settings.get("buddy_support_mode", False)
        self.settings["buddy_support_mode"] = on
        save_settings(self.app_root, self.settings)
        if on:
            # 手动标记覆盖「这次不是月结」的当日抑制
            self._support_dismiss_until = 0.0
            self.progress["support_dismiss_until"] = 0.0
            save_progress(self.app_root, self.progress)
            # 立刻拉长下一次自动对喷
            lo = float(self.settings.get("buddy_banter_min") or 120)
            hi = float(self.settings.get("buddy_banter_max") or 280)
            self._next_banter = time.time() + random.uniform(lo * 2.0, hi * 2.8)
            self.say("已标记月结中·会计改鼓励", urgent=True, life=160)
        else:
            self.say("月结标记关·恢复互怼", urgent=True, life=140)

    def _buddy_support_context(self, *, apply_hold: bool = True) -> dict:
        """
        诊断当前应援状态与触发信号（供门控与误判反馈落盘）。
        apply_hold=True 时，负载命中会写入约 15 分钟保持窗。
        """
        now = time.time()
        dt = datetime.now()
        day = dt.day
        close_day = int(self.settings.get("close_day") or 0)
        reasons: list[str] = []
        cpu = None
        mem_pct = None

        if self.settings.get("buddy_support_mode", False):
            reasons.append("manual")

        dismiss_until = float(
            getattr(self, "_support_dismiss_until", 0)
            or self.progress.get("support_dismiss_until")
            or 0
        )
        self._support_dismiss_until = dismiss_until
        dismissed = (not reasons) and now < dismiss_until

        hold_until = float(getattr(self, "_support_hold_until", 0) or 0)
        if (not dismissed) and self.settings.get("buddy_auto_support", True):
            if now < hold_until:
                reasons.append("hold")
                hr = str(getattr(self, "_support_hold_reason", "") or "")
                if hr and hr not in reasons:
                    reasons.append(hr)
            win_lo = int(self.settings.get("close_window_start_day") or 0)
            win_hi = int(self.settings.get("close_window_end_day") or 0)
            if win_lo > 0 and win_hi > 0:
                a, b = min(win_lo, win_hi), max(win_lo, win_hi)
                if a <= day <= b and "close_day" not in reasons:
                    reasons.append("close_day")
            elif close_day > 0:
                lo = max(1, close_day - 3)
                hi = min(31, close_day + 3)
                if lo <= day <= hi and "close_day" not in reasons:
                    reasons.append("close_day")
            elif (day <= 5 or day >= 25) and "calendar" not in reasons:
                reasons.append("calendar")
            if is_workday():
                s = sample_load_light()
                if s:
                    cpu = float(s.get("cpu") or 0)
                    mem_pct = float(s.get("mem_pct") or 0)
                    if cpu >= 75 or mem_pct >= 85:
                        if "load" not in reasons:
                            reasons.append("load")
                        if apply_hold and now >= hold_until:
                            self._support_hold_until = now + 900
                            self._support_hold_reason = "load"

        # 手动标记不受 dismiss 影响；纯自动信号在 suppress 窗内视为未激活
        if dismissed and "manual" not in reasons:
            active = False
            reasons = []
        else:
            active = bool(reasons)

        return {
            "active": active,
            "reasons": reasons,
            "dismissed": dismissed,
            "cpu": cpu,
            "mem_pct": mem_pct,
            "day": day,
            "weekday": dt.weekday(),
            "hour": dt.hour,
            "close_day": close_day,
            "hold_until": float(getattr(self, "_support_hold_until", 0) or 0),
            "dismiss_until": dismiss_until,
        }

    def _buddy_support_active(self) -> bool:
        """
        应援情境：用户标记月结中，或自动识别
        - 日历：每月 1–5 / 25–月末，或 close_day±3
        - 高负荷工作日：工作日且 CPU≥75% 或 内存≥85%（持续约 15 分钟）
        - 用户「这次不是月结」后：当日抑制自动应援（手动标记仍可开）
        """
        return bool(self._buddy_support_context(apply_hold=True)["active"])

    def dismiss_buddy_support(self):
        """
        一键反馈：这次不是月结 → 取消应援，并落盘信号供以后调阈值。
        抑制自动应援到当天结束；手动「标记月结中」可随时重新打开。
        """
        ctx = self._buddy_support_context(apply_hold=False)
        was_active = bool(ctx.get("active")) or bool(self.settings.get("buddy_support_mode"))
        reasons = list(ctx.get("reasons") or [])
        if self.settings.get("buddy_support_mode") and "manual" not in reasons:
            reasons.append("manual")

        # 即使当前刚好未激活，也允许记一笔「我认为现在不该应援」
        cpu = ctx.get("cpu")
        mem_pct = ctx.get("mem_pct")
        if cpu is None or mem_pct is None:
            s = sample_system(0.0)
            if s:
                if cpu is None:
                    cpu = float(s.get("cpu") or 0)
                if mem_pct is None:
                    mem_pct = float(s.get("mem_pct") or 0)
        event = {
            "ts": int(time.time()),
            "verdict": "not_month_end",
            "was_active": was_active,
            "reasons": reasons,
            "cpu": cpu,
            "mem_pct": mem_pct,
            "day": ctx.get("day"),
            "weekday": ctx.get("weekday"),
            "hour": ctx.get("hour"),
            "close_day": ctx.get("close_day"),
        }
        feedback = list(self.progress.get("support_feedback") or [])
        feedback.append(event)
        self.progress["support_feedback"] = feedback[-200:]

        stats = dict(self.progress.get("support_fp_stats") or {})
        stats["total"] = int(stats.get("total") or 0) + 1
        for r in reasons or ["unknown"]:
            stats[r] = int(stats.get(r) or 0) + 1
        if not was_active:
            stats["idle_click"] = int(stats.get("idle_click") or 0) + 1
        self.progress["support_fp_stats"] = stats

        if self.settings.get("buddy_support_mode", False):
            self.settings["buddy_support_mode"] = False
            save_settings(self.app_root, self.settings)

        self._support_hold_until = 0.0
        self._support_hold_reason = ""
        # 抑制到本地次日 0 点，避免日历/负载当天反复误触发
        tomorrow = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = tomorrow + timedelta(days=1)
        self._support_dismiss_until = tomorrow.timestamp()
        self.progress["support_dismiss_until"] = self._support_dismiss_until
        # 误判取消：不做收工庆祝
        self._support_skip_close = True
        self._support_worthy = False
        self.progress["support_close_pending"] = False
        save_progress(self.app_root, self.progress)
        n = int(stats.get("total") or 0)
        if was_active:
            tip = f"好·今天先不应援·已记{n}次"
            if "load" in reasons or "hold" in reasons:
                tip = f"记下了·负载≠月结·已记{n}次"
            elif "calendar" in reasons or "close_day" in reasons:
                tip = f"记下了·今天先不当月结·已记{n}次"
            self.say(tip, urgent=True, life=170)
        else:
            self.say(f"记下了·今天先不应援·已记{n}次", urgent=True, life=150)
        if self._chrome is not None:
            try:
                self._chrome.rebuild_menus()
            except Exception:
                pass

    def _watch_support_session(self) -> None:
        """应援窗口边沿：真正月结结束 → 挂起收工仪式，等回家/睡觉触发。"""
        now = time.time()
        # 节流：勿每帧扫负载（会卡动画）
        nxt = float(getattr(self, "_next_support_watch", 0) or 0)
        if now < nxt and getattr(self, "_support_watch_ready", False):
            return
        self._next_support_watch = now + 2.5
        ctx = self._buddy_support_context(apply_hold=True)
        active = bool(ctx.get("active"))
        reasons = set(ctx.get("reasons") or [])
        # 日历/月结日/手动标记算「真应援」；纯负载代理不配收工礼花
        worthy_now = bool(reasons & {"manual", "calendar", "close_day"})
        if not getattr(self, "_support_watch_ready", False):
            self._support_was_active = active
            self._support_worthy = bool(active and worthy_now)
            self._support_watch_ready = True
            return
        if active and worthy_now:
            self._support_worthy = True
        if self._support_was_active and not active:
            if self._support_worthy and not self._support_skip_close:
                self.progress["support_close_pending"] = True
                save_progress(self.app_root, self.progress)
            self._support_worthy = False
            self._support_skip_close = False
        self._support_was_active = active

    def _consume_support_close_pending(self) -> bool:
        if not self.progress.get("support_close_pending"):
            return False
        self.progress["support_close_pending"] = False
        save_progress(self.app_root, self.progress)
        return True

    def _play_support_close_ritual(self) -> None:
        """月结收工：专属台词 + 伸懒腰 + 小礼花。"""
        self.roach.target_alpha = 255
        self.roach.belly = False
        self.roach.force_anim("waving", 4.8)
        self.brain._set(State.GREET, 150)
        self.fx("confetti", 20)
        self.fx("star", 8)
        line = PACKS.pick("support_close", Bubble.SUPPORT_CLOSE_PHRASES)
        self.bubbles.clear()
        self.bubbles.push_many([line, "伸个懒腰·收工"], life=155)

    def _maybe_support_close_ritual(self) -> bool:
        """回家/睡觉时：若有挂起的收工仪式则播放。"""
        if not self._consume_support_close_pending():
            return False
        self._play_support_close_ritual()
        return True

    def _check_buddy_banter(self):
        if self._stealth or self._meeting_quiet():
            return
        if not self.settings.get("accountant_buddy", True) or self.buddy is None or not self.buddy.active:
            return
        if self.buddy.bantering:
            return
        now = time.time()
        if now < self._next_banter:
            return
        if self.brain.state in (State.SLEEP, State.DRAGGED, State.LASER, State.PANIC):
            self._next_banter = now + 40
            return
        if self.bubbles.current or self.bubbles._q:
            return
        support = self._buddy_support_active()
        # 高压日：更少开口，间隔拉长
        fire_chance = 0.28 if support else 0.55
        mult = 2.6 if support else 1.0
        if random.random() < fire_chance:
            self.do_banter()
        else:
            lo = float(self.settings.get("buddy_banter_min") or 120) * mult
            hi = float(self.settings.get("buddy_banter_max") or 280) * mult
            self._next_banter = now + random.uniform(min(lo, hi) * 0.5, max(lo, hi))

    def _setup_mac_window(self):
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        app.activateIgnoringOtherApps_(True)
        sw, sh = get_desktop_size()
        self.view = PetView.alloc().initWithPet_(self)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(self.x, sh - self.y - WIN_H, WIN_W, WIN_H),
            0,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_(CAPTION)
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setHasShadow_(False)
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        self.window.setContentView_(self.view)
        self.window.setIgnoresMouseEvents_(True)
        self.window.makeFirstResponder_(self.view)
        self.window.makeKeyAndOrderFront_(None)

    def _setup_win_window(self):
        import ctypes

        if os.environ.get("SDL_VIDEODRIVER") == "dummy":
            del os.environ["SDL_VIDEODRIVER"]
        if not pygame.display.get_init():
            pygame.display.init()
        self.screen = pygame.display.set_mode((WIN_W, WIN_H), pygame.NOFRAME)
        pygame.display.set_caption(CAPTION)
        self.hwnd = pygame.display.get_wm_info()["window"]

        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_TOPMOST = 0x00000008
        LWA_COLORKEY = 0x00000001
        style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            self.hwnd, GWL_EXSTYLE,
            style | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST,
        )
        user32.SetLayeredWindowAttributes(self.hwnd, 0x00FF00FF, 0, LWA_COLORKEY)
        # 清掉可能残留的 TRANSPARENT，并强制刷新样式
        self._win_click_through = True  # 强制走一遍 set(False)
        self._win_set_click_through(False)
        self._win_apply_pos()

    def _win_apply_pos(self):
        if not self.hwnd:
            return
        if getattr(self, "_stealth", False):
            return
        import ctypes
        user32 = ctypes.windll.user32
        HWND_TOPMOST = -1
        SW_SHOWNOACTIVATE = 4
        SWP_NOSIZE = 0x0001
        SWP_NOACTIVATE = 0x0010
        SWP_SHOWWINDOW = 0x0040
        # 「显示桌面」/ Win+D 会把置顶窗一并最小化；每帧拉回，保持始终在桌面上
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        user32.SetWindowPos(
            self.hwnd, HWND_TOPMOST, int(self.x), int(self.y), 0, 0,
            SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def _win_focus_for_input(self):
        """点击小猫后抢焦点，否则 Windows 下快捷键全部无效。"""
        if not self.hwnd:
            return
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = int(self.hwnd)
        try:
            if user32.GetForegroundWindow() == hwnd:
                return
            fg = user32.GetForegroundWindow()
            tid_fg = user32.GetWindowThreadProcessId(fg, None)
            tid_self = user32.GetWindowThreadProcessId(hwnd, None)
            attached = False
            if tid_fg and tid_self and tid_fg != tid_self:
                attached = bool(user32.AttachThreadInput(tid_fg, tid_self, True))
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetFocus(hwnd)
            if attached:
                user32.AttachThreadInput(tid_fg, tid_self, False)
        except Exception:
            try:
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    def stop(self):
        self._persist()
        self._running = False
        if self._chrome is not None:
            try:
                self._chrome.stop()
            except Exception:
                pass
        if IS_MAC and OBJC_OK:
            NSApplication.sharedApplication().terminate_(None)

    def _persist(self):
        self.progress["affection"] = int(self.brain.affection)
        self.settings["skin"] = getattr(self.roach, "skin", "default")
        save_settings(self.app_root, self.settings)
        save_progress(self.app_root, self.progress)

    def _note_progress(self, **kwargs):
        for k, v in kwargs.items():
            if isinstance(v, int):
                self.progress[k] = int(self.progress.get(k) or 0) + v
            else:
                self.progress[k] = v
        self.progress["affection"] = int(self.brain.affection)
        newly = evaluate_achievements(self.progress)
        if newly:
            title = newly[0]
            self.say(f"解锁称号:{title}", urgent=True, life=160)
            self.fx("star", 6)
            save_progress(self.app_root, self.progress)

    def _drain_commands(self):
        while True:
            try:
                cmd = self._cmd_q.get_nowait()
            except queue.Empty:
                break
            self._handle_chrome_cmd(cmd)

    def _handle_chrome_cmd(self, cmd: str):
        if cmd == "quit":
            self.stop()
        elif cmd == "call":
            self.do_call()
        elif cmd == "pet":
            self.do_pet_head()
        elif cmd == "feed":
            self.do_feed()
        elif cmd == "box":
            self.do_box()
        elif cmd == "sleep":
            self.do_sleep()
        elif cmd == "overview":
            self.say_sys_overview()
        elif cmd == "passthrough":
            self.toggle_click_through()
        elif cmd == "status":
            self.say_status()
        elif cmd == "toggle_simple_mode":
            self.toggle_simple_mode()
        elif cmd == "toggle_bubbles":
            self.settings["bubbles_enabled"] = not self.settings.get("bubbles_enabled", True)
            save_settings(self.app_root, self.settings)
            on = self.settings["bubbles_enabled"]
            self.say("气泡开" if on else "气泡关", urgent=True)
        elif cmd == "toggle_worker":
            self.settings["worker_reminders"] = not self.settings.get("worker_reminders", True)
            save_settings(self.app_root, self.settings)
            self.say("打工提醒开" if self.settings["worker_reminders"] else "打工提醒关", urgent=True)
        elif cmd == "toggle_finance":
            self.settings["finance_reminders"] = not self.settings.get("finance_reminders", True)
            save_settings(self.app_root, self.settings)
            self.say("财务提醒开" if self.settings["finance_reminders"] else "财务提醒关", urgent=True)
        elif cmd == "toggle_sys":
            self.settings["sys_alerts"] = not self.settings.get("sys_alerts", True)
            save_settings(self.app_root, self.settings)
            self.say("监控告警开" if self.settings["sys_alerts"] else "监控告警关", urgent=True)
        elif cmd == "next_skin":
            self.cycle_skin()
        elif cmd == "next_appearance":
            self.cycle_appearance()
        elif cmd == "toggle_appearance_lock":
            self.toggle_appearance_lock()
        elif cmd == "titles":
            titles = self.progress.get("titles") or []
            if titles:
                self.bubbles.clear()
                self.bubbles.push_many(["称号"] + titles[:4], life=140)
            else:
                self.say("还没有称号,多摸摸我", urgent=True)
        elif cmd == "reload_packs":
            packs_dir = os.path.join(_BASE_DIR, "packs")
            PACKS.load(packs_dir, self.settings.get("enabled_packs"))
            self.say(f"话术包x{len(PACKS.enabled)}", urgent=True)
        elif cmd == "banter":
            self.do_banter()
        elif cmd == "toggle_buddy":
            self.toggle_buddy()
        elif cmd == "toggle_buddy_support":
            self.toggle_buddy_support()
        elif cmd == "dismiss_buddy_support":
            self.dismiss_buddy_support()
        elif cmd == "replay_onboarding":
            self.replay_onboarding()
        elif cmd == "story":
            self.do_story()
        elif cmd == "toggle_rest":
            self.toggle_rest_reminder()
        elif cmd == "toggle_care":
            self.toggle_care_reminders()
        elif cmd == "cycle_care_preset":
            self.cycle_care_preset()
        elif cmd == "toggle_focus_pomodoro":
            self.toggle_focus_pomodoro()
        elif cmd == "toggle_showcase":
            self.toggle_idle_showcase()
        elif cmd == "toggle_mouse_seek":
            self.toggle_mouse_seek()
        elif cmd == "toggle_autonomy":
            self.toggle_autonomy()
        elif cmd == "toggle_meeting_silence":
            self.toggle_meeting_silence()
        elif cmd == "stealth_shot":
            self._on_screenshot_hotkey()
        elif cmd == "cat_random":
            self.do_cat_random()
        elif cmd == "cat_meow":
            self.do_meow()
        elif cmd == "cat_sun":
            self.do_sunbathe()
        elif cmd == "cat_scratch":
            self.do_scratch()
        elif cmd == "cat_gift":
            self.do_gift()
        elif cmd == "cat_stare":
            self.do_stare()
        elif cmd == "cat_knock":
            self.do_knock()
        elif cmd == "cat_headbutt":
            self.do_headbutt()
        elif cmd == "cat_chirp":
            self.do_chirp()
        elif cmd == "cat_ignore":
            self.do_ignore()
        elif cmd == "cat_knead":
            self.do_knead()
        elif cmd == "cat_groom":
            self.do_groom()
        elif cmd == "toggle_ai":
            self.toggle_ai()
        elif cmd == "cycle_ai_provider":
            self.cycle_ai_provider()
        elif cmd == "set_ai_key":
            self.set_ai_api_key_from_menu()
        elif cmd.startswith("key") or cmd.startswith("keyalt:"):
            self._handle_win_key_cmd(cmd)

    def _win_keys_active(self) -> bool:
        """Windows 焦点外快捷键是否应响应。"""
        if self.settings.get("click_through_force", False):
            return False
        if self._pointer_over_pet or self.dragging or self.right_dragging:
            return True
        return time.time() < float(getattr(self, "_win_keys_until", 0.0) or 0.0)

    def _handle_win_key_cmd(self, cmd: str) -> None:
        """处理 WinFocusKeys 丢进队列的按键命令。"""
        if cmd == "keyesc":
            self.stop()
            return
        self._note_user_act()
        if cmd == "keyspace":
            self.brain.react_jump()
            self.roach.target_scale = 1.2
            self.fx("star", 4)
            return
        if cmd == "keyleft":
            self.brain.react_nudge(-3.5, 0)
            self.roach.set_facing(-1)
            return
        if cmd == "keyright":
            self.brain.react_nudge(3.5, 0)
            self.roach.set_facing(1)
            return
        if cmd == "keydown":
            self.brain.react_nudge(0, 3.5)
            self.roach.set_facing(0, 1)
            return
        if cmd == "keyup":
            self.brain.react_nudge(0, -3.5)
            self.roach.set_facing(0, -1)
            self.roach.target_scale = 1.1
            return
        if cmd.startswith("keyalt:"):
            ch = cmd.split(":", 1)[-1]
            if ch:
                self._cat_hotkey(ch)
            return
        if cmd.startswith("key:"):
            ch = cmd.split(":", 1)[-1]
            if ch:
                self._dispatch_char_key(ch)

    def toggle_click_through(self):
        self.settings["click_through_force"] = not self.settings.get("click_through_force", False)
        save_settings(self.app_root, self.settings)
        on = self.settings["click_through_force"]
        self.say("穿透模式(干活)" if on else "可点互动", urgent=True)
        if on:
            self.fx("dust", 3)
        else:
            self.fx("heart", 3)

    def cycle_skin(self):
        unlocked = list(self.progress.get("unlocked_skins") or ["default"])
        for extra in ("default", "gold", "ghost"):
            if extra not in unlocked:
                unlocked.append(extra)
        cur = getattr(self.roach, "skin", "default")
        try:
            idx = unlocked.index(cur)
        except ValueError:
            idx = 0
        nxt = unlocked[(idx + 1) % len(unlocked)]
        self.roach.apply_skin(nxt)
        self.settings["skin"] = nxt
        save_settings(self.app_root, self.settings)
        self.say(f"皮肤:{nxt}", urgent=True)
        self.fx("star", 4)

    def cycle_appearance(self):
        """切换下一只猫形象；若已锁定则同步写入 slug。"""
        apps = list_cat_appearances()
        if not apps:
            self.say("没有可用形象", urgent=True)
            return
        cur = str((self.appearance or {}).get("slug") or "")
        idx = 0
        for i, app in enumerate(apps):
            if str(app.get("slug") or "") == cur:
                idx = i
                break
        nxt = apps[(idx + 1) % len(apps)]
        self.appearance = dict(nxt)
        self.roach.apply_appearance(self.appearance)
        if self.buddy is not None:
            try:
                self.buddy.roach.apply_appearance(self.appearance)
            except Exception:
                pass
        slug = str(nxt.get("slug") or "")
        self.settings["appearance_slug"] = slug
        save_settings(self.app_root, self.settings)
        label = appearance_label(nxt)
        locked = "·已锁" if self.settings.get("appearance_lock") else ""
        self.say(f"形象:{label}{locked}", urgent=True, life=140)
        self.fx("star", 5)

    def toggle_appearance_lock(self):
        on = not self.settings.get("appearance_lock", False)
        self.settings["appearance_lock"] = on
        slug = str((self.appearance or {}).get("slug") or self.settings.get("appearance_slug") or "")
        if slug:
            self.settings["appearance_slug"] = slug
        save_settings(self.app_root, self.settings)
        label = appearance_label(self.appearance)
        if on:
            self.say(f"形象已锁定:{label}", urgent=True, life=150)
        else:
            self.say("形象解锁(下次随机)", urgent=True, life=140)

    def toggle_simple_mode(self):
        on = not self.settings.get("simple_mode", True)
        self.settings["simple_mode"] = on
        save_settings(self.app_root, self.settings)
        if self._chrome is not None:
            try:
                self._chrome.rebuild_menus()
            except Exception:
                pass
        if on:
            self.say("极简模式开(菜单变短)", urgent=True, life=150)
        else:
            self.say("完整模式开(快捷键全开)", urgent=True, life=150)

    def do_pet_head(self):
        """菜单摸头：等同点猫头。"""
        self._note_user_act()
        self.brain.react_click()
        self.roach.target_alpha = 255
        self.roach.target_scale = 1.08
        self.roach.force_anim("waving", 2.0)
        self.fx("heart", 5)
        self._note_progress(pet_count=1)
        streak = self.brain.pet_streak
        if streak >= 3 and streak % 3 == 0:
            self.do_knead()
            return
        self.maybe_say(random.choice(Bubble.CLICK_PHRASES), chance=0.45)

    def do_sleep(self):
        """菜单睡觉：等同右键睡。"""
        self._note_user_act()
        closing = self._maybe_support_close_ritual()
        if self.brain.state == State.SLEEP:
            self.brain.go_hide()
            if not closing:
                self.fx("star", 3)
                self.say("醒啦", urgent=True)
        else:
            self.brain._set(State.SLEEP, random.randint(400, 800))
            self.roach.belly = False
            self.roach.target_alpha = 255
            if not closing:
                self.say("Zzz…", urgent=True, life=100)

    # ── 问候 / 天气 / 提醒 ────────────────────────────────

    def _start_weather_fetch(self):
        def worker():
            w = fetch_weather_sync()
            self._weather = w
            self._weather_ready = True
            # 天气只缓存，不主动打断说话

        threading.Thread(target=worker, daemon=True).start()

    def _flush_pending_say(self):
        while self._pending_say:
            text, life = self._pending_say.pop(0)
            self.bubbles.push(text, life)
        while self._pending_ai:
            item = self._pending_ai.pop(0)
            kind = item[0]
            if kind == "say":
                _, text, life, urgent = item
                self.say(text, life=life, urgent=urgent)
            elif kind == "story":
                _, lines = item
                self.bubbles.clear()
                self.bubbles.push_many(lines, life=130)
                self._note_progress(story_count=1)
            elif kind == "banter":
                _, script = item
                self._start_banter_with_script(script)
            elif kind == "fail":
                _, text = item
                self.say(text, urgent=True, life=120)

    def _ai_context(self) -> str:
        now = datetime.now()
        bits = [greeting_by_period(), f"{now.hour}点"]
        if is_workday(now):
            bits.append("工作日")
        if self.brain.state == State.HIDE:
            bits.append("趴窝")
        return " ".join(bits)

    def _ai_available(self) -> bool:
        return provider_ready(self.settings)

    def _ai_skip_busy(self) -> bool:
        if self._ai_busy:
            return True
        if self.buddy and self.buddy.bantering:
            return True
        if len(self.bubbles._q) > 4:
            return True
        return False

    def _run_ai(self, kind: str, worker, thinking: str = "想词中..."):
        """后台线程跑 LLM；结果写入 _pending_ai。"""
        if not self._ai_available() or self._ai_skip_busy():
            return False
        self._ai_busy = True
        if thinking:
            self.say(thinking, urgent=True, life=90)

        def job():
            try:
                worker()
            except LLMError:
                self._pending_ai.append(("fail", "AI没词,用本地的"))
            except Exception:
                self._pending_ai.append(("fail", "AI开小差了"))
            finally:
                self._ai_busy = False

        threading.Thread(target=job, daemon=True).start()
        return True

    def _start_banter_with_script(self, script: list[tuple[str, str]] | None = None):
        if not self.settings.get("accountant_buddy", True):
            self.settings["accountant_buddy"] = True
            save_settings(self.app_root, self.settings)
        if self.buddy is None:
            self._init_buddy()
        if self.buddy is None:
            return
        support = self._buddy_support_active()
        if script is None:
            script = pick_banter_script(support=support)
        self.buddy.active = True
        self._sync_layout()
        self.roach.target_alpha = 255
        self.roach.force_anim("waving", 12.0)
        if self.brain.state == State.HIDE:
            self.brain._set(State.GREET, 160)
        self.buddy.start_banter(script)
        self._note_progress(banter_count=1)
        mult = 2.6 if support else 1.0
        lo = float(self.settings.get("buddy_banter_min") or 120) * mult
        hi = float(self.settings.get("buddy_banter_max") or 280) * mult
        self._next_banter = time.time() + random.uniform(min(lo, hi), max(lo, hi))

    def say(self, text: str, life: int = 160, urgent: bool = False):
        # 关气泡时仍允许 urgent（热键/菜单反馈）
        if not self.settings.get("bubbles_enabled", True) and not urgent:
            return
        if urgent:
            self.bubbles.interrupt(text, life)
        else:
            self.bubbles.push(text, life)

    def maybe_say(self, text: str, chance: float = 0.22, life: int = 140, urgent: bool = True):
        if random.random() < chance:
            self.say(text, life=life, urgent=urgent)

    def fx(self, kind: str = "heart", n: int = 5):
        ax, ay = self._fx_anchor
        self.roach.burst(ax, ay, kind, n)

    def say_date(self):
        self.say(date_phrase(), urgent=True)
        self.brain._set(State.GREET, 60)

    def say_time(self):
        self.say(time_phrase(), urgent=True)

    def say_weather(self):
        if self._weather:
            self.say(f"天气 {self._weather}", urgent=True)
        elif self._weather_ready:
            self.say("天气查不到", urgent=True)
        else:
            self.say("查天气中...", urgent=True)
            self._start_weather_fetch()

    def say_status(self):
        e, h = int(self.brain.energy), int(self.brain.hunger)
        aff = int(self.brain.affection)
        mood = "饿" if h > 70 else ("困" if e < 30 else "精神")
        hide = "趴窝" if self.brain.state == State.HIDE else "在外面"
        extra = ""
        if self._stealth:
            extra = " 静默收起"
        elif self._casting:
            extra = " 投屏中"
        elif self._meeting_level == "meeting":
            extra = " 开会安静"
        elif self._focus_pomodoro_active():
            left = max(1, int(math.ceil((float(self._focus_until) - time.time()) / 60)))
            extra = f" 专注剩{left}分"
        elif self._buddy_support_active():
            extra = " 月结应援中"
        else:
            n = int((self.progress.get("support_fp_stats") or {}).get("total") or 0)
            if n > 0:
                extra = f" 误判反馈{n}次"
        self.say(f"{mood} {hide} 亲密度{aff}{extra}", urgent=True)

    def say_sys_cpu(self):
        self.roach.target_alpha = 255
        self.say(format_cpu_line(), urgent=True, life=160)
        self.brain._set(State.GREET, 50)
        self.fx("star", 2)

    def say_sys_mem(self):
        self.roach.target_alpha = 255
        self.say(format_mem_line(), urgent=True, life=160)
        self.brain._set(State.GREET, 50)
        self.fx("star", 2)

    def say_sys_disk(self):
        self.roach.target_alpha = 255
        self.say(format_disk_line(), urgent=True, life=170)
        self.brain._set(State.GREET, 50)
        self.fx("crumb", 2)

    def say_sys_net(self):
        self.roach.target_alpha = 255
        self.say(format_net_line(), urgent=True, life=160)
        self.brain._set(State.GREET, 50)
        self.fx("star", 2)

    def say_sys_overview(self):
        """一口气报 CPU/内存/磁盘/网络。"""
        self.roach.target_alpha = 255
        lines = format_sys_overview()
        self.bubbles.clear()
        self.bubbles.push_many(lines, life=150)
        alerts = sys_alert_messages()
        if alerts:
            self.bubbles.push(alerts[0], life=140)
        self.brain._set(State.GREET, 120)
        self.fx("star", 4)
        if not PSUTIL_OK:
            self.say("请 pip install psutil", urgent=True)

    def say_worker_tip(self):
        """手动触发一条打工人提醒（含黑话）。"""
        due = worker_schedule_due(self._worker_fired)
        text = due or worker_random_tip()
        self.say(text, urgent=True, life=180)
        self.fx("star", 4)
        self.brain._set(State.GREET, 70)

    def say_buzzword(self):
        """随机丢一句黑话（互联网/财务混合）。"""
        self.say(worker_buzzword(), urgent=True, life=150)
        self.roach.target_alpha = 255
        self.brain._set(State.GREET, 60)
        self.fx("star", 3)

    def say_finance_buzz(self):
        """财务人员黑话 / 口头禅专场。"""
        self.say(finance_buzzword(), urgent=True, life=150)
        self.roach.target_alpha = 255
        self.brain._set(State.GREET, 60)
        self.fx("crumb", 4)

    def say_finance_tip(self):
        """财务专属提醒。"""
        self.say(finance_random_tip(), urgent=True, life=170)
        self.roach.target_alpha = 255
        self.brain._set(State.GREET, 70)
        self.fx("star", 3)

    def do_month_close(self):
        """假装月结。"""
        self.roach.target_alpha = 255
        self.brain._set(State.POSE, 130)
        self.roach.force_anim("review", 5.0)
        self.bubbles.clear()
        self.bubbles.push_many(
            [
                "月结启动!",
                PACKS.pick("finance_close", FINANCE_CLOSE),
                PACKS.pick("finance_catchphrases", FINANCE_CATCHPHRASES),
            ],
            life=130,
        )
        self.fx("star", 5)

    def do_audit_panic(self):
        """审计来了。"""
        self.roach.target_alpha = 255
        self.brain.react_panic()
        self.say(PACKS.pick("finance_audit", FINANCE_AUDIT), urgent=True, life=160)
        self.fx("dust", 8)

    def do_reimburse(self):
        """报销审查脸。"""
        self.roach.target_alpha = 255
        self.brain._set(State.CURIOUS, 100)
        self.say(PACKS.pick("finance_reimburse", FINANCE_REIMBURSE), urgent=True, life=150)
        self.fx("crumb", 5)

    def do_tax_check(self):
        """税务自查。"""
        self.roach.target_alpha = 255
        self.brain._set(State.GREET, 100)
        self.bubbles.clear()
        self.bubbles.push_many(
            [
                "税务自查",
                PACKS.pick("finance_tax", FINANCE_TAX),
                PACKS.pick("finance_catchphrases", FINANCE_CATCHPHRASES),
            ],
            life=130,
        )
        self.fx("star", 4)

    def do_payroll_day(self):
        """发薪日模式。"""
        self.roach.target_alpha = 255
        self.brain._set(State.HAPPY, 110)
        self.bubbles.clear()
        self.bubbles.push_many(
            [
                "发薪日!",
                PACKS.pick("finance_payroll", FINANCE_PAYROLL),
                PACKS.pick("finance_catchphrases", FINANCE_CATCHPHRASES),
            ],
            life=130,
        )
        self.fx("heart", 5)

    def do_standup(self):
        """假装开站会。"""
        self.roach.target_alpha = 255
        self.brain._set(State.POSE, 110)
        self.roach.target_scale = 1.1
        self.bubbles.clear()
        self.bubbles.push_many(
            [
                "站会开始!",
                PACKS.pick("worker_standup", WORKER_STANDUP),
                PACKS.pick("worker_standup", WORKER_STANDUP),
            ],
            life=120,
        )
        self.fx("star", 5)

    def do_align(self):
        """对齐一下。"""
        self.roach.target_alpha = 255
        self.brain._set(State.CURIOUS, 100)
        self.say(PACKS.pick("worker_align", WORKER_ALIGN), urgent=True, life=160)
        self.fx("heart", 4)

    def do_review(self):
        """复盘。"""
        self.roach.target_alpha = 255
        self.brain._set(State.GREET, 120)
        self.bubbles.clear()
        self.bubbles.push_many(
            [
                "复盘时间",
                PACKS.pick("worker_review", WORKER_REVIEW),
                PACKS.pick("worker_buzz", WORKER_BUZZ),
            ],
            life=130,
        )
        self.fx("star", 4)

    def do_fish(self):
        """合法摸鱼（猫版：蹲窝偷看）。"""
        self.brain.react_peek()
        self.roach.force_anim("waiting", 3.0)
        self.say(PACKS.pick("worker_fish", WORKER_FISH), urgent=True, life=150)
        self.fx("crumb", 3)

    def do_resist_pua(self):
        """反PUA / 拒绝画饼。"""
        self.roach.target_alpha = 255
        self.brain.react_spin(16)
        self.roach.spin_vel = 16
        self.say(PACKS.pick("worker_pua", WORKER_PUA_RESIST), urgent=True, life=170)
        self.fx("dust", 6)

    def do_chat(self):
        self.brain._set(State.GREET, 80)
        self.roach.target_alpha = 255
        self.roach.force_anim("waving", 3.5)
        self.fx("star", 3)
        ctx = self._ai_context()

        def local_line():
            pool = (
                Bubble.CHAT_PHRASES
                + PACKS.pool("chat", [])
                + PACKS.pool("worker_buzz", WORKER_BUZZ)[:20]
                + PACKS.pool("finance_buzz", FINANCE_BUZZ)[:20]
                + PACKS.pool("finance_catchphrases", FINANCE_CATCHPHRASES)[:20]
                + PACKS.pool("programmer_buzz", [])[:15]
            )
            return random.choice(pool)

        if self._ai_available() and not self._ai_skip_busy():
            def worker():
                try:
                    line = generate_line(self.settings, "chat", ctx)
                    self._pending_ai.append(("say", line, 160, True))
                except LLMError:
                    self._pending_ai.append(("say", local_line(), 160, True))

            if self._run_ai("chat", worker):
                return
        self.say(local_line(), urgent=True, life=160)

    def do_story(self):
        """故事大会：AI 或本地短篇连播气泡。"""
        if self.buddy and self.buddy.bantering:
            return
        self.roach.target_alpha = 255
        self.roach.belly = False
        self.brain.react_pose()
        self.roach.force_anim("review", 8.0)
        self.fx("star", 6)
        self._rest_active = False

        if self._ai_available() and not self._ai_skip_busy():
            def worker():
                try:
                    lines = generate_story_lines(self.settings)
                    self._pending_ai.append(("story", lines))
                except LLMError:
                    self._pending_ai.append(("story", pick_story()))

            if self._run_ai("story", worker, "编故事中..."):
                return
        lines = pick_story()
        self.bubbles.clear()
        self.bubbles.push_many(lines, life=130)
        self._note_progress(story_count=1)

    def do_rest_break(self):
        """休息提醒：现身到屏幕偏中，拉伸并催休息。"""
        if self.buddy and self.buddy.bantering:
            self._next_rest = time.time() + 120
            return
        sw, sh = get_desktop_size()
        self.x = float((sw - WIN_W) // 2)
        self.y = float(max(40, (sh - WIN_H) // 2))
        self.brain.target_x = self.brain.target_y = None
        self.brain.vx = self.brain.vy = 0
        self.roach.target_alpha = 255
        self.roach.belly = False
        self._rest_active = True
        self.bubbles.clear()
        line = PACKS.pick("care_rest", REST_PHRASES)
        self.bubbles.push_many([line, "起来活动一下", "点我可关掉提示"], life=140)
        self.brain.react_dance()
        self.roach.force_anim("waving", 6.0)
        self.fx("star", 8)

    def _pick_care_line(self, kind: str) -> str:
        fallback = {
            "eye": CARE_EYE_PHRASES,
            "water": CARE_WATER_PHRASES,
            "stretch": CARE_STRETCH_PHRASES,
            "rest": REST_PHRASES,
        }.get(kind, REST_PHRASES)
        cat = {
            "eye": "care_eye",
            "water": "care_water",
            "stretch": "care_stretch",
            "rest": "care_rest",
        }.get(kind, "care_rest")
        return PACKS.pick(cat, fallback)

    def do_care_nudge(self, kind: str):
        """护眼/喝水/伸展：轻量提醒（气泡+小动作），不抢屏幕中央。"""
        if kind not in ("eye", "water", "stretch"):
            return
        if self.buddy and self.buddy.bantering:
            self._next_care[kind] = time.time() + 90
            return
        self.roach.target_alpha = 255
        line = self._pick_care_line(kind)
        hint = {
            "eye": "看远处20秒",
            "water": "去接杯水吧",
            "stretch": "站起来伸一下",
        }.get(kind, "")
        self.say(line, urgent=True, life=140)
        if hint and random.random() < 0.55:
            self.bubbles.push(hint, life=100)
        if kind == "stretch":
            self.brain.react_dance()
            self.roach.force_anim("waving", 4.0)
            self.fx("star", 4)
        elif kind == "eye":
            self.brain._set(State.CURIOUS, 70)
            self.fx("star", 3)
        else:  # water
            self.fx("crumb", 4)
            try:
                self.brain._set(State.GREET, 50)
            except Exception:
                pass

    def toggle_rest_reminder(self):
        on = not self.settings.get("rest_reminder", True)
        self.settings["rest_reminder"] = on
        save_settings(self.app_root, self.settings)
        if on:
            iv = float(self.settings.get("rest_reminder_interval_sec") or 3600)
            self._next_rest = time.time() + max(60.0, iv)
            self.say("休息提醒开", urgent=True)
        else:
            self._rest_active = False
            self.say("休息提醒关", urgent=True)

    def toggle_care_reminders(self):
        on = not self.settings.get("care_reminders", True)
        self.settings["care_reminders"] = on
        save_settings(self.app_root, self.settings)
        if on:
            now = time.time()
            self._next_care = {
                "eye": now + max(60.0, float(self.settings.get("care_eye_sec") or 1200) * 0.3),
                "water": now + max(90.0, float(self.settings.get("care_water_sec") or 1800) * 0.35),
                "stretch": now + max(120.0, float(self.settings.get("care_stretch_sec") or 2700) * 0.4),
            }
            preset = str(self.settings.get("care_preset") or "standard")
            self.say(f"养生提醒开({preset})", urgent=True, life=140)
        else:
            self.say("养生提醒关", urgent=True)

    def cycle_care_preset(self):
        """在 gentle / standard / strict 间切换，并写回间隔秒数。"""
        order = ("gentle", "standard", "strict")
        cur = str(self.settings.get("care_preset") or "standard")
        try:
            idx = order.index(cur if cur in order else "standard")
        except ValueError:
            idx = 1
        nxt = order[(idx + 1) % len(order)]
        self._apply_care_preset(nxt, announce=True)

    def _focus_pomodoro_active(self) -> bool:
        return time.time() < float(getattr(self, "_focus_until", 0) or 0)

    def toggle_focus_pomodoro(self):
        """手动开/关 25 分钟专注：期间安静蹲守，结束跑来催休息。"""
        if self._focus_pomodoro_active() or getattr(self, "_focus_end_pending", False):
            self._cancel_focus_pomodoro(announce=True)
            return
        self._start_focus_pomodoro()

    def _start_focus_pomodoro(self) -> None:
        sec = int(self.settings.get("focus_pomodoro_sec") or 1500)
        sec = max(60, min(7200, sec))
        self._focus_until = time.time() + sec
        self._focus_end_pending = False
        self._rest_active = False
        if self.buddy and getattr(self.buddy, "bantering", False):
            try:
                self.buddy.bubbles.clear()
                self.buddy._script.clear()
            except Exception:
                pass
        self.bubbles.clear()
        self.brain.follow = False
        self.brain.go_hide(scramble=False)
        self.roach.belly = False
        self.roach.target_alpha = 255
        self.roach.force_anim("waiting", 10.0)
        mins = max(1, int(round(sec / 60.0)))
        self.say(f"专注{mins}分·我蹲着陪你", urgent=True, life=160)
        self.fx("star", 2)

    def _cancel_focus_pomodoro(self, announce: bool = True) -> None:
        self._focus_until = 0.0
        self._focus_end_pending = False
        if announce:
            self.say("专注取消了", urgent=True, life=120)

    def _check_focus_pomodoro(self) -> None:
        until = float(getattr(self, "_focus_until", 0) or 0)
        if until > 0 and time.time() >= until:
            self._focus_until = 0.0
            # 共享/投屏收起中：等现身后再收工；开会可见时也可立刻安静收工
            if self._stealth:
                self._focus_end_pending = True
            else:
                self._finish_focus_pomodoro()
            return
        if getattr(self, "_focus_end_pending", False) and not self._stealth:
            self._finish_focus_pomodoro()

    def _focus_end_should_stay_put(self) -> bool:
        """开会/共享/投屏时：不跑向指针，避免打断打字或入镜。"""
        if self._stealth:
            return True
        if not self.settings.get("meeting_silence", True):
            return False
        if self._meeting_level in ("meeting", "sharing") or self._casting:
            return True
        return False

    def _finish_focus_pomodoro(self) -> None:
        """番茄结束：默认跑向指针催休息；开会/共享或关闭休息提醒时改为轻提示。"""
        self._focus_end_pending = False
        self._focus_until = 0.0
        now = time.time()
        for kind in ("eye", "water", "stretch"):
            nxt = float(self._next_care.get(kind) or 0)
            self._next_care[kind] = max(nxt, now + random.uniform(700, 1400))
        self._next_rest = max(float(getattr(self, "_next_rest", 0) or 0), now + 1800)

        line = PACKS.pick("focus_done", Bubble.FOCUS_DONE_PHRASES)
        self.roach.target_alpha = 255
        self.roach.belly = False

        # 用户选过「先别催我」/关掉休息提醒：只报中性结束，不跑来催
        if not self.settings.get("rest_reminder", True):
            if self.brain.state not in (State.HIDE, State.SLEEP):
                self.brain.go_hide(scramble=False)
            self._rest_active = False
            self.bubbles.clear()
            neutral = PACKS.pick(
                "focus_done_neutral",
                Bubble.FOCUS_DONE_NEUTRAL_PHRASES,
            )
            self.say(neutral, urgent=True, life=120)
            self.fx("star", 1)
            return

        if self._focus_end_should_stay_put():
            # 安静收工：不 react_call、不抢焦点；仍提示到点
            if self.brain.state not in (State.HIDE, State.SLEEP):
                self.brain.go_hide(scramble=False)
            self._rest_active = False
            self.bubbles.clear()
            soft = PACKS.pick("focus_done_quiet", Bubble.FOCUS_DONE_QUIET_PHRASES)
            if soft in Bubble.FOCUS_DONE_PHRASES or soft == line:
                soft = f"{line}·先轻声"
            self.say(soft, urgent=True, life=140)
            self.fx("star", 2)
            return

        self.brain.react_call()
        self.brain.state_timer = max(int(self.brain.state_timer), 520)
        self.roach.force_anim("waving", 5.5)
        self._rest_active = True
        self.bubbles.clear()
        self.bubbles.push_many([line, "起来活动一下", "点我关掉提示"], life=155)
        self.fx("star", 8)
        self.fx("heart", 4)

    def toggle_idle_showcase(self):
        on = not self.settings.get("idle_showcase", True)
        self.settings["idle_showcase"] = on
        save_settings(self.app_root, self.settings)
        if on:
            lo = float(self.settings.get("idle_showcase_min") or 45)
            hi = float(self.settings.get("idle_showcase_max") or 90)
            self._next_showcase = time.time() + random.uniform(min(lo, hi), max(lo, hi))
            self.say("周期表演开", urgent=True)
        else:
            self.say("周期表演关", urgent=True)

    def toggle_mouse_seek(self):
        on = not self.settings.get("mouse_seek", True)
        self.settings["mouse_seek"] = on
        save_settings(self.app_root, self.settings)
        if on:
            self._mouse_still_since = time.time()
            self.say("寻访开(鼠标久闲找你)", urgent=True, life=140)
        else:
            self._seek_act = None
            self.say("寻访关", urgent=True)

    def toggle_autonomy(self):
        on = not self.settings.get("autonomy", True)
        self.settings["autonomy"] = on
        save_settings(self.app_root, self.settings)
        if on:
            lo = float(self.settings.get("autonomy_min") or 50)
            hi = float(self.settings.get("autonomy_max") or 120)
            self._next_autonomy = time.time() + random.uniform(min(lo, hi), max(lo, hi))
            self.say("自主行为开", urgent=True)
        else:
            self.say("自主行为关", urgent=True)

    def toggle_meeting_silence(self):
        on = not self.settings.get("meeting_silence", True)
        self.settings["meeting_silence"] = on
        save_settings(self.app_root, self.settings)
        if on:
            self.say("静默开(会议/投屏/截图)", urgent=True, life=150)
            self._next_meeting_check = 0.0
            if self._chrome is not None:
                try:
                    self._chrome.set_shot_watch(True)
                except Exception:
                    pass
        else:
            if self._stealth:
                self._set_stealth(False, reason="")
            self._shot_hide_until = 0.0
            if self._chrome is not None:
                try:
                    self._chrome.set_shot_watch(False)
                except Exception:
                    pass
            self.say("会议静默关", urgent=True)

    def _on_screenshot_hotkey(self) -> None:
        """截图快捷键：立刻收起，约 2.5s 后可恢复（仍投屏/共享则继续藏）。"""
        if not self.settings.get("meeting_silence", True):
            return
        self._shot_hide_until = time.time() + 2.5
        self._stealth_clear_at = 0.0
        self._set_stealth(True, reason="screenshot")

    def toggle_ai(self):
        ai = self.settings.setdefault("ai", {})
        if not isinstance(ai, dict):
            ai = {}
            self.settings["ai"] = ai
        on = not bool(ai.get("enabled"))
        ai["enabled"] = on
        save_settings(self.app_root, self.settings)
        if on:
            name = current_provider_name(self.settings)
            if provider_ready(self.settings):
                self.say(f"AI开:{name}", urgent=True)
            else:
                self.say(f"AI开但{name}缺Key", urgent=True)
        else:
            self.say("AI关,用本地词", urgent=True)

    def cycle_ai_provider(self):
        ai = self.settings.setdefault("ai", {})
        if not isinstance(ai, dict):
            ai = {}
            self.settings["ai"] = ai
        cur = current_provider_name(self.settings)
        try:
            idx = PROVIDER_ORDER.index(cur)
        except ValueError:
            idx = 0
        nxt = PROVIDER_ORDER[(idx + 1) % len(PROVIDER_ORDER)]
        ai["provider"] = nxt
        save_settings(self.app_root, self.settings)
        ready = "已配Key" if provider_ready(self.settings, nxt) else "缺Key"
        self.say(f"AI厂商:{nxt}({ready})", urgent=True)

    def _clipboard_text(self) -> str:
        """读取系统剪贴板纯文本（失败返回空串）。"""
        try:
            if IS_MAC and OBJC_OK:
                from AppKit import NSPasteboard, NSPasteboardTypeString

                pb = NSPasteboard.generalPasteboard()
                raw = pb.stringForType_(NSPasteboardTypeString)
                return str(raw or "").strip()
            if IS_WIN:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                CF_UNICODETEXT = 13
                if not user32.OpenClipboard(None):
                    return ""
                try:
                    handle = user32.GetClipboardData(CF_UNICODETEXT)
                    if not handle:
                        return ""
                    ptr = kernel32.GlobalLock(handle)
                    if not ptr:
                        return ""
                    try:
                        return ctypes.wstring_at(ptr).strip()
                    finally:
                        kernel32.GlobalUnlock(handle)
                finally:
                    user32.CloseClipboard()
        except Exception:
            return ""
        return ""

    def _prompt_ai_key_dialog(self, provider: str, has_key: bool) -> str | None:
        """弹出输入框；返回密钥字符串，取消返回 None。空串表示确认清空。"""
        title = "设置 AI 密钥"
        tip = (
            f"当前厂商: {provider}\n"
            f"状态: {'已配置（输入新密钥可替换）' if has_key else '未配置'}\n"
            "也可先复制密钥再点「用剪贴板」。"
        )
        if IS_MAC and OBJC_OK:
            try:
                from AppKit import (
                    NSAlert,
                    NSTextField,
                    NSMakeRect,
                    NSAlertFirstButtonReturn,
                    NSAlertSecondButtonReturn,
                )

                alert = NSAlert.alloc().init()
                alert.setMessageText_(title)
                alert.setInformativeText_(tip)
                alert.addButtonWithTitle_("确定")
                alert.addButtonWithTitle_("用剪贴板")
                alert.addButtonWithTitle_("取消")
                field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 320, 24))
                field.setStringValue_("")
                field.setPlaceholderString_("粘贴或输入 api_key")
                alert.setAccessoryView_(field)
                code = int(alert.runModal())
                if code == int(NSAlertSecondButtonReturn):
                    clip = self._clipboard_text()
                    return clip if clip else None
                if code != int(NSAlertFirstButtonReturn):
                    return None
                return str(field.stringValue() or "")
            except Exception as exc:
                print(f"⚠️ Mac 密钥对话框失败: {exc}")
                return None
        if IS_WIN:
            try:
                import tkinter as tk
                from tkinter import simpledialog, messagebox

                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                # 先问是否直接用剪贴板
                clip = self._clipboard_text()
                if clip and messagebox.askyesno(
                    title,
                    f"{tip}\n\n剪贴板里有内容，要用它作为密钥吗？",
                    parent=root,
                ):
                    root.destroy()
                    return clip
                value = simpledialog.askstring(
                    title,
                    tip + "\n\n输入新密钥（取消=不改）:",
                    parent=root,
                    show="*",
                )
                root.destroy()
                return value
            except Exception as exc:
                print(f"⚠️ Win 密钥对话框失败: {exc}")
                # 回退：仅剪贴板
                clip = self._clipboard_text()
                return clip if clip else None
        return None

    def set_ai_api_key_from_menu(self) -> None:
        """菜单：为当前 AI 厂商写入/替换 api_key（落盘 secrets.json）。"""
        pname = current_provider_name(self.settings)
        ai = self.settings.setdefault("ai", {})
        if not isinstance(ai, dict):
            ai = {}
            self.settings["ai"] = ai
        providers = ai.setdefault("providers", {})
        if not isinstance(providers, dict):
            providers = {}
            ai["providers"] = providers
        entry = providers.setdefault(pname, {})
        if not isinstance(entry, dict):
            entry = {}
            providers[pname] = entry
        has_key = bool(str(entry.get("api_key") or "").strip())
        raw = self._prompt_ai_key_dialog(pname, has_key)
        if raw is None:
            self.say("已取消改密钥", urgent=True, life=90)
            return
        key = str(raw).strip()
        # 去掉误粘贴的引号/空白
        key = key.strip(" \t\r\n\"'")
        if not key:
            self.say("密钥为空,未改动", urgent=True, life=100)
            return
        try:
            save_provider_api_key(self.app_root, pname, key)
        except OSError:
            self.say("保存密钥失败", urgent=True)
            return
        entry["api_key"] = key
        # 确保 secrets 存在后，settings 里不再持久化 key
        save_settings(self.app_root, self.settings)
        # 配好密钥后默认打开 AI，方便立刻试用
        ai["enabled"] = True
        save_settings(self.app_root, self.settings)
        masked = key[:4] + "…" + key[-4:] if len(key) > 10 else "****"
        self.say(f"{pname}密钥已更新({masked})", urgent=True, life=140)
        self.fx("star", 3)

    def say_help(self):
        self.bubbles.clear()
        if self.settings.get("simple_mode", True):
            self.bubbles.push_many([
                "极简:上头摸·菜单投喂/召唤/纸箱/睡觉",
                "右键也可睡觉 · 点菜单「更多」看全部",
                "H帮助 · 关极简:菜单「关闭极简模式」",
            ], life=160)
        else:
            self.bubbles.push_many([
                "上头摸·下身逗·双击跑·连摸踩奶",
                "N纸箱 C回窝 E打猎 Q观鸟 Z跑酷",
                "U露肚 L激光 V炸毛 X扑击 K召唤",
                "⌥M连喵 ⌥S晒太阳 ⌥R抓挠 ⌥G送礼",
                "⌥T死盯 ⌥N推桌 ⌥H蹭头 ⌥C颤叫 ⌥I傲娇",
                "⌥K踩奶 ⌥B舔毛 · 菜单也可点猫咪互动",
                "H帮助 T故事 ,对喷 · Ctrl+Alt+R召唤",
            ], life=150)

    def _check_sys_alerts(self):
        """周期性检查资源，过高时嘀咕一声。"""
        if self._stealth or self._meeting_quiet():
            return
        now = time.time()
        if now < self._next_sys_check:
            return
        lo = float(self.settings.get("sys_check_interval_min") or 50)
        hi = float(self.settings.get("sys_check_interval_max") or 90)
        self._next_sys_check = now + random.uniform(min(lo, hi), max(lo, hi))
        if not self.settings.get("sys_alerts", True):
            refresh_net_sample()
            return
        if not PSUTIL_OK:
            return
        if self.bubbles.current or self.bubbles._q:
            return
        if self.brain.state in (State.SLEEP, State.DRAGGED, State.LASER, State.PANIC):
            return
        # interval=0 避免卡住主循环；网速靠缓存采样
        s = sample_system(interval_cpu=0.0)
        alerts = sys_alert_messages(s)
        if not alerts:
            refresh_net_sample()
            return
        msg = alerts[0]
        if msg == self._last_sys_alert:
            return
        self._last_sys_alert = msg
        self._note_progress(sys_alert_count=1)
        support = self._buddy_support_active()
        if support:
            # 月结应援优先：只轻声嘀咕，不做旋转/缩身/吓跑等抓眼球表演
            soft = self._sys_alert_support_line(s, msg)
            self.say(soft, life=110)
            self.fx("star", 1)
            return
        self.say(msg, life=150)
        # 监控 → 表演联动（非应援）
        if s and s["cpu"] >= 85:
            self.fx("dust", 8)
            self.roach.spin_vel = 14
            self.brain.react_spin(12)
        elif s and s["mem_pct"] >= 85:
            self.fx("dust", 5)
            self.roach.target_scale = 0.9
        elif s and s["disk_pct"] >= 85:
            self.fx("crumb", 4)
        else:
            self.fx("dust", 2)
        if s and s.get("net_down", 0) >= 8 * 1024 * 1024:
            self.fx("star", 5)
            self.brain._set(State.CURIOUS, 60)
        if s and (s["cpu"] >= 90 or s["mem_pct"] >= 92):
            if self.brain.state == State.HIDE:
                self.brain.go_hide(scramble=True)
            else:
                self.brain._set(State.SCARED, 40)

    def _sys_alert_support_line(self, s: dict | None, fallback: str) -> str:
        """应援模式下的安静监控提示（陪伴语气，不渲染焦虑）。"""
        if not s:
            return "我在,慢慢来"
        if s.get("cpu", 0) >= 85:
            return "机器也忙,你辛苦了"
        if s.get("mem_pct", 0) >= 80:
            return "内存紧了,歇口气也行"
        if s.get("disk_pct", 0) >= 85:
            return "磁盘有点满,不急"
        return fallback if len(fallback) <= 16 else "我看着呢,别慌"

    def _check_worker_schedule(self):
        """到点提醒：打卡、喝水、午饭、下班等。"""
        if self._stealth or self._meeting_quiet():
            return
        if not self.settings.get("worker_reminders", True) and not self.settings.get("finance_reminders", True):
            return
        if self.bubbles.current or self.bubbles._q:
            return
        if self.brain.state in (State.SLEEP, State.DRAGGED, State.LASER):
            return
        text = worker_schedule_due(self._worker_fired)
        if text:
            # 财务向文案可单独关掉
            finance_keys = ("财务", "对账", "报销", "发票", "月结", "凭证", "头寸", "税务", "发薪")
            is_fin = any(k in text for k in finance_keys)
            if is_fin and not self.settings.get("finance_reminders", True):
                return
            if (not is_fin) and not self.settings.get("worker_reminders", True):
                return
            self.say(text, life=180)
            self.fx("star", 3)
            if any(k in text for k in (
                "饭", "下班", "打卡", "茶", "站会", "复盘", "对齐", "收工",
                "财务", "对账", "报销", "发票", "月结", "凭证", "头寸", "税务", "发薪",
            )):
                self.brain._set(State.GREET, 80)

    def _check_hourly_chime(self):
        if self._stealth or self._meeting_quiet():
            return
        now = datetime.now()
        if now.minute == 0 and now.hour != self._last_hour_chime:
            self._last_hour_chime = now.hour
            if not is_workday(now):
                return
            if not self.settings.get("worker_reminders", True) and not self.settings.get("finance_reminders", True):
                return
            # 关键整点用打工人提示，少说废话
            tips = {
                9: "开工打卡!今日头寸看一眼",
                10: "喝水时间·进项发票收了吗",
                11: "站起来,应收催一催",
                12: "去吃饭吧,先干饭",
                14: "午后困了?报销别积压",
                15: "下午茶·银企对账了吗",
                16: "同步进度·未达账项清清",
                17: "收尾复盘·凭证审完没",
                18: "可以下班了,回单归档",
                20: "别卷太晚,月结也要睡",
                21: "打工人也该休息",
            }
            if now.hour in tips:
                self.say(tips[now.hour], life=140)
                self.fx("star", 4)
            elif 9 <= now.hour <= 20 and random.random() < 0.45:
                pick = random.random()
                if pick < 0.4 and self.settings.get("finance_reminders", True):
                    self.say(finance_random_tip(), life=130)
                elif pick < 0.7 and self.settings.get("worker_reminders", True):
                    self.say(worker_buzzword(), life=130)
                elif self.settings.get("worker_reminders", True):
                    self.say(worker_random_tip(), life=130)

    def _check_proactive(self):
        if self._stealth or self._meeting_quiet():
            return
        now = time.time()
        if now < self._next_proactive:
            return
        iv = float(self.settings.get("interaction_interval_sec") or 300)
        self._next_proactive = now + random.uniform(iv * 1.2, iv * 2.0)
        if self.brain.state in (State.SLEEP, State.DRAGGED, State.RUN, State.FOLLOW, State.LASER):
            return
        if self.bubbles.current or self.bubbles._q:
            return
        # 趴窝时几乎不主动出来，顶多嘀咕一声
        if self.brain.state == State.HIDE:
            if random.random() < 0.4 and is_workday():
                use_fin = self.settings.get("finance_reminders", True) and random.random() < 0.55
                use_work = self.settings.get("worker_reminders", True)
                if use_fin:
                    self.say(finance_random_tip(), life=120)
                elif use_work:
                    self.say(worker_random_tip(), life=120)
            return
        r = random.random()
        if r < 0.15:
            self.brain.react_dance()
            self.fx("star", 6)
        elif r < 0.3:
            self.brain._set(State.CURIOUS, 90)
        elif r < 0.55 and is_workday() and self.settings.get("finance_reminders", True):
            self.say(finance_random_tip(), life=150)
            self.fx("star", 3)
        elif r < 0.75 and is_workday() and self.settings.get("worker_reminders", True):
            self.say(worker_random_tip(), life=150)
            self.fx("star", 3)
        else:
            self.maybe_say(random.choice(["喝口水?", "摸摸猫?", "小鱼干?"]), chance=0.4)
            self.brain.go_hide()

    def _check_idle_showcase(self):
        """周期随机表演：短暂现身 + 一句闲聊（对标 DeskTopPet 定时切动作/文字）。"""
        if self._stealth or self._meeting_quiet():
            return
        if not self.settings.get("idle_showcase", True):
            return
        now = time.time()
        if now < self._next_showcase:
            return
        lo = float(self.settings.get("idle_showcase_min") or 45)
        hi = float(self.settings.get("idle_showcase_max") or 90)
        self._next_showcase = now + random.uniform(min(lo, hi), max(lo, hi))
        if self.dragging or self.right_dragging:
            return
        if self.buddy and self.buddy.bantering:
            return
        if self.brain.state in (State.SLEEP, State.DRAGGED, State.LASER, State.PANIC, State.RUN, State.FOLLOW):
            return
        if self.bubbles.current or self.bubbles._q:
            return

        pool = (
            list(PACKS.pool("chat", []))
            + Bubble.CHAT_PHRASES[:12]
            + [pick_showcase_line() for _ in range(3)]
        )
        line = random.choice(pool) if pool else pick_showcase_line()

        def apply_line(text: str):
            if self.brain.state == State.HIDE:
                if random.random() < 0.55:
                    self.say(text, life=110)
                    return
                self.roach.target_alpha = 255
                self.brain.react_peek()
                self.say(text, life=120)
                self.fx("star", 2)
                return
            self.roach.target_alpha = 255
            act = random.random()
            if act < 0.14:
                self.brain.react_peek()
            elif act < 0.26:
                self.do_groom()
                self.say(text, life=120)
                return
            elif act < 0.38:
                self.do_meow()
                return
            elif act < 0.48:
                self.do_sunbathe()
                return
            elif act < 0.58:
                self.do_stare()
                return
            elif act < 0.68:
                self.do_headbutt()
                return
            elif act < 0.78:
                self.brain.react_pose()
            elif act < 0.88:
                self.brain.react_dance()
                self.fx("star", 4)
            else:
                self.do_scratch()
                return
            self.say(text, life=120)

        if self._ai_available() and not self._ai_busy and random.random() < 0.45:
            ctx = self._ai_context()

            def worker():
                try:
                    text = generate_line(self.settings, "showcase", ctx)
                except LLMError:
                    text = line
                self._pending_ai.append(("say", text, 120, False))

            # 先做动作，台词异步到达
            if self.brain.state != State.HIDE or random.random() >= 0.55:
                self.roach.target_alpha = 255
                if self.brain.state == State.HIDE:
                    self.brain.react_peek()
                    self.fx("star", 2)
                else:
                    act = random.random()
                    if act < 0.22:
                        self.brain.react_peek()
                    elif act < 0.42:
                        self.do_groom()
                    elif act < 0.62:
                        self.brain.react_pose()
                    elif act < 0.80:
                        self.brain.react_dance()
                        self.fx("star", 4)
                    else:
                        self.brain.react_spin(12)
                        self.roach.spin_vel = 12
                        self.fx("dust", 3)
            if self._run_ai("showcase", worker, thinking=""):
                return

        apply_line(line)

    def _check_rest_reminder(self):
        """每小时休息提醒（对标 DeskTopPet haveRest）。"""
        if self._stealth or self._meeting_quiet():
            return
        if not self.settings.get("rest_reminder", True):
            return
        now = time.time()
        if now < self._next_rest:
            return
        iv = float(self.settings.get("rest_reminder_interval_sec") or 3600)
        self._next_rest = now + max(60.0, iv)
        if self.brain.state in (State.SLEEP, State.DRAGGED, State.LASER, State.PANIC):
            self._next_rest = now + 180
            return
        if self.bubbles.current or self.bubbles._q:
            self._next_rest = now + 90
            return
        hour = datetime.now().hour
        if hour < 8 or hour >= 23:
            return
        self.do_rest_break()

    def _check_care_reminders(self):
        """护眼/喝水/伸展：可自定义间隔的轻量提醒。"""
        if self._stealth or self._meeting_quiet():
            return
        if not self.settings.get("care_reminders", True):
            return
        if self._rest_active:
            return
        if self.dragging or self.right_dragging:
            return
        if self.buddy and self.buddy.bantering:
            return
        if self.bubbles.current or self.bubbles._q:
            return
        if self.brain.state in (State.SLEEP, State.DRAGGED, State.LASER, State.PANIC, State.RUN, State.FOLLOW):
            return
        hour = datetime.now().hour
        if hour < 8 or hour >= 23:
            return
        now = time.time()
        interval_key = {
            "eye": "care_eye_sec",
            "water": "care_water_sec",
            "stretch": "care_stretch_sec",
        }
        defaults = {"eye": 1200, "water": 1800, "stretch": 2700}
        # 同一帧只催一种，优先到期最久的
        due: list[tuple[float, str]] = []
        for kind in ("eye", "water", "stretch"):
            nxt = float(self._next_care.get(kind) or 0)
            if now >= nxt:
                due.append((nxt, kind))
        if not due:
            return
        due.sort()
        kind = due[0][1]
        iv = float(self.settings.get(interval_key[kind]) or defaults[kind])
        self._next_care[kind] = now + max(120.0, iv)
        # 错开其它项，避免连发
        for other, t in list(self._next_care.items()):
            if other != kind and t <= now + 45:
                self._next_care[other] = now + 60 + random.uniform(20, 90)
        self.do_care_nudge(kind)

    def _check_worker_idle_nudge(self):
        """久坐轻推：间隔较长，只在工作时段。"""
        if self._stealth or self._meeting_quiet():
            return
        if not self.settings.get("worker_reminders", True):
            return
        now_ts = time.time()
        if now_ts < self._next_worker_idle:
            return
        self._next_worker_idle = now_ts + random.uniform(1500, 2400)  # 25–40 分钟
        now = datetime.now()
        if not is_workday(now) or not (9 <= now.hour <= 21):
            return
        if self.bubbles.current or self.bubbles._q:
            return
        if self.brain.state in (State.SLEEP, State.DRAGGED, State.FOLLOW, State.LASER):
            return
        tip = random.choice([
            "坐太久啦,起来晃晃",
            "看看远处,放松眼睛",
            "肩膀紧吗?转转",
            "喝口水再战",
            "保存进度了吗?",
            "对齐一下颈椎",
            "异步一下眼睛",
            "合法摸鱼两分钟",
            PACKS.pick("worker_buzz", WORKER_BUZZ),
        ])
        self.say(tip, life=160)
        if self.brain.state != State.HIDE:
            self.brain._set(State.CURIOUS, 100)
            self.fx("star", 3)

    def _check_mouse_near(self, mx: float, my: float):
        cx = self.x + WIN_W / 2
        cy = self.y + WIN_H / 2
        d = math.hypot(mx - cx, my - cy)
        near = d < 140
        if near and not self._near_mouse:
            if self.brain.state == State.HIDE and d < 90:
                now = time.time()
                if now - self._hide_scramble_at > 4.0:
                    # 被发现了：换个窝继续趴
                    self._hide_scramble_at = now
                    self.brain.go_hide(scramble=True)
                    self.fx("dust", 3)
                    self.maybe_say(random.choice(["换窝!", "别吓我!", "喵?!"]), chance=0.2)
            elif self.brain.state == State.IDLE:
                self.brain._set(State.CURIOUS, 90)
                self.roach.target_scale = 1.05
        if not near:
            self.roach.target_scale = 1.0
        self._near_mouse = near

    def _track_mouse_idle(self, mx: float, my: float):
        """更新鼠标静止计时；有明显移动则重置。"""
        now = time.time()
        if self._last_mouse_pos is None:
            self._last_mouse_pos = (mx, my)
            self._mouse_still_since = now
            return
        lx, ly = self._last_mouse_pos
        if math.hypot(mx - lx, my - ly) > 10:
            self._last_mouse_pos = (mx, my)
            self._mouse_still_since = now
            # 用户动了鼠标：取消未完成的寻访动作标记（若还在跑可继续，但不重复触发）
            return
        self._last_mouse_pos = (mx, my)

    def _check_mouse_seek(self, mx: float, my: float):
        """鼠标长时间不动时，跑去找指针并随机互动。"""
        self._track_mouse_idle(mx, my)
        if self._stealth or self._meeting_quiet():
            return
        if not self.settings.get("mouse_seek", True):
            return
        now = time.time()
        idle_need = float(self.settings.get("mouse_idle_sec") or 1800)
        if now - self._mouse_still_since < max(60.0, idle_need):
            return
        if now < self._next_mouse_seek:
            return
        if self.dragging or self.right_dragging or self._rest_active:
            return
        if self._seek_act:
            return
        if self.buddy and self.buddy.bantering:
            return
        if self.brain.state in (State.SLEEP, State.DRAGGED, State.LASER, State.PANIC, State.FOLLOW):
            return
        if self.bubbles.current or self.bubbles._q:
            # 等气泡空一点再来，稍后再试
            self._next_mouse_seek = now + 45
            return

        cooldown = float(self.settings.get("mouse_seek_cooldown_sec") or 900)
        self._next_mouse_seek = now + max(120.0, cooldown)
        self._mouse_still_since = now
        self.do_mouse_seek()

    def do_mouse_seek(self):
        """跑向鼠标，抵达后做随机互动。"""
        acts = (
            "wave", "knead", "dance", "groom", "chat",
            "laser", "yarn", "peek", "pose", "stretch",
            "meow", "sun", "scratch", "gift", "stare",
            "knock", "headbutt", "chirp", "ignore",
        )
        self._seek_act = random.choice(acts)
        self.roach.target_alpha = 255
        self.roach.belly = False
        self.roach.anim_override = None
        self.brain.react_call()
        # 跨屏跑过去可能较久，加长召唤时限
        self.brain.state_timer = max(int(self.brain.state_timer), 520)
        openers = [
            "找你玩!", "还在吗?", "摸鱼太久了!", "喵?",
            "起来动动!", "戳一下你~", "我来巡视了",
            "指针在这!", "陪我玩会儿", "久坐警告喵",
        ]
        self.say(random.choice(openers), urgent=True, life=130)
        self.fx("star", 5)

    def _maybe_finish_mouse_seek(self, mx: float, my: float):
        """抵达指针附近后执行寻访互动。"""
        if not self._seek_act:
            return
        cx = self.x + WIN_W / 2
        cy = self.y + WIN_H / 2
        d = math.hypot(mx - cx, my - cy)
        # 到了，或召唤态已切到开心（CALL 抵达逻辑）
        arrived = d < 90 or (
            self.brain.state == State.HAPPY and self._seek_act is not None
        )
        if not arrived and self.brain.state == State.CALL:
            return
        if not arrived:
            # 召唤被打断则放弃本次互动
            if self.brain.state not in (State.CALL, State.HAPPY, State.GREET, State.RUN):
                self._seek_act = None
            return

        act = self._seek_act
        self._seek_act = None
        self._perform_seek_act(act)

    def _perform_seek_act(self, act: str):
        """寻访抵达后的随机互动包。"""
        if act == "wave":
            self.brain._set(State.GREET, 100)
            self.roach.force_anim("waving", 4.0)
            self.fx("heart", 6)
            self.say(random.choice(["嗨!", "看到你了", "挥爪!", "摸摸?"]), urgent=True, life=120)
        elif act == "knead":
            self.do_knead()
        elif act == "dance":
            self.do_dance()
            self.say(random.choice(["蹦迪醒神!", "甩甩懒腰", "起来嗨!"]), urgent=True, life=110)
        elif act == "groom":
            self.do_groom()
        elif act == "chat":
            self.do_chat()
        elif act == "laser":
            self.do_laser()
            self.say("激光时间!", urgent=True, life=100)
        elif act == "yarn":
            self.do_yarn()
        elif act == "peek":
            self.do_peek()
            self.say(random.choice(["在干嘛?", "发呆呢?", "看你屏幕~"]), urgent=True, life=110)
        elif act == "pose":
            self.do_pose()
        elif act == "meow":
            self.do_meow()
        elif act == "sun":
            self.do_sunbathe()
        elif act == "scratch":
            self.do_scratch()
        elif act == "gift":
            self.do_gift()
        elif act == "stare":
            self.do_stare()
        elif act == "knock":
            self.do_knock()
        elif act == "headbutt":
            self.do_headbutt()
        elif act == "chirp":
            self.do_chirp()
        elif act == "ignore":
            self.do_ignore()
        else:  # stretch
            self.brain.react_dance()
            self.roach.force_anim("waving", 5.0)
            self.fx("star", 8)
            self.bubbles.clear()
            self.bubbles.push_many(
                [
                    random.choice(["久坐提醒!", "起来走走", "活动一下颈椎"]),
                    "我陪你伸个懒腰",
                    "喝口水也好",
                ],
                life=130,
            )

    def do_feed(self, feast: bool = False):
        self._note_user_act()
        self.brain.react_feed(feast=feast)
        self.roach.target_scale = 1.18 if feast else 1.12
        self.roach.belly = False
        self.roach.force_anim("waving", 2.5)
        self.fx("crumb", 12 if feast else 8)
        self._note_progress(feed_count=1)
        if feast:
            self.say(random.choice(["大餐!", "撑住了", "罐头幸福!", "小鱼干雨!"]), urgent=True)
        else:
            self.maybe_say(random.choice(Bubble.FEED_PHRASES), chance=0.35)

    def do_dance(self):
        self.brain.react_dance()
        self.roach.belly = False
        self.roach.force_anim("waving", 4.0)
        self.fx("star", 8)
        self.maybe_say(random.choice(["蹦迪喵!", "甩尾巴!", "猫步!"]), chance=0.35)

    def do_home(self):
        closing = self._maybe_support_close_ritual()
        sw, sh = get_desktop_size()
        self.brain.target_x = (sw - WIN_W) // 2
        self.brain.target_y = sh - WIN_H - 50
        self.brain._set(State.RUN, 180)
        self.brain.pet_streak = 0
        self.roach.belly = False
        if not closing:
            self.fx("star", 2)

    def do_hide(self):
        """回窝趴下（面团猫）。"""
        self.do_loaf()

    def do_loaf(self):
        """面团模式：回角落趴窝；途中用 Running，到了再 Waiting。"""
        closing = self._maybe_support_close_ritual()
        self.roach.belly = False
        # 已在窝里：原地面团，不重新选点乱跑
        if self.brain.state == State.HIDE:
            near = (
                self.brain.target_x is not None
                and math.hypot(self.x - self.brain.target_x, self.y - (self.brain.target_y or self.y)) < 24
            )
            if near:
                self.brain.vx = self.brain.vy = 0
                if not closing:
                    self.roach.force_anim("waiting", 5.0)
                    self.fx("star", 2)
                    self.maybe_say(random.choice(Bubble.LOAF_PHRASES), chance=0.4)
                return
        self.brain.go_hide(scramble=False)
        # 到站后再面团；移动中由 tick 强制 Running
        if not closing:
            self.roach.force_anim("waiting", 6.0)
            self.fx("star", 2)
            self.maybe_say(random.choice(Bubble.LOAF_PHRASES), chance=0.4)

    def do_belly(self):
        self.brain.react_belly()
        self.roach.belly = True
        self.roach.spin = 180
        self.roach.force_anim("waiting", 3.0)
        self.fx("star", 5)
        self.maybe_say(random.choice(["翻肚皮!", "信任你哦", "但别乱摸!", "暖呼呼"]), chance=0.45)

    def do_laser(self):
        self.brain.react_laser()
        self.roach.belly = False
        # 追光：移动中自然 Running，无需锁死动作
        self.roach.anim_override = None
        self.fx("star", 3)
        self.maybe_say(random.choice(["追光点!", "红点在哪!", "魂都被吸走了"]), chance=0.4)

    def do_follow_toggle(self):
        on = self.brain.react_follow_toggle()
        self.roach.belly = False
        self.fx("heart" if on else "dust", 4)
        self.maybe_say("跟着铲屎官" if on else "自己玩去", chance=0.4)

    def do_peek(self):
        """观鸟 / 张望。"""
        self.brain.react_peek()
        self.roach.belly = False
        self.roach.force_anim("waiting", 3.5)
        self.fx("star", 2)
        self.maybe_say(random.choice(Bubble.PEEK_PHRASES), chance=0.45)

    def do_forage(self):
        """打猎 / 玩毛线。"""
        if random.random() < 0.45:
            self.do_yarn()
            return
        self.brain.react_forage()
        self.roach.belly = False
        self.roach.anim_override = None
        self.fx("crumb", 4)
        self.maybe_say(random.choice(Bubble.FORAGE_PHRASES), chance=0.5)

    def do_yarn(self):
        """追毛线球。"""
        self.brain.react_forage()
        self.roach.belly = False
        self.roach.anim_override = None
        self.fx("star", 5)
        self.say(random.choice(Bubble.YARN_PHRASES), urgent=True, life=110)

    def do_zoomie(self):
        self.brain.react_zoomie()
        self.roach.belly = False
        self.roach.anim_override = None
        self.fx("dust", 6)
        self.maybe_say(random.choice(Bubble.ZOOMIE_PHRASES), chance=0.45)

    def do_panic(self):
        self.brain.react_panic()
        self.roach.belly = False
        self.roach.anim_override = None
        self.fx("dust", 10)
        self.say(random.choice(Bubble.PANIC_PHRASES), urgent=True, life=100)

    def do_pose(self):
        self.brain.react_pose()
        self.roach.belly = False
        self.roach.target_scale = 1.15
        self.roach.force_anim("review", 4.0)
        self.fx("star", 10)
        self.maybe_say(random.choice(Bubble.POSE_PHRASES), chance=0.5)

    def do_call(self):
        self.brain.react_call()
        self.roach.belly = False
        self.roach.force_anim("waving", 3.0)
        self.fx("heart", 5)
        self._note_progress(call_count=1)
        self.maybe_say(random.choice(Bubble.CALL_PHRASES), chance=0.45)

    def do_spar(self):
        """扑击玩具老鼠 / 假想敌。"""
        self.do_pounce()

    def do_pounce(self):
        self.brain.react_spin(22)
        self.roach.spin_vel = 18
        self.roach.belly = False
        self.roach.anim_override = None
        self.fx("dust", 8)
        self.maybe_say(random.choice(Bubble.POUNCE_PHRASES), chance=0.5)

    def do_knead(self):
        """踩奶。"""
        self.brain._set(State.HAPPY, 110)
        self.roach.belly = False
        self.roach.target_scale = 1.1
        self.roach.force_anim("waving", 4.0)
        self.fx("heart", 8)
        self.say(random.choice(Bubble.KNEAD_PHRASES), urgent=True, life=120)

    def do_groom(self):
        """舔毛整理仪表。"""
        self.brain._set(State.POSE, 120)
        self.roach.belly = False
        self.roach.force_anim("review", 4.0)
        self.fx("star", 4)
        self.maybe_say(random.choice(Bubble.GROOM_PHRASES), chance=0.55)

    def do_box(self):
        """钻纸箱：换窝跑过去；途中 Running，到站 Waiting。"""
        self.roach.belly = False
        self.fx("star", 4)
        self.say(random.choice(Bubble.BOX_PHRASES), urgent=True, life=110)
        self.brain.go_hide(scramble=True)
        self.roach.force_anim("waiting", 5.0)

    def do_meow(self):
        """连喵三声。"""
        self.brain._set(State.GREET, 110)
        self.roach.belly = False
        self.roach.force_anim("waving", 4.0)
        self.fx("star", 4)
        lines = random.sample(Bubble.MEOW_PHRASES, k=min(3, len(Bubble.MEOW_PHRASES)))
        self.bubbles.clear()
        self.bubbles.push_many(lines, life=90)

    def do_sunbathe(self):
        """晒太阳：趴光斑上。"""
        self.roach.belly = False
        self.brain._set(State.POSE, 180)
        self.roach.force_anim("waiting", 8.0)
        self.roach.target_scale = 1.08
        self.fx("star", 6)
        self.say(random.choice(Bubble.SUN_PHRASES), urgent=True, life=140)

    def do_scratch(self):
        """抓挠沙发 / 屏幕边。"""
        self.roach.belly = False
        self.brain._set(State.ZOOMIE, 70)
        self.brain._pick_target()
        self.roach.anim_override = None
        self.fx("dust", 8)
        self.say(random.choice(Bubble.SCRATCH_PHRASES), urgent=True, life=110)

    def do_gift(self):
        """叼来神秘礼物。"""
        self.brain._set(State.HAPPY, 120)
        self.roach.belly = False
        self.roach.force_anim("waving", 4.0)
        self.fx("heart", 10)
        self.fx("crumb", 6)
        self.brain.affection += 1
        self.say(random.choice(Bubble.GIFT_PHRASES), urgent=True, life=130)

    def do_stare(self):
        """死盯铲屎官。"""
        self.brain._set(State.CURIOUS, 160)
        self.roach.belly = False
        self.roach.force_anim("waiting", 6.0)
        self.fx("star", 2)
        self.bubbles.clear()
        self.bubbles.push_many(
            [
                random.choice(Bubble.STARE_PHRASES),
                "...",
                random.choice(["你先眨眼", "我赢了", "继续盯"]),
            ],
            life=100,
        )

    def do_knock(self):
        """把桌上东西推下去。"""
        self.roach.belly = False
        self.brain.react_spin(14)
        self.roach.spin_vel = 10
        self.fx("dust", 10)
        self.fx("crumb", 8)
        self.say(random.choice(Bubble.KNOCK_PHRASES), urgent=True, life=120)
        self.brain._set(State.HAPPY, 80)

    def do_headbutt(self):
        """蹭头 / 头槌示爱。"""
        self.brain.react_click()
        self.roach.belly = False
        self.roach.target_scale = 1.12
        self.roach.force_anim("waving", 3.5)
        self.fx("heart", 8)
        self._note_progress(pet_count=1)
        self.say(random.choice(Bubble.HEADBUTT_PHRASES), urgent=True, life=120)

    def do_chirp(self):
        """看见鸟/窗外颤叫。"""
        self.brain.react_peek()
        self.roach.belly = False
        self.roach.force_anim("waiting", 4.0)
        self.fx("star", 5)
        self.say(random.choice(Bubble.CHIRP_PHRASES), urgent=True, life=120)

    def do_ignore(self):
        """傲娇无视。"""
        self.roach.belly = False
        self.brain.go_hide(scramble=False)
        self.roach.force_anim("idle", 5.0)
        self.fx("dust", 2)
        self.say(random.choice(Bubble.IGNORE_PHRASES), urgent=True, life=120)

    def do_cat_random(self):
        """随机来一段猫咪专属互动。"""
        acts = (
            self.do_meow, self.do_sunbathe, self.do_scratch, self.do_gift,
            self.do_stare, self.do_knock, self.do_headbutt, self.do_chirp,
            self.do_ignore, self.do_knead, self.do_groom, self.do_box,
            self.do_yarn, self.do_loaf,
        )
        random.choice(acts)()

    def do_stroll(self):
        """自主散步。"""
        self.brain.react_stroll()
        self.roach.belly = False
        self.roach.hanging = False
        self.roach.anim_override = None
        self.fx("dust", 2)
        self.maybe_say(random.choice(Bubble.STROLL_PHRASES), chance=0.45, life=100)

    def do_daydream(self):
        """原地发呆。"""
        self.brain.react_daydream()
        self.roach.belly = False
        self.roach.hanging = False
        self.roach.force_anim("waiting", 5.0)
        self.fx("star", 2)
        self.maybe_say(random.choice(Bubble.DAYDREAM_PHRASES), chance=0.5, life=110)

    def do_nap(self):
        """自主打瞌睡。"""
        self.brain.react_nap()
        self.roach.belly = False
        self.roach.hanging = False
        self.roach.force_anim("waiting", 8.0)
        self.fx("star", 2)
        self.maybe_say(random.choice(Bubble.NAP_PHRASES), chance=0.55, life=120)

    def do_climb(self):
        """攀爬到屏幕上沿。"""
        self.brain.react_climb()
        self.roach.belly = False
        self.roach.hanging = False
        self.roach.anim_override = None
        self.fx("dust", 3)
        self.maybe_say(random.choice(Bubble.CLIMB_PHRASES), chance=0.5, life=110)

    def do_hang(self):
        """倒挂在屏幕边缘。"""
        self.brain.react_hang()
        self.roach.belly = False
        self.roach.hanging = True
        self.roach.force_anim("waiting", 6.0)
        self.fx("star", 3)
        self.maybe_say(random.choice(Bubble.HANG_PHRASES), chance=0.55, life=110)

    def _note_user_act(self):
        """记录用户互动，推迟自主行为。"""
        self._last_user_act = time.time()

    def _routine_mode(self) -> str:
        respect = bool(self.settings.get("autonomy_respect_focus", True))
        meeting = self._meeting_level if self.settings.get("meeting_silence", True) else ""
        return routine_mode(respect_focus=respect, meeting_level=meeting)

    def _set_stealth(self, on: bool, reason: str = "") -> None:
        """共享/投屏/截图时隐藏窗口，避免入镜或投屏翻车。"""
        if bool(on) == bool(self._stealth):
            if on and reason:
                self._stealth_reason = reason
            return
        self._stealth = bool(on)
        if on:
            self._stealth_reason = reason or self._stealth_reason or "hide"
            self.bubbles.clear()
            self.bubble = None
            if self.buddy:
                try:
                    self.buddy.bubbles.clear()
                    self.buddy._script = []
                except Exception:
                    pass
            if self._rest_active:
                self._rest_active = False
            self._hide_pet_window()
            label = {
                "sharing": "共享屏幕",
                "casting": "投屏/镜像",
                "screenshot": "截图",
            }.get(self._stealth_reason, self._stealth_reason)
            print(f"静默: 检测到{label}，已收起桌宠")
        else:
            self._show_pet_window()
            print("静默: 已恢复显示")
            self._stealth_reason = ""

    def _hide_pet_window(self) -> None:
        try:
            if IS_MAC and self.window is not None:
                self.window.orderOut_(None)
            elif IS_WIN and self.hwnd:
                import ctypes
                ctypes.windll.user32.ShowWindow(int(self.hwnd), 0)  # SW_HIDE
        except Exception:
            pass

    def _show_pet_window(self) -> None:
        try:
            if IS_MAC and self.window is not None:
                self._mac_last_origin = None
                sx, sy = self._screen_pos()
                self._mac_place_window(sx, sy, force_front=True)
            elif IS_WIN and self.hwnd:
                import ctypes
                user32 = ctypes.windll.user32
                user32.ShowWindow(int(self.hwnd), 4)  # SW_SHOWNOACTIVATE
                self._win_apply_pos()
        except Exception:
            pass

    def _check_meeting_silence(self) -> None:
        """共享/投屏/截图 → 收起；开会 → 安静档。截图热键每帧检查。"""
        # 启动后几秒内不因检测抖动立刻藏窗（Tahoe 上偶发误判）
        if time.time() - float(getattr(self, "_started_at", 0) or 0) < 4.0:
            return
        now = time.time()
        if not self.settings.get("meeting_silence", True):
            self._meeting_level = ""
            self._casting = False
            self._shot_tool = False
            self._shot_hide_until = 0.0
            self._meeting_busy = False
            self._meeting_end_egg_pending = False
            if self._stealth:
                self._set_stealth(False)
            return

        shot_hotkey = now < self._shot_hide_until

        if now >= self._next_meeting_check:
            self._next_meeting_check = now + 2.0
            info = detect_presence()
            prev_busy = bool(self._meeting_busy)
            self._meeting_level = str(info.get("meeting_level") or "")
            self._casting = bool(info.get("casting"))
            self._shot_tool = bool(info.get("screenshot"))
            busy = self._meeting_level in ("meeting", "sharing")
            if busy and not prev_busy:
                self._meeting_since = now
            elif prev_busy and not busy:
                self._on_meeting_session_end(now)
            self._meeting_busy = busy

        should_hide = (
            self._meeting_level == "sharing"
            or self._casting
            or self._shot_tool
            or shot_hotkey
        )
        if should_hide:
            self._stealth_clear_at = 0.0
            if self._meeting_level == "sharing":
                reason = "sharing"
            elif self._casting:
                reason = "casting"
            else:
                reason = "screenshot"
            self._set_stealth(True, reason=reason)
            return

        # 条件解除：截图快速恢复，共享/投屏稍缓避免闪烁
        if self._stealth:
            delay = 0.6 if self._stealth_reason == "screenshot" else 3.0
            if self._stealth_clear_at <= 0:
                self._stealth_clear_at = now + delay
            elif now >= self._stealth_clear_at:
                self._stealth_clear_at = 0.0
                self._set_stealth(False)
                self._flush_meeting_end_egg()
                if getattr(self, "_focus_end_pending", False):
                    self._finish_focus_pomodoro()
        else:
            self._flush_meeting_end_egg()
            if getattr(self, "_focus_end_pending", False):
                self._finish_focus_pomodoro()

    def _on_meeting_session_end(self, now: float | None = None) -> None:
        """开会/共享落下：挂起一句陪伴彩蛋（短闪误检不报）。"""
        now = time.time() if now is None else now
        started = float(getattr(self, "_meeting_since", 0) or 0)
        duration = (now - started) if started > 0 else 0.0
        if duration < 45.0:
            return
        if now < float(getattr(self, "_meeting_end_egg_at", 0) or 0):
            return
        if self._guide_active():
            return
        self._meeting_end_egg_at = now + 120.0
        self._meeting_end_egg_pending = True

    def _flush_meeting_end_egg(self) -> None:
        if not getattr(self, "_meeting_end_egg_pending", False):
            return
        if self._stealth:
            return
        self._meeting_end_egg_pending = False
        self._play_meeting_end_egg()

    def _play_meeting_end_egg(self) -> None:
        line = PACKS.pick("meeting_end", Bubble.MEETING_END_PHRASES)
        self.roach.target_alpha = 255
        self.fx("star", 3)
        self.say(line, urgent=True, life=150)

    def _meeting_quiet(self) -> bool:
        """安静门控：压制一切「非自身」主动气泡/表演。

        含：专注番茄钟（含结束仪式挂起 `_focus_end_pending`）、stealth 收起、
        开会、共享/投屏、系统专注模式。番茄钟与会议静默同一套规则。
        """
        if self._stealth:
            return True
        if self._focus_pomodoro_active():
            return True
        if getattr(self, "_focus_end_pending", False):
            return True
        if not self.settings.get("meeting_silence", True):
            return False
        if self._meeting_level in ("meeting", "sharing") or self._casting:
            return True
        if self.settings.get("autonomy_respect_focus", True):
            return system_focus_active() is True
        return False

    def _update_quiet_gate(self) -> None:
        """安静门控边沿：进入只闩上；离开则把主动计时全部重新起算（不积压连发）。"""
        quiet = self._meeting_quiet()
        was = bool(getattr(self, "_quiet_gate_on", False))
        if quiet and not was:
            self._quiet_gate_on = True
            return
        if (not quiet) and was:
            self._quiet_gate_on = False
            self._reschedule_proactive_after_quiet()
            return
        self._quiet_gate_on = quiet

    def _reschedule_proactive_after_quiet(self) -> None:
        """安静状态结束后重新计时：延后，不把安静期积压的闹钟一次打完。"""
        now = time.time()
        iv = float(self.settings.get("interaction_interval_sec") or 300)
        rest_iv = float(self.settings.get("rest_reminder_interval_sec") or 3600)
        self._next_rest = now + max(60.0, rest_iv)
        care_defaults = {"eye": 1200, "water": 1800, "stretch": 2700}
        care_keys = {
            "eye": "care_eye_sec",
            "water": "care_water_sec",
            "stretch": "care_stretch_sec",
        }
        for kind, sk in care_keys.items():
            civ = float(self.settings.get(sk) or care_defaults[kind])
            self._next_care[kind] = now + max(120.0, civ)
        lo_sc = float(self.settings.get("idle_showcase_min") or iv * 0.9)
        hi_sc = float(self.settings.get("idle_showcase_max") or iv * 1.1)
        self._next_showcase = now + random.uniform(min(lo_sc, hi_sc), max(lo_sc, hi_sc))
        lo_au = float(self.settings.get("autonomy_min") or iv * 0.9)
        hi_au = float(self.settings.get("autonomy_max") or iv * 1.1)
        self._next_autonomy = now + random.uniform(min(lo_au, hi_au), max(lo_au, hi_au))
        self._next_proactive = now + random.uniform(iv * 1.2, iv * 2.0)
        lo_b = float(self.settings.get("buddy_banter_min") or 120)
        hi_b = float(self.settings.get("buddy_banter_max") or 280)
        self._next_banter = now + random.uniform(min(lo_b, hi_b), max(lo_b, hi_b))
        self._next_worker_idle = now + random.uniform(1500, 2400)
        lo_sys = float(self.settings.get("sys_check_interval_min") or 50)
        hi_sys = float(self.settings.get("sys_check_interval_max") or 90)
        self._next_sys_check = now + random.uniform(min(lo_sys, hi_sys), max(lo_sys, hi_sys))
        # 鼠标寻访：安静期不算「久闲」，解除后重新累计静止时间
        self._mouse_still_since = now
        cool = float(self.settings.get("mouse_seek_cooldown_sec") or 900)
        self._next_mouse_seek = now + max(60.0, cool * 0.15)

    def _check_autonomy(self):
        """无人操作时按作息加权触发散步/发呆/瞌睡/攀爬/倒挂。"""
        if self._stealth or self._meeting_quiet():
            return
        if not self.settings.get("autonomy", True):
            return
        now = time.time()
        if now < self._next_autonomy:
            return
        lo = float(self.settings.get("autonomy_min") or 50)
        hi = float(self.settings.get("autonomy_max") or 120)
        self._next_autonomy = now + random.uniform(min(lo, hi), max(lo, hi))

        idle_need = float(self.settings.get("autonomy_idle_sec") or 45)
        if now - self._last_user_act < max(15.0, idle_need):
            return
        if self.dragging or self.right_dragging or self._rest_active:
            return
        if self.buddy and self.buddy.bantering:
            return
        if self.bubbles.current or self.bubbles._q:
            return
        st = self.brain.state
        if st not in (State.HIDE, State.IDLE):
            return
        # 窝里还在跑向目标时先别打断
        if st == State.HIDE and self.brain.target_x is not None:
            if math.hypot(self.x - self.brain.target_x, self.y - (self.brain.target_y or self.y)) > 14:
                return

        mode = self._routine_mode()
        if mode == "sleepish":
            weights = [
                (self.do_nap, 0.42),
                (self.do_daydream, 0.28),
                (self.do_stroll, 0.18),
                (self.do_hang, 0.07),
                (self.do_climb, 0.05),
            ]
        elif mode == "quiet":
            weights = [
                (self.do_daydream, 0.34),
                (self.do_nap, 0.26),
                (self.do_stroll, 0.24),
                (self.do_climb, 0.10),
                (self.do_hang, 0.06),
            ]
        else:  # active
            weights = [
                (self.do_stroll, 0.30),
                (self.do_climb, 0.22),
                (self.do_daydream, 0.20),
                (self.do_hang, 0.16),
                (self.do_nap, 0.12),
            ]
        r = random.random()
        acc = 0.0
        chosen = weights[-1][0]
        for fn, w in weights:
            acc += w
            if r <= acc:
                chosen = fn
                break
        chosen()
        # 避免与周期表演同帧抢戏
        iv = float(self.settings.get("interaction_interval_sec") or 300)
        self._next_showcase = max(
            self._next_showcase,
            now + random.uniform(max(60.0, iv * 0.15), max(90.0, iv * 0.25)),
        )

    def _cat_hotkey(self, chars: str) -> bool:
        """Alt/⌥ + 字母：猫咪专属快捷键。返回是否已处理。"""
        # 极简模式不暴露十多组 ⌥ 键，避免误触与记忆负担
        if self.settings.get("simple_mode", True):
            return False
        cat_map = {
            "m": self.do_meow,
            "s": self.do_sunbathe,
            "r": self.do_scratch,
            "g": self.do_gift,
            "t": self.do_stare,
            "n": self.do_knock,
            "h": self.do_headbutt,
            "c": self.do_chirp,
            "i": self.do_ignore,
            "k": self.do_knead,
            "b": self.do_groom,
            "y": self.do_yarn,
            "l": self.do_loaf,
            "p": self.do_pounce,
            "a": self.do_cat_random,
        }
        fn = cat_map.get(chars)
        if fn:
            self._note_user_act()
            fn()
            return True
        return False

    def _pygame_key_char(self, ev) -> str:
        """Windows：优先用物理键位，避免 Alt/输入法导致 unicode 为空或错码。"""
        key = ev.key
        if pygame.K_a <= key <= pygame.K_z:
            return chr(key)
        if pygame.K_0 <= key <= pygame.K_9:
            return chr(key)
        punct = {
            pygame.K_SEMICOLON: ";",
            pygame.K_SLASH: "/",
            pygame.K_LEFTBRACKET: "[",
            pygame.K_RIGHTBRACKET: "]",
            pygame.K_BACKSLASH: "\\",
            pygame.K_QUOTE: "'",
            pygame.K_COMMA: ",",
            pygame.K_PERIOD: ".",
            pygame.K_MINUS: "-",
            pygame.K_EQUALS: "=",
        }
        if key in punct:
            return punct[key]
        # 小键盘数字
        if pygame.K_KP0 <= key <= pygame.K_KP9:
            return chr(ord("0") + (key - pygame.K_KP0))
        ch = (ev.unicode or "").lower()
        return ch[:1] if ch else ""

    def _dispatch_char_key(self, chars: str) -> None:
        """Mac/Windows 共用的字符快捷键（不含修饰键专属猫互动）。"""
        if not chars:
            return
        self._note_user_act()
        # 极简：只保留最常用几个键
        if self.settings.get("simple_mode", True):
            simple_map = {
                "n": self.do_box,
                "k": self.do_call,
                "f": self.do_feed,
                "h": self.say_help,
                "s": self.say_status,
                "-": self.toggle_click_through,
                "=": self.cycle_skin,
            }
            fn = simple_map.get(chars)
            if fn:
                fn()
            return
        if chars == "p":
            self.brain.react_poke()
            self.fx("dust", 5)
            self.maybe_say(random.choice(Bubble.POKE_PHRASES), chance=0.35)
            return
        if chars == "r":
            self.brain.react_dblclick()
            self.fx("star", 4)
            self.maybe_say(random.choice(["冲!", "开跑!", "喵闪!"]), chance=0.25)
            return
        if chars == "n":
            self.do_box()
            return
        if chars == ",":
            self.do_banter()
            return
        if chars == ".":
            self.toggle_buddy()
            return
        mapping = {
            "d": self.say_date,
            "t": self.do_story,
            "w": self.say_weather,
            "s": self.say_status,
            "f": self.do_feed,
            "a": self.do_dance,
            "m": self.do_follow_toggle,
            "c": self.do_hide,
            "b": self.do_home,
            "u": self.do_belly,
            "l": self.do_laser,
            "g": self.say_worker_tip,
            "j": self.say_buzzword,
            "y": self.do_align,
            "1": self.do_standup,
            "2": self.do_review,
            "3": self.do_fish,
            "4": self.do_resist_pua,
            "5": self.say_finance_buzz,
            "6": self.do_month_close,
            "7": self.do_audit_panic,
            "8": self.do_reimburse,
            "9": self.do_tax_check,
            "0": self.do_payroll_day,
            ";": self.say_finance_tip,
            "/": self.say_sys_overview,
            "[": self.say_sys_cpu,
            "]": self.say_sys_mem,
            "\\": self.say_sys_disk,
            "'": self.say_sys_net,
            "h": self.say_help,
            "-": self.toggle_click_through,
            "=": self.cycle_skin,
            "q": self.do_peek,
            "e": self.do_forage,
            "z": self.do_zoomie,
            "k": self.do_call,
            "v": self.do_panic,
            "o": self.do_pose,
            "i": self.do_chat,
            "x": self.do_spar,
        }
        fn = mapping.get(chars)
        if fn:
            fn()

    # ── 坐标换算 ──────────────────────────────────────────

    def _sh(self) -> int:
        return get_desktop_size()[1]

    def _screen_pos(self) -> tuple[float, float]:
        # macOS Cocoa 窗口原点在左下；Windows / 逻辑坐标均为左上
        if IS_MAC:
            return self.x, self._sh() - self.y - WIN_H
        return self.x, self.y

    def _mac_place_window(self, sx: float, sy: float, *, force_front: bool = False) -> None:
        """更新 Mac 窗位；仅在移动或显式要求时 orderFront，避免每帧抢焦点卡顿。"""
        if self.window is None:
            return
        origin = (int(sx), int(sy))
        last = getattr(self, "_mac_last_origin", None)
        if last != origin:
            self.window.setFrameOrigin_(origin)
            self._mac_last_origin = origin
        if force_front:
            self.window.orderFrontRegardless()

    def _event_local(self, event) -> tuple[int, int]:
        if IS_WIN or getattr(event, "_is_pygame", False):
            loc = event.locationInWindow()
            return int(loc.x), int(loc.y)
        loc = self.view.convertPoint_fromView_(event.locationInWindow(), None)
        return int(loc.x), int(loc.y)

    def _global_mouse(self) -> tuple[float, float]:
        if IS_WIN:
            import ctypes
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return float(pt.x), float(pt.y)
        loc = NSEvent.mouseLocation()
        return loc.x, self._sh() - loc.y

    def _hit(self, mx: int, my: int) -> bool:
        if self._guide_hit_choice(mx, my):
            return True
        bx, by = PAD_X, ROACH_Y
        bw, bh = self.roach.sw, self.roach.sh
        if bx <= mx <= bx + bw and by <= my <= by + bh:
            return True
        if self.bubble and my < ROACH_Y:
            return True
        if self._guide_lines or self._guide_choices:
            # 引导层占窗口上半，保证可点
            if my < ROACH_Y + 56:
                return True
        return False

    def _guide_hit_choice(self, mx: int, my: int) -> str | None:
        for rect, cid in getattr(self, "_guide_btn_rects", []) or []:
            if rect.collidepoint(mx, my):
                return cid
        return None

    def _update_mouse_passthrough(self):
        """鼠标不在小猫上时穿透点击；强制穿透模式下始终穿透。"""
        if self.settings.get("click_through_force", False):
            self._pointer_over_pet = False
            if IS_MAC and self.window is not None:
                self.window.setIgnoresMouseEvents_(True)
                return
            if IS_WIN and self.hwnd:
                self._win_set_click_through(True)
            return
        mx, my = self._global_mouse()
        lx, ly = int(mx - self.x), int(my - self.y)
        over = (
            self.dragging or self.right_dragging
            or (0 <= lx < self.canvas.get_width() and 0 <= ly < self.canvas.get_height() and self._hit(lx, ly))
        )
        self._pointer_over_pet = bool(over)
        if IS_MAC and self.window is not None:
            self.window.setIgnoresMouseEvents_(not over)
            return
        if IS_WIN and self.hwnd:
            # Windows 用颜色键透明：品红像素本就可点穿，无需 WS_EX_TRANSPARENT。
            # 旧逻辑来回切换 TRANSPARENT 且未 FRAMECHANGED，会导致整窗永久点不中。
            self._win_set_click_through(False)

    def _win_set_click_through(self, enabled: bool):
        if enabled == self._win_click_through:
            return
        import ctypes
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020
        style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        if enabled:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style)
        # 必须 FRAMECHANGED，否则样式切换不生效（互动全废的常见原因）
        user32.SetWindowPos(
            self.hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        self._win_click_through = enabled

    # ── 鼠标 ──────────────────────────────────────────────

    def _modifiers(self, event) -> tuple[bool, bool, bool, bool]:
        """返回 (alt/option, shift, control, meta)。
        meta: Mac=⌘，Windows=Win。
        """
        if IS_WIN or getattr(event, "_is_pygame", False):
            mods = pygame.key.get_mods()
            return (
                bool(mods & pygame.KMOD_ALT),
                bool(mods & pygame.KMOD_SHIFT),
                bool(mods & pygame.KMOD_CTRL),
                bool(mods & pygame.KMOD_META),
            )
        flags = int(event.modifierFlags())
        option = bool(flags & (1 << 19))
        shift = bool(flags & (1 << 17))
        control = bool(flags & (1 << 18))
        command = bool(flags & (1 << 20))
        return option, shift, control, command

    def _mouse_mod_actions(
        self, alt: bool, shift: bool, control: bool, meta: bool, event=None
    ) -> bool:
        """点击修饰键：操作语义跨平台一致，键位按平台。
        Mac: ⌘召唤  Ctrl大餐  ⌥喂食  Shift跳舞；Ctrl+Shift 或 ⌘+⌥ 弹菜单
        Win: Ctrl大餐  Alt喂食  Shift跳舞；Ctrl+Shift 弹菜单（召唤用 K / Ctrl+Alt+R）
        """
        # 双端：Ctrl+Shift+点小猫 → 弹出菜单（托盘/菜单栏备用）
        if control and shift and self._chrome is not None:
            if IS_MAC:
                if self._chrome.pop_context_menu(event=event):
                    return True
            else:
                # Win pygame 事件：用当前光标屏幕坐标
                if self._chrome.pop_context_menu():
                    return True
        if IS_MAC:
            if meta and alt and event is not None:
                if self._chrome is not None and self._chrome.pop_context_menu(event=event):
                    return True
            if meta:
                self.do_call()
                return True
            if control:
                self.do_feed(feast=True)
                return True
        else:
            # Windows：Win 键召唤（常被系统吞）；Ctrl+Shift 已用于菜单
            if meta:
                self.do_call()
                return True
            if control:
                self.do_feed(feast=True)
                return True
        if alt:
            self.do_feed()
            return True
        if shift:
            self.do_dance()
            return True
        return False

    def on_mouse_down(self, event):
        mx, my = self._event_local(event)
        if not self._hit(mx, my):
            return
        if IS_WIN:
            self._win_focus_for_input()
            # 点过后 90 秒内即使鼠标稍稍移开也可按快捷键
            self._win_keys_until = time.time() + 90.0
        self._note_user_act()

        # 首次引导：只认气泡按钮，不挡拖拽以外的工作
        if self._guide_active() and self._guide_step >= 0:
            cid = self._guide_hit_choice(mx, my)
            if cid:
                self._on_guide_choice(cid)
                return
            # 点在猫身上仍可拖，不自动选题
            self.dragging = True
            self.drag_start = (event.locationInWindow().x, event.locationInWindow().y)
            self.press_time = time.time()
            self.press_pos = (mx, my)
            self.moved_while_press = False
            self.shake_accum = 0.0
            return

        # 睡觉时单击叫醒，稍后仍会回窝趴着
        if self.brain.state == State.SLEEP:
            self.brain._set(State.HAPPY, 50)
            self.roach.target_scale = 1.05
            self.fx("star", 3)
            self.dragging = True
            self.drag_start = (event.locationInWindow().x, event.locationInWindow().y)
            self.press_time = time.time()
            self.press_pos = (mx, my)
            self.moved_while_press = False
            self.shake_accum = 0.0
            self._rest_active = False
            return

        # 点击可取消当次休息提示
        if self._rest_active:
            self._rest_active = False
            self.bubbles.clear()
            self.say("好,继续撸代码", urgent=True, life=90)
            self.fx("heart", 3)
            self.dragging = True
            self.drag_start = (event.locationInWindow().x, event.locationInWindow().y)
            self.press_time = time.time()
            self.press_pos = (mx, my)
            self.moved_while_press = False
            self.shake_accum = 0.0
            return

        # 翻肚时点一下翻回来
        if self.brain.state == State.BELLY or self.roach.belly:
            self.roach.belly = False
            self.roach.spin = 0
            self.brain._set(State.HAPPY, 50)
            self.fx("heart", 4)
            return

        alt, shift, control, meta = self._modifiers(event)
        if self._mouse_mod_actions(alt, shift, control, meta, event):
            return

        now = pygame.time.get_ticks()
        if now - self.click_time < 350:
            self.click_count += 1
        else:
            self.click_count = 1
        self.click_time = now

        # 小猫正面：上半摸头，下半逗弄/吓跑
        head_side = my < ROACH_Y + self.roach.sh * 0.48

        if self.click_count >= 3:
            self.click_count = 0
            self.brain.pet_streak = 0
            self.brain.react_poke()
            self.roach.target_scale = 0.88
            self.fx("dust", 6)
            # 三连击：炸毛吐槽
            if random.random() < 0.35:
                opener = pick_story()[0]
                self.say(opener, urgent=True, life=110)
            elif self._ai_available() and not self._ai_busy and random.random() < 0.4:
                def worker():
                    try:
                        text = generate_line(self.settings, "poke", "被连戳尾巴")
                    except LLMError:
                        text = random.choice(Bubble.POKE_PHRASES)
                    self._pending_ai.append(("say", text, 120, True))
                self._run_ai("poke", worker, thinking="")
            else:
                self.maybe_say(random.choice(Bubble.POKE_PHRASES), chance=0.55)
        elif self.click_count == 2:
            self.brain.react_dblclick()
            self.roach.target_scale = 1.15
            self.roach.force_anim("running", 2.0)
            self.fx("star", 5)
            self.maybe_say(random.choice(["冲!", "跑酷!", "喵闪!", "起飞!"]), chance=0.3)
        elif head_side:
            self.brain.react_click()
            self.roach.target_scale = 1.08
            self.roach.force_anim("waving", 2.0)
            self.fx("heart", 5)
            self._note_progress(pet_count=1)
            streak = self.brain.pet_streak
            if streak >= 3 and streak % 3 == 0:
                self.do_knead()
                return
            if streak >= 5 and streak % 5 == 0:
                self.say(random.choice(["好感爆棚!", "还要摸!", f"连摸{streak}下~"]), urgent=True)
                self.fx("heart", 8)
            elif self._ai_available() and not self._ai_busy and random.random() < 0.2:
                def worker():
                    try:
                        text = generate_line(self.settings, "click", "被摸头")
                    except LLMError:
                        text = random.choice(Bubble.CLICK_PHRASES)
                    self._pending_ai.append(("say", text, 120, True))
                self._run_ai("click", worker, thinking="")
            else:
                self.maybe_say(random.choice(Bubble.CLICK_PHRASES), chance=0.35)
        else:
            self.brain.react_poke()
            self.roach.target_scale = 0.92
            self.fx("dust", 5)
            if self._ai_available() and not self._ai_busy and random.random() < 0.25:
                def worker():
                    try:
                        text = generate_line(self.settings, "poke", "戳尾巴")
                    except LLMError:
                        text = random.choice(Bubble.POKE_PHRASES)
                    self._pending_ai.append(("say", text, 120, True))
                self._run_ai("poke", worker, thinking="")
            else:
                self.maybe_say(random.choice(Bubble.POKE_PHRASES), chance=0.4)

        self.dragging = True
        self.drag_start = (event.locationInWindow().x, event.locationInWindow().y)
        self.press_time = time.time()
        self.press_pos = (mx, my)
        self.moved_while_press = False
        self.shake_accum = 0.0
        self.last_drag_dir = 0.0

    def on_mouse_up(self, event):
        if self.dragging:
            held = time.time() - self.press_time
            dist = math.hypot(self.x - self.prev_x, self.y - self.prev_y)
            # 长按：伸懒腰 / 跳舞
            if held > 0.55 and not self.moved_while_press:
                self.do_dance()
            elif self.shake_accum > 40:
                self.brain.react_spin(22)
                self.roach.spin_vel = 22
                self.fx("dust", 8)
                self.maybe_say(random.choice(["晕乎乎...", "天旋地转喵", "别摇了!"]), chance=0.3)
            else:
                self.brain.react_drop(self.x - self.prev_x, self.y - self.prev_y)
                if dist > 8:
                    self.fx("dust", 4)
            self.roach.target_scale = 1.0
        self.dragging = False
        self.shake_accum = 0.0

    def on_mouse_drag(self, event):
        if not self.dragging:
            return
        loc = event.locationInWindow()
        dx = loc.x - self.drag_start[0]
        # macOS 视图坐标与窗口拖动方向需翻转；Windows/pygame 同向
        raw_dy = loc.y - self.drag_start[1]
        dy = -raw_dy if IS_MAC else raw_dy
        if abs(dx) + abs(dy) > 3:
            self.moved_while_press = True
        # 检测左右甩动
        if abs(dx) > 2:
            direction = 1.0 if dx > 0 else -1.0
            if self.last_drag_dir and direction != self.last_drag_dir:
                self.shake_accum += abs(dx)
            self.last_drag_dir = direction
        self.x += dx
        self.y += dy
        self.drag_start = (loc.x, loc.y)
        self.x, self.y = self.brain.clamp(self.x, self.y)
        if IS_MAC and self.window is not None:
            sx, sy = self._screen_pos()
            self.window.setFrameOrigin_((sx, sy))
        elif IS_WIN:
            self._win_apply_pos()

    def on_right_click(self, event):
        mx, my = self._event_local(event)
        if not self._hit(mx, my):
            return
        if IS_WIN:
            self._win_focus_for_input()
            self._win_keys_until = time.time() + 90.0
        self._note_user_act()
        if self._guide_active() and self._guide_step >= 0:
            # 引导中右键不当睡觉/跳过，避免误触；仍可拖
            self.right_dragging = True
            self.right_drag_start = (event.locationInWindow().x, event.locationInWindow().y)
            return
        if self.brain.state == State.SLEEP:
            closing = self._maybe_support_close_ritual()
            self.brain.go_hide()
            if not closing:
                self.fx("star", 3)
        else:
            closing = self._maybe_support_close_ritual()
            self.brain._set(State.SLEEP, random.randint(400, 800))
            self.roach.belly = False
            self.roach.target_alpha = 255
            if not closing:
                self.maybe_say("Zzz", chance=0.2)
        self.right_dragging = True
        self.right_drag_start = (event.locationInWindow().x, event.locationInWindow().y)

    def on_right_drag(self, event):
        if not self.right_dragging:
            return
        # 右键拖：用力甩飞
        loc = event.locationInWindow()
        dx = loc.x - self.right_drag_start[0]
        raw_dy = loc.y - self.right_drag_start[1]
        dy = -raw_dy if IS_MAC else raw_dy
        self.x += dx * 1.4
        self.y += dy * 1.4
        self.right_drag_start = (loc.x, loc.y)
        self.x, self.y = self.brain.clamp(self.x, self.y)
        if IS_MAC and self.window is not None:
            sx, sy = self._screen_pos()
            self.window.setFrameOrigin_((sx, sy))
        elif IS_WIN:
            self._win_apply_pos()

    def on_right_up(self, event):
        if self.right_dragging:
            self.brain.react_drop(self.x - self.prev_x, self.y - self.prev_y)
            if math.hypot(self.x - self.prev_x, self.y - self.prev_y) > 10:
                self.fx("dust", 6)
        self.right_dragging = False

    def on_middle_click(self, event):
        mx, my = self._event_local(event)
        if not self._hit(mx, my):
            return
        # 一条合并信息，不刷屏
        parts = [date_phrase(), time_phrase()]
        if self._weather:
            parts.append(self._weather)
        self.say(" · ".join(parts), urgent=True, life=200)

    def on_scroll(self, event):
        mx, my = self._global_mouse()
        lx, ly = int(mx - self.x), int(my - self.y)
        if not (0 <= lx < WIN_W and 0 <= ly < WIN_H and self._hit(lx, ly)):
            return
        dy = float(event.scrollingDeltaY())
        if abs(dy) < 0.1:
            dy = float(event.deltaY())
        strength = max(8, min(28, abs(dy) * 3 + 10))
        self.roach.spin_vel = strength if dy >= 0 else -strength
        self.brain.react_spin(strength)
        self.fx("star", 3)

    def _handle_key(self, event) -> bool:
        """处理快捷键，返回 True 表示应退出。"""
        etype = event.type()
        if etype not in (10, 11, 12):
            return False
        try:
            code = event.keyCode()
        except Exception:
            return False
        if code == 53 and etype in (10, 12):
            self._persist()
            self._running = False
            if self._chrome is not None:
                try:
                    self._chrome.stop()
                except Exception:
                    pass
            return True
        if etype != 10:
            return False

        # 方向键 keyCode: 左123 右124 下125 上126
        if code == 123:
            self.brain.react_nudge(-3.5, 0)
            self.roach.set_facing(-1)
            return False
        if code == 124:
            self.brain.react_nudge(3.5, 0)
            self.roach.set_facing(1)
            return False
        if code == 125:
            self.brain.react_nudge(0, 3.5)
            self.roach.set_facing(0, 1)
            return False
        if code == 126:
            self.brain.react_nudge(0, -3.5)
            self.roach.set_facing(0, -1)
            self.roach.target_scale = 1.1
            return False
        if code == 49:  # Space
            self.brain.react_jump()
            self.roach.target_scale = 1.2
            self.fx("star", 4)
            return False

        chars = (event.charactersIgnoringModifiers() or "").lower()
        # ⌥ + 字母：仅猫咪专属，不再落入普通快捷键
        try:
            flags = int(event.modifierFlags())
            option = bool(flags & (1 << 19))
        except Exception:
            option = False
        if option:
            if chars:
                self._cat_hotkey(chars)
            return False
        self._dispatch_char_key(chars)
        return False

    # ── 渲染 ──────────────────────────────────────────────

    def _draw_bubble(self):
        # 引导步骤：自定义文案+按钮层（非阻塞）
        if self._guide_active() and self._guide_step >= 0 and (
            self._guide_lines or self._guide_choices
        ):
            self._draw_guide_panel()
            return
        if not self.bubble:
            return
        text = self.bubble.text
        font = self.font if len(text) <= 12 else self.font_sm
        rendered = font.render(text, True, (30, 30, 30))
        tw, th = rendered.get_size()
        pad = 8
        bw, bh = tw + pad * 2, th + pad * 2
        alpha = min(255, self.bubble.life * 4)
        total_h = bh + 10
        b = pygame.Surface((bw + 4, total_h), pygame.SRCALPHA)
        r = pygame.Rect(0, 0, bw, bh)
        pygame.draw.rect(b, (255, 255, 255, alpha), r, border_radius=8)
        pygame.draw.rect(b, (160, 160, 160, alpha), r, 1, border_radius=8)
        cx = bw // 2
        pygame.draw.polygon(
            b, (255, 255, 255, alpha),
            [(cx - 6, bh), (cx + 6, bh), (cx, bh + 9)],
        )
        b.blit(rendered, (pad, pad))
        bx = max(2, min(WIN_W - b.get_width() - 2, (WIN_W - b.get_width()) // 2))
        self.canvas.blit(b, (bx, 4))

    def _draw_guide_panel(self) -> None:
        """首次引导：气泡文案 + 可点小按钮。"""
        font = self.font_sm
        lines = list(self._guide_lines or [])
        choices = list(self._guide_choices or [])
        pad = 6
        gap = 4
        max_w = WIN_W - 8
        # 排版文案
        renders = [font.render(t, True, (35, 35, 40)) for t in lines if t]
        text_h = sum(r.get_height() for r in renders) + max(0, len(renders) - 1) * 2
        # 按钮：每行最多 2 个
        btn_rows: list[list[dict]] = []
        row: list[dict] = []
        for ch in choices:
            row.append(ch)
            if len(row) >= 2:
                btn_rows.append(row)
                row = []
        if row:
            btn_rows.append(row)
        btn_h = 18
        btns_h = len(btn_rows) * (btn_h + gap) if btn_rows else 0
        bh = pad + text_h + (8 if btn_rows else 0) + btns_h + pad
        bw = max_w
        panel = pygame.Surface((bw, bh + 8), pygame.SRCALPHA)
        alpha = 245
        pygame.draw.rect(panel, (255, 255, 255, alpha), pygame.Rect(0, 0, bw, bh), border_radius=8)
        pygame.draw.rect(panel, (150, 150, 155, alpha), pygame.Rect(0, 0, bw, bh), 1, border_radius=8)
        y = pad
        for r in renders:
            x = max(pad, (bw - r.get_width()) // 2)
            panel.blit(r, (x, y))
            y += r.get_height() + 2
        if btn_rows:
            y += 4
        self._guide_btn_rects = []
        panel_x = max(2, (WIN_W - bw) // 2)
        panel_y = 2
        for brow in btn_rows:
            n = len(brow)
            slot_w = (bw - pad * 2 - gap * (n - 1)) // max(1, n)
            x0 = pad
            for ch in brow:
                label = str(ch.get("label") or "")
                cid = str(ch.get("id") or "")
                tr = font.render(label, True, (40, 55, 80))
                br = pygame.Rect(x0, y, slot_w, btn_h)
                pygame.draw.rect(panel, (232, 240, 255, 255), br, border_radius=6)
                pygame.draw.rect(panel, (120, 150, 200, 255), br, 1, border_radius=6)
                panel.blit(
                    tr,
                    (
                        br.x + max(2, (br.w - tr.get_width()) // 2),
                        br.y + max(1, (br.h - tr.get_height()) // 2),
                    ),
                )
                # 画布绝对坐标供点击
                abs_r = pygame.Rect(panel_x + br.x, panel_y + br.y, br.w, br.h)
                self._guide_btn_rects.append((abs_r, cid))
                x0 += slot_w + gap
            y += btn_h + gap
        # 小三角
        cx = bw // 2
        pygame.draw.polygon(
            panel, (255, 255, 255, alpha),
            [(cx - 5, bh), (cx + 5, bh), (cx, bh + 7)],
        )
        self.canvas.blit(panel, (panel_x, panel_y))

    def paint(self):
        self.canvas.fill((0, 0, 0, 0))
        moving = math.hypot(self.brain.vx, self.brain.vy) > 0.45
        sleeping = self.brain.state == State.SLEEP

        self._draw_bubble()
        self.roach.draw(self.canvas, PAD_X, ROACH_Y, moving, sleeping)
        if self.buddy and self.buddy.visible:
            self.buddy.draw(self.canvas, PAD_X, ROACH_Y, PET_W)

        if sleeping:
            t = pygame.time.get_ticks() // 500
            zzz = self.font.render("Z" * (1 + t % 3), True, (100, 100, 180))
            self.canvas.blit(zzz, (min(WIN_W, self.canvas.get_width()) - 36, 6))

        if self.brain.state == State.LASER:
            mx, my = self._global_mouse()
            lx, ly = int(mx - self.x), int(my - self.y)
            cw, ch = self.canvas.get_size()
            if 0 <= lx < cw and 0 <= ly < ch:
                pygame.draw.circle(self.canvas, (255, 60, 60, 180), (lx, ly), 4)

        if IS_MAC:
            self._nsimage = canvas_to_nsimage(self.canvas)
        elif IS_WIN and self.screen is not None:
            self.screen.fill(_WIN_COLORKEY)
            self.screen.blit(self.canvas, (0, 0))
            pygame.display.update()

    def _refresh_view(self):
        if IS_MAC and self.view is not None:
            self.view.setNeedsDisplay_(True)
            self.view.displayIfNeeded()

    def tick(self):
        self.brain.refresh_screen(*get_desktop_size())
        mx, my = self._global_mouse()
        busy = self.dragging or self.right_dragging

        b = self.brain.update(mx, my, self.x, self.y, busy)
        if b:
            self.bubbles.push(b.text, b.life)

        self._flush_pending_say()
        self._drain_commands()
        if self._chrome is not None:
            self._chrome.mac_status_tick()
        self.bubble = self.bubbles.tick()

        self._check_meeting_silence()
        self._check_focus_pomodoro()
        self._update_quiet_gate()
        self._check_onboarding_timeout()
        if self._guide_active():
            # 引导期间不抢戏：跳过主动喧哗；答谢气泡仍可 tick
            if not busy:
                self.x, self.y = self.brain.clamp(self.x, self.y)
            sx, sy = self._screen_pos()
            if IS_MAC and self.window is not None:
                self._mac_place_window(sx, sy)
            elif IS_WIN:
                self._win_apply_pos()
            self._update_mouse_passthrough()
            self.paint()
            self._refresh_view()
            self.prev_x, self.prev_y = self.x, self.y
            return
        self._check_hourly_chime()
        self._check_worker_schedule()
        self._check_worker_idle_nudge()
        self._check_sys_alerts()
        self._check_proactive()
        self._check_autonomy()
        self._check_idle_showcase()
        self._check_rest_reminder()
        self._check_care_reminders()
        self._check_buddy_banter()
        self._watch_support_session()
        self._check_mouse_near(mx, my)
        self._check_mouse_seek(mx, my)
        self._maybe_finish_mouse_seek(mx, my)

        if self._stealth:
            # 收起期间仍更新脑回路，但不把窗口拉回前台
            if not busy:
                spd = math.hypot(self.brain.vx, self.brain.vy)
                has_target = (
                    self.brain.target_x is not None
                    and math.hypot(
                        self.x - self.brain.target_x,
                        self.y - (self.brain.target_y or self.y),
                    ) > 10
                )
                if spd <= 0.45 and not has_target:
                    self.brain.vx = self.brain.vy = 0
                self.x += self.brain.vx
                self.y += self.brain.vy
                self.x, self.y = self.brain.clamp(self.x, self.y)
            self.prev_x, self.prev_y = self.x, self.y
            return

        if not busy:
            # 无有效目标时清掉残余速度，避免坐姿微平移
            spd = math.hypot(self.brain.vx, self.brain.vy)
            has_target = (
                self.brain.target_x is not None
                and math.hypot(
                    self.x - self.brain.target_x,
                    self.y - (self.brain.target_y or self.y),
                ) > 10
            )
            if spd <= 0.45 and not has_target:
                self.brain.vx = self.brain.vy = 0
            self.x += self.brain.vx
            self.y += self.brain.vy
            self.x, self.y = self.brain.clamp(self.x, self.y)

        moving = math.hypot(self.brain.vx, self.brain.vy) > 0.45
        if moving:
            self.roach.set_facing(self.brain.vx, self.brain.vy)

        st = self.brain.state
        self.roach.belly = st == State.BELLY
        # 倒挂：到达边缘后才翻转为挂着；移动途中保持正常
        if st == State.HANG:
            at_edge = (
                self.brain.target_x is not None
                and math.hypot(self.x - self.brain.target_x, self.y - (self.brain.target_y or self.y)) < 14
            )
            self.roach.hanging = bool(at_edge) or (
                self.brain.target_x is None and self.brain.vx == 0 and self.brain.vy == 0
            )
        else:
            self.roach.hanging = False
        hide_settled = False
        if st == State.HIDE:
            hide_settled = (
                self.brain.target_x is not None
                and math.hypot(self.x - self.brain.target_x, self.y - (self.brain.target_y or self.y)) < 12
            )
            # 回窝仍清晰可见，略缩小表示趴下
            self.roach.target_scale = 0.94 if hide_settled else 1.0
        elif st == State.CLIMB and not moving:
            self.roach.target_scale = 0.96
        elif st == State.HANG and self.roach.hanging:
            self.roach.target_scale = 1.0
        self.roach.target_alpha = 255
        self.roach.alpha = 255

        # 移动统一 Running；静止再按状态选动作
        anim = "running" if moving else cat_anim_for_state(st, False, hide_settled=hide_settled)
        self.roach.tick(
            moving, math.hypot(self.brain.vx, self.brain.vy),
            st == State.SLEEP, st == State.HAPPY,
            dancing=(st == State.DANCE) and not moving,
            spinning=(st == State.SPIN) and not moving,
            anim=anim,
        )

        if self.buddy and self.buddy.active:
            main_busy = bool(self.bubbles.current or self.bubbles._q)
            self.buddy.tick(main_busy)
            # 对喷结束淡出后收回双宠窗口宽度
            self._sync_layout()

        if st == State.HAPPY:
            self.roach.target_scale = 1.0 + math.sin(pygame.time.get_ticks() * 0.015) * 0.06
        elif st == State.SLEEP:
            self.roach.target_scale = 0.95
        elif st == State.DANCE:
            self.roach.target_scale = 1.08
        elif st == State.POSE:
            self.roach.target_scale = 1.12 + math.sin(pygame.time.get_ticks() * 0.01) * 0.03
        elif st == State.ZOOMIE:
            self.roach.target_scale = 1.05
        elif st == State.PANIC:
            self.roach.target_scale = 0.9 + abs(math.sin(pygame.time.get_ticks() * 0.04)) * 0.12
        elif st == State.PEEK:
            self.roach.target_scale = 1.02

        sx, sy = self._screen_pos()
        if IS_MAC and self.window is not None:
            self._mac_place_window(sx, sy)
        elif IS_WIN:
            self._win_apply_pos()
        self._update_mouse_passthrough()
        self.paint()
        self._refresh_view()
        self.prev_x, self.prev_y = self.x, self.y
        # 周期性落盘，避免异常退出丢进度
        now = time.time()
        if now >= getattr(self, "_next_persist", 0):
            self._next_persist = now + 90
            self.progress["affection"] = int(self.brain.affection)
            save_progress(self.app_root, self.progress)

    def _print_help_banner(self):
        print("习性: 平时角落趴窝，互动才跑出来；鼠标凑近会换窝")
        print("产品: 菜单栏/托盘 | Ctrl+Alt+R召唤 /总览 P穿透 S状态 Q退出")
        if self.settings.get("simple_mode", True):
            print("极简模式(默认): 菜单点 摸头/投喂/召唤/纸箱/睡觉；其余在「更多互动/设置」")
            print("快捷键精简: N纸箱 K召唤 F投喂 H帮助 · 右键睡觉 · 上头摸 · Esc退出")
            print("关闭极简: 菜单「关闭极简模式」可恢复全部快捷键与扁平菜单")
            if IS_WIN:
                print("Windows: 托盘在任务栏右下(可能在 ^ 里) | Ctrl+Shift+点小猫也可弹菜单")
            else:
                print("Mac: 菜单栏右上角「猫」| 若无图标: Ctrl+Shift+点 或 ⌘+⌥+点小猫")
            return
        if IS_WIN:
            print("Windows: 鼠标放在小猫上（或刚点过）按 N/C/Alt+M… 即可，不必点控制台")
            print("Windows: 托盘在任务栏右下(可能在 ^ 里) | 显示桌面后会自动回来")
            print("点击修饰: Ctrl+Shift+点=菜单 | Ctrl+点=大餐 | Alt+点=喂食 | Shift+点=跳舞")
            print("召唤: K 或 Ctrl+Alt+R 或托盘「召唤过来」")
            print("猫咪专属: Alt+字母 (M连喵 S晒太阳 R抓挠 G送礼 T死盯 N推桌 … A随机)")
        else:
            print("点击修饰: ⌘点=召唤 | Ctrl点=大餐 | ⌥点=喂食 | Shift点=跳舞")
            print("菜单备用: Ctrl+Shift+点 或 ⌘+⌥+点小猫（Tahoe 菜单栏被挡时）")
            print("猫咪专属: ⌥+字母 (M连喵 S晒太阳 R抓挠 G送礼 T死盯 N推桌 … A随机)")
        print("设置: settings.json（气泡/提醒/穿透/话术包/皮肤）")
        print("打工: G提醒 J黑话混 Y对齐 | 1站会 2复盘 3摸鱼 4反PUA")
        print("监控: /总览  [CPU  ]内存  \\磁盘  '网络  (需 psutil)")
        print("财务: 5行话口头禅 6月结 7审计 8报销 9税务 0发薪 ;财务提醒")
        print("互动: 上头摸/下身逗 | 双击跑 | 连摸踩奶 | 滚轮转 | 右键睡 | 中键日期 | 方向键/空格")
        print("双宠: ,对喷  .开关会计猫 | Ctrl+Alt+B对喷")
        print("故事: T / Ctrl+Alt+T 故事大会 | 菜单开关休息提醒(约每小时)")
        print("养生: 菜单开关养生提醒(护眼/喝水/伸展) | 切换养生节奏 gentle/standard/strict")
        print("寻访: 鼠标约30分钟不动会跑来找你互动 | 菜单可开关")
        print("自主: 无人操作时散步/发呆/瞌睡/攀爬/倒挂 | 昼夜/专注/会议切换节奏 | 菜单可关")
        print("会议静默: 共享/投屏收起 · 截图快捷键躲闪 | 菜单可关")
        print("AI: Ctrl+Alt+A开关 | 菜单切换厂商/设置密钥 | secrets.json亦可")
        print("猫咪: N纸箱 C回窝 E打猎/毛线 Q观鸟 Z跑酷 V炸毛 O摆拍 X扑击")
        print("      U露肚 L激光 K召唤 M跟随 | -穿透 =皮肤 D日期 W天气 S状态 H帮助 Esc退出")
        print("      报时: 中键点小猫 或 菜单状态")
        print("菜单可「开启极简模式」收起冷门快捷键")

    def run(self):
        if IS_MAC:
            self._run_mac()
        else:
            self._run_win()

    def _run_mac(self):
        clock = pygame.time.Clock()
        app = NSApplication.sharedApplication()
        self._print_help_banner()
        # 确保窗口先出现在前台，再挂菜单栏
        if self.window is not None and not self._stealth:
            sx, sy = self._screen_pos()
            self._mac_place_window(sx, sy, force_front=True)
        while self._running:
            event = app.nextEventMatchingMask_untilDate_inMode_dequeue_(
                0xFFFFFFFF,
                NSDate.dateWithTimeIntervalSinceNow_(0.0),
                NSDefaultRunLoopMode,
                True,
            )
            if event:
                if self._handle_key(event):
                    break
                app.sendEvent_(event)
            self.tick()
            clock.tick(FPS)

    def _run_win(self):
        clock = pygame.time.Clock()
        self._print_help_banner()
        while self._running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self._running = False
                    break
                if ev.type == pygame.KEYDOWN:
                    if IS_WIN:
                        if ev.key == pygame.K_ESCAPE:
                            self._persist()
                            if self._chrome is not None:
                                try:
                                    self._chrome.stop()
                                except Exception:
                                    pass
                            self._running = False
                            break
                        # pynput 桥失败时回退 pygame 焦点键，与 Mac 本地键能力对齐
                        chrome_ok = (
                            self._chrome is not None
                            and self._chrome.win_focus_keys_alive()
                        )
                        if not chrome_ok:
                            if self._handle_pygame_key(ev):
                                self._running = False
                                break
                    elif self._handle_pygame_key(ev):
                        self._running = False
                        break
                elif ev.type == pygame.MOUSEBUTTONDOWN:
                    pe = _PygameBridgeEvent(ev.pos, ev.button)
                    if ev.button == 1:
                        self.on_mouse_down(pe)
                    elif ev.button == 3:
                        self.on_right_click(pe)
                    elif ev.button == 2:
                        self.on_middle_click(pe)
                elif ev.type == pygame.MOUSEBUTTONUP:
                    pe = _PygameBridgeEvent(ev.pos, ev.button)
                    if ev.button == 1:
                        self.on_mouse_up(pe)
                    elif ev.button == 3:
                        self.on_right_up(pe)
                elif ev.type == pygame.MOUSEMOTION:
                    if self.dragging:
                        self.on_mouse_drag(_PygameBridgeEvent(ev.pos, 1))
                    elif self.right_dragging:
                        self.on_right_drag(_PygameBridgeEvent(ev.pos, 3))
                elif ev.type == pygame.MOUSEWHEEL:
                    pe = _PygameBridgeEvent(pygame.mouse.get_pos(), 0, wheel=ev.y)
                    self.on_scroll(pe)
            if not self._running:
                break
            self.tick()
            clock.tick(FPS)

    def _handle_pygame_key(self, ev) -> bool:
        """Windows/pygame 快捷键，返回 True 表示退出。"""
        if ev.key == pygame.K_ESCAPE:
            self._persist()
            if self._chrome is not None:
                try:
                    self._chrome.stop()
                except Exception:
                    pass
            return True
        # 方向键 / 空格
        if ev.key == pygame.K_LEFT:
            self.brain.react_nudge(-3.5, 0)
            self.roach.set_facing(-1)
            return False
        if ev.key == pygame.K_RIGHT:
            self.brain.react_nudge(3.5, 0)
            self.roach.set_facing(1)
            return False
        if ev.key == pygame.K_DOWN:
            self.brain.react_nudge(0, 3.5)
            self.roach.set_facing(0, 1)
            return False
        if ev.key == pygame.K_UP:
            self.brain.react_nudge(0, -3.5)
            self.roach.set_facing(0, -1)
            self.roach.target_scale = 1.1
            return False
        if ev.key == pygame.K_SPACE:
            self.brain.react_jump()
            self.roach.target_scale = 1.2
            self.fx("star", 4)
            return False

        chars = self._pygame_key_char(ev)
        mods = pygame.key.get_mods()
        # Alt + 字母：仅猫咪专属（用物理键位，不依赖 unicode）
        if mods & pygame.KMOD_ALT:
            if chars:
                self._cat_hotkey(chars)
            return False
        # Ctrl/Win 组合交给全局热键；焦点内单键走统一表
        self._dispatch_char_key(chars)
        return False


class _PygameBridgeEvent:
    """把 pygame 鼠标事件适配成现有 on_mouse_* 接口。"""

    _is_pygame = True

    def __init__(self, pos, button=1, wheel=0):
        self._x, self._y = int(pos[0]), int(pos[1])
        self._button = button
        self._wheel = wheel

    def locationInWindow(self):
        class _Loc:
            pass
        loc = _Loc()
        loc.x = self._x
        loc.y = self._y
        return loc

    def modifierFlags(self):
        return 0

    def scrollingDeltaY(self):
        return float(self._wheel)

    def deltaY(self):
        return float(self._wheel)


def create_pet() -> RoachPet:
    return RoachPet()


if __name__ == "__main__":
    # 打包后工作目录用 exe 旁；资源仍从 resource_dir / _MEIPASS 读
    os.chdir(app_dir())
    print("🐱 小猫桌宠已启动，按 Esc 退出")
    print(f"   平台: {sys.platform} | 工作目录: {os.getcwd()}")
    print(f"   资源目录: {_BASE_DIR}")
    try:
        create_pet().run()
    except Exception as exc:
        print(f"❌ 启动失败: {exc}")
        raise
    finally:
        pygame.quit()
    sys.exit()
