/* ================================================================== *
 * Addon: MCP Agent
 *
 * A complete agent panel that talks to your MCP servers through the
 * global `mcpClient`. The agent can list tools, decide which to call,
 * execute them, and feed results back — all inside its own panel.
 *
 * It re-uses the chat UI's provider stack via `window.createClient(...)`
 * (same as the main chat) and sends tool definitions via
 * `mcpClient.getSelectedToolsForAPI()`, exactly like chat.v1.js does.
 *
 * Uses: dom:read, dom:write, storage:local, ui:notify, net:fetch
 * ================================================================== */

ChatAddons.register({
    id: 'workspace:mcp-agent',
    name: 'MCP Agent',
    version: '1.0.0',
    description: 'Kompletter Agent, der auf deine MCP-Server zugreift, Tools ausführt und Ergebnisse verarbeitet.',
    author: 'g4f',
    builtin: false,
    permissions: ['dom:read', 'dom:write', 'storage:local', 'ui:notify', 'net:fetch'],

    STORAGE_KEY: 'chat.addons.data.pa-mcp-agent.config',
    DEFAULT_MODEL: '', // '' => use the model selected in the main chat

    // ----------------------------------------------------------------
    // Lifecycle
    // ----------------------------------------------------------------
    load() {
        return (async () => {
            // Make sure the global MCP client is available
            const mcp = this._getMCP();
            if (!mcp) {
                ChatAddonHost.notify('MCP Client nicht gefunden – bitte Seite neu laden', 'error', 5000);
                return;
            }
            await this._injectPanel();
            this._renderTools();
        })();
    },

    unload() {
        const panel = document.getElementById('pa-mcp-agent-panel');
        if (panel) panel.remove();
    },

    // ----------------------------------------------------------------
    // MCP access (bare global — same pattern as addon-host.js)
    // ----------------------------------------------------------------
    _getMCP() {
        // Workspace addons run in page scope, so `mcpClient` resolves to
        // the global instance created by chat.v1.js / addon-init.js.
        try {
            if (typeof mcpClient !== 'undefined' && mcpClient && typeof mcpClient.getAllTools === 'function') {
                return mcpClient;
            }
            if (typeof global !== 'undefined' && global.mcpClient && typeof global.mcpClient.getAllTools === 'function') {
                return global.mcpClient;
            }
            if (typeof window !== 'undefined' && window.mcpClient && typeof window.mcpClient.getAllTools === 'function') {
                return window.mcpClient;
            }
        } catch (e) { /* ignore */ }
        return null;
    },

    _getVirtualTools() {
        // Discover tools from virtual servers registered by other addons
        // (e.g. pa-browser-errors.js registers a "browser" server).
        const tools = [];
        if (typeof window !== 'undefined' && Array.isArray(window._paVirtualServers)) {
            for (const server of window._paVirtualServers) {
                if (!server || !Array.isArray(server.tools)) continue;
                for (const tool of server.tools) {
                    tools.push({
                        name: tool.name,
                        serverName: server.name || 'virtual',
                        description: tool.description || '',
                        inputSchema: tool.inputSchema || { type: 'object', properties: {} },
                        _virtual: true,
                        _server: server,
                    });
                }
            }
        }
        return tools;
    },

    _getTools() {
        const mcp = this._getMCP();
        let mcpTools = [];
        if (mcp) {
            try {
                const tools = mcp.getAllTools();
                mcpTools = Array.isArray(tools) ? tools : [];
            } catch (e) {
                console.error('[mcp-agent] getAllTools error', e);
            }
        }
        // Merge real MCP tools with virtual browser tools
        return [...mcpTools, ...this._getVirtualTools()];
    },

    _getSelectedToolsForAPI() {
        const mcp = this._getMCP();
        let mcpTools = [];
        if (mcp) {
            try {
                const tools = mcp.getSelectedToolsForAPI();
                mcpTools = Array.isArray(tools) ? tools : [];
            } catch (e) {
                console.error('[mcp-agent] getSelectedToolsForAPI error', e);
            }
        }
        // Also include virtual tools in API format
        const virtualApiTools = this._getVirtualTools().map(t => ({
            type: 'function',
            function: {
                name: t.name,
                description: t.description,
                parameters: t.inputSchema || { type: 'object', properties: {} },
            },
        }));
        return [...mcpTools, ...virtualApiTools];
    },

    // Read the provider selected in the main chat UI.
    _getSelectedProvider() {
        try {
            const sel = document.querySelector('#provider, select[name="provider"], .provider-select');
            if (sel && sel.value) return sel.value;
        } catch (e) { /* ignore */ }
        return 'default';
    },

    // Read the model selected in the main chat UI.
    _getSelectedModel() {
        try {
            const sel = document.querySelector('#model, select[name="model"], .model-select');
            if (sel && sel.value) return sel.value;
        } catch (e) { /* ignore */ }
        return '';
    },

    async _executeToolCall(toolCall) {
        // First check if this is a virtual (browser) tool
        const toolName = toolCall?.function?.name;
        if (toolName) {
            const virtualTools = this._getVirtualTools();
            const vTool = virtualTools.find(t => t.name === toolName);
            if (vTool && vTool._server) {
                const handler = vTool._server.tools.find(t => t.name === toolName)?.handler;
                if (typeof handler === 'function') {
                    let args = {};
                    try { args = JSON.parse(toolCall.function.arguments || '{}'); } catch (e) { /* ignore */ }
                    return await handler(args);
                }
            }
        }

        // Otherwise delegate to the real MCP client
        const mcp = this._getMCP();
        if (!mcp) throw new Error('MCP Client nicht verfügbar');
        if (typeof mcp.executeToolCalls === 'function') {
            const results = await mcp.executeToolCalls([toolCall]);
            return Array.isArray(results) ? results[0] : results;
        }
        if (typeof mcp.executeToolCall === 'function') {
            return await mcp.executeToolCall(toolCall);
        }
        throw new Error('MCP Client unterstützt kein Tool-Executing');
    },

    // ----------------------------------------------------------------
    // UI injection
    // ----------------------------------------------------------------
    _injectPanel() {
        return new Promise((resolve) => {
            const tryInject = () => {
                const anchor = document.querySelector('.input-area, .chat-input-area, #chat-input-area, main .bottom, .message-input-area, #userInput');
                if (!anchor) {
                    setTimeout(tryInject, 400);
                    return;
                }
                if (document.getElementById('pa-mcp-agent-panel')) {
                    resolve();
                    return;
                }

                const panel = document.createElement('div');
                panel.id = 'pa-mcp-agent-panel';
                panel.className = 'pa-mcp-agent';
                panel.innerHTML = `
                    <div class="pa-mcp-agent-header">
                        <span class="pa-mcp-agent-title">🤖 MCP Agent</span>
                        <span class="pa-mcp-agent-status" id="pa-mcp-agent-status">verbinde…</span>
                        <button class="pa-mcp-agent-errors" id="pa-mcp-agent-errors" title="Browser-Fehler als Kontext senden">🐛 <span id="pa-mcp-agent-errorcount">0</span></button>
                        <button class="pa-mcp-agent-toggle" id="pa-mcp-agent-toggle" title="Tools anzeigen/verbergen">🧰 <span id="pa-mcp-agent-toolcount">0</span></button>
                        <button class="pa-mcp-agent-clear" id="pa-mcp-agent-clear" title="Verlauf löschen">🗑️</button>
                    </div>
                    <div class="pa-mcp-agent-body" id="pa-mcp-agent-body">
                        <div class="pa-mcp-agent-tools" id="pa-mcp-agent-tools"></div>
                        <div class="pa-mcp-agent-log" id="pa-mcp-agent-log"></div>
                        <div class="pa-mcp-agent-input-row">
                            <textarea class="pa-mcp-agent-input" id="pa-mcp-agent-input" placeholder="Frag den Agenten… (Enter = senden, Shift+Enter = neue Zeile)" rows="1"></textarea>
                            <button class="pa-mcp-agent-send" id="pa-mcp-agent-send" title="Senden">➤</button>
                        </div>
                    </div>
                `;
                anchor.prepend(panel);
                this._bindPanelEvents(panel);
                this._appendLog('👋 Agent bereit. Tools von deinen MCP-Servern werden unten angezeigt.', 'info');
                resolve();
            };
            tryInject();
        });
    },

    _bindPanelEvents(panel) {
        const input = panel.querySelector('#pa-mcp-agent-input');
        const sendBtn = panel.querySelector('#pa-mcp-agent-send');
        const toggleBtn = panel.querySelector('#pa-mcp-agent-toggle');
        const clearBtn = panel.querySelector('#pa-mcp-agent-clear');
        const errorsBtn = panel.querySelector('#pa-mcp-agent-errors');

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this._send();
            }
        });
        sendBtn.addEventListener('click', () => this._send());
        toggleBtn.addEventListener('click', () => {
            const toolsEl = panel.querySelector('#pa-mcp-agent-tools');
            toolsEl.classList.toggle('pa-mcp-agent-tools-collapsed');
        });
        clearBtn.addEventListener('click', () => {
            const log = panel.querySelector('#pa-mcp-agent-log');
            log.innerHTML = '';
            this._appendLog('🗑️ Verlauf gelöscht.', 'info');
        });
        if (errorsBtn) {
            errorsBtn.addEventListener('click', () => this._sendErrors());
        }
        // Auto-grow textarea
        input.addEventListener('input', () => {
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 160) + 'px';
        });
        // Update error count periodically
        this._updateErrorCount();
        setInterval(() => this._updateErrorCount(), 3000);
    },

    // ----------------------------------------------------------------
    // Rendering helpers
    // ----------------------------------------------------------------
    _renderTools() {
        const panel = document.getElementById('pa-mcp-agent-panel');
        if (!panel) return;
        const toolsEl = panel.querySelector('#pa-mcp-agent-tools');
        const countEl = panel.querySelector('#pa-mcp-agent-toolcount');
        const statusEl = panel.querySelector('#pa-mcp-agent-status');
        const tools = this._getTools();
        const selected = this._getSelectedToolsForAPI();

        countEl.textContent = tools.length;
        if (tools.length === 0) {
            statusEl.textContent = 'keine Tools';
            toolsEl.innerHTML = `<div class="pa-mcp-agent-empty">Keine MCP-Tools gefunden. Bitte Server in den MCP-Einstellungen aktivieren und Seite neu laden.</div>`;
            return;
        }
        statusEl.textContent = `${tools.length} Tools • ${selected.length} aktiv`;

        const selectedIds = new Set(selected.map(t => t.function?.name));
        toolsEl.innerHTML = '';
        const list = document.createElement('div');
        list.className = 'pa-mcp-agent-tool-list';
        for (const tool of tools.slice(0, 50)) {
            const isSel = selectedIds.has(tool.name);
            const item = document.createElement('button');
            item.className = 'pa-mcp-agent-tool' + (isSel ? ' selected' : '');
            item.title = tool.description || tool.name;
            item.innerHTML = `<span class="pa-mcp-agent-tool-name">${this._esc(tool.name)}</span><span class="pa-mcp-agent-tool-server">${this._esc(tool.serverName || '')}</span>`;
            item.addEventListener('click', () => {
                // Click tool => insert a "use tool" instruction into the prompt
                const input = document.getElementById('pa-mcp-agent-input');
                if (input) {
                    input.value += (input.value ? '\n' : '') + `Nutze das Tool ${tool.name}: `;
                    input.focus();
                }
            });
            list.appendChild(item);
        }
        toolsEl.appendChild(list);
    },

    _appendLog(text, cls = '') {
        const panel = document.getElementById('pa-mcp-agent-panel');
        if (!panel) return;
        const log = panel.querySelector('#pa-mcp-agent-log');
        const line = document.createElement('div');
        line.className = 'pa-mcp-agent-line ' + cls;
        line.innerHTML = text;
        log.appendChild(line);
        log.scrollTop = log.scrollHeight;
        return line;
    },

    _setStatus(text) {
        const panel = document.getElementById('pa-mcp-agent-panel');
        if (panel) {
            const status = panel.querySelector('#pa-mcp-agent-status');
            if (status) status.textContent = text;
        }
    },

    _esc(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },

    // Get browser console errors from the browser-errors addon
    _getBrowserErrors() {
        try {
            // Check if the browser-errors addon is loaded
            const addon = ChatAddons.get && ChatAddons.get('workspace:browser-errors');
            if (addon && typeof addon.getErrors === 'function') {
                return addon.getErrors();
            }
            // Fallback: check global scope
            if (typeof window !== 'undefined' && window._paBrowserErrors) {
                return window._paBrowserErrors.getErrors();
            }
        } catch (e) { /* ignore */ }
        return [];
    },

    // Update the error count badge in the panel header
    _updateErrorCount() {
        const el = document.getElementById('pa-mcp-agent-errorcount');
        if (!el) return;
        const count = this._getBrowserErrors().length;
        el.textContent = count;
        const btn = document.getElementById('pa-mcp-agent-errors');
        if (btn) {
            btn.style.opacity = count > 0 ? '1' : '0.5';
            btn.title = count > 0 ? `${count} Browser-Fehler – klicken um an Agent zu senden` : 'Keine Browser-Fehler';
        }
    },

    // Send browser errors directly to the agent as a prompt
    async _sendErrors() {
        const errors = this._getBrowserErrors();
        if (errors.length === 0) {
            this._appendLog('ℹ️ Keine Browser-Fehler erfasst.', 'info');
            return;
        }
        const errorText = errors.map(e =>
            `[${e.type}] ${e.time}: ${e.message}${e.stack ? '\n' + e.stack : ''}`
        ).join('\n\n');

        const prompt = `The following browser console errors were captured. Please analyze them and suggest fixes:\n\n${errorText}`;
        const input = document.querySelector('#pa-mcp-agent-input');
        if (input) {
            input.value = prompt;
            input.dispatchEvent(new Event('input'));
        }
        this._send();
    },

    // ----------------------------------------------------------------
    // Agent loop
    // ----------------------------------------------------------------
    async _send() {
        const panel = document.getElementById('pa-mcp-agent-panel');
        if (!panel) return;
        const input = panel.querySelector('#pa-mcp-agent-input');
        const sendBtn = panel.querySelector('#pa-mcp-agent-send');
        const text = (input.value || '').trim();
        if (!text) return;

        input.value = '';
        input.style.height = 'auto';
        sendBtn.disabled = true;

        this._appendLog(`<span class="pa-mcp-agent-user">🧑 <b>Du:</b></span> ${this._esc(text)}`, 'user');

        const tools = this._getSelectedToolsForAPI();
        if (tools.length === 0) {
            this._appendLog(`⚠️ <b>Keine MCP-Tools ausgewählt.</b> Aktiviere Tools in den MCP-Einstellungen, damit der Agent sie nutzen kann. Ich antworte trotzdem – aber ohne Werkzeuge.`, 'warn');
        }

        // History (kept in memory)
        this._history = this._history || [];

        // Inject browser console errors as context if available
        const browserErrors = this._getBrowserErrors();
        let userContent = text;
        if (browserErrors.length > 0) {
            const errorSummary = browserErrors.slice(-5).map(e =>
                `[${e.type}] ${e.time}: ${e.message}${e.stack ? '\n' + e.stack.split('\n').slice(0, 3).join('\n') : ''}`
            ).join('\n');
            userContent = `${text}\n\n--- Browser Console Errors (recent) ---\n${errorSummary}\n--- End Errors ---\n\nYou can use the browser.get_errors tool to get full error details, or browser.clear_errors to clear them.`;
            this._appendLog(`📋 <b>${browserErrors.length} Browser-Fehler</b> als Kontext hinzugefügt`, 'info');
        }

        this._history.push({ role: 'user', content: userContent });

        try {
            const responseLine = this._appendLog(`<span class="pa-mcp-agent-ai">🤖 <b>Agent:</b></span> <span class="pa-mcp-agent-stream"></span>`, 'ai');
            const streamEl = responseLine.querySelector('.pa-mcp-agent-stream');

            await this._runAgentLoop(this._history, streamEl, tools);

            this._appendLog('', 'sep');
        } catch (err) {
            console.error('[mcp-agent] error', err);
            this._appendLog(`❌ <b>Fehler:</b> ${this._esc(err.message || String(err))}`, 'error');
        } finally {
            sendBtn.disabled = false;
            this._setStatus(`${this._getTools().length} Tools`);
            input.focus();
        }
    },

    async _runAgentLoop(messages, streamEl, tools) {
        // Max iterations to prevent infinite tool loops
        const MAX_ITER = 8;
        let iter = 0;
        let full = '';

        // Resolve provider + model from the main chat UI so the agent
        // uses the same backend the user has selected in the settings.
        const provider = this._getSelectedProvider();
        const model = this.DEFAULT_MODEL || this._getSelectedModel();

        // Build the agent system prompt
        const sysPrompt = [
            'Du bist ein leistungsfähiger Agent, der auf MCP-Tools zugreifen kann.',
            'Wenn du ein Tool aufrufst, gib eine Tool-Call-Nachricht im Format:',
            '```tool',
            '{"name": "toolname", "arguments": {"...": "..."}}',
            '```',
            'Warte auf das Ergebnis und verarbeite es in deiner Antwort.',
            'Rufe Tools nur auf, wenn sie wirklich helfen. Nutze exakte Tool-Namen aus der Liste.',
        ].join('\n');

        const msgs = [{ role: 'system', content: sysPrompt }, ...messages];

        while (iter < MAX_ITER) {
            iter++;

            const client = await window.createClient(provider, {});
            const params = {
                model: model || undefined,
                messages: msgs,
                tools: tools.length > 0 ? tools : undefined,
                stream: true,
            };
            if (!params.model) delete params.model;
            if (!params.tools) delete params.tools;

            let collectedToolCalls = [];
            let hasToolCall = false;

            const stream = await client.chat.completions.create(params);
            for await (const chunk of stream) {
                const delta = chunk?.choices?.[0]?.delta;
                if (!delta) continue;
                if (delta.reasoning) {
                    // Show reasoning inline in a muted style
                    const r = document.createElement('span');
                    r.className = 'pa-mcp-agent-reasoning';
                    r.textContent = delta.reasoning;
                    streamEl.appendChild(r);
                }
                if (delta.content) {
                    full += delta.content;
                    const node = document.createElement('span');
                    node.textContent = delta.content;
                    streamEl.appendChild(node);
                    streamEl.scrollIntoView({ block: 'nearest' });
                }
                if (delta.tool_calls) {
                    hasToolCall = true;
                    for (const tc of delta.tool_calls) {
                        if (!collectedToolCalls[tc.index]) {
                            collectedToolCalls[tc.index] = { id: tc.id, function: { name: '', arguments: '' } };
                        }
                        if (tc.id) collectedToolCalls[tc.index].id = tc.id;
                        if (tc.function?.name) collectedToolCalls[tc.index].function.name += tc.function.name;
                        if (tc.function?.arguments) collectedToolCalls[tc.index].function.arguments += tc.function.arguments;
                    }
                }
            }

            // Normalize tool calls
            collectedToolCalls = collectedToolCalls.filter(tc => tc && tc.function && tc.function.name);

            if (!hasToolCall || collectedToolCalls.length === 0) {
                // No tools needed — final answer complete
                messages.push({ role: 'assistant', content: full });
                return full;
            }

            // ----- Tool calls detected: display + execute -----
            for (const tc of collectedToolCalls) {
                let args;
                try {
                    args = JSON.parse(tc.function.arguments || '{}');
                } catch (e) {
                    args = {};
                }
                const callInfo = document.createElement('div');
                callInfo.className = 'pa-mcp-agent-call';
                callInfo.innerHTML = `🔧 <b>Tool-Call:</b> ${this._esc(tc.function.name)} <span class="pa-mcp-agent-callargs">${this._esc(JSON.stringify(args).slice(0, 200))}</span>`;
                streamEl.appendChild(callInfo);

                let result;
                try {
                    this._setStatus(`⏳ ${tc.function.name}…`);
                    result = await this._executeToolCall({
                        id: tc.id || `call_${Date.now()}_${iter}`,
                        function: {
                            name: tc.function.name,
                            arguments: args,
                        },
                    });
                } catch (err) {
                    console.error('[mcp-agent] tool error', err);
                    result = { content: `Fehler: ${err.message || err}` };
                }

                const resultText = this._toolResultToString(result);
                const resultInfo = document.createElement('div');
                resultInfo.className = 'pa-mcp-agent-result';
                const resultHeader = document.createElement('div');
                resultHeader.className = 'pa-mcp-agent-result-header';
                resultHeader.textContent = `↩️ Ergebnis (${resultText.length} Zeichen)`;
                resultInfo.appendChild(resultHeader);
                const resultBody = document.createElement('div');
                resultBody.className = 'pa-mcp-agent-result-body';
                resultBody.textContent = resultText.slice(0, 2000);
                resultInfo.appendChild(resultBody);
                streamEl.appendChild(resultInfo);
                streamEl.scrollIntoView({ block: 'nearest' });

                // Feed back into the conversation (OpenAI-style)
                messages.push({
                    role: 'assistant',
                    content: null,
                    tool_calls: [{
                        id: tc.id || `call_${Date.now()}_${iter}`,
                        type: 'function',
                        function: {
                            name: tc.function.name,
                            arguments: tc.function.arguments || '{}',
                        },
                    }],
                });
                messages.push({
                    role: 'tool',
                    tool_call_id: tc.id || `call_${Date.now()}_${iter}`,
                    content: resultText,
                });
            }
            // Continue loop: model processes tool results
        }

        messages.push({ role: 'assistant', content: full });
        return full;
    },

    _toolResultToString(result) {
        try {
            if (result == null) return '(kein Ergebnis)';
            if (typeof result === 'string') return result;
            if (Array.isArray(result)) {
                return result.map(item => {
                    if (item == null) return '';
                    if (typeof item === 'string') return item;
                    if (item.content != null) return typeof item.content === 'string' ? item.content : JSON.stringify(item.content);
                    return JSON.stringify(item);
                }).join('\n');
            }
            if (result.content != null) {
                return typeof result.content === 'string' ? result.content : JSON.stringify(result.content);
            }
            return JSON.stringify(result);
        } catch (e) {
            return String(result);
        }
    },
});

// Minimal CSS injection (guarded — idempotent)
(function injectMcpAgentCss() {
    if (document.getElementById('pa-mcp-agent-css')) return;
    const style = document.createElement('style');
    style.id = 'pa-mcp-agent-css';
    style.textContent = `
.pa-mcp-agent {
    display: flex; flex-direction: column; gap: 6px;
    margin-bottom: 8px; padding: 8px 10px;
    background: rgba(255,255,255,.03);
    border: 1px solid var(--blur-border, #333);
    border-radius: 12px;
    font-size: 13px; line-height: 1.5;
    max-height: 420px; overflow: hidden;
}
.pa-mcp-agent-header {
    display: flex; align-items: center; gap: 8px;
    font-weight: 600; color: var(--text, #e8e8e8);
}
.pa-mcp-agent-title { flex: 1; }
.pa-mcp-agent-status {
    font-size: 11px; font-weight: 400; opacity: .6;
    background: rgba(139,61,255,.15); color: var(--accent, #8b3dff);
    padding: 2px 8px; border-radius: 10px; white-space: nowrap;
}
.pa-mcp-agent-toggle, .pa-mcp-agent-clear, .pa-mcp-agent-errors {
    background: none; border: none; cursor: pointer;
    font-size: 14px; opacity: .6; padding: 2px 6px; border-radius: 6px;
}
.pa-mcp-agent-toggle:hover, .pa-mcp-agent-clear:hover, .pa-mcp-agent-errors:hover { opacity: 1; background: rgba(255,255,255,.08); }
.pa-mcp-agent-errors { color: #f87171; }
.pa-mcp-agent-errors:hover { background: rgba(248,113,113,.12); }
.pa-mcp-agent-body { display: flex; flex-direction: column; gap: 6px; min-height: 0; }
.pa-mcp-agent-tools {
    display: flex; flex-direction: column; gap: 4px; max-height: 110px; overflow: auto;
    border: 1px solid rgba(255,255,255,.06); border-radius: 8px; padding: 4px;
    background: rgba(0,0,0,.15);
}
.pa-mcp-agent-tools-collapsed { display: none; }
.pa-mcp-agent-tool-list { display: flex; flex-wrap: wrap; gap: 4px; }
.pa-mcp-agent-tool {
    display: inline-flex; align-items: center; gap: 6px;
    background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.08);
    color: inherit; border-radius: 8px; padding: 2px 8px; font-size: 12px; cursor: pointer;
    transition: border-color .15s, background .15s;
}
.pa-mcp-agent-tool:hover { border-color: var(--accent, #8b3dff); background: rgba(139,61,255,.12); }
.pa-mcp-agent-tool.selected { border-color: var(--accent, #8b3dff); background: rgba(139,61,255,.18); }
.pa-mcp-agent-tool-name { font-weight: 600; }
.pa-mcp-agent-tool-server { font-size: 10px; opacity: .5; }
.pa-mcp-agent-empty { font-size: 12px; opacity: .6; padding: 4px 8px; }
.pa-mcp-agent-log {
    display: flex; flex-direction: column; gap: 4px;
    max-height: 240px; overflow: auto; padding: 4px 2px;
    font-size: 13px;
}
.pa-mcp-agent-line { white-space: pre-wrap; word-break: break-word; }
.pa-mcp-agent-line.user { opacity: .9; }
.pa-mcp-agent-line.ai { }
.pa-mcp-agent-line.info { opacity: .6; font-size: 12px; }
.pa-mcp-agent-line.warn { color: #f0b429; font-size: 12px; }
.pa-mcp-agent-line.error { color: #f87171; font-size: 12px; }
.pa-mcp-agent-line.sep { border-top: 1px solid rgba(255,255,255,.06); margin: 4px 0; }
.pa-mcp-agent-reasoning { display: block; color: #9ca3af; font-style: italic; font-size: 12px; white-space: pre-wrap; }
.pa-mcp-agent-call {
    display: block; margin: 4px 0; padding: 4px 8px;
    background: rgba(59,130,246,.12); border-left: 3px solid #3b82f6;
    border-radius: 6px; font-size: 12px; color: #93c5fd;
}
.pa-mcp-agent-callargs { opacity: .6; font-family: monospace; }
.pa-mcp-agent-result {
    display: block; margin: 2px 0 6px; padding: 4px 8px;
    background: rgba(34,197,94,.08); border-left: 3px solid #22c55e;
    border-radius: 6px; font-size: 11px; color: #86efac;
}
.pa-mcp-agent-result-header { font-weight: 600; margin-bottom: 2px; }
.pa-mcp-agent-result-body {
    font-family: monospace; white-space: pre-wrap; word-break: break-word;
    max-height: 160px; overflow: auto; opacity: .85; font-size: 11px;
}
.pa-mcp-agent-input-row {
    display: flex; align-items: flex-end; gap: 8px;
}
.pa-mcp-agent-input {
    flex: 1; resize: none; min-height: 36px; max-height: 160px;
    background: rgba(0,0,0,.25); border: 1px solid var(--blur-border, #333);
    border-radius: 10px; color: inherit; padding: 8px 10px; font-size: 13px;
    font-family: inherit; outline: none;
}
.pa-mcp-agent-input:focus { border-color: var(--accent, #8b3dff); }
.pa-mcp-agent-send {
    width: 40px; height: 40px; border: none; border-radius: 10px;
    background: var(--accent, #8b3dff); color: #fff; cursor: pointer;
    font-size: 16px; flex-shrink: 0;
}
.pa-mcp-agent-send:disabled { opacity: .4; cursor: not-allowed; }
.pa-mcp-agent-send:hover:not(:disabled) { filter: brightness(1.15); }
`;
    document.head.appendChild(style);
})();
