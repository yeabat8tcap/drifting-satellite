import time
import subprocess
import numpy as np
import sounddevice as sd
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | ClapDetector | %(message)s")

CLAP_THRESHOLD = 0.15      # Adjust based on microphone sensitivity (0.0 to 1.0 peak)
DEBOUNCE_SECONDS = 5.0     # Prevent multiple triggers within this window

last_trigger_time = 0

def audio_callback(indata, frames, time_info, status):
    global last_trigger_time
    
    if status:
        logging.warning("Audio status: %s", status)
    
    # Calculate peak amplitude in the current audio chunk
    peak = np.max(np.abs(indata))
    
    if peak > CLAP_THRESHOLD:
        current_time = time.time()
        
        # Debounce to prevent multiple opens in rapid succession
        if (current_time - last_trigger_time) > DEBOUNCE_SECONDS:
            last_trigger_time = current_time
            logging.info(f"Clap detected! (Peak: {peak:.3f}) Waking up Jarvis...")
            
            # Spin up Web UI in default browser
            try:
                subprocess.Popen(["open", "http://localhost:7860/?wake=true"])
            except Exception as e:
                logging.error(f"Failed to open browser: {e}")

def main():
    logging.info(f"Starting background Clap Detector (Threshold: {CLAP_THRESHOLD})... Listening indefinitely.")
    # Block endlessly using the default microphone stream
    with sd.InputStream(callback=audio_callback, channels=1, samplerate=16000):
        while True:
            time.sleep(1)

if __name__ == "__main__":
    main()
