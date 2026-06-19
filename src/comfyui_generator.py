"""
ComfyUI image generator for card art using Flux2-Klein.

Submits workflows to a running ComfyUI instance (default: http://localhost:8188),
polls for completion, and saves the output image into the appropriate
images/color/<type>/ directory so svg_generator can pick it up.
"""

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import csv

COMFYUI_URL = "http://localhost:8188"

# Card type → subdirectory under images/color/
TYPE_DIR = {
    "Creature": "creatures",
    "Being": "creatures",
    "Hero": "heroes",
    "Location": "locations",
    "Structure": "creatures",
    "Attachment": "attachment",
    "Spell": "spells",
    "Artefact": "Artefact",
}

# Image dimensions for card art (width × height in pixels)
IMAGE_WIDTH = 768
IMAGE_HEIGHT = 512

FLUX_KLEIN_WORKFLOW = {
    "9": {
        "inputs": {
            "filename_prefix": "Flux2-Klein",
            "images": ["75:65", 0],
        },
        "class_type": "SaveImage",
        "_meta": {"title": "Save Image"},
    },
    "76": {
        "inputs": {"value": "PROMPT_PLACEHOLDER"},
        "class_type": "PrimitiveStringMultiline",
        "_meta": {"title": "Prompt"},
    },
    "75:61": {
        "inputs": {"sampler_name": "euler"},
        "class_type": "KSamplerSelect",
        "_meta": {"title": "KSamplerSelect"},
    },
    "75:62": {
        "inputs": {
            "steps": 20,
            "width": ["75:68", 0],
            "height": ["75:69", 0],
        },
        "class_type": "Flux2Scheduler",
        "_meta": {"title": "Flux2Scheduler"},
    },
    "75:63": {
        "inputs": {
            "cfg": 5,
            "model": ["75:70", 0],
            "positive": ["75:74", 0],
            "negative": ["75:67", 0],
        },
        "class_type": "CFGGuider",
        "_meta": {"title": "CFGGuider"},
    },
    "75:64": {
        "inputs": {
            "noise": ["75:73", 0],
            "guider": ["75:63", 0],
            "sampler": ["75:61", 0],
            "sigmas": ["75:62", 0],
            "latent_image": ["75:66", 0],
        },
        "class_type": "SamplerCustomAdvanced",
        "_meta": {"title": "SamplerCustomAdvanced"},
    },
    "75:65": {
        "inputs": {
            "samples": ["75:64", 0],
            "vae": ["75:72", 0],
        },
        "class_type": "VAEDecode",
        "_meta": {"title": "VAE Decode"},
    },
    "75:66": {
        "inputs": {
            "width": ["75:68", 0],
            "height": ["75:69", 0],
            "batch_size": 1,
        },
        "class_type": "EmptyFlux2LatentImage",
        "_meta": {"title": "Empty Flux 2 Latent"},
    },
    "75:67": {
        "inputs": {
            "text": "modern clothes, cowboy hat, modern city, futuristic, sci-fi, photo, photograph, 3d render, cartoon, anime, text, logo, watermark, frame, border, white background",
            "clip": ["75:71", 0],
        },
        "class_type": "CLIPTextEncode",
        "_meta": {"title": "CLIP Text Encode (Negative)"},
    },
    "75:68": {
        "inputs": {"value": IMAGE_WIDTH},
        "class_type": "PrimitiveInt",
        "_meta": {"title": "Width"},
    },
    "75:69": {
        "inputs": {"value": IMAGE_HEIGHT},
        "class_type": "PrimitiveInt",
        "_meta": {"title": "Height"},
    },
    "75:73": {
        "inputs": {"noise_seed": 0},
        "class_type": "RandomNoise",
        "_meta": {"title": "RandomNoise"},
    },
    "75:70": {
        "inputs": {
            "unet_name": "flux-2-klein-base-9b-fp8.safetensors",
            "weight_dtype": "default",
        },
        "class_type": "UNETLoader",
        "_meta": {"title": "Load Diffusion Model"},
    },
    "75:71": {
        "inputs": {
            "clip_name": "qwen_3_8b_fp8mixed.safetensors",
            "type": "flux2",
            "device": "default",
        },
        "class_type": "CLIPLoader",
        "_meta": {"title": "Load CLIP"},
    },
    "75:72": {
        "inputs": {"vae_name": "flux2-vae.safetensors"},
        "class_type": "VAELoader",
        "_meta": {"title": "Load VAE"},
    },
    "75:74": {
        "inputs": {
            "text": ["76", 0],
            "clip": ["75:71", 0],
        },
        "class_type": "CLIPTextEncode",
        "_meta": {"title": "CLIP Text Encode (Positive)"},
    },
}


def build_prompt(name: str, card_type: str, subtype: str, effect: str) -> str:
    """Construct a lore-consistent prompt with deterministic scene variation."""

    def pick(options: list[str]) -> str:
        if not options:
            return ""
        idx = sum(ord(ch) for ch in name) % len(options)
        return options[idx]

    subtype_tokens = [part.strip() for part in (subtype or "").split(",") if part.strip()]
    subtype_lower = [token.lower() for token in subtype_tokens]
    primary_subtype = subtype_tokens[0] if subtype_tokens else ""

    # Core subject line (avoid awkward repetitions like "Clay named Clay").
    if card_type == "Location":
        subject = f"the mythic place {name}"
    elif card_type in ("Spell", "Attachment", "Artefact"):
        subject = f"{name}, an ancient {primary_subtype.lower() if primary_subtype else 'mystic force'}"
    elif primary_subtype and primary_subtype.lower() == name.lower():
        subject = f"{name}, an ancient mythic entity"
    elif subtype_tokens:
        subject = f"{name}, a {', '.join(subtype_tokens[:3]).lower()} from Bronze Age Mesopotamia"
    else:
        subject = f"{name}, a mythic being from Bronze Age Mesopotamia"

    # Classify by subtype keywords, including multi-value subtype cells.
    role = "generic"
    if any(token in subtype_lower for token in ("monster", "beast", "demon")):
        role = "monster"
    elif any(token in subtype_lower for token in ("god", "deity")):
        role = "divine"
    elif any(token in subtype_lower for token in ("hero", "king", "human", "priestess", "guide")):
        role = "heroic"
    elif any(token in subtype_lower for token in ("plant", "clay", "spirit")):
        role = "mythic"

    role_scenes = {
        "heroic": [
            "standing on the ramparts of Uruk at dusk, mudbrick walls and torchlight behind",
            "crossing a date-palm oasis at dawn, canal water reflecting bronze armor",
            "before carved temple reliefs and cuneiform steles in a sacred courtyard",
            "on a riverbank beside reed boats and clay tablets under wind-whipped clouds",
        ],
        "monster": [
            "emerging from a cedar forest in stormlight, shattered trunks and flying embers",
            "towering over a flooded plain with ruined ziggurats half-submerged in silt",
            "at a mountain pass beneath lightning, broken stone idols and dust swirling",
            "rising from the depths of the underworld river in black mist and moonlight",
        ],
        "divine": [
            "enthroned above a stepped ziggurat with sunbeams cutting through incense smoke",
            "descending through celestial storm clouds over the city of Uruk",
            "framed by radiant temple fire and sacred banners in a moonlit sanctuary",
            "appearing above the Euphrates in a halo of gold and lapis light",
        ],
        "mythic": [
            "formed from wet clay and river silt beside a ritual basin and reed mats",
            "in a forgotten shrine lit by oil lamps and drifting temple smoke",
            "in the shadow of colossal gates engraved with lions and winged bulls",
            "at the edge of a moonlit marsh where reeds bend in a warm desert wind",
        ],
        "generic": [
            "in ancient Mesopotamia with monumental mudbrick architecture and distant ziggurats",
            "in a stormy Bronze Age landscape of river deltas, reeds, and stepped temples",
            "on sacred ground near Uruk under dramatic skies and windblown dust",
            "amid ruined temple stones and cuneiform carvings at twilight",
        ],
    }
    background = pick(role_scenes.get(role, role_scenes["generic"]))

    # Effect-based mood details.
    effect_hints = []
    effect_lower = (effect or "").lower()
    if "banish" in effect_lower:
        effect_hints.append("a severe, fateful atmosphere")
    if "draw" in effect_lower:
        effect_hints.append("floating cuneiform glyphs and arcane tablets")
    if "flood" in effect_lower:
        effect_hints.append("rising muddy floodwaters and heavy rain")
    if "move" in effect_lower:
        effect_hints.append("dynamic forward motion, cloak and debris swept by wind")
    if "discard" in effect_lower:
        effect_hints.append("scattered ritual papers and broken relic fragments")
    if "destroy" in effect_lower:
        effect_hints.append("fractured stone and sparks from violent impact")

    camera = pick([
        "dramatic low-angle composition",
        "wide cinematic composition with deep perspective",
        "three-quarter portrait composition",
        "close mid-shot with intense focal depth",
    ])

    style = pick([
        "epic historical oil painting",
        "baroque mythological painting",
        "romantic-era grand history painting",
        "classical fine-art oil on canvas",
    ])

    extra = f", {', '.join(effect_hints)}" if effect_hints else ""
    prompt = (
        f"{subject}, {background}{extra}, {camera}, {style}, "
        f"rich impasto textures, dramatic chiaroscuro, masterpiece-level detail, "
        f"Bronze Age Mesopotamian attire, Sumerian architecture, authentic ancient setting, "
        f"no modern clothing, no cowboy hats, no modern props, no firearms, "
        f"no text, no UI, no borders, no card frame, no watermark, no white background, illustration only"
    )
    return prompt


def _api_post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _api_get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def _submit_workflow(prompt: str, width: int, height: int, seed: int) -> str:
    """Submit workflow to ComfyUI and return the prompt_id."""
    import copy
    workflow = copy.deepcopy(FLUX_KLEIN_WORKFLOW)
    workflow["76"]["inputs"]["value"] = prompt
    workflow["75:68"]["inputs"]["value"] = width
    workflow["75:69"]["inputs"]["value"] = height
    workflow["75:73"]["inputs"]["noise_seed"] = seed

    payload = {"prompt": workflow}
    result = _api_post(f"{COMFYUI_URL}/prompt", payload)
    return result["prompt_id"]


def _wait_for_result(prompt_id: str, poll_interval: float = 1.5, timeout: float = 300) -> dict:
    """Poll ComfyUI history until the prompt finishes. Returns the outputs dict."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            history = _api_get(f"{COMFYUI_URL}/history/{prompt_id}")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"ComfyUI unreachable: {exc}") from exc

        if prompt_id in history:
            entry = history[prompt_id]
            if entry.get("status", {}).get("completed"):
                return entry["outputs"]
            if entry.get("status", {}).get("status_str") == "error":
                msgs = entry.get("status", {}).get("messages", [])
                raise RuntimeError(f"ComfyUI generation failed: {msgs}")

        time.sleep(poll_interval)

    raise TimeoutError(f"ComfyUI generation timed out after {timeout}s")


def _download_image(filename: str, subfolder: str, dest_path: str) -> None:
    """Download a generated image from ComfyUI and save it to dest_path."""
    params = urllib.parse.urlencode({
        "filename": filename,
        "subfolder": subfolder,
        "type": "output",
    })
    url = f"{COMFYUI_URL}/view?{params}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(resp.read())


def generate_card_image(
    name: str,
    card_type: str,
    subtype: str = "",
    effect: str = "",
    prompt: str = "",
    width: int = IMAGE_WIDTH,
    height: int = IMAGE_HEIGHT,
    seed: int | None = None,
    output_base_dir: str = "images/color",
    overwrite: bool = False,
) -> str:
    """
    Generate a card art image via ComfyUI and save it to the correct directory.

    Returns the path to the saved image file.
    """
    type_key = card_type.split()[0]  # handle "Creature (Transform)" etc.
    subdir = TYPE_DIR.get(type_key, "creatures")
    dest_path = os.path.join(output_base_dir, subdir, f"{name}.png")

    if not overwrite and os.path.exists(dest_path):
        print(f"  [comfyui] Skipping '{name}' — image already exists")
        return dest_path

    if not prompt:
        prompt = build_prompt(name, type_key, subtype, effect)

    if seed is None:
        seed = random.randint(0, 2**32 - 1)

    print(f"  [comfyui] Generating '{name}' ({type_key}) — seed {seed}")
    print(f"            prompt: {prompt}")

    prompt_id = _submit_workflow(prompt, width, height, seed)
    outputs = _wait_for_result(prompt_id)

    # Find the SaveImage output (node "9")
    save_node_output = outputs.get("9", {})
    images = save_node_output.get("images", [])
    if not images:
        raise RuntimeError(f"No images in ComfyUI output for '{name}': {outputs}")

    img_info = images[0]
    _download_image(img_info["filename"], img_info.get("subfolder", ""), dest_path)
    print(f"  [comfyui] Saved → {dest_path}")
    return dest_path


def generate_missing_images(
    csv_file_path: str,
    output_base_dir: str = "images/color",
    overwrite: bool = False,
    comfyui_url: str = COMFYUI_URL,
) -> None:
    """
    Read a card CSV and generate ComfyUI images for any cards that are missing art.

    Args:
        csv_file_path: Path to the CSV file (same format used by process_csv_with_template).
        output_base_dir: Root directory where images/color/<type>/ folders live.
        overwrite: If True, regenerate even if an image already exists.
        comfyui_url: Base URL for the ComfyUI API.
    """
    global COMFYUI_URL
    COMFYUI_URL = comfyui_url

    with open(csv_file_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total = len(rows)
    generated = 0
    skipped = 0

    for idx, row in enumerate(rows, 1):
        name = row.get("Name", "").strip() or "Untitled"
        card_type = row.get("Type", "Creature").strip()
        subtype = row.get("Subtype", "").strip()
        effect = row.get("Effect", "").strip()

        type_key = card_type.split()[0]
        subdir = TYPE_DIR.get(type_key, "creatures")
        dest_path = os.path.join(output_base_dir, subdir, f"{name}.png")

        if not overwrite and os.path.exists(dest_path):
            skipped += 1
            continue

        print(f"[{idx}/{total}] {name} ({card_type})")
        try:
            generate_card_image(
                name=name,
                card_type=card_type,
                subtype=subtype,
                effect=effect,
                output_base_dir=output_base_dir,
                overwrite=overwrite,
            )
            generated += 1
        except Exception as exc:
            print(f"  [comfyui] ERROR for '{name}': {exc}")

    print(f"\nDone. Generated: {generated}, Skipped (existing): {skipped}, Total: {total}")
