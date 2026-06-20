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
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen3.6:35b"

# Workflow JSON file (relative to the project root, i.e. the directory containing src/)
WORKFLOW_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "workflows", "z_image_turbo.json")

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


def _load_workflow(workflow_file: str = WORKFLOW_FILE) -> dict:
    """Load a ComfyUI workflow from a JSON file."""
    with open(workflow_file, encoding="utf-8") as f:
        return json.load(f)


def build_prompt(name: str, card_type: str, subtype: str, effect: str) -> str:
    """Construct a lore-consistent prompt tuned for Z Image Turbo (AuraFlow, cfg=1)."""

    def pick(options: list[str]) -> str:
        if not options:
            return ""
        idx = sum(ord(ch) for ch in name) % len(options)
        return options[idx]

    subtype_tokens = [part.strip() for part in (subtype or "").split(",") if part.strip()]
    subtype_lower = [token.lower() for token in subtype_tokens]
    primary_subtype = subtype_tokens[0] if subtype_tokens else ""

    # ── Subject description ────────────────────────────────────────────────────
    if card_type == "Location":
        subject = (
            f"a sweeping view of {name}, a mythic Bronze Age Mesopotamian place filling the "
            f"entire frame from edge to edge"
        )
    elif card_type in ("Spell", "Attachment", "Artefact"):
        kind = primary_subtype.lower() if primary_subtype else "mystic artefact"
        subject = (
            f"{name}, an ancient {kind} rendered as the sole subject filling the frame, "
            f"hovering in dramatic light with cuneiform engravings and ritual power radiating from it"
        )
    elif primary_subtype and primary_subtype.lower() == name.lower():
        subject = f"{name}, an ancient mythic entity filling the frame completely"
    elif subtype_tokens:
        subject = (
            f"{name}, a {', '.join(subtype_tokens[:3]).lower()} — "
            f"the figure filling the frame from edge to edge, caught mid-action"
        )
    else:
        subject = (
            f"{name}, a mythic being from Bronze Age Mesopotamia filling the frame, "
            f"commanding presence, caught at a decisive moment"
        )

    # ── Role classification ────────────────────────────────────────────────────
    role = "generic"
    if any(t in subtype_lower for t in ("monster", "beast", "demon")):
        role = "monster"
    elif any(t in subtype_lower for t in ("god", "deity")):
        role = "divine"
    elif any(t in subtype_lower for t in ("hero", "king", "human", "priestess", "guide")):
        role = "heroic"
    elif any(t in subtype_lower for t in ("plant", "clay", "spirit")):
        role = "mythic"

    # ── Background & lighting ──────────────────────────────────────────────────
    role_scenes = {
        "heroic": [
            ("the ramparts of Uruk at dusk behind them, mudbrick walls and guttering torchlight, "
             "warm amber glow from the right dying into cool blue shadow on the left"),
            ("a date-palm oasis at dawn, canal water catching bronze-gold light below, "
             "the horizon a band of rose and indigo, figure lit from low left by the rising sun"),
            ("a sacred courtyard of carved temple reliefs and cuneiform steles, "
             "oil-lamp firelight flickering across stone surfaces in deep orange and near-black shadow"),
            ("a riverbank under wind-whipped storm clouds, reed boats tossing in brown water, "
             "cold blue stormlight overhead with a single shaft of sun catching the figure from the right"),
        ],
        "monster": [
            ("a cedar forest annihilated — shattered trunks and flying embers, "
             "hellish orange firelight from below casting the creature in silhouette against roiling smoke"),
            ("a flooded plain at night, ruined ziggurats half-submerged in silt, "
             "cold blue moonlight reflected in the black water, the creature rising from it"),
            ("a mountain pass under lightning, broken stone idols and swirling dust, "
             "blue-white lightning strike illuminating the scene in violent flash"),
            ("the underworld river in absolute darkness, cold blue-white phosphorescent mist, "
             "the creature emerging from water that reflects no light"),
        ],
        "divine": [
            ("above a stepped ziggurat, sunbeams cutting through dense incense smoke in golden shafts, "
             "the deity blazing at the center of the composition like a second sun"),
            ("descending through a storm over Uruk, celestial light parting dark clouds, "
             "the city far below lit by divine radiance while storm dark encircles"),
            ("a moonlit temple sanctuary, sacred banners catching firelight, "
             "the deity framed by an arch of carved bull-men, lapis and gold in the torchlight"),
            ("above the Euphrates, the river reflecting a halo of gold and lapis, "
             "night sky dense with stars, divine figure the only source of warm light in the scene"),
        ],
        "mythic": [
            ("beside a ritual basin filled with Euphrates silt, reed mats and clay offering bowls, "
             "single oil lamp casting warm amber across damp clay surfaces"),
            ("a forgotten underground shrine lit only by oil lamps, "
             "drifting temple smoke softening the edges, the subject emerging from amber into shadow"),
            ("in the shadow of colossal city gates engraved with lions and winged bulls, "
             "late-day gold light angling through the gate arch and cutting across the cobblestones"),
            ("at the edge of a moonlit marsh, reeds bending in warm desert wind, "
             "cold silver moonlight on the water and warm firelight from a distant settlement behind"),
        ],
        "generic": [
            ("against monumental mudbrick architecture, distant ziggurats in a smoky sky, "
             "dramatic late-afternoon sidelight from the right throwing deep shadow left"),
            ("a stormy Bronze Age delta, reeds bowing in wind, stepped temples behind, "
             "bruised purple-grey stormlight above with warm ember light at the horizon"),
            ("sacred ground near Uruk under dramatic skies, windblown dust catching the light, "
             "three-quarter warm sunlight from the left, cool shadow dominating the right half"),
            ("ruined temple stones and cuneiform carvings at twilight, "
             "last amber light of day from low left, blue dusk advancing from the right"),
        ],
    }
    scene = pick(role_scenes.get(role, role_scenes["generic"]))

    # ── Effect-driven atmosphere ───────────────────────────────────────────────
    atmosphere_hints = []
    effect_lower = (effect or "").lower()
    if "banish" in effect_lower:
        atmosphere_hints.append("a severe fateful atmosphere, the air itself vibrating with finality")
    if "draw" in effect_lower:
        atmosphere_hints.append("cuneiform glyphs drifting through the air like embers")
    if "flood" in effect_lower:
        atmosphere_hints.append("rising muddy floodwaters climbing the edges of the frame")
    if "move" in effect_lower:
        atmosphere_hints.append("cloak and debris swept violently by forward motion")
    if "discard" in effect_lower:
        atmosphere_hints.append("scattered clay tablets and broken ritual fragments on the ground")
    if "destroy" in effect_lower:
        atmosphere_hints.append("fractured stone and impact sparks filling the background")
    atmosphere = (", " + ", ".join(atmosphere_hints)) if atmosphere_hints else ""

    # ── Camera ────────────────────────────────────────────────────────────────
    camera = pick([
        "extreme low-angle close-up, subject filling the entire frame from below",
        "wide cinematic composition, subject dominating the foreground edge to edge",
        "three-quarter portrait, subject filling the frame top to bottom",
        "dramatic close mid-shot, subject filling the frame with intense focal depth",
    ])

    prompt = (
        f"{camera} — {subject}, {scene}{atmosphere}. "
        f"Painted in the style of a fusion between Caravaggio's violent chiaroscuro and "
        f"ancient Mesopotamian cylinder seal art translated into full painterly realism — "
        f"the graphic severity of cuneiform-era divine iconography rendered with oil-painting "
        f"depth and tonal drama. Bronze Age Mesopotamian attire, authentic ancient setting, "
        f"no modern elements. Full bleed, no borders, no margins, no white space, "
        f"no card frame, no text, no watermark. Ultra-detailed, cinematic card art composition."
    )
    return prompt


def _generate_prompt_with_llm(name: str, card_type: str, subtype: str, effect: str) -> str:
    """
    Ask the local ollama LLM to write a vivid, card-specific image generation prompt
    for Z Image Turbo. Falls back to build_prompt() on any error.
    """
    system = """You write image generation prompts for a trading card game. Cards depict creatures, heroes, gods, locations, spells, and artefacts from world mythologies: Mesopotamian, Greek, Norse, Egyptian, Celtic, Indian, Chinese, Persian, etc.

Write ONE dense paragraph prompt (no bullet points, no preamble, no explanation — just the raw prompt text). Rules:
- Open with a camera directive ending in " — ": e.g. "Extreme low-angle close-up, the subject filling the frame from below — "
- Describe the subject with vivid physical specifics: build, skin tone, clothing, weapon, expression, posture, mid-action
- Describe the background with concrete, named light sources, their colours, and their directions (e.g. "warm amber torchlight from the left dying into cold blue shadow on the right")
- Match the mythology to the card: Greek heroes look Greek (tunic, bronze armour, olive skin), Norse gods look Norse, Mesopotamian figures have lapis and gold. Do NOT apply Mesopotamian aesthetics to non-Mesopotamian cards.
- End every prompt with exactly this sentence: "Painted in the style of a fusion between Caravaggio's violent chiaroscuro and mythological oil painting realism. Full bleed, no borders, no margins, no white space, no card frame, no text, no watermark. Ultra-detailed, cinematic card art composition."
- Stay under 180 words total."""

    user_msg = (
        f"Card name: {name}\n"
        f"Type: {card_type}\n"
        f"Subtype: {subtype or 'none'}\n"
        f"Effect text: {effect or 'none'}\n\n"
        f"Write the image generation prompt."
    )

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "think": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())
    return result["message"]["content"].strip()


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


def _submit_workflow(prompt: str, width: int, height: int, seed: int, workflow_file: str = WORKFLOW_FILE) -> str:
    """Submit workflow to ComfyUI and return the prompt_id."""
    workflow = _load_workflow(workflow_file)
    # Z Image Turbo injection points
    workflow["57:27"]["inputs"]["text"] = prompt
    workflow["57:13"]["inputs"]["width"] = width
    workflow["57:13"]["inputs"]["height"] = height
    workflow["57:3"]["inputs"]["seed"] = seed

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
    use_llm: bool = True,
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
        if use_llm:
            try:
                prompt = _generate_prompt_with_llm(name, type_key, subtype, effect)
            except Exception as exc:
                print(f"  [comfyui] ollama unavailable ({exc}), falling back to template prompt")
                prompt = build_prompt(name, type_key, subtype, effect)
        else:
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
    use_llm: bool = True,
) -> None:
    """
    Read a card CSV and generate ComfyUI images for any cards that are missing art.

    Args:
        csv_file_path: Path to the CSV file (same format used by process_csv_with_template).
        output_base_dir: Root directory where images/color/<type>/ folders live.
        overwrite: If True, regenerate even if an image already exists.
        comfyui_url: Base URL for the ComfyUI API.
        use_llm: If True (default), use ollama to write prompts; falls back to templates.
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
                use_llm=use_llm,
            )
            generated += 1
        except Exception as exc:
            print(f"  [comfyui] ERROR for '{name}': {exc}")

    print(f"\nDone. Generated: {generated}, Skipped (existing): {skipped}, Total: {total}")
