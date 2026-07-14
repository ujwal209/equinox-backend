import asyncio
import httpx
import os
import logging

logger = logging.getLogger("uvicorn.error")

async def start_render_pinger():
    """
    Background task to ping the backend's own public URL every 10 minutes
    to prevent the Render free-tier container from going to sleep.
    """
    # Render automatically sets RENDER_EXTERNAL_URL to the public domain
    public_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_BACKEND_URL")
    
    if not public_url:
        logger.warning("[Pinger] RENDER_EXTERNAL_URL or PUBLIC_BACKEND_URL is not set. Render keep-alive pinger disabled.")
        return
        
    logger.info(f"[Pinger] Starting Render keep-alive pinger for: {public_url}")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            await asyncio.sleep(600)  # Ping every 10 minutes (Render free tier sleeps after 15 mins of inactivity)
            try:
                # Ping the API health endpoint to register traffic
                health_endpoint = f"{public_url.rstrip('/')}/api/v1/health"
                res = await client.get(health_endpoint)
                if res.status_code == 200:
                    logger.info(f"[Pinger] Keep-alive ping successful: {health_endpoint} -> 200 OK")
                else:
                    logger.warning(f"[Pinger] Keep-alive ping returned status code {res.status_code}: {health_endpoint}")
            except Exception as e:
                logger.error(f"[Pinger] Keep-alive ping failed to reach {public_url}: {e}")
