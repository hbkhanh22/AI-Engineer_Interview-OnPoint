import os
import io
import time
import json
import logging
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging to console and file
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AI-Interpreter-Backend")

try:
    log_file_path = os.path.join(os.path.dirname(__file__), "server.log")
    file_handler = logging.FileHandler(log_file_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)
    logger.info(f"Logging to file enabled: {log_file_path}")
except Exception as e:
    logger.error(f"Failed to enable file logging: {e}")

app = FastAPI(title="Real-Time AI Interpreter")

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration from environment
MOCK_MODE = os.getenv("MOCK_MODE", "True").lower() == "true"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Initialize Gemini API client if not in mock mode
gemini_model = None
if not MOCK_MODE:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-2.0-flash')
        logger.info("Gemini API successfully configured.")
    except Exception as e:
        logger.error(f"Error initializing Gemini client: {e}. Falling back to Mock Mode.")
        MOCK_MODE = True

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "mock_mode": MOCK_MODE,
        "gemini_configured": bool(GEMINI_API_KEY)
    }

async def process_audio_with_gemini(audio_bytes: bytes, direction: str, mime_type: str, session_settings: dict) -> Dict[str, str]:
    """Sends audio bytes directly to Gemini for simultaneous transcription and translation."""
    is_mock = session_settings.get("mock_mode", True)
    
    if is_mock:
        time.sleep(1.0) # Simulate processing
        if direction == "en_to_vi":
            return {
                "original": "Good morning, thank you for attending today's town hall meeting.",
                "translated": "Chào buổi sáng, cảm ơn bạn đã đến tham dự cuộc họp hội trường hôm nay."
            }
        else:
            return {
                "original": "Xin chào buổi sáng, cảm ơn các bạn đã tham gia buổi họp nội bộ hôm nay.",
                "translated": "Hello morning, thank you for attending today's internal meeting."
            }

    # Otherwise, use real Gemini API
    api_key = session_settings.get("custom_gemini_key", "").strip()
    if not api_key:
        api_key = GEMINI_API_KEY # Fallback to .env key
        
    if not api_key:
        return {
            "original": "[Lỗi Cấu Hình API Key]",
            "translated": "API Key của Gemini chưa được thiết lập. Vui lòng nhập API Key ở bảng cấu hình bên trái giao diện web hoặc thiết lập trong tệp .env."
        }

    try:
        import google.generativeai as genai
        # Globally configure genai with the session key (safe for single-user developer runs)
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.0-flash')
        
        src_lang = "English" if direction == "en_to_vi" else "Vietnamese"
        target_lang = "Vietnamese" if direction == "en_to_vi" else "English"
        
        # Prepare the audio content object for Gemini
        audio_content = {
            "mime_type": mime_type,
            "data": audio_bytes
        }
        
        prompt = (
            f"You are a professional near-real-time interpreter. Listen to the provided audio carefully.\n"
            f"1. Transcribe the audio exactly into its original spoken language: {src_lang}.\n"
            f"2. Translate that transcription into the target language: {target_lang}.\n"
            f"CRITICAL REQUIREMENTS:\n"
            f"- Preserve all numbers, names, dates, and currency values exactly.\n"
            f"- Translate spoken idioms and jargon into natural spoken equivalents in the target language.\n"
            f"- Output MUST be a valid JSON object with EXACTLY two keys: 'original' and 'translated'.\n"
            f"- Do not wrap the JSON output in markdown formatting or backticks. Return raw JSON string."
        )

        # Retry logic with exponential backoff for 429 rate limits
        response = None
        retries = 3
        backoff_delay = 1.5
        for attempt in range(retries):
            try:
                response = model.generate_content([audio_content, prompt])
                break # Success
            except Exception as ex:
                ex_str = str(ex).lower()
                if ("429" in ex_str or "quota" in ex_str or "limit" in ex_str) and attempt < retries - 1:
                    logger.warning(f"Gemini API rate limit hit. Retrying in {backoff_delay}s... (Attempt {attempt+1}/{retries})")
                    time.sleep(backoff_delay)
                    backoff_delay *= 2
                else:
                    raise ex

        # Parse JSON from Gemini response
        response_text = response.text.strip()
        
        # Strip potential markdown wrapping if Gemini ignored the instruction
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
            response_text = response_text.strip()
            
        result = json.loads(response_text)
        return {
            "original": result.get("original", ""),
            "translated": result.get("translated", "")
        }
    except Exception as e:
        err_msg = str(e)
        logger.error(f"Gemini Multimodal STT & Translation Error: {err_msg}")
        
        # Check specifically for rate limits (429)
        if "429" in err_msg or "quota" in err_msg.lower() or "limit" in err_msg.lower():
            return {
                "original": "[Lỗi Giới Hạn Quota (429)]",
                "translated": "Bạn đang vượt quá giới hạn cuộc gọi (Rate Limit) của Gemini API Free Tier (15 lượt/phút). Vui lòng đợi vài giây trước khi nói câu tiếp theo để tránh quá tải API."
            }
            
        return {
            "original": "[Gemini processing error]",
            "translated": f"[Error: {err_msg}]"
        }

@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("New WebSocket connection established")
    
    session_settings = {
        "direction": "en_to_vi",
        "mime_type": "audio/webm",
        "mock_mode": MOCK_MODE,
        "custom_gemini_key": ""
    }
    
    try:
        while True:
            message = await websocket.receive()
            
            if "text" in message:
                try:
                    data = json.loads(message["text"])
                    # Handle config updates
                    if data.get("type") == "config":
                        session_settings["direction"] = data.get("direction", "en_to_vi")
                        session_settings["mime_type"] = data.get("mimeType", "audio/webm")
                        session_settings["mock_mode"] = data.get("mock_mode", MOCK_MODE)
                        session_settings["custom_gemini_key"] = data.get("custom_gemini_key", "")
                        logger.info(f"Updated session settings: {session_settings}")
                        await websocket.send_text(json.dumps({
                            "status": "config_updated",
                            "settings": {
                                "direction": session_settings["direction"],
                                "mime_type": session_settings["mime_type"],
                                "mock_mode": session_settings["mock_mode"],
                                "has_custom_key": bool(session_settings["custom_gemini_key"])
                            }
                        }))
                except json.JSONDecodeError:
                    logger.warning("Received invalid JSON text signal")
                    
            elif "bytes" in message:
                audio_bytes = message["bytes"]
                start_time = time.time()
                
                direction = session_settings["direction"]
                mime_type = session_settings.get("mime_type", "audio/webm")
                
                logger.info(f"Received audio segment of: {len(audio_bytes)} bytes. Mime: {mime_type}. Header: {audio_bytes[:20]}")
                
                # Call Gemini Multimodal Audio API directly
                result = await process_audio_with_gemini(audio_bytes, direction, mime_type, session_settings)
                
                total_latency = time.time() - start_time
                
                response_data = {
                    "type": "result",
                    "original": result["original"],
                    "translated": result["translated"],
                    "metrics": {
                        "stt_latency_sec": round(total_latency * 0.7, 3), # Estimated split
                        "translation_latency_sec": round(total_latency * 0.3, 3),
                        "total_latency_sec": round(total_latency, 3)
                    }
                }
                
                await websocket.send_text(json.dumps(response_data))
                logger.info(f"Processed audio chunk via Gemini Multimodal. Total latency: {total_latency:.2f}s")

    except WebSocketDisconnect:
        logger.info("WebSocket connection disconnected by client")
    except Exception as e:
        logger.error(f"Error in websocket loop: {e}")

# Mount static files to serve the frontend
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
    logger.info(f"Serving static frontend files from: {frontend_dir}")
else:
    logger.warning(f"Frontend directory not found at {frontend_dir}. Server will run API-only.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
