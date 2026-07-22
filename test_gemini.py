import os
from dotenv import load_dotenv
from google import genai

def test_gemini_connection():
    print("Loading .env file...")
    load_dotenv()
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[Error] GEMINI_API_KEY not found in the environment. Please check your .env file.")
        return
        
    print("[Success] GEMINI_API_KEY found! Initializing client...")
    try:
        client = genai.Client()
        
        print("Sending a test prompt to Gemini (gemini-flash-latest)...")
        # Using a fast and cheap model for the test
        response = client.models.generate_content(
            model='gemini-flash-latest',
            contents='Say "Hello, world! The API connection is working!" and nothing else.'
        )
        
        print("\nSuccess! Here is the response from Gemini:")
        print("-" * 40)
        print(response.text)
        print("-" * 40)
        
    except Exception as e:
        print("\n[Error] An error occurred while calling the Gemini API:")
        print(e)

if __name__ == "__main__":
    test_gemini_connection()
