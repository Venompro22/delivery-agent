"""📝 Understands Chinese WeChat delivery messages with rules (no paid AI).

Finds who, phone, how many, what, where and by when, with or without spaces between
words, and remembers where in the text each piece was found, so the dashboard can
highlight what the AI understood.

How: take out the clear pieces first (phone, quantity, time), blank out polite filler
words, and what is left splits into pieces: a piece with 路/号/楼/小区… is the address,
a short piece starting with a Chinese surname is the name."""
import re
from typing import List, Optional

PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
QTY = re.compile(r"([一二两三四五六七八九十]|\d{1,2})\s*(桶|箱|瓶|件|袋)(?:装?水|矿泉水|纯净水)?")
PRODUCT = re.compile(r"桶装水|矿泉水|纯净水|山泉水|农夫山泉|怡宝|娃哈哈|百岁山|景田")
TIME = re.compile(r"(今天|明天|后天)?(早上|上午|中午|下午|傍晚|晚上)?(\d{1,2})(?:点(半|\d{1,2}分?)?|[:：](\d{2}))(前|之前|以前)?")
SOON = re.compile(r"(半小时|半个小时|\d{1,3}分钟|[一两二三\d]个?小时)(内|以内|之内)|马上|尽快|赶紧|越快越好")
USUAL = re.compile(r"老地方|老位置|老样子|老规矩|还是那[里儿边]|跟上次一样|和上次一样|同上次")
ADDR_MARK = re.compile(r"路|街|道|巷|号|楼|栋|幢|室|单元|小区|宿舍|公寓|大厦|花园|市场|学校|大学|门口|前台")
FILLER = re.compile(r"你好|您好|老板|麻烦|帮我|请|给我|我要|要|送到|送来|送|一下|谢谢|谢了|的|吧|呢|啊|哦")
SURNAMES = ("王李张刘陈杨黄赵周吴徐孙马朱胡郭何林罗高郑梁谢宋唐许邓韩冯曹彭曾肖田董潘袁蔡蒋余于杜叶程魏苏吕丁"
            "任卢姚沈钟姜崔谭陆范汪廖石金韦贾夏付方邹熊白孟秦邱侯江尹薛闫段雷龙黎史陶贺毛郝顾龚邵万覃武钱戴严欧莫孔向常汤康易乔赖文")
NAME = re.compile(rf"[{SURNAMES}](?:先生|女士|小姐|老师|阿姨|叔叔|姐|哥|总|师傅|[\u4e00-\u9fff]{{1,2}})")
CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
SPLIT = re.compile(r"另外|还有|再来|\n|；|;")


def _num(v: str) -> Optional[int]:
    return int(v) if v.isdigit() else CN_NUM.get(v)


def parse_message(text: str) -> dict:
    text = text or ""
    entities = []                          # [{"type", "text", "start", "end"}]

    def add(kind, m):
        entities.append({"type": kind, "text": m.group(0), "start": m.start(), "end": m.end()})

    m = PHONE.search(text)
    phone = m.group(0) if m else None
    if m: add("phone", m)

    m = QTY.search(text)
    qty = _num(m.group(1)) if m else None
    if m: add("qty", m)

    m = PRODUCT.search(text)
    product = m.group(0) if m else None
    if m and not any(e["start"] <= m.start() < e["end"] for e in entities): add("product", m)

    # By when: "下午5点前", "明天上午10点半", "5:30"
    deadline, day = None, 0
    m = TIME.search(text)
    if m:
        hour, minute = int(m.group(3)), 0
        if m.group(4) == "半": minute = 30
        elif m.group(4): minute = int(m.group(4).rstrip("分"))
        elif m.group(5): minute = int(m.group(5))
        if m.group(2) in ("下午", "傍晚", "晚上") and hour < 12: hour += 12
        if m.group(2) == "中午" and hour < 6: hour += 12          # 中午1点 = 13:00
        if hour < 24 and minute < 60:
            deadline, day = f"{hour:02d}:{minute:02d}", {"明天": 1, "后天": 2}.get(m.group(1), 0)
            add("time", m)

    # How soon: "半小时内", "20分钟内", "尽快"
    soon_minutes, urgent = None, False
    m = SOON.search(text)
    if m:
        add("time", m); urgent = True
        g = m.group(1) or ""
        if g.startswith("半"): soon_minutes = 30
        elif "分钟" in g: soon_minutes = int(re.match(r"\d+", g).group())
        elif "小时" in g: soon_minutes = 60 * (_num(g[0]) or 1)

    m = USUAL.search(text)
    usual = bool(m)
    if m: add("usual", m)

    # What is left: blank out what we understood and the polite filler, then split into pieces
    rest = list(text)
    for e in entities:
        for i in range(e["start"], e["end"]):
            rest[i] = " "
    rest = FILLER.sub(lambda x: " " * len(x.group(0)), "".join(rest))
    name, address_parts = None, []
    for tok in re.finditer(r"[^\s,，。.!！?？;；、]+", rest):
        word = tok.group(0)
        if ADDR_MARK.search(word):
            address_parts.append(tok)
        elif name is None and 2 <= len(word) <= 4 and NAME.fullmatch(word):
            name = word
            entities.append({"type": "name", "text": word, "start": tok.start(), "end": tok.end()})
    for tok in address_parts:
        entities.append({"type": "address", "text": tok.group(0), "start": tok.start(), "end": tok.end()})

    entities.sort(key=lambda e: e["start"])
    return {"name": name, "phone": phone, "address": " ".join(t.group(0) for t in address_parts) or None,
            "deadline": deadline, "day": day, "soon_minutes": soon_minutes, "urgent": urgent,
            "qty": qty, "product": product, "usual_place": usual, "entities": entities}


def parse_many(text: str) -> List[dict]:
    """One message, several orders: "王芳两桶文三路100号；另外李明一桶阳光小区" → 2 orders."""
    found = [parse_message(p) for p in SPLIT.split(text or "") if p.strip()]
    orders = [f for f in found if f["qty"] or f["address"] or f["name"]]
    return orders if len(orders) > 1 else [parse_message(text)]


# The old helpers, still used around the app
def find_phone(text): return parse_message(text)["phone"]
def find_address(text): return parse_message(text)["address"]
def find_name(text): return parse_message(text)["name"]
def find_deadline(text): return parse_message(text)["deadline"]


if __name__ == "__main__":
    for t in ["李明 13912345678 西湖区文三路100号 5号楼3楼 下午3点前",
              "我要两桶水文三路200号下午5点前13800138001",
              "王姐 老地方 两桶 半小时内",
              "明天上午10点半 送3桶农夫山泉 阳光小区3单元502室"]:
        r = parse_message(t)
        print(t, "\n  →", {k: v for k, v in r.items() if k != "entities" and v not in (None, False, 0)})
