// State management
let socket = null;
let mediaRecorder = null;
let audioContext = null;
let analyser = null;
let microphoneStream = null;

let isRecording = false;
let vadState = 'IDLE'; // IDLE, SPEAKING, SILENT
let silenceTimer = null;
const SILENCE_DURATION_MS = 1800; // Increased to 1.8s to lower Requests Per Minute (RPM) and avoid Gemini 429 Rate Limits
const VOLUME_THRESHOLD = 0.025; // Sensitivity of voice detection (0.01 to 0.1)

let originalFeed = document.getElementById('originalFeed');
let translatedFeed = document.getElementById('translatedFeed');
let recordBtn = document.getElementById('recordBtn');
let statusDot = document.getElementById('statusDot');
let statusText = document.getElementById('statusText');
let translationDirection = document.getElementById('translationDirection');
let mockModeSwitch = document.getElementById('mockModeSwitch');
let apiKeyGroup = document.getElementById('apiKeyGroup');

// API Key input
let geminiKeyInput = document.getElementById('geminiKey');

// Metrics elements
let sttLatencyEl = document.getElementById('sttLatency');
let transLatencyEl = document.getElementById('transLatency');
let totalLatencyEl = document.getElementById('totalLatency');

// Show/hide API keys based on Mock Mode
mockModeSwitch.addEventListener('change', (e) => {
    if (e.target.checked) {
        apiKeyGroup.classList.add('hidden');
    } else {
        apiKeyGroup.classList.remove('hidden');
    }
    sendConfig();
});

// Update config when API key is edited
geminiKeyInput.addEventListener('input', () => {
    sendConfig();
});

// Change lang badges on selection
translationDirection.addEventListener('change', (e) => {
    const direction = e.target.value;
    const srcBadge = document.getElementById('sourceLangBadge');
    const targetBadge = document.getElementById('targetLangBadge');
    const srcTitle = document.getElementById('sourceLangTitle');
    const targetTitle = document.getElementById('targetLangTitle');

    if (direction === 'en_to_vi') {
        srcBadge.textContent = 'EN';
        srcTitle.textContent = 'Original Text (English)';
        targetBadge.textContent = 'VI';
        targetTitle.textContent = 'Interpretation (Vietnamese)';
    } else {
        srcBadge.textContent = 'VI';
        srcTitle.textContent = 'Original Text (Vietnamese)';
        targetBadge.textContent = 'EN';
        targetTitle.textContent = 'Interpretation (English)';
    }

    sendConfig();
});

// Setup WebSocket Connection
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host || '127.0.0.1:8000';
    const wsUrl = `${protocol}//${host}/ws/stream`;

    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        statusDot.className = 'status-dot connected';
        statusText.textContent = 'Connected';
        console.log('WebSocket connected');
        sendConfig();
    };

    socket.onclose = () => {
        statusDot.className = 'status-dot';
        statusText.textContent = 'Disconnected';
        console.log('WebSocket disconnected. Reconnecting in 3s...');
        setTimeout(connectWebSocket, 3000);
    };

    socket.onerror = (error) => {
        console.error('WebSocket Error:', error);
    };

    socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'result') {
            appendResult(data.original, data.translated);
            updateMetrics(data.metrics);
        }
    };
}

function getSupportedMimeType() {
    if (typeof MediaRecorder === 'undefined') return 'audio/webm';
    if (MediaRecorder.isTypeSupported('audio/webm')) {
        return 'audio/webm';
    }
    if (MediaRecorder.isTypeSupported('audio/ogg')) {
        return 'audio/ogg';
    }
    return 'audio/wav';
}

function sendConfig() {
    if (socket && socket.readyState === WebSocket.OPEN) {
        const config = {
            type: 'config',
            direction: translationDirection.value,
            mimeType: getSupportedMimeType(),
            mock_mode: mockModeSwitch.checked,
            custom_gemini_key: geminiKeyInput.value.trim()
        };
        socket.send(JSON.stringify(config));
    }
}

// Append transcription results to the lists
function appendResult(originalText, translatedText) {
    // Hide placeholders
    document.getElementById('originalPlaceholder').style.display = 'none';
    document.getElementById('translatedPlaceholder').style.display = 'none';

    // Original Bubble
    const origBubble = document.createElement('div');
    origBubble.className = 'bubble';
    origBubble.textContent = originalText;
    originalFeed.appendChild(origBubble);
    originalFeed.scrollTop = originalFeed.scrollHeight;

    // Translation Bubble
    const transBubble = document.createElement('div');
    transBubble.className = 'bubble translation';
    transBubble.textContent = translatedText;
    translatedFeed.appendChild(transBubble);
    translatedFeed.scrollTop = translatedFeed.scrollHeight;
}

// Update performance latency metrics on UI
function updateMetrics(metrics) {
    sttLatencyEl.textContent = `${metrics.stt_latency_sec.toFixed(2)}s`;
    transLatencyEl.textContent = `${metrics.translation_latency_sec.toFixed(2)}s`;
    totalLatencyEl.textContent = `${metrics.total_latency_sec.toFixed(2)}s`;
}

// Initialize Web Audio API and VAD Analyser
async function initAudioMode() {
    try {
        microphoneStream = await navigator.mediaDevices.getUserMedia({ 
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true
            } 
        });

        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        analyser = audioContext.createAnalyser();
        const source = audioContext.createMediaStreamSource(microphoneStream);
        source.connect(analyser);

        analyser.fftSize = 512;
        monitorAudioLevel();
    } catch (err) {
        console.error('Error accessing microphone:', err);
        alert('Could not access microphone. Please allow mic permissions.');
        stopRecordingUI();
    }
}

// Browser-based VAD (Voice Activity Detection)
function monitorAudioLevel() {
    if (!isRecording) return;

    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    analyser.getByteTimeDomainData(dataArray);

    // Calculate volume level (Root Mean Square - RMS)
    let sum = 0;
    for (let i = 0; i < bufferLength; i++) {
        const val = (dataArray[i] - 128) / 128;
        sum += val * val;
    }
    const rms = Math.sqrt(sum / bufferLength);

    // VAD State Machine
    if (rms > VOLUME_THRESHOLD) {
        if (vadState === 'IDLE') {
            console.log('Voice activity detected. Starting new segment...');
            vadState = 'SPEAKING';
            startSegmentRecording();
        } else if (vadState === 'SILENT') {
            console.log('Voice activity resumed. Continuing current segment...');
            vadState = 'SPEAKING';
        }
        
        if (silenceTimer) {
            clearTimeout(silenceTimer);
            silenceTimer = null;
        }
    } else {
        if (vadState === 'SPEAKING') {
            vadState = 'SILENT';
            console.log('Silence detected. Slicing segment in 1.8s...');
            silenceTimer = setTimeout(() => {
                stopSegmentRecording();
                vadState = 'IDLE';
            }, SILENCE_DURATION_MS);
        }
    }

    requestAnimationFrame(monitorAudioLevel);
}

// Start capturing a spoken phrase
function startSegmentRecording() {
    // Stop any existing recorder first to prevent overlapping streams
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        try {
            mediaRecorder.stop();
        } catch (e) {
            console.warn("Stopped overlapping recorder");
        }
    }

    const currentChunks = []; // Local array isolated to this specific recording session
    const mimeType = getSupportedMimeType();
    
    // Choose format supported by browser
    const options = { mimeType: mimeType };
    if (mimeType === 'audio/wav') {
        options.mimeType = ''; // Let browser choose default if WAV isn't natively supported by MediaRecorder
    }

    const recorder = new MediaRecorder(microphoneStream, options);
    mediaRecorder = recorder; // Keep global reference only for manual stopping
    
    recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
            currentChunks.push(event.data);
        }
    };

    recorder.onstop = () => {
        const audioBlob = new Blob(currentChunks, { type: recorder.mimeType || 'audio/webm' });
        
        if (audioBlob.size > 1000 && socket && socket.readyState === WebSocket.OPEN) {
            const reader = new FileReader();
            reader.onloadend = () => {
                // Ensure we only send if the user is still actively recording
                if (isRecording) {
                    socket.send(reader.result);
                }
            };
            reader.readAsArrayBuffer(audioBlob);
        }
    };

    recorder.start(100);
}

// Stop and package the segment
function stopSegmentRecording() {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
    }
}

// ==================== RECORDING BUTTON TRIGGER ====================
recordBtn.addEventListener('click', async () => {
    if (!isRecording) {
        // Start session
        isRecording = true;
        recordBtn.classList.add('recording');
        recordBtn.innerHTML = '<i class="fa-solid fa-square"></i>';
        
        sendConfig(); // Ensure backend is aligned on settings
        await initAudioMode();
    } else {
        // Stop session
        stopRecordingUI();
    }
});

function stopRecordingUI() {
    isRecording = false;
    vadState = 'IDLE';
    recordBtn.classList.remove('recording');
    recordBtn.innerHTML = '<i class="fa-solid fa-microphone"></i>';

    // Clear timers
    if (silenceTimer) {
        clearTimeout(silenceTimer);
        silenceTimer = null;
    }

    // Stop Media Recorder
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
    }

    // Stop Mic Streams
    if (microphoneStream) {
        microphoneStream.getTracks().forEach(track => track.stop());
        microphoneStream = null;
    }

    // Close Audio Context
    if (audioContext) {
        audioContext.close();
        audioContext = null;
    }
}

// Connect to WebSockets on page load
window.addEventListener('load', () => {
    connectWebSocket();
});
