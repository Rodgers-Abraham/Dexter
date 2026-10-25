import json
import sys
import base64
import logging
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openrouter_client")

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR     = _get_base_dir()
API_KEY_PATH = BASE_DIR / "config" / "api_keys.json"

def _load_api_key() -> str:
    try:
        with open(API_KEY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        key = data.get("gemini_api_key", "").strip()
        if not key:
            raise ValueError("gemini_api_key is empty in api_keys.json")
        return key
    except FileNotFoundError:
        raise RuntimeError(f"api_keys.json not found at: {API_KEY_PATH}")
    except Exception as e:
        raise RuntimeError(f"Failed to load Gemini API key: {e}")

DEFAULT_MODEL       = "gemini-2.5-flash"
DEFAULT_MAX_TOKENS  = 1024
DEFAULT_TEMPERATURE = 0.6

class OpenRouterClient:
    def __init__(self) -> None:
        self.api_key = _load_api_key()
        self.client  = genai.Client(api_key=self.api_key)

    def _convert_messages(self, messages: list[dict]) -> tuple[Optional[str], list[types.Content]]:
        system_instruction = None
        contents = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            if role == "system":
                if system_instruction:
                    system_instruction += "\n" + content
                else:
                    system_instruction = content
            else:
                g_role = "model" if role == "assistant" else "user"
                contents.append(
                    types.Content(role=g_role, parts=[types.Part.from_text(text=str(content))])
                )
                
        return system_instruction, contents

    def chat(
        self,
        prompt: str,
        system: str = (
            "You are a component of MARK XXV, an AI assistant inspired by DEXTER. "
            "Be concise, helpful, and precise."
        ),
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        logger.info(f"[Gemini] Sending chat request (prompt length: {len(prompt)})")
        try:
            resp = self.client.models.generate_content(
                model=DEFAULT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                )
            )
            return resp.text.strip()
        except Exception as e:
            logger.error(f"[Gemini] Chat failed: {e}")
            raise RuntimeError(f"Gemini API error: {e}")

    def chat_json(
        self,
        prompt: str,
        system: str = (
            "Return ONLY valid JSON. "
            "No markdown fences, no extra text, no explanation."
        ),
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> dict:
        logger.info(f"[Gemini] Sending JSON chat request")
        try:
            resp = self.client.models.generate_content(
                model=DEFAULT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0.2,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json",
                )
            )
            return json.loads(resp.text.strip())
        except Exception as e:
            logger.error(f"[Gemini] JSON Chat failed: {e}")
            raise ValueError(f"Gemini JSON parse failed: {e}")

    def vision(
        self,
        prompt: str,
        image_b64: str,
        mime: str = "image/png",
        system: str = "Analyze the image and describe what you see clearly and concisely.",
        model: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> str:
        logger.info(f"[Gemini] Sending vision request")
        try:
            img_bytes = base64.b64decode(image_b64)
            resp = self.client.models.generate_content(
                model=DEFAULT_MODEL,
                contents=[
                    types.Part.from_bytes(data=img_bytes, mime_type=mime),
                    prompt
                ],
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0.2,
                    max_output_tokens=max_tokens,
                )
            )
            return resp.text.strip()
        except Exception as e:
            logger.error(f"[Gemini] Vision failed: {e}")
            raise RuntimeError(f"Gemini API error: {e}")

    def vision_from_file(
        self,
        prompt: str,
        image_path: str,
        system: str = "Analyze the image and describe what you see clearly and concisely.",
        model: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> str:
        path = Path(image_path)
        mime_map = {
            ".png":  "image/png",
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif":  "image/gif",
        }
        mime = mime_map.get(path.suffix.lower(), "image/png")

        with open(path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        return self.vision(prompt, image_b64, mime, system, model, max_tokens)

    def multi_turn(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        logger.info(f"[Gemini] Sending multi-turn request ({len(messages)} messages)")
        try:
            system_instruction, contents = self._convert_messages(messages)
            config = types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            if system_instruction:
                config.system_instruction = system_instruction
                
            resp = self.client.models.generate_content(
                model=DEFAULT_MODEL,
                contents=contents,
                config=config,
            )
            return resp.text.strip()
        except Exception as e:
            logger.error(f"[Gemini] Multi-turn failed: {e}")
            raise RuntimeError(f"Gemini API error: {e}")

    def available_models(self) -> dict:
        return {
            "text_models":   [DEFAULT_MODEL],
            "vision_models": [DEFAULT_MODEL],
            "rate_limited":  [],
            "total_text":    1,
            "total_vision":  1,
        }

client = OpenRouterClient()

if __name__ == "__main__":
    print("=" * 55)
    print("  MARK XXV — Gemini Client Self-Test")
    print("=" * 55)

    print("\n[TEST 1] Basic chat...")
    try:
        reply = client.chat("Introduce yourself in one sentence.")
        print(f"  Response : {reply}")
        print(f"  Status   : PASS ✓")
    except Exception as e:
        print(f"  Status   : FAIL ✗ — {e}")

    print("\n[TEST 2] JSON mode...")
    try:
        data = client.chat_json(
            'List 3 programming languages. Format: {"languages": ["a", "b", "c"]}',
            system="Return only valid JSON. No extra text."
        )
        print(f"  Response : {data}")
        print(f"  Status   : PASS ✓")
    except Exception as e:
        print(f"  Status   : FAIL ✗ — {e}")

    print("\n[TEST 3] Multi-turn conversation...")
    try:
        history = [
            {"role": "system",    "content": "You are a helpful assistant. Be brief."},
            {"role": "user",      "content": "My name is Tony."},
            {"role": "assistant", "content": "Hello Tony, how can I help you?"},
            {"role": "user",      "content": "What is my name?"},
        ]
        reply = client.multi_turn(history)
        print(f"  Response : {reply}")
        print(f"  Status   : PASS ✓")
    except Exception as e:
        print(f"  Status   : FAIL ✗ — {e}")

    print("\n[TEST 4] Model pool info...")
    info = client.available_models()
    print(f"  Text models   : {info['total_text']}")
    print(f"  Vision models : {info['total_vision']}")
    print(f"  Rate limited  : {info['rate_limited'] or 'none'}")
    print(f"  Status        : PASS ✓")

    print("\n" + "=" * 55)
    print("  All tests complete.")
    print("=" * 55)
