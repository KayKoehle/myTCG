"""Neural policy: deterministic featurization and dependency-free inference.

The network (Linear→ReLU→Linear→ReLU→policy head) is trained with PyTorch
(`training.py`) but must also run on Android where torch is unavailable.
`scripts/export_policy.py` converts a checkpoint into a JSON weights file that
`PurePolicy` evaluates with nothing beyond the standard library.

Featurization lives here — `training.py` imports it — so training and every
inference path build *identical* features. Token hashing uses crc32, never
Python's builtin `hash()`, which is randomized per process.
"""
from __future__ import annotations

import base64
import json
import math
import struct
import zlib
from array import array
from pathlib import Path

_HASH_START = 32


def _hash_tokens(text: str, hash_dim: int) -> list[int]:
    tokens = text.replace(";", " ").replace("|", " ").replace(",", " ").replace("=", " ").split()
    if not tokens:
        return [0]
    return [zlib.crc32(tok.encode("utf-8")) % hash_dim for tok in tokens]


def _parse_observation(observation: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in observation.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _safe_tuple(value: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        import ast

        parsed = ast.literal_eval(value)
        if isinstance(parsed, tuple) and len(parsed) == 2:
            return int(parsed[0]), int(parsed[1])
    except Exception:  # noqa: BLE001
        pass
    return default


def obs_to_features(observation: str, feature_dim: int) -> list[float]:
    """L2-normalized feature vector for an observation string."""
    vec = [0.0] * feature_dim
    fields = _parse_observation(observation)

    phase_map = {"MULLIGAN": 0, "DRAW": 0, "MAIN": 1, "GAME_OVER": 2}
    phase = fields.get("phase", "")
    if phase in phase_map:
        vec[phase_map[phase]] = 1.0

    turn = float(fields.get("turn", "0") or 0)
    vec[3] = min(1.0, turn / 50.0)
    vec[4] = float(fields.get("current", "0") or 0)

    vp0, vp1 = _safe_tuple(fields.get("vp", "(0,0)"), (0, 0))
    vec[5] = vp0 / 4.0
    vec[6] = vp1 / 4.0

    mana0, mana1 = _safe_tuple(fields.get("mana", "(0,0)"), (0, 0))
    vec[7] = mana0 / 10.0
    vec[8] = mana1 / 10.0

    deck0, deck1 = _safe_tuple(fields.get("deck_sizes", "(0,0)"), (0, 0))
    vec[9] = min(1.0, deck0 / 60.0)
    vec[10] = min(1.0, deck1 / 60.0)

    own_hand = fields.get("own_hand", "")
    vec[11] = min(1.0, len([c for c in own_hand.split(",") if c]) / 20.0) if own_hand else 0.0

    opp_hand = fields.get("opponent_hand", "")
    if opp_hand.startswith("size="):
        try:
            vec[12] = min(1.0, float(opp_hand.split("=", 1)[1]) / 20.0)
        except ValueError:
            vec[12] = 0.0
    else:
        vec[12] = min(1.0, len([c for c in opp_hand.split(",") if c]) / 20.0) if opp_hand else 0.0

    board = fields.get("board", "")
    base = 13
    for idx, part in enumerate(board.split("|")[:6]):
        if "=" not in part:
            continue
        cards = part.split("=", 1)[1]
        vec[base + idx] = min(1.0, len([c for c in cards.split(",") if c]) / 10.0)

    vec[19] = 0.0 if fields.get("pending_choice", "None") == "None" else 1.0

    hash_dim = max(1, feature_dim - _HASH_START)
    for idx in _hash_tokens(observation, hash_dim):
        vec[_HASH_START + idx] += 1.0

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0.0:
        vec = [v / norm for v in vec]
    return vec


def _decode_array(payload: str, dtype: str) -> array:
    raw = base64.b64decode(payload)
    if dtype == "f2":
        count = len(raw) // 2
        return array("f", struct.unpack(f"<{count}e", raw))
    if dtype == "f4":
        values = array("f")
        values.frombytes(raw)
        return values
    raise ValueError(f"Unsupported dtype: {dtype}")


class PurePolicy:
    """Stdlib-only forward pass of the exported actor network.

    Weight matrices are flat `array('f')` buffers. The input is sparse (a few
    hundred non-zero features out of 4096), so the first layer only visits
    non-zero rows.
    """

    def __init__(self, feature_dim: int, hidden_dim: int, action_dim: int, w1: array, b1: array, w2: array, b2: array, wp: array, bp: array):
        self.feature_dim = feature_dim
        self.hidden_dim = hidden_dim
        self.action_dim = action_dim
        # w1 is stored [feature][hidden] so a non-zero feature selects one row.
        self._w1, self._b1 = w1, b1
        self._w2, self._b2 = w2, b2
        self._wp, self._bp = wp, bp

    @classmethod
    def load(cls, path: str | Path) -> "PurePolicy":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        dtype = data.get("dtype", "f2")
        return cls(
            feature_dim=int(data["feature_dim"]),
            hidden_dim=int(data["hidden_dim"]),
            action_dim=int(data["action_dim"]),
            w1=_decode_array(data["w1"], dtype),
            b1=_decode_array(data["b1"], dtype),
            w2=_decode_array(data["w2"], dtype),
            b2=_decode_array(data["b2"], dtype),
            wp=_decode_array(data["wp"], dtype),
            bp=_decode_array(data["bp"], dtype),
        )

    def logits(self, features: list[float]) -> list[float]:
        hidden = self.hidden_dim
        h1 = list(self._b1)
        w1 = self._w1
        for i, value in enumerate(features):
            if value == 0.0:
                continue
            offset = i * hidden
            for j in range(hidden):
                h1[j] += w1[offset + j] * value
        for j in range(hidden):
            if h1[j] < 0.0:
                h1[j] = 0.0

        h2 = list(self._b2)
        w2 = self._w2
        for i in range(hidden):
            value = h1[i]
            if value == 0.0:
                continue
            offset = i * hidden
            for j in range(hidden):
                h2[j] += w2[offset + j] * value
        for j in range(hidden):
            if h2[j] < 0.0:
                h2[j] = 0.0

        out = list(self._bp)
        wp = self._wp
        action_dim = self.action_dim
        for i in range(hidden):
            value = h2[i]
            if value == 0.0:
                continue
            offset = i * action_dim
            for j in range(action_dim):
                out[j] += wp[offset + j] * value
        return out

    def best_legal_index(self, observation: str, num_legal: int) -> int:
        """Index (into the legal-action list) of the highest-logit action."""
        features = obs_to_features(observation, self.feature_dim)
        logits = self.logits(features)
        limit = min(num_legal, self.action_dim)
        if limit <= 0:
            return 0
        best = 0
        for idx in range(1, limit):
            if logits[idx] > logits[best]:
                best = idx
        return best


def find_default_weights() -> Path | None:
    """Exported weights bundled next to the engine package, if any."""
    candidate = Path(__file__).resolve().parent.parent / "model" / "policy_weights.json"
    if candidate.exists():
        return candidate
    return None
