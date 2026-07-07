"""
تست سریع AI API key
اجرا: python test_ai.py
"""
import os
from dotenv import load_dotenv

load_dotenv()

def test_provider(name, base_url, api_key, model):
    print(f"\n{'─'*40}")
    print(f"🔍 تست {name}...")
    print(f"   کلید: {api_key[:12]}..." if api_key else f"   ❌ {name}_API_KEY در .env نیست")
    if not api_key:
        return False
    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "سلام، یه جمله کوتاه فارسی بگو"}],
            max_tokens=50,
        )
        print(f"   ✅ کار می‌کنه! پاسخ: {resp.choices[0].message.content[:80]}")
        return True
    except Exception as e:
        print(f"   ❌ خطا: {str(e)[:120]}")
        return False

providers = [
    ("Mistral",     "https://api.mistral.ai/v1",
     os.environ.get("MISTRAL_API_KEY", ""),       "mistral-small-latest"),
    ("Groq",        "https://api.groq.com/openai/v1",
     os.environ.get("GROQ_API_KEY", ""),          "llama-3.3-70b-versatile"),
    ("OpenRouter",  "https://openrouter.ai/api/v1",
     os.environ.get("OPENROUTER_API_KEY", ""),    "google/gemma-4-31b-it:free"),
    ("Gemini",      "https://generativelanguage.googleapis.com/v1beta/openai/",
     os.environ.get("GEMINI_API_KEY", ""),        "gemini-1.5-flash"),
]

print("=" * 40)
print("  FamilyGraph — تست AI providers")
print("=" * 40)

working = []
for name, url, key, model in providers:
    if test_provider(name, url, key, model):
        working.append(name)

print(f"\n{'='*40}")
if working:
    print(f"✅ providers کاری: {', '.join(working)}")
    print(f"   برنامه از {working[0]} استفاده می‌کنه (اولویت)")
else:
    print("❌ هیچ provider کاری پیدا نشد!")
    print("\n📌 راهنما:")
    print("   • Mistral (توصیه شده برای ایران):")
    print("     ۱. برو: console.mistral.ai")
    print("     ۲. ثبت‌نام با ایمیل")
    print("     ۳. API Keys → Create Key")
    print("     ۴. در .env بذار: MISTRAL_API_KEY=xxxx")
    print("")
    print("   • OpenRouter:")
    print("     ۱. برو: openrouter.ai/keys")
    print("     ۲. کلید sk-or-v1-... بساز")
    print("     ۳. در .env بذار: OPENROUTER_API_KEY=sk-or-v1-...")
print("=" * 40)
