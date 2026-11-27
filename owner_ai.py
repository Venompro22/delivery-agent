"""Owner assistant: answers questions about the shop's numbers with local rules (no API key).

Understands Chinese, English and Moroccan Darija (Arabic script or Latin "3arbizi").
The numbers always come from fileapp.stats_data(), the same dict /api/stats returns,
so the chat and the stats page can never disagree.
"""
from __future__ import annotations

import re

CJK = re.compile(r"[一-鿿]")
ARABIC = re.compile(r"[؀-ۿ]")
# Latin-script Darija markers (digits stand for Arabic letters: 3=ع, 7=ح, 9=ق, 2=ء)
DARIJA_LATIN = re.compile(r"\b(ch7al|chhal|chal|chno|chnou|achno|chkoun|chkon|lyoum|lyom|lioum|dyal|talab|tlab|dial|"
                          r"simana|semana|ssimana|osbou3|flous|flouss|dkhl|dakhl|dkhlna|m3atl|m3tl|t3atl|"
                          r"t2khr|a7san|ahsan|kaytba3|kayba3|bzaf|bzzaf|sel3a|sl3a|wach|wesh|3tih|3ti|"
                          r"khlas|khalass|lghi|wsal|wslat|zid|tmn|taman)\b", re.I)


def detect_lang(text: str) -> str:
    """'zh', 'darija' or 'en'. Darija words win over Chinese characters, because a
    Darija command can still name a Chinese customer ("lghi talab dyal 王芳")."""
    if ARABIC.search(text) or DARIJA_LATIN.search(text):
        return "darija"
    return "zh" if CJK.search(text) else "en"


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.I) is not None


WEEK = r"本周|这周|这个星期|一周|七天|7天|week|7 ?days|simana|semana|ssimana|osbou3|سيمانة|السيمانة|سمانة|أسبوع|اسبوع|الأسبوع"
DRIVER = r"司机|配送员|骑手|driver|livreur|chauffeur|موزع|سائق|ليفرور|الليفرور|شيفور"
BEST = r"最好|最棒|送得最多|送最多|最多单|第一|best|\btop\b|a7san|ahsan|احسن|أحسن|meilleur|lwl|lowel|الأول"
PRODUCT = (r"产品|商品|卖得|卖的|畅销|热销|卖最多|product|item|sell|sold|selling|popular|sel3a|sl3a|produit|"
           r"kaytba3|kayba3|سلعة|السلعة|منتوج|منتج|كيتباع|تباع")
# "Late right now" (open orders), not "late deliveries" (finished ones)
NOW = (r"现在|目前|此刻|要迟到|会迟到|快迟到|将要|\bnow\b|currently|at risk|going to be late|"
       r"\bdaba\b|\bdba\b|دابا|دبا|دروك|هاد الساعة")
LATE = r"迟到|晚到|超时|延误|晚了|\blate\b|delay|m3atl|m3tl|t3atl|t2khr|retard|معطل|متأخر|تعطل|تأخير|تعطلات|روطار"
REVENUE = (r"收入|营业额|销售额|赚了|挣了|进账|流水|revenue|earn|made|money|sales|income|turnover|"
           r"flous|flouss|dkhl|dakhl|dkhlna|ch7al|chhal|فلوس|الفلوس|دخل|دخلنا|مدخول|شحال|بشحال")


# A question word. Pasted WeChat messages ("车坏了 会晚到") have none, so they are not questions.
ASK = (r"多少|几|哪|谁|吗|？|\?|怎么样|情况|how|what|who|which|show|tell|ch7al|chhal|chkoun|chkon|chno|achno|"
       r"wach|wesh|شحال|شكون|شنو|واش|؟|كم")


def question_topic(text: str) -> str | None:
    """Which stats question this is, or None when it is not one.
    Chinese needs a question word (customer and driver messages are Chinese); "late" always
    does, because drivers write "要迟到了". Order matters: "best driver" also looks like revenue."""
    asks = _has(ASK, text)
    loose = asks or not CJK.search(text)
    if _has(DRIVER, text) and _has(BEST, text) and loose:
        return "best_driver"
    if _has(PRODUCT, text) and loose and \
            (_has(BEST, text) or _has(r"畅销|热销|卖最多|popular|bzaf|bzzaf|بزاف|كتر|أكثر|اكثر", text)):
        return "top_products"
    if _has(LATE, text) and asks:
        return "late_now" if _has(NOW, text) else "late"
    if _has(REVENUE, text) and loose:
        return "revenue_week" if _has(WEEK, text) else "revenue_today"
    return None


# One template per topic and language. {…} are filled from stats_data().
T = {
    "revenue_today": {
        "zh": "今天收入 {revenue_today:g} 元（已送达的订单）。待收款 {to_collect:g} 元。",
        "en": "Revenue today: {revenue_today:g} yuan (delivered orders). Still to collect: {to_collect:g} yuan.",
        "darija": "دخلتي اليوم {revenue_today:g} يوان (الطلبات اللي توصلات). باقي ما تخلصش: {to_collect:g} يوان.",
    },
    "revenue_week": {
        "zh": "最近7天收入 {revenue_week:g} 元，其中今天 {revenue_today:g} 元。待收款 {to_collect:g} 元。",
        "en": "Revenue over the last 7 days: {revenue_week:g} yuan, {revenue_today:g} of it today. Still to collect: {to_collect:g} yuan.",
        "darija": "دخلتي هاد 7 أيام {revenue_week:g} يوان، منهم {revenue_today:g} اليوم. باقي ما تخلصش: {to_collect:g} يوان.",
    },
    "late": {
        "zh": "迟到送达的订单：{late} 单。准时率 {on_time}。",
        "en": "Late deliveries: {late}. On-time rate: {on_time}.",
        "darija": "الطلبات اللي توصلات معطلة: {late}. نسبة فالوقت: {on_time}.",
    },
    "best_driver": {
        "zh": "本周最好的司机是 {name}：7天送了 {week} 单，今天 {today} 单，准时率 {on_time}。",
        "en": "Best driver this week: {name} with {week} deliveries in 7 days ({today} today), on time {on_time}.",
        "darija": "أحسن ليفرور هاد السيمانة هو {name}: وصل {week} طلبات ف 7 أيام ({today} اليوم)، فالوقت {on_time}.",
    },
    "top_products": {
        "zh": "最近7天卖得最好的：{list}。",
        "en": "Best sellers over the last 7 days: {list}.",
        "darija": "السلعة اللي تباعت بزاف هاد 7 أيام: {list}.",
    },
}
LATE_NOW = {
    "head": {"zh": "现在有 {n} 单会迟到：", "en": "{n} open orders will be late:",
             "darija": "دابا كاين {n} طلبات غادي يتعطلو:"},
    "line": {"zh": "• {customer}（司机：{driver}）晚 {minutes_late} 分钟 · 订单 {id}",
             "en": "• {customer} (driver: {driver}) {minutes_late} min late · order {id}",
             "darija": "• {customer} (الليفرور: {driver}) معطل ب {minutes_late} دقيقة · طلب {id}"},
    "none": {"zh": "现在没有会迟到的订单。", "en": "No open order is running late right now.",
             "darija": "دابا حتى طلب ما معطل."},
    "no_driver": {"zh": "未分配", "en": "unassigned", "darija": "بلا ليفرور"},
}
NO_DATA = {
    "best_driver": {"zh": "最近7天还没有司机送达订单。", "en": "No driver delivered an order in the last 7 days.",
                    "darija": "حتى ليفرور ما وصل شي طلب هاد 7 أيام."},
    "top_products": {"zh": "最近7天还没有卖出产品。", "en": "No products sold in the last 7 days.",
                     "darija": "ما تباعت حتى سلعة هاد 7 أيام."},
}
UNKNOWN = {"zh": "未知", "en": "n/a", "darija": "ما معروفش"}


def answer(text: str, get_stats, get_late_now) -> dict | None:
    """{"action": "answer", "topic", "lang", "reply"} for a stats question, else None.
    get_stats() → fileapp.stats_data(), get_late_now() → fileapp.late_now(); both are
    only called once the text is known to be a question, so other messages cost nothing."""
    topic = question_topic(text)
    if not topic:
        return None
    lang = detect_lang(text)
    if topic == "late_now":
        late = get_late_now()
        if not late:
            return {"action": "answer", "topic": topic, "lang": lang, "reply": LATE_NOW["none"][lang]}
        lines = [LATE_NOW["head"][lang].format(n=len(late))]
        lines += [LATE_NOW["line"][lang].format(**{**x, "driver": x["driver"] or LATE_NOW["no_driver"][lang]})
                  for x in late]
        return {"action": "answer", "topic": topic, "lang": lang, "reply": "\n".join(lines)}
    stats = get_stats()

    def pct(v):
        return f"{v}%" if v is not None else UNKNOWN[lang]

    values = dict(stats)
    if topic == "late":
        values["late"] = sum(d["late"] for d in stats["drivers"])
        values["on_time"] = pct(stats["on_time_pct"])
    elif topic == "best_driver":
        # stats["drivers"] is already sorted by deliveries this week; skip the "no driver" row
        best = next((d for d in stats["drivers"] if d["week"] and d["name"] != "No driver"), None)
        if not best:
            return {"action": "answer", "topic": topic, "lang": lang, "reply": NO_DATA[topic][lang]}
        values.update(best, on_time=pct(best["on_time_pct"]))
    elif topic == "top_products":
        top = stats["top_products"][:3]
        if not top:
            return {"action": "answer", "topic": topic, "lang": lang, "reply": NO_DATA[topic][lang]}
        sep = "、" if lang == "zh" else ", "
        values["list"] = sep.join(f"{p['name']} ×{p['qty']}{p['unit']}" for p in top)
    return {"action": "answer", "topic": topic, "lang": lang, "reply": T[topic][lang].format(**values)}


# ---------------------------------------------------------------------------
# Step 2: owner actions. parse_action() only builds a preview; nothing changes until
# the owner presses Confirm, which runs fileapp.run_owner_action() on the stored action.

FEE = (r"配送费|运费|送货费|delivery fee|delivery charge|shipping fee|frais de livraison|ثمن التوصيل|تمن التوصيل|"
       r"taman tawsil|tmn tawsil|tawsil|التوصيل")
ADD_PRODUCT = (r"添加产品|新增产品|新产品|加产品|上架|add (?:a )?(?:new )?product|new product|add item|"
               r"zid (?:produit|sel3a|sl3a)|زيد (?:سلعة|منتوج|منتج)|سلعة جديدة")
ASSIGN = r"分配|派给|交给|给司机|assign|give .+ to|3tih?\b|عطي|عطيه|عطيها"
PAID = r"已付|付款|付了|收钱|收款|已收|\bpaid\b|khlas|khalass|خلص|تخلص"
DELIVER = r"已送达|送达|已送到|送到了|\bdelivered\b|mark .*delivered|wsal|wslat|توصل|وصلات|وصل"
CANCEL = r"取消|\bcancel|lghi|ألغي|الغي|لغي|annul"
PRICE = r"价格|价钱|改价|\bprice\b|taman|\btmn\b|ثمن|تمن|prix"

PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def _numbers(text: str) -> list[float]:
    """Numbers the owner typed, phones removed."""
    return [float(n) for n in re.findall(r"\d+(?:\.\d+)?", PHONE.sub(" ", text))]


def _find_order(text: str, orders: list):
    """(order, how) for the order the text points at: by id, phone or customer name."""
    lowered = text.lower()
    for o in orders:
        if re.search(rf"(?<![0-9a-f]){re.escape(o.id)}(?![0-9a-f])", lowered):
            return o, "id"
    m = PHONE.search(text)
    hits = [o for o in orders if m and o.phone == m.group(0)]
    how = "phone"
    if not hits:
        names = [o.customer_name for o in orders if len(o.customer_name or "") >= 2
                 and o.customer_name.lower() in lowered]
        longest = max(names, key=len, default=None)
        hits, how = [o for o in orders if o.customer_name == longest], "name"
    if not hits:
        return None, None
    # Several orders for the same customer: the newest still-open one, else the newest
    hits.sort(key=lambda o: (o.status.value in ("pending", "requested"), o.created_at or 0), reverse=True)
    return hits[0], how


def _product_fields(text: str) -> dict | None:
    """"添加产品 矿泉水 2元/瓶" → {"name": "矿泉水", "price": 2.0, "unit": "瓶"}."""
    rest = re.sub(ADD_PRODUCT, " ", text, count=1, flags=re.I)
    m = re.search(r"\d+(?:\.\d+)?", rest)
    if not m:
        return None
    name = re.sub(r"[:：,，]|价格|price|\bat\b|\bfor\b", " ", rest[:m.start()], flags=re.I).strip()
    unit = re.sub(r"元|块|yuan|rmb|dh|每|\bper\b|\ba\b|/|[:：,，]", " ", rest[m.end():], flags=re.I).strip()
    return {"name": name, "price": float(m.group(0)), "unit": unit.split()[0] if unit else ""} if name else None


def _pay_method(text: str) -> str:
    if _has(r"微信|wechat|weixin", text):
        return "wechat"
    if _has(r"支付宝|alipay", text):
        return "alipay"
    return "cash"


def parse_action(text: str, orders: list, drivers: list) -> dict | None:
    """The action the owner asks for, as plain data ({"kind", …, "lang"}), or None.
    A recognised action that cannot be built gets {"kind", "error"} instead."""
    lang = detect_lang(text)
    if _has(FEE, text):
        nums = _numbers(text)
        return ({"kind": "fee", "fee": nums[-1], "lang": lang} if nums
                else {"kind": "fee", "lang": lang, "error": "no amount"})
    if _has(ADD_PRODUCT, text):
        fields = _product_fields(text)
        return ({"kind": "add_product", **fields, "lang": lang} if fields
                else {"kind": "add_product", "lang": lang, "error": "need a name and a price"})

    kind = next((k for k, p in (("assign", ASSIGN), ("paid", PAID), ("deliver", DELIVER),
                                ("cancel", CANCEL), ("price", PRICE)) if _has(p, text)), None)
    if not kind:
        return None
    order, how = _find_order(text, orders)
    if not order:
        return {"kind": kind, "lang": lang, "error": "order not found"}
    action = {"kind": kind, "order_id": order.id, "by": how, "lang": lang}
    if kind == "assign":
        lowered = text.lower()
        names = [d for d in drivers if d["name"] and d["name"].lower() in lowered]
        if not names:
            return {**action, "error": "driver not found"}
        action["driver_id"] = max(names, key=lambda d: len(d["name"]))["id"]
    elif kind == "paid":
        action["method"] = _pay_method(text)
    elif kind == "price":
        nums = _numbers(text.lower().replace(order.id, " "))
        if not nums:
            return {**action, "error": "no amount"}
        action["price"] = nums[-1]
    return action


PREVIEW = {
    "assign": {"zh": "把 {order} 的订单分配给 {driver}", "en": "Assign {order}'s order to {driver}",
               "darija": "نعطي الطلب ديال {order} ل {driver}"},
    "deliver": {"zh": "把 {order} 的订单标记为已送达", "en": "Mark {order}'s order as delivered",
                "darija": "نحط الطلب ديال {order} توصل"},
    "paid": {"zh": "{order} 的订单已付款（{method}）", "en": "Mark {order}'s order as paid ({method})",
             "darija": "نحط الطلب ديال {order} تخلص ({method})"},
    "cancel": {"zh": "取消 {order} 的订单", "en": "Cancel {order}'s order", "darija": "نلغي الطلب ديال {order}"},
    "price": {"zh": "把 {order} 的订单价格从 {old} 改成 {price:g} 元",
              "en": "Change {order}'s order price from {old} to {price:g} yuan",
              "darija": "نبدل الثمن ديال {order} من {old} ل {price:g} يوان"},
    "add_product": {"zh": "添加产品：{name}，{price:g} 元/{unit}", "en": "Add product: {name}, {price:g} yuan / {unit}",
                    "darija": "نزيد سلعة: {name}، {price:g} يوان / {unit}"},
    "fee": {"zh": "把配送费从 {old} 改成 {fee:g} 元", "en": "Change the delivery fee from {old} to {fee:g} yuan",
            "darija": "نبدل ثمن التوصيل من {old} ل {fee:g} يوان"},
}
DONE = {"zh": "✅ 已完成：", "en": "✅ Done: ", "darija": "✅ تم: "}
CONFIRM_HINT = {"zh": "按确认执行，什么都还没改。", "en": "Nothing has changed yet. Press Confirm to do it.",
                "darija": "مازال ما تبدل والو. ورك على تأكيد."}


def describe(action: dict, **names) -> str:
    """One line for the preview card. `names` holds order / driver / old for display."""
    values = {"order": "?", "driver": "?", "old": "-", "unit": "-", **action, **names}
    if not values["unit"]:
        values["unit"] = "-"
    return PREVIEW[action["kind"]][action["lang"]].format(**values)


class PendingActions:
    """Previewed actions waiting for Confirm, in the database so they survive restarts and
    work for Telegram (no browser session). A token works once and expires."""
    TTL = 15 * 60

    def __init__(self, path: str):
        import db
        self.db, self.path = db, path
        con = db.connect(path)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS ai_pending (token TEXT PRIMARY KEY, "
                        "action TEXT NOT NULL, created REAL NOT NULL)")
            con.commit()
        finally:
            con.close()

    def put(self, action: dict) -> str:
        import json
        import secrets
        import time
        token = secrets.token_urlsafe(16)
        con = self.db.connect(self.path)
        try:
            con.execute("DELETE FROM ai_pending WHERE created < ?", (time.time() - self.TTL,))
            con.execute("INSERT INTO ai_pending (token, action, created) VALUES (?, ?, ?)",
                        (token, json.dumps(action, ensure_ascii=False), time.time()))
            con.commit()
        finally:
            con.close()
        return token

    def take(self, token: str) -> dict | None:
        """The action for this token, removed so it can never run twice (None if unknown/expired)."""
        import json
        import time
        con = self.db.connect(self.path)
        try:
            row = con.execute("SELECT action, created FROM ai_pending WHERE token = ?", (str(token),)).fetchone()
            gone = con.execute("DELETE FROM ai_pending WHERE token = ?", (str(token),)).rowcount
            con.commit()
        finally:
            con.close()
        if not row or not gone or row[1] < time.time() - self.TTL:
            return None
        return json.loads(row[0])


HELP = {
    "zh": "可以问我：今天收入多少？本周收入？几单迟到？最好的司机？什么卖得最好？\n"
          "或者下指令：李明 已付款 微信 · 取消 王芳 · 把李明分配给Ahmed · 王芳 价格 30 · "
          "添加产品 矿泉水 2元/瓶 · 配送费 5",
    "en": "Ask me: revenue today? this week? late orders? best driver? top products?\n"
          "Or give a command: mark Karim paid wechat · cancel Karim · assign Karim to Ahmed · "
          "Karim price 30 · add product Water 2 yuan bottle · delivery fee 5",
    "darija": "سولني: شحال دخلنا اليوم؟ هاد السيمانة؟ شحال من طلب معطل؟ شكون أحسن ليفرور؟ شنو كيتباع بزاف؟\n"
              "ولا عطيني أمر: Karim khlas wechat · lghi Karim · 3ti Karim l Ahmed · Karim taman 30 · "
              "zid sel3a Water 2 bottle · taman tawsil 5",
}
BUTTONS = {"zh": ("确认", "取消"), "en": ("Confirm", "Cancel"), "darija": ("تأكيد", "لا")}
DROPPED = {"zh": "已取消，什么都没改。", "en": "Cancelled, nothing changed.", "darija": "تلغى، ما تبدل والو."}
