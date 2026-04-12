import asyncio
import os
from contextlib import asynccontextmanager
from typing import Dict

import uvicorn
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from dotenv import load_dotenv

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.frames.frames import LLMRunFrame

# WebRTC Transport
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.base_transport import TransportParams

from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.aggregators.llm_context import LLMContext

# Local Models
from pipecat.services.ollama.llm import OLLamaLLMService
from pipecat.services.whisper.stt import WhisperSTTServiceMLX
from pipecat.services.kokoro.tts import KokoroTTSService

# Screenpipe Integration
from screenpipe_client import ScreenpipeClient
from context_processor import ScreenpipeContextProcessor

load_dotenv()

# Dictionary to hold current WebRTC connections
pcs_map: Dict[str, SmallWebRTCConnection] = {}

ice_servers = [
    IceServer(
        urls="stun:stun.l.google.com:19302",
    )
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Cleanup all connections on teardown
    coros = [pc.disconnect() for pc in pcs_map.values()]
    await asyncio.gather(*coros)
    pcs_map.clear()

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/public", StaticFiles(directory="public"), name="public")

async def run_jarvis(webrtc_connection: SmallWebRTCConnection):
    logger.info("Initializing Drifting Satellite Pipeline for WebRTC...")

    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    # Core Pipecat Engines
    stt = WhisperSTTServiceMLX()
    
    screenpipe_client = ScreenpipeClient()
    
    # Enable Vision LLM (restored to 30B Qwen for strict OCR accuracy under downscaling logic)
    llm = OLLamaLLMService(
        settings=OLLamaLLMService.Settings(
            model="qwen3-vl:30b",
            temperature=0.0
        )
    )
    tts = KokoroTTSService(voice_id="am_fenrir")
    # Intercept Kokoro ONNX streaming generator to natively increase speed by 15% 
    original_create_stream = tts._kokoro.create_stream
    def accelerate_stream(*args, **kwargs):
        kwargs["speed"] = 1.15 # overwrite the hardcoded 1.0 from Pipecat
        return original_create_stream(*args, **kwargs)
    tts._kokoro.create_stream = accelerate_stream
    
    context = LLMContext()
    context.add_message({
        "role": "system",
        "content": (
            "You are Jarvis, a highly capable local AI assistant. "
            "You are running 100% locally on a Mac Studio Ultra. "
            "IMPORTANT RULES: "
            "1. You are engaging in a real-time voice conversation. Speak ONLY in plain, conversational English paragraphs."
            "2. NEVER use markdown, formatting, asterisks, bullet points, numbered lists, braces, or brackets of any kind. This breaks the text-to-speech engine."
            "3. Keep casual responses relatively concise. However, if the user explicitly asks you to 'explain', 'elaborate', or 'do a deep dive', you are fully permitted to explain the topic thoroughly in multiple conversational sentences."
            "4. If the user asks about their screen or you receive attached context, answer naturally as if you are looking at their screen natively. Do not say 'The image shows' or 'On your screen'."
        )
    })
    
    # Ultra-low latency VAD for real-time engagement
    fast_vad = SileroVADAnalyzer(params=VADParams(stop_secs=0.2))
    smart_turn = LocalSmartTurnAnalyzerV3()

    from pipecat.turns.user_turn_strategies import UserTurnStrategies
    from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=fast_vad,
            user_turn_strategies=UserTurnStrategies(
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=smart_turn)]
            )
        ),
    )
    
    context_processor = ScreenpipeContextProcessor(client=screenpipe_client)

    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.frames.frames import TextFrame, LLMTextFrame, TTSTextFrame

    class MarkdownStripper(FrameProcessor):
        async def process_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
            await super().process_frame(frame, direction)
            # Actively strip markdown astersisks, hashes, and LaTeX math formatting that cause TTS glitch generation
            if isinstance(frame, (TextFrame, LLMTextFrame, TTSTextFrame)) and hasattr(frame, "text"):
                frame.text = frame.text.replace("*", "").replace("#", "").replace(r"\(", "").replace(r"\)", "").replace(r"\[", "").replace(r"\]", "")
            await self.push_frame(frame, direction)

    markdown_stripper = MarkdownStripper()
    pipeline = Pipeline([
        transport.input(),
        stt,
        user_aggregator,
        context_processor,
        llm,
        markdown_stripper,
        tts,
        transport.output(),
        assistant_aggregator
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Web client successfully joined the connection.")
        # Trigger an initial observation frame or system setup
        context.add_message({"role": "user", "content": "Jarvis, are you online? Respond shortly."})
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Web client left. Shutting down pipeline.")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


@app.get("/")
async def index():
    return RedirectResponse(url="/public/index.html")

@app.post("/api/offer")
async def offer(request: dict, background_tasks: BackgroundTasks):
    pc_id = request.get("pc_id")

    if pc_id and pc_id in pcs_map:
        pipecat_connection = pcs_map[pc_id]
        logger.info(f"Reusing existing connection for pc_id: {pc_id}")
        await pipecat_connection.renegotiate(
            sdp=request["sdp"],
            type=request["type"],
            restart_pc=request.get("restart_pc", False),
        )
    else:
        pipecat_connection = SmallWebRTCConnection(ice_servers)
        await pipecat_connection.initialize(sdp=request["sdp"], type=request["type"])

        @pipecat_connection.event_handler("closed")
        async def handle_disconnected(webrtc_connection: SmallWebRTCConnection):
            logger.info(f"Discarding peer connection for pc_id: {webrtc_connection.pc_id}")
            pcs_map.pop(webrtc_connection.pc_id, None)

        background_tasks.add_task(run_jarvis, pipecat_connection)

    answer = pipecat_connection.get_answer()
    pcs_map[answer["pc_id"]] = pipecat_connection

    return answer

if __name__ == "__main__":
    logger.info("Starting up FastAPI Server on http://localhost:7860")
    uvicorn.run(app, host="0.0.0.0", port=7860)
