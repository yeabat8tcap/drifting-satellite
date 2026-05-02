const connectBtn = document.getElementById('connect-btn');
const statusText = document.querySelector('#status-indicator .text');
const statusIndicator = document.getElementById('status-indicator');
const orb = document.getElementById('orb');
const remoteAudio = document.getElementById('remote-audio');

let peerConnection = null;
let localStream = null;
let isConnecting = false;

async function startConnection() {
    if (isConnecting || peerConnection) return;
    isConnecting = true;
    connectBtn.innerText = "Connecting...";
    connectBtn.disabled = true;

    try {
        console.log("Requesting microphone access...");
        localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
        console.log("Microphone access granted.");

        peerConnection = new RTCPeerConnection({
            iceServers: [
                { urls: "stun:stun.l.google.com:19302" }
            ]
        });

        const dataChannel = peerConnection.createDataChannel("pipecat");
        dataChannel.onmessage = (e) => console.log("Pipecat Event:", e.data);

        // Web Audio visualizer setup
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const analyser = audioCtx.createAnalyser();
        const source = audioCtx.createMediaStreamSource(localStream);
        source.connect(analyser);
        analyser.fftSize = 256;
        const dataArray = new Uint8Array(analyser.frequencyBinCount);

        function renderVisualizer() {
            if (!isConnecting && (!peerConnection || peerConnection.connectionState !== 'connected')) {
                return;
            }
            requestAnimationFrame(renderVisualizer);
            analyser.getByteFrequencyData(dataArray);
            let sum = 0;
            for(let i = 0; i < dataArray.length; i++) sum += dataArray[i];
            let avg = sum / dataArray.length;
            let scale = 1 + (avg / 256);
            if (orb.classList.contains("active")) {
                orb.style.transform = `scale(${scale})`;
            }
        }
        renderVisualizer();

        // Add local microphone track to connection
        localStream.getTracks().forEach(track => {
            peerConnection.addTrack(track, localStream);
        });

        // Listen for remote audio track from Jarvis
        peerConnection.ontrack = (event) => {
            console.log("Received remote track.");
            if (event.streams && event.streams[0]) {
                remoteAudio.srcObject = event.streams[0];
            } else {
                let inboundStream = new MediaStream([event.track]);
                remoteAudio.srcObject = inboundStream;
            }
        };

        // Handle connection state changes
        peerConnection.onconnectionstatechange = () => {
            console.log("Connection State:", peerConnection.connectionState);
            if (peerConnection.connectionState === 'connected') {
                isConnecting = false;
                connectBtn.disabled = false;
                statusText.innerText = "Connected";
                statusIndicator.classList.add("connected");
                orb.classList.add("active");
                connectBtn.innerText = "Disconnect";
                connectBtn.classList.remove("primary");
                connectBtn.classList.add("disconnect");
            } else if (peerConnection.connectionState === 'disconnected' || peerConnection.connectionState === 'failed') {
                stopConnection();
            }
        };

        // Create Offer
        const offer = await peerConnection.createOffer();
        await peerConnection.setLocalDescription(offer);

        // Wait for ICE gathering to complete so we send all candidates in one SDP blob
        console.log("Gathering ICE candidates...");
        await new Promise((resolve) => {
            if (peerConnection.iceGatheringState === 'complete') {
                resolve();
            } else {
                const checkState = () => {
                    if (peerConnection.iceGatheringState === 'complete') {
                        peerConnection.removeEventListener('icegatheringstatechange', checkState);
                        resolve();
                    }
                };
                peerConnection.addEventListener('icegatheringstatechange', checkState);
                // Fallback timeout in case gathering gets stuck
                setTimeout(() => {
                    peerConnection.removeEventListener('icegatheringstatechange', checkState);
                    resolve();
                }, 3000);
            }
        });

        console.log("Sending SDP offer to server...");
        // Use the localDescription which now has gathered ICE candidates
        const response = await fetch('/api/offer', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                sdp: peerConnection.localDescription.sdp,
                type: peerConnection.localDescription.type,
            }),
        });

        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }

        const answer = await response.json();
        console.log("Received SDP answer from server.");
        
        if (peerConnection) {
            await peerConnection.setRemoteDescription(new RTCSessionDescription({
                type: answer.type,
                sdp: answer.sdp
            }));
        }

    } catch (e) {
        console.error("Connection failed:", e);
        stopConnection();
        alert("Could not connect to Jarvis. " + e.message);
    }
}

function stopConnection() {
    isConnecting = false;
    connectBtn.disabled = false;

    if (peerConnection) {
        peerConnection.close();
        peerConnection = null;
    }
    if (localStream) {
        localStream.getTracks().forEach(track => track.stop());
        localStream = null;
    }
    
    statusText.innerText = "Disconnected";
    statusIndicator.classList.remove("connected");
    orb.classList.remove("active");
    
    connectBtn.innerText = "Connect to Jarvis";
    connectBtn.classList.remove("disconnect");
    connectBtn.classList.add("primary");
    remoteAudio.srcObject = null;
}

connectBtn.addEventListener('click', () => {
    if (peerConnection && (peerConnection.connectionState === 'connected' || isConnecting)) {
        stopConnection();
    } else {
        startConnection();
    }
});

// Auto-connect overlay if URL contains `?wake=true`
window.addEventListener('load', () => {
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('wake') === 'true') {
        // Create full-screen tap-to-wake overlay to bypass browser AudioContext lock
        const overlay = document.createElement('div');
        overlay.style.position = 'fixed';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100vw';
        overlay.style.height = '100vh';
        overlay.style.backgroundColor = 'rgba(10, 10, 15, 0.9)';
        overlay.style.backdropFilter = 'blur(10px)';
        overlay.style.zIndex = '9999';
        overlay.style.display = 'flex';
        overlay.style.justifyContent = 'center';
        overlay.style.alignItems = 'center';
        overlay.style.cursor = 'pointer';
        overlay.style.color = '#fff';
        overlay.style.fontFamily = "'Outfit', sans-serif";
        overlay.style.fontSize = '2rem';
        overlay.style.fontWeight = '700';
        overlay.innerHTML = 'TAP ANYWHERE TO WAKE JARVIS';

        document.body.appendChild(overlay);

        overlay.addEventListener('click', () => {
            overlay.remove();
            startConnection();
        });
    }
});
