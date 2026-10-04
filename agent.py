from intent_nn import predict_intent
from order_parser import parse_message, find_phone

MIN_CONFIDENCE = 0.6
DRIVER_INTENTS = ("delivered", "delay", "not_home")


def handle_message(text, orders):
    intent, confidence = predict_intent(text)
    sender = "driver" if intent in DRIVER_INTENTS else "customer"

    def ask_human(reason=""):
        return {"action": "ask_human", "sender": sender, "intent": intent, "reason": reason}

    # 1) 🙋 Not sure enough → let a human decide
    if confidence < MIN_CONFIDENCE:
        return ask_human(f"low confidence ({confidence:.0%})")

    # 2) 📦 Customer: new order → fill the form
    if intent == "new_order":
        return {"action": "fill_form", "sender": sender, "data": parse_message(text)}

    # 3) ⏰ Driver: delay → warn (no specific order needed)
    if intent == "delay":
        return {"action": "notify_delay", "sender": sender,
                "reply": "您好，因路况原因配送会稍有延迟，敬请谅解 🙏"}

    # Everything else needs an existing order → find it by phone
    phone = find_phone(text)
    order = next((o for o in orders if o["phone"] == phone and o["status"] == "pending"), None)
    if not order:
        return ask_human("order not found")

    # 4) ❌ Customer: cancel
    if intent == "cancel":
        return {"action": "cancel", "sender": sender, "order_id": order["id"],
                "reply": "好的，您的订单已取消"}

    # 5) 🤔 Customer: question
    if intent == "question":
        return {"action": "reply", "sender": sender, "order_id": order["id"],
                "reply": "您的订单正在配送中 🚚"}

    # 6) ✅ Driver: delivered → mark the order as delivered
    if intent == "delivered":
        return {"action": "deliver", "sender": sender, "order_id": order["id"]}

    # 7) 🏠 Driver: customer not home → someone should call the customer
    if intent == "not_home":
        return {"action": "call_customer", "sender": sender, "order_id": order["id"],
                "phone": order["phone"]}

    return ask_human("unknown intent")


if __name__ == "__main__":
    fake_orders = [
        {"id": "a1", "name": "李明", "phone": "13912345678", "status": "pending"},
        {"id": "b2", "name": "王芳", "phone": "15800002222", "status": "pending"},
    ]
    tests = [
        "李明 13912345678 文三路100号 我要两桶水",
        "取消订单 不要了 13912345678",
        "我的水在哪里 15800002222",
        "已送达 13912345678",
        "堵车了 晚一点到",
        "客户不在家 电话不接 15800002222",
        "取消 19900000000",
    ]
    for t in tests:
        print(handle_message(t, fake_orders), "←", t)