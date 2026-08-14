"""
generate_images_flux.py — Batch image generation via Flux (Replicate) or Grok (xAI).

Reads image_prompts_one_line_per_prompt.md, generates each image and saves it
to {project}/images/ with the correct filename.

Designed as a fallback for when Flo/other generators produce bad results.
Use --from-report to only regenerate FAIL/WARN shots from a review run.

Usage:
    # Generate all 90 images (Flux dev, default)
    python generate_images_flux.py --project ep01

    # Use Grok instead (cheaper — $0.02/image)
    python generate_images_flux.py --project ep01 --backend grok

    # Use Grok high-fidelity
    python generate_images_flux.py --project ep01 --backend grok --model grok-hd

    # Generate a single shot
    python generate_images_flux.py --project ep01 --shot 7

    # Regenerate only shots that failed/warned in the review
    python generate_images_flux.py --project ep01 --from-report

    # Force overwrite existing images
    python generate_images_flux.py --project ep01 --overwrite

──────────────────────────────────────────────────────────
BACKENDS & PRICING (July 2026)
──────────────────────────────────────────────────────────
Flux via Replicate (--backend replicate):
    schnell   black-forest-labs/flux-schnell     ~$0.003/image  →  ~$0.27 for 90
    dev       black-forest-labs/flux-dev         ~$0.025/image  →  ~$2.25 for 90  ← default
    pro       black-forest-labs/flux-1.1-pro     ~$0.040/image  →  ~$3.60 for 90
    Token: REPLICATE_API_TOKEN in .env
    Get token: https://replicate.com/account/api-tokens

Grok via xAI (--backend grok):
    grok      aurora (standard quality)          ~$0.020/image  →  ~$1.80 for 90
    grok-hd   aurora (high fidelity)             ~$0.060/image  →  ~$5.40 for 90
    Token: XAI_API_KEY in .env
    Get token: https://console.x.ai
──────────────────────────────────────────────────────────

Requirements:
    pip install replicate requests openai
"""

import argparse
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from generation_gate import GateBlocked, require_generation_ready

try:
    from openai import OpenAI as _OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False

# ── constants ──────────────────────────────────────────────────────────────────
PROMPTS_FILE = "image_prompts_one_line_per_prompt.md"
REPORT_FILE  = "review_report.md"

# ── Flux (Replicate) ───────────────────────────────────────────────────────────
FLUX_MODELS = {
    "schnell": "black-forest-labs/flux-schnell",
    "dev":     "black-forest-labs/flux-dev",
    "pro":     "black-forest-labs/flux-1.1-pro",
}
DEFAULT_FLUX_MODEL = "dev"

# Common parameters per model (only include what each model supports)
MODEL_PARAMS = {
    "schnell": {"num_outputs": 1, "output_format": "png", "aspect_ratio": "16:9", "go_fast": True},
    "dev":     {"num_outputs": 1, "output_format": "png", "aspect_ratio": "16:9", "guidance": 3.5},
    "pro":     {"output_format": "png", "aspect_ratio": "16:9", "output_quality": 100,
                "safety_tolerance": 5, "prompt_upsampling": False},
}

# ── Grok (xAI) ────────────────────────────────────────────────────────────────
XAI_BASE_URL = "https://api.x.ai/v1"
GROK_MODELS = {
    "grok":    "grok-imagine-image",          # standard  ~$0.02/image
    "grok-hd": "grok-imagine-image-quality",  # higher quality ~$0.06/image
}
GROK_MODEL_PARAMS = {
    "grok":    {},
    "grok-hd": {},
}

COST_MAP = {
    "schnell":  0.003,
    "dev":      0.025,
    "pro":      0.040,
    "grok":     0.020,
    "grok-hd":  0.060,
}

# ── style prefix injected before every prompt ──────────────────────────────────
# This is the key to style consistency — every prompt starts with the same
# style description so Flux maintains the doodle look across all 90 images.
STYLE_PREFIX = (
    "Flat digital cartoon illustration for an educational, friendly, approachable "
    "YouTube video essay, warm cream background (#FAF7F2), "
    "bold black outlines, expressive characters, vibrant colors. "
    "MASCOT (use this EXACT description every time): chubby round Indian cartoon boy, "
    "gold/amber round glasses, spiky black hair, warm tan brown skin (#D4A85C), "
    "cream kurta, off-white baggy pajama trousers with gathered ankles, brown leather sandals. "
    "Expressive face, thick eyebrows, short arms. NOT a generic Asian character, NOT pale-skinned, "
    "NOT in Western clothing. He is distinctly Indian — kurta and sandals are mandatory. "
    "No photorealism, no 3D rendering. 16:9 landscape composition. "
    "CRITICAL — do not render ANY text, words, titles, headings, captions, channel names, or "
    "logos anywhere in the image, even if a name or phrase is mentioned in the scene "
    "description below. All text is added separately afterward; the illustration itself "
    "must be completely wordless except where a scene explicitly calls for an in-world "
    "prop with text on it (a sign, a document, a banner someone is holding). "
)

STYLE_SUFFIX = (
    " Bold outlines, vibrant colors, 16:9. No title text, no channel name rendered in the image."
)


# ═══════════════════════════════════════════════════════════════════════════════
# PARSE PROMPTS FILE  (same logic as review_images.py)
# ═══════════════════════════════════════════════════════════════════════════════

def parse_prompts(md_path: str) -> list[dict]:
    shots = []
    with open(md_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("**SHOT"):
                continue
            shot_m  = re.search(r"\*\*SHOT (\d+)\*\*", line)
            fname_m = re.search(r"`([^`]+\.png)`", line)
            prompt_m = re.search(r"PROMPT:\s*(.+?)\s*OVERLAY:", line)
            type_m  = re.search(r"TYPE:\s*(\w+)", line)
            if not shot_m or not fname_m or not prompt_m:
                continue
            shots.append({
                "shot":     f"SHOT {shot_m.group(1).zfill(2)}",
                "filename": fname_m.group(1),
                "prompt":   prompt_m.group(1),
                # Legacy prompts files with no TYPE field predate the TYPE-aware
                # routing scheme entirely — every shot in one of those really was
                # an AI/cartoon shot, so CARTOON is the correct default, not an
                # unsafe guess.
                "type":     (type_m.group(1).upper() if type_m else "CARTOON"),
            })
    return shots


# ═══════════════════════════════════════════════════════════════════════════════
# PARSE REVIEW REPORT — extract filenames that need regeneration
# ═══════════════════════════════════════════════════════════════════════════════

def parse_report_issues(report_path: str) -> set[str]:
    """
    Read review_report.md and return the set of filenames marked WARN or FAIL.
    Looks for table rows containing '⚠ WARN' or '✗ FAIL'.
    """
    issues = set()
    if not os.path.exists(report_path):
        return issues
    with open(report_path, "r", encoding="utf-8") as f:
        for line in f:
            # Table row format: | SHOT XX | `filename.png` | ⚠ WARN | ...
            if ("WARN" in line or "FAIL" in line) and "`" in line:
                fname_m = re.search(r"`([^`]+\.png)`", line)
                if fname_m:
                    issues.add(fname_m.group(1))
    return issues


# ═══════════════════════════════════════════════════════════════════════════════
# LOAD API TOKEN
# ═══════════════════════════════════════════════════════════════════════════════

def load_env_key(script_dir: Path, key_name: str) -> str:
    """Load an API key from environment or .env file."""
    value = os.environ.get(key_name)
    if value:
        return value
    env_file = script_dir / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key_name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# GENERATE ONE IMAGE
# ═══════════════════════════════════════════════════════════════════════════════

def generate_image_flux(shot_prompt: str, model_key: str) -> bytes:
    """Call Flux via Replicate. Returns raw PNG bytes."""
    try:
        import replicate
    except ImportError:
        print("❌ replicate package not found. Run: pip install replicate")
        sys.exit(1)

    full_prompt = STYLE_PREFIX + shot_prompt + STYLE_SUFFIX
    model_id    = FLUX_MODELS[model_key]
    params      = dict(MODEL_PARAMS[model_key])
    params["prompt"] = full_prompt

    output = replicate.run(model_id, input=params)

    url = str(output[0]) if isinstance(output, list) else str(output)
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def generate_image_grok(shot_prompt: str, model_key: str, xai_client) -> bytes:
    """Call Grok Imagine via xAI API. Returns raw PNG bytes."""
    if not _OPENAI_AVAILABLE:
        raise RuntimeError("openai package not installed. Run: pip install openai")

    full_prompt = STYLE_PREFIX + shot_prompt + STYLE_SUFFIX

    response = xai_client.images.generate(
        model=GROK_MODELS[model_key],
        prompt=full_prompt,
        n=1,
    )

    # Response may contain a URL or base64 data
    img_data = response.data[0]
    if getattr(img_data, "url", None):
        img_response = requests.get(img_data.url, timeout=60)
        img_response.raise_for_status()
        return img_response.content
    elif getattr(img_data, "b64_json", None):
        import base64
        return base64.b64decode(img_data.b64_json)
    else:
        raise RuntimeError(f"Unexpected response format: {img_data}")


# ═══════════════════════════════════════════════════════════════════════════════
# ENSURE REAL PNG  (Grok returns JPEG bytes regardless of requested format)
# ═══════════════════════════════════════════════════════════════════════════════

TARGET_W, TARGET_H = 1280, 720  # All pipeline images must match this size

def ensure_png(path: Path) -> Path:
    """
    Ensure file is a real PNG at exactly 1280×720.
    - Converts JPEG-encoded data (Grok often returns JPEG with .png extension)
    - Resizes if xAI returned a non-standard size
    Returns the (possibly updated) path.
    """
    try:
        from PIL import Image as _Image
        img = _Image.open(path)
        changed = False
        if img.format == "JPEG":
            img = img.convert("RGB")
            changed = True
        if img.size != (TARGET_W, TARGET_H):
            img = img.resize((TARGET_W, TARGET_H), _Image.LANCZOS)
            changed = True
        if changed:
            img.save(path, format="PNG")
    except Exception:
        pass   # PIL not available or file unreadable — leave as-is
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main_legacy_v2():
    parser = argparse.ArgumentParser(
        description="Batch image generation via Flux (Replicate) or Grok (xAI)"
    )
    parser.add_argument("--project", required=True,
                        help="Project folder (e.g. ep01), relative or absolute")
    parser.add_argument("--backend", choices=["replicate", "grok"], default="grok",
                        help="Image generation backend (default: grok)")
    parser.add_argument("--model", default=None,
                        help=("Flux: schnell/dev/pro  |  Grok: grok/grok-hd  "
                              "(defaults: dev for Flux, grok for Grok)"))
    parser.add_argument("--shot", type=int, default=None,
                        help="Generate a single shot number only (e.g. --shot 7)")
    parser.add_argument("--from-report", action="store_true",
                        help="Only regenerate shots marked FAIL or WARN in review_report.md")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite images that already exist in images/")
    parser.add_argument("--workers", type=int, default=4,
                        help="Concurrent image-generation requests (default: 4). "
                             "Each scene's image is independent, so this is safe to raise; "
                             "lower it if the backend ever starts rate-limiting.")
    parser.add_argument("--prompt-override", default=None,
                        help="Use this exact text as the shot's PROMPT instead of the one in "
                             "the markdown file — for regenerating a single --shot with extra "
                             "instructions (e.g. reviewer feedback) without editing the source file.")
    args = parser.parse_args()

    # Resolve default model per backend
    if args.model is None:
        args.model = "dev" if args.backend == "replicate" else "grok"

    # Validate model choice
    valid_models = list(FLUX_MODELS.keys()) if args.backend == "replicate" else list(GROK_MODELS.keys())
    if args.model not in valid_models:
        print(f"❌ Model '{args.model}' is not valid for backend '{args.backend}'.")
        print(f"   Valid options: {', '.join(valid_models)}")
        sys.exit(1)

    # ── resolve paths ──────────────────────────────────────────────────────────
    script_dir  = Path(__file__).parent
    project_dir = (
        Path(args.project) if Path(args.project).is_absolute()
        else (script_dir / args.project).resolve()
    )
    if not project_dir.is_dir():
        print(f"❌ Project folder not found: {project_dir}")
        sys.exit(1)

    prompts_path = project_dir / PROMPTS_FILE
    if not prompts_path.exists():
        print(f"❌ Prompts file not found: {prompts_path}")
        sys.exit(1)

    # Preflight before the API client is built, before the images directory is
    # created and before any key is read. --from-report and --shot are retry
    # paths and go through the same gate: a retry spends exactly as much as a
    # first attempt.
    try:
        require_generation_ready(project_dir, f"generate_images_flux ({args.backend})")
    except GateBlocked as e:
        print(f"\n{e}")
        print("\nNo images were generated and no API client was created.")
        sys.exit(1)

    images_dir = project_dir / "images"
    images_dir.mkdir(exist_ok=True)

    # ── load token & init client ───────────────────────────────────────────────
    xai_client = None
    if args.backend == "replicate":
        token = load_env_key(script_dir, "REPLICATE_API_TOKEN")
        if not token:
            print("❌ REPLICATE_API_TOKEN not set.")
            print("   Add it to .env: REPLICATE_API_TOKEN=r8_...")
            print("   Get token: https://replicate.com/account/api-tokens")
            sys.exit(1)
        os.environ["REPLICATE_API_TOKEN"] = token
    else:
        if not _OPENAI_AVAILABLE:
            print("❌ openai package not installed. Run: pip install openai")
            sys.exit(1)
        token = load_env_key(script_dir, "XAI_API_KEY")
        if not token:
            print("❌ XAI_API_KEY not set.")
            print("   Add it to .env: XAI_API_KEY=xai-...")
            print("   Get token: https://console.x.ai")
            sys.exit(1)
        xai_client = _OpenAI(api_key=token, base_url=XAI_BASE_URL)

    # ── parse prompts ──────────────────────────────────────────────────────────
    shots = parse_prompts(str(prompts_path))
    if not shots:
        print("❌ No shots parsed from prompts file.")
        sys.exit(1)

    # ── filter: only CARTOON shots in a full-batch run ─────────────────────────
    # MAP/CHART/PHOTO shots are handled by generate_india_map.py/generate_chart.py/
    # search_pexels.py respectively — this script has no business touching them.
    # Confirmed as a real, active bug (not theoretical): route_images.py's AI-batch
    # call runs this script with no --shot filter, and without this check it was
    # regenerating a Grok cartoon for EVERY shot regardless of type, silently
    # clobbering correctly-generated charts and photos immediately after they were
    # created (10 of 114 shots on a real production run — every non-CARTOON shot
    # except the one MAP shot, which only survived because it happened to be
    # regenerated by hand afterward). --shot explicitly targeting a non-CARTOON
    # shot is still allowed through — that's a deliberate manual override, not
    # the accidental full-batch case this guards against.
    if args.shot is None:
        non_cartoon = [s for s in shots if s["type"] != "CARTOON"]
        if non_cartoon:
            print(f"  Skipping {len(non_cartoon)} non-CARTOON shot(s) "
                  f"(MAP/CHART/PHOTO — handled by their own scripts)")
            shots = [s for s in shots if s["type"] == "CARTOON"]

    # ── filter: single shot ────────────────────────────────────────────────────
    if args.shot is not None:
        target = f"SHOT {str(args.shot).zfill(2)}"
        shots = [s for s in shots if s["shot"] == target]
        if not shots:
            print(f"❌ {target} not found in prompts file.")
            sys.exit(1)

    if args.prompt_override is not None:
        if args.shot is None:
            print("❌ --prompt-override only makes sense together with --shot (one shot at a time).")
            sys.exit(1)
        shots[0]["prompt"] = args.prompt_override

    # ── filter: from report ────────────────────────────────────────────────────
    if args.from_report:
        report_path = project_dir / REPORT_FILE
        issue_files = parse_report_issues(str(report_path))
        if not issue_files:
            print(f"✓ No WARN/FAIL shots found in {report_path}. Nothing to regenerate.")
            return
        shots = [s for s in shots if s["filename"] in issue_files]
        print(f"  Regenerating {len(shots)} shots flagged in review report:")
        for s in shots:
            print(f"    {s['shot']} · {s['filename']}")
        print()

    # ── filter: skip existing ──────────────────────────────────────────────────
    if not args.overwrite:
        before = len(shots)
        shots  = [s for s in shots
                  if not any((images_dir / f"{Path(s['filename']).stem}{ext}").exists()
                             for ext in [".png", ".jpg", ".jpeg", ".webp"])]
        skipped = before - len(shots)
        if skipped:
            print(f"  Skipping {skipped} shot(s) — images already exist "
                  f"(use --overwrite to replace)\n")

    if not shots:
        print("✓ Nothing to generate.")
        return

    # ── summary ────────────────────────────────────────────────────────────────
    if args.backend == "replicate":
        backend_label = f"Flux/{args.model} via Replicate ({FLUX_MODELS[args.model]})"
    else:
        backend_label = f"Grok/{args.model} via xAI ({GROK_MODELS[args.model]})"

    est_cost = len(shots) * COST_MAP[args.model]

    print(f"\n{'═' * 55}")
    print(f"Image Batch Generator — {project_dir.name}")
    print(f"Backend  : {backend_label}")
    print(f"Shots    : {len(shots)}")
    print(f"Est. cost: ~${est_cost:.2f}")
    print(f"Output   : {images_dir}")
    print(f"{'═' * 55}\n")

    # ── generation loop ────────────────────────────────────────────────────────
    # Each shot's image is fully independent (own prompt, own output file) — no
    # shared state between them — so this fans out across a small thread pool
    # instead of one request at a time. The openai-compatible client already
    # has its own default max_retries=2 for transient errors; no manual
    # backoff/sleep is needed once requests aren't all sequential.
    def _generate_one(shot):
        filename    = shot["filename"]
        output_path = images_dir / filename
        try:
            if args.backend == "replicate":
                img_bytes = generate_image_flux(shot["prompt"], args.model)
            else:
                img_bytes = generate_image_grok(shot["prompt"], args.model, xai_client)
            output_path.write_bytes(img_bytes)
            # Ensure the saved file is real PNG (Grok returns JPEG bytes with .png ext)
            output_path = ensure_png(output_path)
            size_kb = len(output_path.read_bytes()) // 1024
            return shot, size_kb, None
        except Exception as e:
            return shot, None, str(e)

    done = 0
    failed = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_generate_one, shot): shot for shot in shots}
        for i, future in enumerate(as_completed(futures), 1):
            shot, size_kb, error = future.result()
            if error:
                print(f"[{i:02d}/{len(shots)}] {shot['shot']} · {shot['filename']}  ✗  ERROR — {error}")
                failed.append({"shot": shot["shot"], "filename": shot["filename"], "error": error})
            else:
                print(f"[{i:02d}/{len(shots)}] {shot['shot']} · {shot['filename']}  ✓  ({size_kb} KB)")
                done += 1

    # ── summary ────────────────────────────────────────────────────────────────
    print(f"\n{'═' * 55}")
    print(f"Done: {done}/{len(shots)} generated")
    if failed:
        print(f"Failed: {len(failed)}")
        for f in failed:
            print(f"  ✗ {f['shot']} · {f['filename']} — {f['error'][:80]}")
        print(f"\nRetry failed shots:")
        for f in failed:
            shot_num = int(f["shot"].split()[1])
            print(f"  python generate_images_flux.py --project {args.project} "
                  f"--shot {shot_num} --overwrite")
    print(f"\nImages saved to: {images_dir}")
    if done > 0:
        print(f"\nNext step — run the review agent:")
        print(f"  python review_images.py --project {args.project}")
    print(f"{'═' * 55}\n")


def main() -> int:
    """Refusal shim (Task 2B-B2b-2b). generate_images_flux.py's project-mode
    CLI (`--project` is required for every invocation — there is no
    non-project mode in this file) is obsolete: canonical image generation
    is dispatched in-process through route_images.py's canonical dispatcher
    (dispatch_routes()/dispatch_snapshot_routes()), which is itself still
    universally refused pending Task 2B-B2b-3's separately authorized
    activation.

    This shim calls no provider/API/GPU/downloader/adapter and constructs
    no client — it does not even reach generation_gate, since there is
    nothing left to gate once every path here refuses unconditionally. The
    real implementation (--backend/--model/--shot/--from-report/--overwrite/
    --prompt-override, all still fully defined above) is preserved as
    main_legacy_v2() — reachable only by calling it directly, never through
    this CLI's default entry point, so there is no ambiguous default that
    could bypass the canonical gate.
    """
    print("generate_images_flux.py's project-mode CLI is obsolete.")
    print("Canonical image generation is dispatched through the canonical")
    print("route_images.py workflow:")
    print("  python route_images.py --project <project>")
    print("(currently refused everywhere until canonical execution is")
    print(" activated — Task 2B-B2b-3).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
