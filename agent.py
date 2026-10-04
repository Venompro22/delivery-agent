from intent_nn import predict_intent
from order_parser import parse_message, find_phone

MIN_CONFIDENCE = 0.6


def handle_message(text, orders):
    intent, confidence = predict_intent(text)

    def ask_human(reason=""):
        return {"action": "ask_human", "intent": intent, "reason": reason}

    # 1) 🙋 Not sure enough → let a human decide
    if confidence < MIN_CONFIDENCE:
        return ask_human(f"low confidence ({confidence:.0%})")

    # 2) 📦 New order → fill the form
    if intent == "new_order":
        return {"action": "fill_form", "data": parse_message(text)}

    # Cancel / question need an existing order → find it by phone
    phone = find_phone(text)
    order = next((o for o in orders if o["phone"] == phone and o["status"] == "pending"), None)
    if not order:
        return ask_human("order not found")

    # 3) ❌ Cancel
    if intent == "cancel":
        return {"action": "cancel", "order_id": order["id"], "reply": "好的，您的订单已取消"}

    # 4) 🤔 Question
    if intent == "question":
        return {"action": "reply", "order_id": order["id"], "reply": "您的订单正在配送中 🚚"}

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
        "取消 19900000000",
    ]
    for t in tests:
        print(handle_message(t, fake_orders), "←", t)