import os
import re
import sys
from dotenv import load_dotenv
from google import genai

def generate_images_from_markdown(md_file, output_dir):
    # Load environment variables
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[Error] GEMINI_API_KEY not found in .env file.")
        sys.exit(1)
        
    client = genai.Client()
    os.makedirs(output_dir, exist_ok=True)
    
    if not os.path.exists(md_file):
        print(f"[Error] File not found: {md_file}")
        sys.exit(1)
        
    with open(md_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    success_count = 0
    skip_count = 0
    error_count = 0

    print(f"Starting image generation. Output directory: {output_dir}\n")

    for idx, line in enumerate(lines, 1):
        if "PROMPT:" not in line:
            continue
            
        try:
            # Extract filename and prompt using regex
            filename_match = re.search(r'→ `([^`]+)`', line)
            if not filename_match:
                print(f"[Warning] Line {idx}: Could not find filename. Skipping.")
                continue
                
            filename = filename_match.group(1)
            
            prompt_match = re.search(r'PROMPT:\s*(.*?)\s*(?:OVERLAY:|CUE:|$)', line)
            if not prompt_match:
                print(f"[Warning] Line {idx}: Could not parse PROMPT. Skipping.")
                continue
                
            prompt = prompt_match.group(1).strip()
            output_path = os.path.join(output_dir, filename)
            
            # Skip if already generated
            if os.path.exists(output_path):
                print(f"[Skip] {filename} (already exists)")
                skip_count += 1
                continue

            print(f"Generating {filename}...")
            
            # For 2026 google-genai SDK, use generate_images
            result = client.models.generate_images(
                model='imagen-4.0-generate-001',
                prompt=prompt,
                config=genai.types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="16:9",
                    output_mime_type="image/png"
                )
            )
            
            if not result.generated_images:
                print(f"[Failed] {filename}: No images returned.")
                error_count += 1
                continue

            # Save the image
            for generated_image in result.generated_images:
                # The image might be available as generated_image.image or bytes
                # depending on the SDK exact version, we handle PIL Image
                generated_image.image.save(output_path)
            
            print(f"[Saved] {filename}")
            success_count += 1
                
        except Exception as e:
            print(f"[Error] on line {idx} for {filename if 'filename' in locals() else 'unknown'}: {e}")
            error_count += 1

    print("\n" + "="*40)
    print("FINISHED IMAGE GENERATION")
    print(f"Successfully generated: {success_count}")
    print(f"Skipped (already exist): {skip_count}")
    print(f"Errors: {error_count}")
    print("="*40)

if __name__ == "__main__":
    generate_images_from_markdown(
        md_file="ep01/image_prompts_one_line_per_prompt_fixed.md",
        output_dir="ep01/images"
    )
