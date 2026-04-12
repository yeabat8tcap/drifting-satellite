import aiohttp
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

class ScreenpipeClient:
    def __init__(self, base_url="http://localhost:3030"):
        self.base_url = base_url

    async def get_latest_context(self, seconds_ago=120):
        """
        Fetches the absolute latest screen context from Screenpipe via Accessibility data and Raw Image.
        Returns a tuple: (context_string, image_file_path)
        """
        start_time = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
        
        async with aiohttp.ClientSession() as session:
            try:
                url = f"{self.base_url}/search"
                params = {
                    "content_type": "all",
                    "limit": 100,
                    "start_time": start_time
                }
                
                async with session.get(url, params=params) as response:
                    if response.status != 200:
                        logger.warning(f"Screenpipe returned {response.status}")
                        return "No active screen context available.", None
                        
                    data = await response.json()
                    
                    if not data or "data" not in data or not data["data"]:
                        return "Screen is currently unchanged or inactive.", None

                    context_lines = []
                    image_path = None
                    target_app = None
                    
                    # Process directly (Screenpipe naturally returns newest first)
                    for item in data["data"]:
                        item_type = item.get("type", "")
                        content = item.get("content", {})
                        
                        app_name = content.get("app_name") or content.get("window_name") or "System"
                        
                        # Grab the absolute freshest image only
                        if not image_path and content.get("file_path"):
                            image_path = content.get("file_path")
                            target_app = app_name  # Track which app the image belongs to
                            
                        # Only feed Accessibility focus context if it belongs to the current app on screen
                        if item_type == "Accessibility" and app_name == target_app:
                            text = content.get("text", "").strip()
                            if text:
                                context_lines.append(f"The user is focused on a UI element in {app_name} containing the text: {text[:200]}")
                                
                    if not image_path and not context_lines:
                        return "Screen is currently inactive.", None
                        
                    unique_lines = []
                    for line in context_lines:
                        if line not in unique_lines:
                            unique_lines.append(line)
                                
                    return "\n".join(unique_lines[:3]), image_path
                    
            except aiohttp.ClientError as e:
                logger.warning(f"Could not connect to Screenpipe at {self.base_url}: {e}")
                return "Screenpipe is not accessible.", None
