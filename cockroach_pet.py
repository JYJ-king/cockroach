"""桌面蟑螂宠物 — macOS AppKit / Windows 透明置顶窗口 + pygame 离屏绘制。"""

import math
import os
import queue
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from enum import Enum, auto

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

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
    evaluate_achievements,
    load_progress,
    load_settings,
    save_progress,
    save_settings,
)
from desktop_chrome import DesktopChrome
from accountant_buddy import AccountantBuddy
from story_mode import (
    CLICK_BANTER_PHRASES,
    POKE_BANTER_PHRASES,
    pick_rest_line,
    pick_showcase_line,
    pick_story,
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

CAPTION = "Roach"
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
SKIN_TINTS = {
    "gold": (1.25, 1.05, 0.55),
    "ghost": (0.75, 0.95, 1.2),
}
SPRITE_W = 130
PET_W, PET_H = 160, 180
BUBBLE_ZONE = 48
WIN_W = PET_W + 70
WIN_H = BUBBLE_ZONE + PET_H + 28
PAD_X = (WIN_W - PET_W) // 2
ROACH_Y = BUBBLE_ZONE + 6
BUDDY_SLOT_W = PET_W + 36
DUAL_WIN_W = WIN_W + BUDDY_SLOT_W

WALK_SPEED = 1.8
RUN_SPEED = 3.6
BOB_AMPLITUDE = 3.5
# 原图 cockroach.png：头（触角/前胸）在左上，翅尖（尾）在右下。
# 加载时按此角转正：头朝上、尾朝下。pygame 正角为逆时针。
SPRITE_ROTATE_DEG = -45
# 转正后本地朝向（与 set_facing 同一套 atan2(vy,vx)，屏坐标 y 向下）：
# 0=右, 90=下, ±180=左, -90=上。转正后头朝上 → -90。
SPRITE_UPRIGHT_HEADING = -90.0
LEG_COLOR = (138, 72, 32)
LEG_COLOR_DARK = (100, 48, 20)
ANTENNA_COLOR = (90, 55, 30)
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# Windows 色键透明（品红底会被抠掉）
_WIN_COLORKEY = (255, 0, 255)

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
        "morning": "早上好!",
        "noon": "中午好!",
        "afternoon": "下午好!",
        "evening": "晚上好!",
        "night": "夜深了哦~",
    }[period_of_day()]


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
        return random.choice(["打工人早上好", "今天也要稳住", "先喝口水开工", "对齐今天的自己"])
    if period == "noon":
        return random.choice(["中午要记得吃饭哦", "午休十分钟也好", "闭环之前先干饭"])
    if period == "afternoon":
        return random.choice(["下午继续干!", "摸鱼合法但护眼", "下午茶续命"])
    if period == "evening":
        return random.choice(["别卷太晚", "下班路上注意安全", "收工别忘打卡"])
    return random.choice(["夜班也辛苦了", "早点睡吧打工人", "线上稳住,人先睡"])


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


def sample_system(interval_cpu: float = 0.15) -> dict | None:
    """采集 CPU / 内存 / 磁盘 / 网络。失败返回 None。"""
    if not PSUTIL_OK:
        return None
    try:
        # 短间隔采样，气泡场景可接受
        cpu = float(psutil.cpu_percent(interval=interval_cpu))
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage(_disk_root())
        up, down = refresh_net_sample()
        # 再采一次让网速更稳（若首次）
        if up == 0 and down == 0:
            time.sleep(0.2)
            up, down = refresh_net_sample()
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
    """资源告警文案（偏蟑螂吐槽）。"""
    s = s or sample_system(0.05)
    if not s:
        return []
    msgs = []
    if s["cpu"] >= 85:
        msgs.append(f"CPU发烧{s['cpu']:.0f}%!少开点标签")
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


def get_desktop_size() -> tuple[int, int]:
    if IS_MAC and OBJC_OK:
        f = NSScreen.mainScreen().frame()
        return int(f.size.width), int(f.size.height)
    if IS_WIN:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        except Exception:
            pass
    # 回退：pygame 显示器信息
    try:
        if pygame.display.get_init():
            info = pygame.display.Info()
            if info.current_w > 0 and info.current_h > 0:
                return int(info.current_w), int(info.current_h)
    except Exception:
        pass
    return 1280, 800


def surface_to_nsimage(surface: pygame.Surface):
    """pygame Surface → NSImage（经 PNG 内存，不做垂直翻转）。"""
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
    PEEK = auto()      # 探头张望
    FORAGE = auto()    # 沿边觅食
    ZOOMIE = auto()    # 短暂疯跑
    PANIC = auto()     # 受惊乱窜
    POSE = auto()      # 摆拍定格
    CALL = auto()      # 被召唤靠近


class Bubble:
    PHRASES = [
        "爬爬爬~", "嘎吱嘎吱", "嘿嘿", "我是小强!",
        "找点吃的", "休息一会儿", "别开灯...", "缝里最安全",
    ]
    CLICK_PHRASES = CLICK_BANTER_PHRASES
    POKE_PHRASES = POKE_BANTER_PHRASES
    FEED_PHRASES = ["好吃!", "嚼嚼", "谢谢~", "还有吗?", "香!"]
    CHAT_PHRASES = [
        "今天键盘很香", "你又坐很久了", "我在角落看着你",
        "别踩到我~", "听说蟑螂活很久", "黑暗真舒服",
        "要不要喂一口?", "工作加油哦", "我去巡房了",
        "鼠标别晃太凶", "屏幕好亮啊",
        "对齐了吗打工人", "闭环了再摸鱼", "颗粒度再细点",
        "站会别超时", "需求又改了?", "保存了吗老板",
        "账对平了吗", "发票贴好没", "别反结账啊",
        "月结别熬夜", "报销我帮你盯",
    ]
    FORAGE_PHRASES = ["找屑屑...", "这边有味道", "觅食中", "发现面包渣?"]
    PANIC_PHRASES = ["喷雾!!", "要命!", "逃逃逃!", "关灯关灯!"]
    POSE_PHRASES = ["茄子~", "拍好看点", "我帅吗?", "定格!"]
    CALL_PHRASES = ["来了来了", "叫我?", "马上到", "干嘛呀"]
    ZOOMIE_PHRASES = ["疯了!", "Zoom!", "冲刺!!", "停不下来"]
    PEEK_PHRASES = ["谁在那?", "张望一下", "安全吗?", "探头~"]

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
    """短促视觉反馈：爱心 / 屑屑 / 星星，替代频繁说话。"""

    def __init__(self, x, y, kind: str = "heart"):
        self.x, self.y = float(x), float(y)
        self.vx = random.uniform(-1.2, 1.2)
        self.vy = random.uniform(-2.5, -0.8)
        self.life = random.randint(28, 48)
        self.kind = kind
        self.size = random.randint(3, 6)

    def tick(self) -> bool:
        self.x += self.vx
        self.y += self.vy
        self.vy += 0.08
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


def load_roach_sprite(width: int = SPRITE_W, skin: str = "default") -> pygame.Surface:
    """加载贴图：重建透明 → 转正 → 皮肤着色 → 缩放。"""
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
        raw.convert(),  # 先去掉坏 alpha 再缩放，避免透明黑渗进躯干
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


# ── 蟑螂渲染（照片贴图 + 轻量姿态动画）──────────────────

class RoachRenderer:
    """使用 cockroach.png；步态用轻微颠簸/摆动表现。"""

    def __init__(self, skin: str = "default"):
        self.skin = skin
        self.base = load_roach_sprite(SPRITE_W, skin=skin)
        self.sw, self.sh = self.base.get_size()
        self.facing = 1
        # 贴近 cockroach.png 原图：头朝左上，避免启动时竖直「立着」
        self.heading = -135.0
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
        self.gait = 0.0
        self.gait_amp = 0.0
        self.particles: list[FxParticle] = []

    def apply_skin(self, skin: str):
        self.skin = skin
        self.base = load_roach_sprite(SPRITE_W, skin=skin)
        self.sw, self.sh = self.base.get_size()

    def set_facing(self, vx: float, vy: float = 0.0):
        if abs(vx) + abs(vy) > 0.08:
            self.heading = math.degrees(math.atan2(vy, vx))
            self.facing = 1 if vx >= 0 else -1

    def burst(self, cx: float, cy: float, kind: str, n: int = 5):
        for _ in range(n):
            self.particles.append(FxParticle(cx, cy, kind))

    def tick(self, moving: bool, speed: float, sleeping: bool, happy: bool,
             dancing: bool = False, spinning: bool = False):
        self.happy = happy
        target_amp = 0.0
        if sleeping:
            self.phase += 0.04
            self.bob = math.sin(self.phase) * 0.8
            self.tilt = 0.0
            self.gait += 0.02
        elif dancing:
            self.phase += 0.35
            self.bob = math.sin(self.phase * 3) * 5
            self.tilt = math.sin(self.phase * 2) * 8
            self.spin += math.sin(self.phase) * 4
            self.gait += 0.5
            target_amp = 1.0
        elif spinning:
            self.phase += 0.2
            self.bob = 1
            self.spin += self.spin_vel or 18
            self.spin_vel *= 0.985
            self.gait += 0.55
            target_amp = 0.7
        elif moving:
            self.phase += 0.28 + speed * 0.12
            self.gait += 0.25 + speed * 0.16
            target_amp = min(1.0, 0.5 + speed * 0.2)
            self.bob = math.sin(self.gait * 2) * BOB_AMPLITUDE * target_amp
            self.tilt = math.sin(self.gait) * 5.0 * target_amp
        else:
            self.phase += 0.05
            self.gait += 0.05
            target_amp = 0.1
            self.bob = math.sin(self.phase) * 0.4
            self.tilt = math.sin(self.phase * 0.5) * 0.5
            if abs(self.spin_vel) > 0.3:
                self.spin += self.spin_vel
                self.spin_vel *= 0.92
            elif abs(self.spin) > 1 and not self.belly:
                self.spin *= 0.85

        self.gait_amp += (target_amp - self.gait_amp) * 0.2
        self.scale += (self.target_scale - self.scale) * 0.12
        self.alpha += (self.target_alpha - self.alpha) * 0.15
        self.particles = [p for p in self.particles if p.tick()]

    def _compose_local(self, s: float, moving: bool) -> pygame.Surface:
        """贴图本体；走动时略微压扁模拟步态。"""
        bw = max(1, int(self.sw * s))
        bh = max(1, int(self.sh * s))
        # 步态：轻微上下压扁 / 左右晃
        squash = 1.0 - 0.04 * self.gait_amp * abs(math.sin(self.gait * 2))
        stretch = 1.0 + 0.03 * self.gait_amp * abs(math.sin(self.gait * 2))
        dw = max(1, int(bw * stretch))
        dh = max(1, int(bh * squash))

        img = self.base
        if self.belly:
            img = pygame.transform.flip(img, False, True)
        body = pygame.transform.smoothscale(img, (dw, dh))
        if self.happy:
            tinted = body.copy()
            glow = pygame.Surface(tinted.get_size(), pygame.SRCALPHA)
            glow.fill((255, 190, 60, 35))
            tinted.blit(glow, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
            body = tinted

        pad = 8
        local = pygame.Surface((dw + pad * 2, dh + pad * 2), pygame.SRCALPHA)
        local.blit(body, (pad, pad))
        if abs(self.tilt) > 0.4:
            local = pygame.transform.rotate(local, self.tilt * 0.35)
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
        cy = oy + PET_H / 2 + self.bob

        if self.alpha >= 1:
            local = self._compose_local(s, moving)
            # 贴图本地头朝上。pygame.rotate 正角=逆时针，故取反：
            # turn = -(目标朝向 - 本地朝上) ，例如朝右 heading=0 → turn=-90（顺时针）
            turn = -(self.heading - SPRITE_UPRIGHT_HEADING) + self.spin
            if abs(turn) > 0.05:
                local = pygame.transform.rotate(local, turn)

            if self.alpha < 250:
                local = local.copy()
                local.set_alpha(max(min_alpha, int(self.alpha)))

            rect = local.get_rect(center=(int(cx), int(cy)))
            sh_a = max(0, min(50, int(50 * self.alpha / 255)))
            if sh_a > 0:
                sh_w = int(min(rect.width, rect.height) * 0.7)
                shad = pygame.Surface((max(8, sh_w), 8), pygame.SRCALPHA)
                pygame.draw.ellipse(shad, (0, 0, 0, sh_a), shad.get_rect())
                surf.blit(shad, (rect.centerx - shad.get_width() // 2, rect.bottom - 4))
            surf.blit(local, rect)

            if sleeping:
                veil = pygame.Surface((rect.width, max(4, rect.height // 4)), pygame.SRCALPHA)
                veil.fill((80, 80, 120, 70))
                surf.blit(veil, (rect.left, rect.top + 4))

        for p in self.particles:
            a = max(0, min(255, p.life * 6))
            if p.kind == "heart":
                col = (255, 90, 120, a)
            elif p.kind == "crumb":
                col = (180, 120, 60, a)
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
        """选屏幕角落/边缘缝隙作为藏身处。"""
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
        """回到躲藏：平时默认态。scramble=True 时换角落逃窜。"""
        self.follow = False
        self._pick_hide_spot(avoid_current=scramble)
        self._set(State.HIDE, 99999)
        if scramble:
            # 先受惊冲刺一小段，再钻进目标角落
            self.vx = random.choice([-1, 1]) * RUN_SPEED * 0.9
            self.vy = random.uniform(-1.2, 1.2)

    def _rest(self):
        """互动结束后回到躲藏（跟随模式除外）。"""
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
                    self.vx *= 0.7
                    self.vy *= 0.7
                    if abs(self.vx) + abs(self.vy) < 0.05:
                        self.vx = self.vy = 0
                    # 藏好了就一直躲着，只偶尔微动
                    if self.state_timer < 120:
                        self.state_timer = random.randint(800, 2400)
                    if random.random() < 0.0015:
                        self.vx = random.uniform(-0.35, 0.35)
                        self.vy = random.uniform(-0.25, 0.25)
                else:
                    sp = WALK_SPEED * 1.55
                    self.vx, self.vy = dx / d * sp, dy / d * sp
            # 不自动现身；只有互动才会离开 HIDE

        elif self.state == State.PEEK:
            # 从藏身处探头张望，轻微左右晃
            self.vx = math.sin(self.state_timer * 0.15) * 0.35
            self.vy = 0
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

        elif self.state == State.IDLE:
            self.vx *= 0.85
            self.vy *= 0.85
            if self.state_timer <= 0:
                # 闲着也会钻回缝里，极少主动乱逛
                if random.random() < 0.12:
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
        self.roach = RoachRenderer(skin=skin)
        self.canvas = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        self.font = load_cjk_font(14)
        self.font_sm = load_cjk_font(12)

        self.x = float(self.brain.target_x or (sw - WIN_W) // 2)
        self.y = float(self.brain.target_y or (sh - WIN_H - 50))
        self.prev_x, self.prev_y = self.x, self.y
        self.roach.target_alpha = 70
        self.roach.alpha = 70
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
        self._next_proactive = time.time() + 180
        self._next_worker_idle = time.time() + 300
        lo_sc = float(self.settings.get("idle_showcase_min") or 45)
        hi_sc = float(self.settings.get("idle_showcase_max") or 90)
        self._next_showcase = time.time() + random.uniform(min(lo_sc, hi_sc), max(lo_sc, hi_sc))
        rest_iv = float(self.settings.get("rest_reminder_interval_sec") or 3600)
        self._next_rest = time.time() + max(60.0, rest_iv)
        self._rest_active = False
        lo = float(self.settings.get("sys_check_interval_min") or 50)
        hi = float(self.settings.get("sys_check_interval_max") or 90)
        self._next_sys_check = time.time() + random.uniform(lo, hi)
        self._last_sys_alert = ""
        self._near_mouse = False
        self._hide_scramble_at = 0.0
        self._fx_anchor = (PAD_X + PET_W / 2, ROACH_Y + PET_H / 2)
        self._running = True
        self.screen = None
        self.hwnd = None
        self.window = None
        self.view = None
        self._win_click_through = False
        self._chrome = None
        self.buddy = None
        self._next_banter = time.time() + random.uniform(90, 160)

        if IS_MAC:
            if not OBJC_OK:
                raise RuntimeError("macOS 需要: pip3 install pygame pyobjc-framework-Cocoa")
            self._setup_mac_window()
        elif IS_WIN:
            self._setup_win_window()
        else:
            raise RuntimeError(f"暂不支持平台: {sys.platform}")

        self._init_buddy()
        self._chrome = DesktopChrome(self._cmd_q.put, self.settings)
        self._chrome.start()
        self._start_weather_fetch()
        self._queue_startup_greetings()
        self.tick()

    def _init_buddy(self):
        """创建会计蟑螂同伴（默认开启，可设置关闭）。"""
        if not self.settings.get("accountant_buddy", True):
            self.buddy = None
            self._sync_layout()
            return
        buddy_roach = RoachRenderer(skin="default")
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
        self._win_click_through = False
        self._win_apply_pos()

    def do_banter(self):
        """手动触发一次打工蟑螂 vs 会计蟑螂对喷。"""
        if self._ai_available() and not self._ai_skip_busy():
            def worker():
                try:
                    script = generate_banter_script(self.settings)
                    self._pending_ai.append(("banter", script))
                except LLMError:
                    self._pending_ai.append(("banter", None))

            if self._run_ai("banter", worker, "对喷想词中..."):
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
            self.say("会计蟑螂待命", urgent=True)
        else:
            if self.buddy:
                self.buddy.active = False
                self.buddy.bubbles.clear()
                self.buddy._script.clear()
            self._sync_layout()
            self.say("会计去对账了", urgent=True)

    def _check_buddy_banter(self):
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
        if random.random() < 0.55:
            self.do_banter()
        else:
            lo = float(self.settings.get("buddy_banter_min") or 120)
            hi = float(self.settings.get("buddy_banter_max") or 280)
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
        self._win_apply_pos()

    def _win_apply_pos(self):
        if not self.hwnd:
            return
        import ctypes
        user32 = ctypes.windll.user32
        HWND_TOPMOST = -1
        SWP_NOSIZE = 0x0001
        SWP_NOACTIVATE = 0x0010
        SWP_SHOWWINDOW = 0x0040
        user32.SetWindowPos(
            self.hwnd, HWND_TOPMOST, int(self.x), int(self.y), 0, 0,
            SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

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
        elif cmd == "overview":
            self.say_sys_overview()
        elif cmd == "passthrough":
            self.toggle_click_through()
        elif cmd == "status":
            self.say_status()
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
        elif cmd == "story":
            self.do_story()
        elif cmd == "toggle_rest":
            self.toggle_rest_reminder()
        elif cmd == "toggle_showcase":
            self.toggle_idle_showcase()
        elif cmd == "toggle_ai":
            self.toggle_ai()
        elif cmd == "cycle_ai_provider":
            self.cycle_ai_provider()

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
            bits.append("躲着")
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
        self.buddy.active = True
        self._sync_layout()
        self.roach.target_alpha = 255
        if self.brain.state == State.HIDE:
            self.brain._set(State.GREET, 160)
        self.buddy.start_banter(script)
        self._note_progress(banter_count=1)
        lo = float(self.settings.get("buddy_banter_min") or 120)
        hi = float(self.settings.get("buddy_banter_max") or 280)
        self._next_banter = time.time() + random.uniform(min(lo, hi), max(lo, hi))

    def _queue_startup_greetings(self):
        if self.settings.get("bubbles_enabled", True):
            tip = f"{greeting_by_period()} {date_phrase()}"
            self.bubbles.push(tip, life=160)
            if is_workday():
                self.bubbles.push(worker_startup_tip(), life=140)
        # 探头打个招呼，随后钻回角落
        self.brain._set(State.GREET, 100)
        self.roach.target_alpha = 255

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
        hide = "躲着" if self.brain.state == State.HIDE else "在外面"
        self.say(f"{mood} {hide} 亲密度{aff}", urgent=True)

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
        """合法摸鱼。"""
        self.roach.target_alpha = 200
        self.brain.react_peek()
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
        line = pick_rest_line()
        self.bubbles.push_many([line, "起来活动一下", "点我可关掉提示"], life=140)
        self.brain.react_dance()
        self.fx("star", 8)

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

    def say_help(self):
        self.bubbles.clear()
        self.bubbles.push_many([
            "点头摸·点尾吓·双击跑",
            "/总览 [CPU ]内存 \\磁盘 '网络",
            "G打工 J黑话 5财务话 ;财务提醒",
            "9税务 0发薪 6月结 7审计 8报销",
            "1站会 2复盘 3摸鱼 4反PUA",
            "Q探头 E觅食 Z疯跑 H帮助",
            "T故事大会 D日期 W天气 S状态",
            "Ctrl+Alt+R召唤 P穿透 B对喷 T故事 A开AI",
            ",对喷 .开关会计蟑螂",
            "菜单:故事/休息/AI开关与厂商",
        ], life=150)

    def _check_sys_alerts(self):
        """周期性检查资源，过高时缝里嘀咕一声。"""
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
        self.say(msg, life=150)
        # 监控 → 表演联动
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

    def _check_worker_schedule(self):
        """到点提醒：打卡、喝水、午饭、下班等。"""
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
        now = time.time()
        if now < self._next_proactive:
            return
        self._next_proactive = now + random.uniform(300, 600)
        if self.brain.state in (State.SLEEP, State.DRAGGED, State.RUN, State.FOLLOW, State.LASER):
            return
        if self.bubbles.current or self.bubbles._q:
            return
        # 躲着时几乎不主动出来，顶多缝里嘀咕一声
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
            self.maybe_say(random.choice(["喝口水?", "活动下~", "发票呢?"]), chance=0.4)
            self.brain.go_hide()

    def _check_idle_showcase(self):
        """周期随机表演：短暂现身 + 一句闲聊（对标 DeskTopPet 定时切动作/文字）。"""
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
            if act < 0.28:
                self.brain.react_peek()
            elif act < 0.55:
                self.brain.react_pose()
            elif act < 0.78:
                self.brain.react_dance()
                self.fx("star", 4)
            else:
                self.brain.react_spin(12)
                self.roach.spin_vel = 12
                self.fx("dust", 3)
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
                    if act < 0.28:
                        self.brain.react_peek()
                    elif act < 0.55:
                        self.brain.react_pose()
                    elif act < 0.78:
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

    def _check_worker_idle_nudge(self):
        """久坐轻推：间隔较长，只在工作时段。"""
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
                    # 被发现了：换个角落继续躲
                    self._hide_scramble_at = now
                    self.brain.go_hide(scramble=True)
                    self.fx("dust", 3)
                    self.maybe_say("溜了!", chance=0.12)
            elif self.brain.state == State.IDLE:
                self.brain._set(State.CURIOUS, 90)
                self.roach.target_scale = 1.05
        if not near:
            self.roach.target_scale = 1.0
        self._near_mouse = near

    def do_feed(self, feast: bool = False):
        self.brain.react_feed(feast=feast)
        self.roach.target_scale = 1.18 if feast else 1.12
        self.roach.belly = False
        self.fx("crumb", 12 if feast else 8)
        self._note_progress(feed_count=1)
        if feast:
            self.say(random.choice(["大餐!", "撑住了", "太幸福!"]), urgent=True)
        else:
            self.maybe_say(random.choice(Bubble.FEED_PHRASES), chance=0.35)

    def do_dance(self):
        self.brain.react_dance()
        self.roach.belly = False
        self.fx("star", 8)
        self.maybe_say("蹦迪!", chance=0.25)

    def do_home(self):
        sw, sh = get_desktop_size()
        self.brain.target_x = (sw - WIN_W) // 2
        self.brain.target_y = sh - WIN_H - 50
        self.brain._set(State.RUN, 180)
        self.brain.pet_streak = 0
        self.roach.belly = False
        self.roach.target_alpha = 255

    def do_hide(self):
        self.brain.react_hide()
        self.roach.target_alpha = 70
        self.roach.belly = False
        self.fx("dust", 4)
        self.maybe_say("躲起来~", chance=0.2)

    def do_belly(self):
        self.brain.react_belly()
        self.roach.belly = True
        self.roach.spin = 180
        self.fx("star", 5)
        self.maybe_say("翻了!", chance=0.3)

    def do_laser(self):
        self.brain.react_laser()
        self.roach.belly = False
        self.roach.target_alpha = 255
        self.fx("star", 3)
        self.maybe_say("追光点!", chance=0.25)

    def do_follow_toggle(self):
        on = self.brain.react_follow_toggle()
        self.roach.target_alpha = 255
        self.roach.belly = False
        self.fx("heart" if on else "dust", 4)
        self.maybe_say("跟着你" if on else "不跟了", chance=0.35)

    def do_peek(self):
        self.brain.react_peek()
        self.roach.target_alpha = 200
        self.roach.belly = False
        self.fx("star", 2)
        self.maybe_say(random.choice(Bubble.PEEK_PHRASES), chance=0.4)

    def do_forage(self):
        self.brain.react_forage()
        self.roach.target_alpha = 255
        self.roach.belly = False
        self.fx("crumb", 4)
        self.maybe_say(random.choice(Bubble.FORAGE_PHRASES), chance=0.45)

    def do_zoomie(self):
        self.brain.react_zoomie()
        self.roach.target_alpha = 255
        self.roach.belly = False
        self.fx("dust", 6)
        self.maybe_say(random.choice(Bubble.ZOOMIE_PHRASES), chance=0.4)

    def do_panic(self):
        self.brain.react_panic()
        self.roach.target_alpha = 255
        self.roach.belly = False
        self.fx("dust", 10)
        self.say(random.choice(Bubble.PANIC_PHRASES), urgent=True, life=100)

    def do_pose(self):
        self.brain.react_pose()
        self.roach.target_alpha = 255
        self.roach.belly = False
        self.roach.target_scale = 1.15
        self.fx("star", 10)
        self.maybe_say(random.choice(Bubble.POSE_PHRASES), chance=0.5)

    def do_call(self):
        self.brain.react_call()
        self.roach.target_alpha = 255
        self.roach.belly = False
        self.fx("heart", 5)
        self._note_progress(call_count=1)
        self.maybe_say(random.choice(Bubble.CALL_PHRASES), chance=0.4)

    def do_spar(self):
        """假想敌对打：原地旋转扑腾。"""
        self.brain.react_spin(26)
        self.roach.spin_vel = 26
        self.roach.target_alpha = 255
        self.roach.belly = False
        self.fx("dust", 8)
        self.maybe_say(random.choice(["打打打!", "哈!", "谁怕谁!"]), chance=0.4)

    # ── 坐标换算 ──────────────────────────────────────────

    def _sh(self) -> int:
        return get_desktop_size()[1]

    def _screen_pos(self) -> tuple[float, float]:
        # macOS Cocoa 窗口原点在左下；Windows / 逻辑坐标均为左上
        if IS_MAC:
            return self.x, self._sh() - self.y - WIN_H
        return self.x, self.y

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
        bx, by = PAD_X, ROACH_Y
        bw, bh = self.roach.sw, self.roach.sh
        if bx <= mx <= bx + bw and by <= my <= by + bh:
            return True
        if self.bubble and my < ROACH_Y:
            return True
        return False

    def _update_mouse_passthrough(self):
        """鼠标不在蟑螂上时穿透点击；强制穿透模式下始终穿透。"""
        if self.settings.get("click_through_force", False):
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
        if IS_MAC and self.window is not None:
            self.window.setIgnoresMouseEvents_(not over)
            return
        if IS_WIN and self.hwnd:
            self._win_set_click_through(not over)

    def _win_set_click_through(self, enabled: bool):
        if enabled == self._win_click_through:
            return
        import ctypes
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        if enabled:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style)
        self._win_click_through = enabled

    # ── 鼠标 ──────────────────────────────────────────────

    def _modifiers(self, event) -> tuple[bool, bool, bool, bool]:
        """返回 (option/alt, shift, control, command/meta)。"""
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

    def on_mouse_down(self, event):
        mx, my = self._event_local(event)
        if not self._hit(mx, my):
            return

        # 睡觉时单击叫醒，稍后仍会钻回角落
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
            self.say("好,继续肝", urgent=True, life=90)
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

        option, shift, control, command = self._modifiers(event)
        if command:
            self.do_call()
            return
        if control:
            self.do_feed(feast=True)
            return
        if option:
            self.do_feed()
            return
        if shift:
            self.do_dance()
            return

        now = pygame.time.get_ticks()
        if now - self.click_time < 350:
            self.click_count += 1
        else:
            self.click_count = 1
        self.click_time = now

        # 头/尾分区：左半摸头，右半吓跑（相对贴图）
        head_side = mx < WIN_W / 2

        if self.click_count >= 3:
            self.click_count = 0
            self.brain.pet_streak = 0
            self.brain.react_poke()
            self.roach.target_scale = 0.88
            self.fx("dust", 6)
            # 三连击：吐槽 + 偶尔抛一句故事开头
            if random.random() < 0.35:
                opener = pick_story()[0]
                self.say(opener, urgent=True, life=110)
            elif self._ai_available() and not self._ai_busy and random.random() < 0.4:
                def worker():
                    try:
                        text = generate_line(self.settings, "poke", "被连戳")
                    except LLMError:
                        text = random.choice(Bubble.POKE_PHRASES)
                    self._pending_ai.append(("say", text, 120, True))
                self._run_ai("poke", worker, thinking="")
            else:
                self.maybe_say(random.choice(Bubble.POKE_PHRASES), chance=0.55)
        elif self.click_count == 2:
            self.brain.react_dblclick()
            self.roach.target_scale = 1.15
            self.fx("star", 5)
            self.maybe_say("冲!", chance=0.2)
        elif head_side:
            self.brain.react_click()
            self.roach.target_scale = 1.08
            self.fx("heart", 5)
            self._note_progress(pet_count=1)
            streak = self.brain.pet_streak
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
                self.maybe_say("晕...", chance=0.25)
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
        if self.brain.state == State.SLEEP:
            self.brain.go_hide()
            self.fx("star", 3)
        else:
            self.brain._set(State.SLEEP, random.randint(400, 800))
            self.roach.belly = False
            self.roach.target_alpha = 255
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
        if chars == "d":
            self.say_date()
        elif chars == "t":
            self.do_story()
        elif chars == "w":
            self.say_weather()
        elif chars == "s":
            self.say_status()
        elif chars == "f":
            self.do_feed()
        elif chars == "p":
            self.brain.react_poke()
            self.fx("dust", 5)
            self.maybe_say(random.choice(Bubble.POKE_PHRASES), chance=0.35)
        elif chars == "a":
            self.do_dance()
        elif chars == "m":
            self.do_follow_toggle()
        elif chars == "c":
            self.do_hide()
        elif chars == "b":
            self.do_home()
        elif chars == "u":
            self.do_belly()
        elif chars == "l":
            self.do_laser()
        elif chars == "g":
            self.say_worker_tip()
        elif chars == "j":
            self.say_buzzword()
        elif chars == "y":
            self.do_align()
        elif chars == "1":
            self.do_standup()
        elif chars == "2":
            self.do_review()
        elif chars == "3":
            self.do_fish()
        elif chars == "4":
            self.do_resist_pua()
        elif chars == "5":
            self.say_finance_buzz()
        elif chars == "6":
            self.do_month_close()
        elif chars == "7":
            self.do_audit_panic()
        elif chars == "8":
            self.do_reimburse()
        elif chars == "9":
            self.do_tax_check()
        elif chars == "0":
            self.do_payroll_day()
        elif chars == ";":
            self.say_finance_tip()
        elif chars == "/":
            self.say_sys_overview()
        elif chars == "[":
            self.say_sys_cpu()
        elif chars == "]":
            self.say_sys_mem()
        elif chars == "\\":
            self.say_sys_disk()
        elif chars == "'":
            self.say_sys_net()
        elif chars == "h":
            self.say_help()
        elif chars == "-":
            self.toggle_click_through()
        elif chars == "=":
            self.cycle_skin()
        elif chars == "r":
            self.brain.react_dblclick()
            self.fx("star", 4)
            self.maybe_say("冲!", chance=0.2)
        elif chars == "q":
            self.do_peek()
        elif chars == "e":
            self.do_forage()
        elif chars == "z":
            self.do_zoomie()
        elif chars == "k":
            self.do_call()
        elif chars == "v":
            self.do_panic()
        elif chars == "o":
            self.do_pose()
        elif chars == "i":
            self.do_chat()
        elif chars == "x":
            self.do_spar()
        elif chars == "n":
            # 捉迷藏：先现身再立刻换角落躲
            self.do_peek()
            self.say("来抓我呀!", urgent=True, life=100)
            self.brain.go_hide(scramble=True)
            self.fx("dust", 5)
        elif chars == ",":
            self.do_banter()
        elif chars == ".":
            self.toggle_buddy()
        return False

    # ── 渲染 ──────────────────────────────────────────────

    def _draw_bubble(self):
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

    def paint(self):
        self.canvas.fill((0, 0, 0, 0))
        moving = abs(self.brain.vx) + abs(self.brain.vy) > 0.1
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
        self.bubble = self.bubbles.tick()

        self._check_hourly_chime()
        self._check_worker_schedule()
        self._check_worker_idle_nudge()
        self._check_sys_alerts()
        self._check_proactive()
        self._check_idle_showcase()
        self._check_rest_reminder()
        self._check_buddy_banter()
        self._check_mouse_near(mx, my)

        if not busy:
            self.x += self.brain.vx
            self.y += self.brain.vy
            self.x, self.y = self.brain.clamp(self.x, self.y)

        moving = abs(self.brain.vx) + abs(self.brain.vy) > 0.1
        if moving:
            self.roach.set_facing(self.brain.vx, self.brain.vy)

        st = self.brain.state
        self.roach.belly = st == State.BELLY
        if st == State.HIDE:
            # 藏好后更淡；逃窜途中稍亮一点
            settled = (
                self.brain.target_x is not None
                and math.hypot(self.x - self.brain.target_x, self.y - (self.brain.target_y or self.y)) < 12
            )
            self.roach.target_alpha = 55 if settled else 120
            self.roach.target_scale = 0.92 if settled else 1.0
        else:
            self.roach.target_alpha = 255

        self.roach.tick(
            moving, math.hypot(self.brain.vx, self.brain.vy),
            st == State.SLEEP, st == State.HAPPY,
            dancing=(st == State.DANCE), spinning=(st == State.SPIN),
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
            self.window.setFrameOrigin_((sx, sy))
            self.window.orderFrontRegardless()
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
        print("习性: 平时躲角落，互动才出来；鼠标凑近会换地方躲")
        print("产品: 菜单栏/托盘 | Ctrl+Alt+R召唤 /总览 P穿透 S状态 Q退出")
        print("设置: settings.json（气泡/提醒/穿透/话术包/皮肤）")
        print("打工: G提醒 J黑话混 Y对齐 | 1站会 2复盘 3摸鱼 4反PUA")
        print("监控: /总览  [CPU  ]内存  \\磁盘  '网络  (需 psutil)")
        print("财务: 5行话口头禅 6月结 7审计 8报销 9税务 0发薪 ;财务提醒")
        print("互动: 点头摸/点尾吓 | 双击跑 | ⌘/Win召唤 Ctrl大餐 | 滚轮转")
        print("      Alt喂食 Shift跳舞 | 右键睡 | 中键日期 | 方向键/空格")
        print("双宠: ,对喷  .开关会计蟑螂 | Ctrl+Alt+B对喷")
        print("故事: T / Ctrl+Alt+T 故事大会 | 菜单开关休息提醒(约每小时)")
        print("AI: Ctrl+Alt+A开关 | 菜单切换厂商(deepseek/doubao/qwen) | secrets.json填Key")
        print("快捷键: Q探头 E觅食 Z疯跑 K召唤 V受惊 O摆拍 I闲聊 X对打 N躲猫猫")
        print("      M跟随 C躲 L追光 U翻肚 B回家 A舞 F喂 P戳 R跑")
        print("      -穿透 =皮肤 D日期 T故事 W天气 S状态 H帮助 Esc退出")
        print("      报时: 中键点蟑螂 或 菜单状态")

    def run(self):
        if IS_MAC:
            self._run_mac()
        else:
            self._run_win()

    def _run_mac(self):
        clock = pygame.time.Clock()
        app = NSApplication.sharedApplication()
        self._print_help_banner()
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
                    if self._handle_pygame_key(ev):
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

        chars = (ev.unicode or "").lower()
        if not chars and pygame.K_a <= ev.key <= pygame.K_z:
            chars = chr(ev.key)
        if not chars and pygame.K_0 <= ev.key <= pygame.K_9:
            chars = chr(ev.key)
        # 复用 mac 字符分支：构造伪 NS key 事件太重，直接复制映射
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
        if chars == "p":
            self.brain.react_poke()
            self.fx("dust", 5)
            self.maybe_say(random.choice(Bubble.POKE_PHRASES), chance=0.35)
        elif chars == "r":
            self.brain.react_dblclick()
            self.fx("star", 4)
            self.maybe_say("冲!", chance=0.2)
        elif chars == "n":
            self.do_peek()
            self.say("来抓我呀!", urgent=True, life=100)
            self.brain.go_hide(scramble=True)
            self.fx("dust", 5)
        elif chars == ",":
            self.do_banter()
        elif chars == ".":
            self.toggle_buddy()
        elif chars in mapping:
            mapping[chars]()
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
    print("🪳 桌宠已启动，按 Esc 退出")
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
