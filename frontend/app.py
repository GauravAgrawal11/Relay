"""
app.py: FastAPI web application providing a ChatGPT-style conversational UI for The Relay.
Renders an interactive web interface with session history, citation inspection pills,
and real-time query answering across Attention, Moshi, and Dynamic Chunking.
"""

from pathlib import Path
import sys
from typing import Any, Dict, Optional
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from conversation import ConversationalRelay
from pipeline import RelayPipeline

app = FastAPI(title="The Relay - ChatGPT UI", version="1.0.0")

# Default pipeline and conversational wrapper
pipeline = RelayPipeline(source="stand-in", retrieval_setting="setting_b")
chat_relay = ConversationalRelay(pipeline=pipeline, cache_enabled=True)


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default_session"


HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>The Relay — Academic RAG Assistant</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {
            --bg-main: #212121;
            --bg-sidebar: #171717;
            --bg-card: #2f2f2f;
            --bg-user-msg: #2f2f2f;
            --text-main: #ececec;
            --text-muted: #b4b4b4;
            --accent: #10a37f;
            --accent-hover: #1a7f64;
            --border: #424242;
            --tag-bg: #1c3b31;
            --tag-text: #4ade80;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
        body { display: flex; height: 100vh; background-color: var(--bg-main); color: var(--text-main); overflow: hidden; }
        
        /* Sidebar */
        .sidebar { width: 260px; background-color: var(--bg-sidebar); border-right: 1px solid var(--border); display: flex; flex-direction: column; padding: 14px; }
        .brand { font-size: 1.15rem; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 10px; margin-bottom: 20px; }
        .brand i { color: var(--accent); }
        .new-chat-btn { background: transparent; border: 1px solid var(--border); color: var(--text-main); border-radius: 8px; padding: 10px 14px; text-align: left; cursor: pointer; display: flex; align-items: center; gap: 10px; font-size: 0.9rem; transition: background 0.2s; }
        .new-chat-btn:hover { background: var(--bg-card); }
        .papers-header { font-size: 0.75rem; text-transform: uppercase; color: var(--text-muted); font-weight: 700; margin: 24px 0 10px 0; letter-spacing: 0.5px; }
        .paper-badge { background: var(--bg-card); border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; font-size: 0.8rem; }
        .paper-badge strong { display: block; color: #fff; margin-bottom: 2px; }
        .paper-badge span { color: var(--text-muted); font-size: 0.75rem; }
        .sidebar-footer { margin-top: auto; font-size: 0.75rem; color: var(--text-muted); border-top: 1px solid var(--border); padding-top: 12px; }

        /* Main Chat Window */
        .main { flex: 1; display: flex; flex-direction: column; position: relative; }
        .chat-header { height: 50px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; padding: 0 20px; font-size: 0.95rem; font-weight: 600; }
        .chat-container { flex: 1; overflow-y: auto; padding: 24px 15%; display: flex; flex-direction: column; gap: 20px; }
        
        /* Messages */
        .msg { display: flex; gap: 16px; width: 100%; max-width: 800px; margin: 0 auto; }
        .msg.user .avatar { background: #5436da; }
        .msg.assistant .avatar { background: var(--accent); }
        .avatar { width: 34px; height: 34px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 0.9rem; flex-shrink: 0; color: #fff; }
        .msg-body { flex: 1; font-size: 0.95rem; line-height: 1.6; }
        .msg-body .provenance { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; }
        .citation-pill { background: var(--tag-bg); color: var(--tag-text); border: 1px solid #14532d; border-radius: 4px; padding: 2px 8px; font-size: 0.75rem; font-family: monospace; font-weight: 600; display: inline-flex; align-items: center; gap: 4px; }
        .telemetry { font-size: 0.75rem; color: var(--text-muted); margin-top: 6px; }

        /* Input Area */
        .input-wrapper { padding: 16px 15% 24px 15%; background: var(--bg-main); }
        .input-box { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; display: flex; align-items: center; padding: 10px 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.25); }
        .input-box input { flex: 1; background: transparent; border: none; color: #fff; font-size: 0.95rem; outline: none; }
        .send-btn { background: var(--accent); border: none; width: 34px; height: 34px; border-radius: 8px; color: #fff; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: background 0.2s; }
        .send-btn:hover { background: var(--accent-hover); }
        .send-btn:disabled { background: var(--border); cursor: not-allowed; }
        .quick-prompts { display: flex; gap: 8px; margin-bottom: 12px; overflow-x: auto; }
        .quick-btn { background: var(--bg-card); border: 1px solid var(--border); border-radius: 16px; color: var(--text-muted); padding: 4px 12px; font-size: 0.75rem; cursor: pointer; white-space: nowrap; }
        .quick-btn:hover { color: #fff; border-color: var(--accent); }
    </style>
</head>
<body>
    <div class="sidebar">
        <div class="brand">
            <i class="fa-solid fa-bolt"></i> The Relay
        </div>
        <button class="new-chat-btn" onclick="clearChat()">
            <i class="fa-solid fa-plus"></i> New Chat
        </button>

        <div class="papers-header">Indexed Research Papers</div>
        <div class="paper-badge">
            <strong>Attention Is All You Need</strong>
            <span>1706.03762v7 • 15 pages</span>
        </div>
        <div class="paper-badge">
            <strong>Moshi: Spoken Dialogue</strong>
            <span>2410.00037v1 • 66 pages</span>
        </div>
        <div class="paper-badge">
            <strong>Dynamic Chunking (H-Net)</strong>
            <span>2507.07955v2 • 39 pages</span>
        </div>

        <div class="sidebar-footer">
            <div><strong>Backend:</strong> Stand-in (Calibrated)</div>
            <div><strong>Retrieval:</strong> BM25 + ONNX Vectors</div>
        </div>
    </div>

    <div class="main">
        <div class="chat-header">
            <span>The Relay — Technical Paper Assistant</span>
            <span style="font-size:0.75rem; color:var(--tag-text);"><i class="fa-solid fa-circle" style="font-size:8px;"></i> Gate Ready</span>
        </div>

        <div class="chat-container" id="chatContainer">
            <div class="msg assistant">
                <div class="avatar"><i class="fa-solid fa-robot"></i></div>
                <div class="msg-body">
                    Hello! I am <strong>The Relay</strong>, an academic Q&A pipeline grounded strictly in the texts of <em>Attention Is All You Need</em>, <em>Moshi</em>, and <em>Dynamic Chunking (H-Net)</em>.
                    <br><br>
                    Every factual claim I make is cited with its exact paper provenance (e.g., <code>[attention v7 s3.2 p.4]</code>). How can I assist you today?
                </div>
            </div>
        </div>

        <div class="input-wrapper">
            <div class="quick-prompts">
                <button class="quick-btn" onclick="sendPrompt('What is the frame rate of Mimi in Moshi?')">Mimi frame rate</button>
                <button class="quick-btn" onclick="sendPrompt('What is scaled dot-product attention in Attention Is All You Need?')">Scaled dot-product attention</button>
                <button class="quick-btn" onclick="sendPrompt('Does H-Net require explicit supervision for dynamic chunking?')">H-Net supervision</button>
                <button class="quick-btn" onclick="sendPrompt('What is the battery life of Apple Vision Pro?')">Out-of-scope question</button>
            </div>
            <div class="input-box">
                <input type="text" id="userInput" placeholder="Ask a question about Attention, Moshi, or Dynamic Chunking..." onkeydown="if(event.key==='Enter') sendMessage()">
                <button class="send-btn" id="sendBtn" onclick="sendMessage()"><i class="fa-solid fa-arrow-up"></i></button>
            </div>
        </div>
    </div>

    <script>
        const chatContainer = document.getElementById('chatContainer');
        const userInput = document.getElementById('userInput');
        const sendBtn = document.getElementById('sendBtn');
        let currentSessionId = 'session_' + Math.random().toString(36).substring(2, 9);

        function clearChat() {
            currentSessionId = 'session_' + Math.random().toString(36).substring(2, 9);
            chatContainer.innerHTML = `
                <div class="msg assistant">
                    <div class="avatar"><i class="fa-solid fa-robot"></i></div>
                    <div class="msg-body">
                        New conversation initialized. Ask me anything about the three arXiv papers!
                    </div>
                </div>
            `;
        }

        function sendPrompt(text) {
            userInput.value = text;
            sendMessage();
        }

        async function sendMessage() {
            const query = userInput.value.trim();
            if (!query) return;

            // Append User Message
            appendMessage('user', query);
            userInput.value = '';
            userInput.disabled = true;
            sendBtn.disabled = true;

            // Temporary Assistant Placeholder
            const loadingMsgId = appendLoading();

            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: query, session_id: currentSessionId })
                });
                const data = await response.json();
                removeLoading(loadingMsgId);
                appendMessage('assistant', data.answer, data.retrieved_tags, data.timing_first_token_ms, data.timing_total_ms);
            } catch (err) {
                removeLoading(loadingMsgId);
                appendMessage('assistant', 'Error communicating with The Relay pipeline: ' + err.message);
            } finally {
                userInput.disabled = false;
                sendBtn.disabled = false;
                userInput.focus();
            }
        }

        function appendMessage(role, text, tags = [], ttft = null, totalMs = null) {
            const msgDiv = document.createElement('div');
            msgDiv.className = `msg ${role}`;
            
            let tagsHtml = '';
            if (tags && tags.length > 0) {
                tagsHtml = '<div class="provenance">' + tags.map(t => `<span class="citation-pill"><i class="fa-solid fa-bookmark"></i> ${t}</span>`).join('') + '</div>';
            }

            let telemetryHtml = '';
            if (ttft !== null && totalMs !== null) {
                telemetryHtml = `<div class="telemetry"><i class="fa-solid fa-clock"></i> First token: ${ttft.toFixed(1)}ms • Total: ${totalMs.toFixed(1)}ms</div>`;
            }

            msgDiv.innerHTML = `
                <div class="avatar"><i class="fa-solid ${role === 'user' ? 'fa-user' : 'fa-robot'}"></i></div>
                <div class="msg-body">
                    ${text.replace(/\\n/g, '<br>')}
                    ${tagsHtml}
                    ${telemetryHtml}
                </div>
            `;
            chatContainer.appendChild(msgDiv);
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

        function appendLoading() {
            const id = 'loading_' + Date.now();
            const msgDiv = document.createElement('div');
            msgDiv.className = 'msg assistant';
            msgDiv.id = id;
            msgDiv.innerHTML = `
                <div class="avatar"><i class="fa-solid fa-robot"></i></div>
                <div class="msg-body" style="color:var(--text-muted);">
                    <i class="fa-solid fa-spinner fa-spin"></i> Retrieving passages & executing through gate...
                </div>
            `;
            chatContainer.appendChild(msgDiv);
            chatContainer.scrollTop = chatContainer.scrollHeight;
            return id;
        }

        function removeLoading(id) {
            const el = document.getElementById(id);
            if (el) el.remove();
        }
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def get_index():
    return HTMLResponse(content=HTML_CONTENT)


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    res = await chat_relay.chat(
        user_message=req.message,
        session_id=req.session_id,
        scenario="interactive",
    )
    return JSONResponse(content=res)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8501)

