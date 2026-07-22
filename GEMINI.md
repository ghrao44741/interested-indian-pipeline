# Gemini API Setup & Usage Guide

This project is configured to use the Google Gemini API (via the `google-genai` Python library) to automate parts of the **Interested Indian Video Pipeline**.

## 1. How Gemini Fits into the Pipeline

The current pipeline requires a manual "Stage 3" where you paste the output of `auto_split_scenes_v1_stage3_export.py` into a chat interface to get image prompts, and then manually generate images to put in the `images/` folder. Gemini can automate these steps entirely.

### A. Stage 1: Scriptwriting (Text Generation)
You can use the Gemini API (e.g., `gemini-3.1-pro` for complex reasoning) to draft the initial long-form essays (`script_*.txt`) by providing it with research material and your channel's specific brand voice guidelines.

### B. Stage 3: Automated Image Prompt Generation
Instead of pasting the "PROPOSED CREATIVE SPLIT" from the terminal into a chat window, you can write a script that passes the generated `timestamped_script.txt` directly to the Gemini API (`gemini-flash-latest`). Gemini can return structured JSON containing the image prompts for each scene or visual group.

### C. Stage 3.5: Automated Image Generation
Once the prompts are generated, you can use Gemini's image generation models (`gemini-3.1-flash-image`) to automatically generate the PNGs and save them directly into the `{project}/images/` folder as `SCENE-XXX.png` or `{group_id}.png`. This allows you to immediately run `stitch_video_longform.py` without manual asset creation.

---

## 2. Setup & Authentication

### Installation
The required packages are already installed. If setting up a new environment, run:
```powershell
pip install google-genai google-generativeai python-dotenv
```

### Authentication
1. Get an API key from [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Create a `.env` file in the root of this project and add:
```env
GEMINI_API_KEY="your_actual_api_key_here"
```

*(A `test_gemini.py` script is included in the project root to verify your connection).*

---

## 3. Automation Examples

### Example: Automating Stage 3 Prompt Generation
Here is how you can programmatically pass the scene splits to Gemini to get image prompts:

```python
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client()

def generate_prompts_for_project(project_dir):
    script_path = os.path.join(project_dir, "timestamped_script.txt")
    with open(script_path, "r", encoding="utf-8") as f:
        shot_list = f.read()

    system_instruction = (
        "You are an expert art director. Read the following shot list and "
        "generate a highly detailed image generation prompt for each shot."
    )

    response = client.models.generate_content(
        model='gemini-flash-latest',
        contents=f"{system_instruction}\n\n{shot_list}"
    )
    
    print(response.text)
```

### Example: Automating Image Generation
*Note: Gemini 3.1 Flash Image costs a fraction of a cent for 100 images, making it far more cost-effective for bulk pipeline generation than Midjourney or DALL-E 3.*

```python
# Example pseudo-code for image generation
response = client.models.generate_images(
    model='gemini-3.1-flash-image',
    prompt='Minimalist 2D doodle, white bg, two bar columns: Karnataka bar tall (orange), UP bar much shorter (grey)',
    config=genai.types.GenerateImagesConfig(
        number_of_images=1,
        aspect_ratio="16:9" # Matches the 1920x1080 resolution of stitch_video_longform.py
    )
)

# Save to {project}/images/SCENE-001.png
for generated_image in response.generated_images:
    generated_image.image.save('ep01/images/SCENE-001.png')
```
