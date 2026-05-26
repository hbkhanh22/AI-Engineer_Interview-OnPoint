import sys
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Whisper-Diagnostic")

logger.info(f"Python Version: {sys.version}")

try:
    import av
    logger.info(f"PyAV version: {av.__version__}")
    logger.info("PyAV is installed and can be imported successfully.")
except Exception as e:
    logger.error(f"PyAV import failed: {e}")

try:
    from faster_whisper import WhisperModel
    logger.info("faster-whisper is installed.")
    
    # Try loading a tiny model to verify PyAV and ctranslate2 are working
    logger.info("Attempting to load Whisper 'tiny' model on CPU...")
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    logger.info("Model loaded successfully!")
except Exception as e:
    logger.error(f"faster-whisper loading failed: {e}")
