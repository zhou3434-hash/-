"""内置城市数据：坐标、交通、以及费用系数。

费用模型说明
------------
本智能体的预算工具采用「基准值 × 城市系数」的模型：

* `hotel` / `food` / `local_transit` / `misc` 为**基准日单价**（单人、经济档、人民币）；
* `ticket_factor` 为门票整体水平系数（1.0 为全国平均）；
* 不同档位（经济/舒适/高端）在预算工具中再乘以档位倍率。

数值为公开信息整理的经验值，仅用于规划参考，不构成实际报价。
"""

from __future__ import annotations

from typing import Any

#: 城市基础数据
#: lat/lon 用于距离估算与「周边景点」检索
CITIES: dict[str, dict[str, Any]] = {
    # ---------------------------- 中国 ----------------------------
    "北京": {
        "country": "中国", "lat": 39.9042, "lon": 116.4074, "airport": "PEK/PKX",
        "hotel": 320, "food": 130, "local_transit": 45, "misc": 60, "ticket_factor": 1.0,
        "highlights": "故宫、长城、胡同文化、皇家园林",
        "best_season": "4-5月、9-10月",
        "rail_hub": True,
    },
    "上海": {
        "country": "中国", "lat": 31.2304, "lon": 121.4737, "airport": "PVG/SHA",
        "hotel": 380, "food": 150, "local_transit": 40, "misc": 70, "ticket_factor": 0.9,
        "highlights": "外滩、迪士尼、石库门、江南水乡",
        "best_season": "3-5月、9-11月",
        "rail_hub": True,
    },
    "杭州": {
        "country": "中国", "lat": 30.2741, "lon": 120.1551, "airport": "HGH",
        "hotel": 300, "food": 120, "local_transit": 35, "misc": 55, "ticket_factor": 0.85,
        "highlights": "西湖、灵隐寺、龙井茶园、宋韵文化",
        "best_season": "3-5月、9-11月",
        "rail_hub": True,
    },
    "成都": {
        "country": "中国", "lat": 30.5728, "lon": 104.0668, "airport": "TFU/CTU",
        "hotel": 260, "food": 110, "local_transit": 30, "misc": 50, "ticket_factor": 0.75,
        "highlights": "熊猫基地、火锅、都江堰、川西环线",
        "best_season": "3-6月、9-11月",
        "rail_hub": True,
    },
    "西安": {
        "country": "中国", "lat": 34.3416, "lon": 108.9398, "airport": "XIY",
        "hotel": 250, "food": 100, "local_transit": 30, "misc": 45, "ticket_factor": 0.95,
        "highlights": "兵马俑、城墙、大唐不夜城、面食",
        "best_season": "3-5月、9-11月",
        "rail_hub": True,
    },
    "厦门": {
        "country": "中国", "lat": 24.4798, "lon": 118.0894, "airport": "XMN",
        "hotel": 300, "food": 120, "local_transit": 30, "misc": 55, "ticket_factor": 0.8,
        "highlights": "鼓浪屿、环岛路、闽南小吃、海岛风光",
        "best_season": "3-5月、10-12月",
        "rail_hub": False,
    },
    "三亚": {
        "country": "中国", "lat": 18.2528, "lon": 109.5119, "airport": "SYX",
        "hotel": 450, "food": 150, "local_transit": 50, "misc": 80, "ticket_factor": 0.9,
        "highlights": "亚龙湾、蜈支洲岛、热带海滨、度假酒店",
        "best_season": "10月-次年4月",
        "rail_hub": False,
    },
    "丽江": {
        "country": "中国", "lat": 26.8721, "lon": 100.2299, "airport": "LJG",
        "hotel": 240, "food": 100, "local_transit": 40, "misc": 50, "ticket_factor": 0.9,
        "highlights": "古城、玉龙雪山、束河、纳西文化",
        "best_season": "4-6月、9-11月",
        "rail_hub": False,
    },
    "桂林": {
        "country": "中国", "lat": 25.2736, "lon": 110.2900, "airport": "KWL",
        "hotel": 220, "food": 90, "local_transit": 35, "misc": 45, "ticket_factor": 0.9,
        "highlights": "漓江、阳朔、喀斯特山水、遇龙河",
        "best_season": "4-10月",
        "rail_hub": True,
    },
    "青岛": {
        "country": "中国", "lat": 36.0671, "lon": 120.3826, "airport": "TAO",
        "hotel": 280, "food": 120, "local_transit": 30, "misc": 55, "ticket_factor": 0.75,
        "highlights": "八大关、栈桥、崂山、啤酒海鲜",
        "best_season": "5-10月",
        "rail_hub": True,
    },
    "重庆": {
        "country": "中国", "lat": 29.5630, "lon": 106.5516, "airport": "CKG",
        "hotel": 250, "food": 110, "local_transit": 35, "misc": 50, "ticket_factor": 0.75,
        "highlights": "洪崖洞、轻轨穿楼、火锅、武隆天坑",
        "best_season": "3-5月、9-11月",
        "rail_hub": True,
    },
    "长沙": {
        "country": "中国", "lat": 28.2282, "lon": 112.9388, "airport": "CSX",
        "hotel": 230, "food": 100, "local_transit": 30, "misc": 45, "ticket_factor": 0.7,
        "highlights": "岳麓山、橘子洲、文和友、湘菜小吃",
        "best_season": "3-5月、9-11月",
        "rail_hub": True,
    },
    # ---------------- 周边 / 短途目的地（一日游、两日游常用） ----------------
    "都江堰": {
        "country": "中国", "lat": 31.0020, "lon": 103.6100, "airport": "—（成都双流/天府转城际）",
        "hotel": 220, "food": 90, "local_transit": 30, "misc": 40, "ticket_factor": 0.8,
        "highlights": "都江堰水利工程、青城山、街子古镇",
        "best_season": "3-6月、9-11月",
        "rail_hub": True,
    },
    "峨眉山": {
        "country": "中国", "lat": 29.5200, "lon": 103.3300, "airport": "—（成都转高铁约1.5小时）",
        "hotel": 260, "food": 100, "local_transit": 45, "misc": 45, "ticket_factor": 1.1,
        "highlights": "金顶云海日出、猴群、报国寺、温泉",
        "best_season": "4-6月、9-11月",
        "rail_hub": True,
    },
    "苏州": {
        "country": "中国", "lat": 31.2989, "lon": 120.5853, "airport": "—（上海/无锡转高铁）",
        "hotel": 300, "food": 120, "local_transit": 30, "misc": 55, "ticket_factor": 0.85,
        "highlights": "古典园林、平江路、金鸡湖、苏帮菜",
        "best_season": "3-5月、9-11月",
        "rail_hub": True,
    },
    "南京": {
        "country": "中国", "lat": 32.0603, "lon": 118.7969, "airport": "NKG",
        "hotel": 290, "food": 115, "local_transit": 30, "misc": 55, "ticket_factor": 0.8,
        "highlights": "中山陵、明孝陵、夫子庙、民国建筑",
        "best_season": "3-5月、10-11月",
        "rail_hub": True,
    },
    "张家界": {
        "country": "中国", "lat": 29.1274, "lon": 110.4790, "airport": "DYG",
        "hotel": 260, "food": 100, "local_transit": 50, "misc": 50, "ticket_factor": 1.2,
        "highlights": "武陵源石英砂岩峰林、天门山、玻璃栈道",
        "best_season": "4-6月、9-10月",
        "rail_hub": True,
    },
    "黄山": {
        "country": "中国", "lat": 30.1300, "lon": 118.1670, "airport": "TXN",
        "hotel": 320, "food": 110, "local_transit": 50, "misc": 50, "ticket_factor": 1.15,
        "highlights": "奇松怪石云海温泉、宏村西递古村落",
        "best_season": "4-5月、9-11月",
        "rail_hub": True,
    },
    "珠海": {
        "country": "中国", "lat": 22.2707, "lon": 113.5767, "airport": "ZUH",
        "hotel": 330, "food": 130, "local_transit": 45, "misc": 60, "ticket_factor": 1.25,
        "highlights": "长隆海洋王国、情侣路、海岛、亲子度假",
        "best_season": "10月-次年4月",
        "rail_hub": True,
    },
    "洛阳": {
        "country": "中国", "lat": 34.6197, "lon": 112.4540, "airport": "LYA",
        "hotel": 240, "food": 95, "local_transit": 30, "misc": 45, "ticket_factor": 0.95,
        "highlights": "龙门石窟、白马寺、牡丹、汉服古城",
        "best_season": "4月牡丹、9-10月",
        "rail_hub": True,
    },
    "西双版纳": {
        "country": "中国", "lat": 22.0017, "lon": 100.7975, "airport": "JHG",
        "hotel": 300, "food": 110, "local_transit": 50, "misc": 55, "ticket_factor": 0.95,
        "highlights": "热带雨林、傣族风情、野象谷、泼水节",
        "best_season": "11月-次年4月",
        "rail_hub": True,
    },
    # ---------------------------- 海外 ----------------------------
    "东京": {
        "country": "日本", "lat": 35.6762, "lon": 139.6503, "airport": "NRT/HND",
        "hotel": 700, "food": 280, "local_transit": 90, "misc": 150, "ticket_factor": 1.1,
        "highlights": "浅草寺、涩谷、迪士尼、筑地美食",
        "best_season": "3-5月、10-11月",
        "rail_hub": True,
    },
    "巴黎": {
        "country": "法国", "lat": 48.8566, "lon": 2.3522, "airport": "CDG/ORY",
        "hotel": 850, "food": 350, "local_transit": 110, "misc": 200, "ticket_factor": 1.2,
        "highlights": "埃菲尔铁塔、卢浮宫、塞纳河、左岸咖啡",
        "best_season": "4-6月、9-10月",
        "rail_hub": True,
    },
    "曼谷": {
        "country": "泰国", "lat": 13.7563, "lon": 100.5018, "airport": "BKK/DMK",
        "hotel": 300, "food": 130, "local_transit": 60, "misc": 100, "ticket_factor": 0.7,
        "highlights": "大皇宫、湄南河、夜市、街头小吃",
        "best_season": "11月-次年2月",
        "rail_hub": False,
    },
}

#: 档位倍率（作用于住宿/餐饮/市内交通/其他）
TIER_FACTORS: dict[str, dict[str, float]] = {
    "经济": {"hotel": 0.6, "food": 0.7, "local_transit": 0.8, "misc": 0.7},
    "舒适": {"hotel": 1.0, "food": 1.0, "local_transit": 1.0, "misc": 1.0},
    "高端": {"hotel": 2.6, "food": 2.0, "local_transit": 1.6, "misc": 1.8},
}

#: 城市间交通基准（单人单程，人民币）——按距离粗估的参考价位
INTERCITY = {
    "air_per_km": 0.55,        # 飞机每公里
    "air_min": 380.0,          # 飞机最低价
    "rail_per_km": 0.42,       # 高铁每公里（二等座）
    "rail_min": 60.0,
    "rail_max_km": 1600.0,     # 超过此距离默认建议飞机
    "rail_pref_km": 1200.0,    # 高铁相对优势区间
}

#: 常用出发地（当用户没说常住城市时的默认值）
DEFAULT_ORIGIN = "北京"

#: 城市名别名，便于模糊匹配
CITY_ALIASES: dict[str, str] = {
    "北京市": "北京", "首都": "北京", "魔都": "上海", "上海市": "上海",
    "杭州市": "杭州", "蓉城": "成都", "成都市": "成都", "锦城": "成都",
    "西安市": "西安", "长安": "西安", "鹭岛": "厦门", "厦门市": "厦门",
    "鹿城": "三亚", "三亚市": "三亚", "丽江市": "丽江", "桂林市": "桂林",
    "岛城": "青岛", "青岛市": "青岛", "山城": "重庆", "重庆市": "重庆",
    "星城": "长沙", "长沙市": "长沙",
    "都江堰市": "都江堰", "灌县": "都江堰", "青城山": "都江堰",
    "峨眉山市": "峨眉山", "峨嵋山": "峨眉山", "峨眉": "峨眉山",
    "苏州市": "苏州", "姑苏": "苏州", "金陵": "南京", "南京市": "南京",
    "张家界市": "张家界", "大庸": "张家界", "黄山市": "黄山", "黃山": "黄山", "宏村": "黄山",
    "珠海市": "珠海", "长隆": "珠海", "横琴": "珠海",
    "洛阳市": "洛阳", "龙门石窟": "洛阳",
    "西双版纳傣族自治州": "西双版纳", "版纳": "西双版纳", "景洪": "西双版纳", "勐腊": "西双版纳",
    "tokyo": "东京", "東京": "东京", "paris": "巴黎", "bangkok": "曼谷",
    "京都市": "京都",
}
