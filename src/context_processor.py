import asyncio
import base64
from typing import AsyncGenerator
from loguru import logger
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import (
    Frame,
    LLMContextFrame
)
from screenpipe_client import ScreenpipeClient

class ScreenpipeContextProcessor(FrameProcessor):
    def __init__(self, client: ScreenpipeClient):
        super().__init__()
        self.client = client

    async def process_frame(self, frame: Frame, direction):
        await super().process_frame(frame, direction)
        
        # Intercept the context frame pushed by UserAggregator at the end of the turn
        if isinstance(frame, LLMContextFrame):
            messages = frame.context.get_messages()
            if messages and messages[-1].get("role") == "user":
                message = messages[-1]
                logger.info("User turn finished. Extracting native screenshot to attach as Vision payload...")
                
                try:
                    context, image_path = await self.client.get_latest_context()
                    
                    original_text = message.get("content", "")
                        
                    # Build standard text prompt
                    prompt_text = original_text
                    if context and context != "Screen is currently inactive.":
                        prompt_text = f"{original_text}\n\nVisible context currently on the screen: {context}"
                        
                    # If Screenpipe found a valid raw screenshot frame, load it as base64 and structure for Vision LLMs
                    if image_path:
                        try:
                            def _process_image(p):
                                from PIL import Image
                                import io
                                with Image.open(p) as img:
                                    if img.mode != "RGB":
                                        img = img.convert("RGB")
                                    # Compress image to 1080p boundary. This balances visual OCR density against Qwen 30B's token generation limits.
                                    img.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
                                    buffered = io.BytesIO()
                                    img.save(buffered, format="JPEG", quality=85)
                                    return base64.b64encode(buffered.getvalue()).decode('utf-8')
                                    
                            # Synchronous image processing block offloaded securely to native threadpool to explicitly prevent WebRTC dropout hanging from asyncio main thread occlusion
                            base64_img = await asyncio.to_thread(_process_image, image_path)
                            # Replace the flat text string with the OpenAI-compatible multimodal array payload
                            message["content"] = [
                                {"type": "text", "text": prompt_text},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}
                            ]
                            logger.info(f"Successfully attached native screenshot vision frame: {image_path}")
                            
                        except Exception as img_err:
                            logger.error(f"Failed to load raw image file {image_path}: {img_err}")
                            message["content"] = prompt_text
                    else:
                        message["content"] = prompt_text
                            
                except Exception as e:
                    logger.error(f"Failed to fetch screenpipe context payload: {e}")
            
        # Pass the (potentially modified) frame down the pipeline
        await self.push_frame(frame, direction)
