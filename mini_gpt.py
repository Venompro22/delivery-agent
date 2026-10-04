import numpy as np
from intent_nn import DATA

# 📚 Our "book": all messages, one per line ("\n" = end of a message)
text = "\n".join(t for t, _ in DATA) + "\n"
chars = sorted(set(text))
V = len(chars)                                  # vocabulary size
stoi = {c: i for i, c in enumerate(chars)}      # char → number
itos = {i: c for c, i in stoi.items()}          # number → char

# 📊 Count: counts[a, b] = how many times char b came right after char a
counts = np.zeros((V, V))
for i in range(len(text) - 1):
    a = text[i]
    b = text[i + 1]
    counts[stoi[a], stoi[b]] += 1

# 🎲 Turn counts into probabilities (each row sums to 1) — "+0.01" = small smoothing
probs = (counts + 0.01) / (counts + 0.01).sum(axis=1, keepdims=True)


def generate(max_len=30):
    out = ""
    current = "\n"   # start like a new message
    if current not in stoi:
        raise ValueError(f"Character '{current}' not in vocabulary")
    for _ in range(max_len):
        row = probs[stoi[current]]
        nxt = itos[np.random.choice(V, p=row)]
        if nxt == "\n":
            break
        out += nxt
        current = nxt
    return out


if __name__ == "__main__":
    print(f"📚 {len(text)} characters · {V} different")
    print(f"After '取' the most likely next char is: '{itos[counts[stoi['取']].argmax()]}'")
    print("\n🤖 Generated messages:")
    np.random.seed(0)
    for _ in range(8):
        print("  ", generate())