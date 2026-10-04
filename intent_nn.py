import numpy as np
import os

# 📚 Data: (message, label)
# 👤 customer: 0 = new order · 1 = cancel · 2 = question
# 🚚 driver:   3 = delivered · 4 = delay · 5 = not home
DATA = [
    # 📦 0 new order (customer)
    ("我要两桶水 文三路100号", 0),
    ("送一桶水到5号楼3楼", 0),
    ("我要订水 电话13800001234", 0),
    ("帮我送三桶水 下午3点前", 0),
    ("给我送两桶纯净水", 0),
    ("订一桶水 送到公司", 0),
    ("麻烦送水 阳光小区3单元", 0),
    ("我想买四桶水 今天能送吗", 0),
    ("来两桶水 地址西湖区", 0),
    # ❌ 1 cancel (customer)
    ("取消订单 不要了", 1),
    ("不要了 取消吧", 1),
    ("我的订单取消一下", 1),
    ("算了 不要水了 取消", 1),
    ("不用送了 谢谢", 1),
    ("订单不要了 退掉吧", 1),
    ("帮我取消刚才的订单", 1),
    ("今天不需要了 取消", 1),
    ("不要水了谢谢", 1),
    # 🤔 2 question (customer)
    ("我的水在哪里", 2),
    ("什么时候到", 2),
    ("水送到哪了 怎么还没到", 2),
    ("司机在哪里 多久到", 2),
    ("订单到哪里了", 2),
    ("还要多久才到", 2),
    ("怎么还没送到", 2),
    ("我的水什么时候能到", 2),
    ("请问司机快到了吗", 2),
    # ✅ 3 delivered (driver)
    ("已送达 13912345678", 3),
    ("送到了 客户已签收", 3),
    ("水已经送到 5号楼", 3),
    ("完成 已交给客户", 3),
    ("已经送完了", 3),
    ("订单完成 已签收", 3),
    ("放在门口了 已送达", 3),
    ("客户收到了", 3),
    ("这单送好了", 3),
    # ⏰ 4 delay (driver)
    ("堵车了 晚一点到", 4),
    ("路上堵车 要迟到了", 4),
    ("车坏了 会晚到", 4),
    ("下雨 路不好走 晚点到", 4),
    ("前面出事故了 要晚一点", 4),
    ("电动车没电了 晚点到", 4),
    ("路太堵了 可能迟到半小时", 4),
    ("找不到路 会晚一些", 4),
    ("交通管制 要绕路 晚到", 4),
    # 🏠 5 not home (driver)
    ("客户不在家 电话不接", 5),
    ("敲门没人 打电话也不接", 5),
    ("没人在家 怎么办", 5),
    ("联系不上客户", 5),
    ("到了没人开门", 5),
    ("客户电话关机", 5),
    ("按门铃没人应", 5),
    ("地址找不到人", 5),
    ("打了三次电话都没接", 5),
]
LABELS = ["📦 new order", "❌ cancel", "🤔 question",
          "✅ delivered", "⏰ delay", "🏠 not home"]


def build_vocab(texts):
    chars = set()
    for text in texts:
        for ch in text:
            if ch != " ":
                chars.add(ch)
    return sorted(chars)


def text_to_vector(text, vocab):
    return [1 if ch in text else 0 for ch in vocab]


def softmax(z):
    e = np.exp(z - z.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


INTENTS = ["new_order", "cancel", "question", "delivered", "delay", "not_home"]
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "intent_model.npz")


def predict_intent(text):
    m = np.load(MODEL_PATH)
    vocab = list(m["vocab"])
    vector = np.array(text_to_vector(text, vocab))
    p = softmax(vector @ m["W"] + m["b"])
    best = p.argmax()
    return INTENTS[best], float(p[best])


if __name__ == "__main__":
    # ✂️ Split: 80% to learn from, 20% kept hidden for an honest exam
    np.random.seed(42)
    order = np.random.permutation(len(DATA))
    cut = int(len(DATA) * 0.8)
    train = [DATA[i] for i in order[:cut]]
    test = [DATA[i] for i in order[cut:]]

    texts = [t for t, _ in train]
    vocab = build_vocab(texts)

    X = np.array([text_to_vector(text, vocab) for text in texts])
    y = np.array([label for _, label in train])
    W = np.random.randn(len(vocab), len(LABELS)) * 0.01
    b = np.zeros(len(LABELS))

    Y = np.eye(len(LABELS))[y]
    lr = 0.5

    for epoch in range(501):
        probs = softmax(X @ W + b)
        loss = -np.mean(np.log(probs[range(len(y)), y] + 1e-9))

        error = probs - Y
        dW = X.T @ error / len(y)
        db = error.mean(axis=0)

        W -= lr * dW
        b -= lr * db

        if epoch % 100 == 0:
            acc = (probs.argmax(axis=1) == y).mean()
            print(f"epoch {epoch:3d} | loss {loss:.3f} | accuracy {acc:.0%}")

    # 🧪 Honest exam: messages the model NEVER saw while learning
    Xt = np.array([text_to_vector(t, vocab) for t, _ in test])
    yt = np.array([label for _, label in test])
    test_acc = (softmax(Xt @ W + b).argmax(axis=1) == yt).mean()
    print(f"\n🧪 Train: {len(train)} examples · Test: {len(test)} hidden examples")
    print(f"🧪 Accuracy on hidden test set: {test_acc:.0%}")

    print("\n🎯 Testing on NEW messages:")
    new_messages = [
        "我想要一桶水",
        "不要了谢谢",
        "我的水什么时候送到",
        "取消",
        "司机到哪了",
        "已经送到了",
        "堵车 晚点到",
        "客户电话不接",
    ]
    for msg in new_messages:
        v = np.array(text_to_vector(msg, vocab))
        p = softmax(v @ W + b)
        best = p.argmax()
        print(f"{LABELS[best]:<14} {p[best]:.0%}  ←  {msg}")

    np.savez("intent_model.npz", W=W, b=b, vocab=vocab)
    print("\n💾 Model saved to intent_model.npz")