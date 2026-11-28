import numpy as np

np.random.seed(1)
sentence = "我要两桶水"
T = len(sentence)          # number of characters
D = 8                      # size of each character's vector (embedding)

# 1) Each character becomes a vector (random for now — training would make them meaningful)
x = np.random.randn(T, D)

# 2) Three learned "lenses": Query (my question), Key (my label), Value (my content)
Wq = np.random.randn(D, D)
Wk = np.random.randn(D, D)
Wv = np.random.randn(D, D)
Q, K, V = x @ Wq, x @ Wk, x @ Wv

# 3) How well does each question match each key?  (T × T table)
scores = Q @ K.T / np.sqrt(D)

# 4) Mask: a character can NOT look at characters that come after it
mask = np.triu(np.ones((T, T)), k=1) == 1
scores[mask] = -np.inf

# 5) Softmax each row → attention weights (each row sums to 100%)
weights = np.exp(scores - scores.max(axis=1, keepdims=True))
weights /= weights.sum(axis=1, keepdims=True)

# 6) Each character gathers information from the ones it attends to
out = weights @ V

print("Who looks at whom? (row = the char that is looking)\n")
print("      " + "     ".join(sentence))
for i, ch in enumerate(sentence):
    print(f"{ch}  " + "  ".join(f"{w:4.0%}" for w in weights[i]))
print(f"\nOutput shape: {out.shape}  ← every char now carries info from its past")
