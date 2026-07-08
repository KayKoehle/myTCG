"""Export a trained torch checkpoint to a stdlib-readable weights file.

The output feeds `engine/policy.py::PurePolicy`, which runs on Android
(Chaquopy) where PyTorch is unavailable.

    uv run --group ai python scripts/export_policy.py \
        --checkpoint stats/checkpoints/ai_nn_distributed_latest.pt \
        --out src/server/model/policy_weights.json

Run `python scripts/sync_mobile.py` afterwards to bundle it into the app.
"""
from __future__ import annotations

import argparse
import base64
import json
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


def _encode(tensor, dtype: str) -> str:
    values = tensor.detach().cpu().float().reshape(-1).tolist()
    if dtype == "f2":
        raw = struct.pack(f"<{len(values)}e", *values)
    elif dtype == "f4":
        raw = struct.pack(f"<{len(values)}f", *values)
    else:
        raise ValueError(f"Unsupported dtype: {dtype}")
    return base64.b64encode(raw).decode("ascii")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", default="stats/checkpoints/ai_nn_distributed_latest.pt")
    parser.add_argument("--out", default="src/server/model/policy_weights.json")
    parser.add_argument("--dtype", choices=["f2", "f4"], default="f2", help="f2 = float16 (half size), f4 = float32 (exact)")
    args = parser.parse_args()

    import torch

    checkpoint = torch.load(Path(args.checkpoint), map_location="cpu")
    state = checkpoint["model_state"]
    backbone = state["backbone"]
    policy_head = state["policy_head"]

    # nn.Linear stores weight as [out, in]; PurePolicy wants [in][out] for a
    # sparse-input first layer, so transpose everything once here.
    payload = {
        "feature_dim": int(checkpoint["feature_dim"]),
        "hidden_dim": int(checkpoint["hidden_dim"]),
        "action_dim": int(checkpoint["action_dim"]),
        "dtype": args.dtype,
        "w1": _encode(backbone["0.weight"].t(), args.dtype),
        "b1": _encode(backbone["0.bias"], args.dtype),
        "w2": _encode(backbone["2.weight"].t(), args.dtype),
        "b2": _encode(backbone["2.bias"], args.dtype),
        "wp": _encode(policy_head["weight"].t(), args.dtype),
        "bp": _encode(policy_head["bias"], args.dtype),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload), encoding="utf-8")
    size_mb = out.stat().st_size / 1e6
    print(f"Exported {args.checkpoint} -> {out} ({size_mb:.1f} MB, dtype={args.dtype})")


if __name__ == "__main__":
    main()
