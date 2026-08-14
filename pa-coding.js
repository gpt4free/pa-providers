/* ================================================================== *
 * Addon: Coding Agent (pa-coding)
 *
 * A coding-specialized agent panel built on the MCP tool stack.
 * It re-uses the same provider stack as the main chat
 * (`window.createClient(...)`) and the same MCP tool execution
 * (`mcpClient.executeToolCalls(...)`) as the MCP Agent, but:
 *
 *   1. Uses a coding-tuned system prompt.
 *   2. Filters MCP tools by default to coding-relevant ones (file ops,
 *      git, diff/patch, search, browser debugging) — user can toggle.
 *   3. Renders code blocks with copy buttons and syntax-aware styling.
 *   4. Lets you inject the selected text/code from the editor or chat
 *      into a prompt (file/snippet injection).
 *
 * Uses: dom:read, dom:write, storage:local, ui:notify, net:fetch
 * ================================================================== */

ChatAddons.register({
    id: 'workspace:pa-coding',
    name: 'Coding Agent',
    version: '1.0.4',
    description: 'Coding-fokussierter Agent: schreibt, debuggt, refaktoriert und erklärt Code mit deinen MCP-Tools.',
    author: 'g4f',
    builtin: false,
    permissions: ['dom:read', 'dom:write', 'storage:local', 'ui:notify', 'net:fetch'],

    STORAGE_KEY: 'chat.addons.data.pa-coding.config',
    DEFAULT_MODEL: '', // '' => use the model selected in the main chat

    // Coding-relevant tool name substrings (case-insensitive) used to
    // filter MCP tools. Virtual tools keep their server prefix (e.g.
    // "virtual.*") so we match against the full dotted name.
    _logIdCounter: 0,
    _lastStreamLineId: null,

    CODING_TOOL_MATCH: [
        // filesystem / file ops
        'file', 'read', 'write', 'edit', 'append', 'patch', 'diff',
        'glob', 'search', 'grep', 'find', 'list', 'mkdir', 'stat',
        // git / repo
        'git', 'commit', 'branch', 'repo', 'github',
        // code execution / analysis
        'execute', 'run', 'terminal', 'shell', 'bash', 'python',
        'compile', 'test', 'lint', 'analyze', 'inspect',
        // browser / debugging
        'browser', 'dom', 'console', 'error',
    ],

    // ----------------------------------------------------------------
    // Lifecycle
    // ----------------------------------------------------------------
    load() {
        return (async () => {
            const mcp = this._getMCP();
            if (!mcp) {
                ChatAddonHost.notify('MCP Client nicht gefunden – bitte Seite neu laden', 'error', 5000);
                return;
            }
            await this._injectPanel();
            this._applyConfigToInputs();
            this._renderTools();
        })();
    },

    unload() {
        this._loaded = false;
        const panel = document.getElementById('pa-coding-panel');
        if (panel) {
            panel.parentElement.childNodes.forEach((n) => { if (n.nodeType === Node.ELEMENT_NODE) n.style.display = ''; });
            panel.remove();
        }
    },

    // ----------------------------------------------------------------
    // MCP access (bare global — same pattern as addon-host.js)
    // ----------------------------------------------------------------
    _getMCP() {
        try {
            if (typeof mcpClient !== 'undefined' && mcpClient) return mcpClient;
            if (typeof global !== 'undefined' && global.mcpClient) return global.mcpClient;
            if (typeof window !== 'undefined' && window.mcpClient) return window.mcpClient;
        } catch (e) { /* ignore */ }
        return null;
    },

    // Virtual tools registered by other addons via window._paVirtualServers
    _getVirtualTools() {
        const tools = [];
        if (typeof window !== 'undefined' && Array.isArray(window._paVirtualServers)) {
            for (const server of window._paVirtualServers) {
                if (!server || !Array.isArray(server.tools)) continue;
                for (const tool of server.tools) {
                    tools.push({
                        name: tool.name,
                        serverName: server.name || '',
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
                console.error('[pa-coding] getAllTools error', e);
            }
        }
        return [...mcpTools, ...this._getVirtualTools()];
    },

    // Only coding-relevant tools (used when the "nur Coding-Tools" filter is on)
    _filterCodingTools(tools) {
        const lowerName = (t) => (t.name || t.function?.name || '').toLowerCase();
        return tools.filter((t) => {
            const n = lowerName(t);
            return this.CODING_TOOL_MATCH.some((m) => n.includes(m));
        });
    },

    // Tools in OpenAI function-call API format, respecting the coding filter
    _getSelectedToolsForAPI(forceAll) {
        const allTools = this._getTools();
        const shouldFilter = forceAll !== true;
        let pool = shouldFilter ? this._filterCodingTools(allTools) : allTools;
        if (pool.length === 0) pool = allTools; // fallback: nothing matched
        const out = [];
        for (const t of pool) {
            out.push({
                type: 'function',
                function: {
                    name: t.name,
                    description: t.description,
                    parameters: (t.inputSchema && typeof t.inputSchema === 'object') ? t.inputSchema : { type: 'object', properties: {} },
                },
            });
        }
        return out;
    },

    // ----------------------------------------------------------------
    // Provider / model from the main chat UI
    // ----------------------------------------------------------------
    _getSelectedProvider() {
        try {
            const sel = document.querySelector('#provider, select[name="provider"], .provider-select');
            if (sel && sel.value) return sel.value;
        } catch (e) { /* ignore */ }
        return 'default';
    },

    _getSelectedModel() {
        try {
            const sel = document.querySelector('#model, select[name="model"], .model-select');
            if (sel && sel.value) return sel.value;
        } catch (e) { /* ignore */ }
        return '';
    },

    // ----------------------------------------------------------------
    // Tool execution (virtual first, then real MCP)
    // ----------------------------------------------------------------
    async _executeToolCall(toolCall) {
        const toolName = toolCall?.function?.name;
        if (toolName) {
            const vTool = this._getVirtualTools().find((t) => t.name === toolName);
            if (vTool && vTool._server) {
                const handler = vTool._server.tools.find((t) => t.name === toolName)?.handler;
                if (typeof handler === 'function') {
                    let args = {};
                    const rawArgs = toolCall.function.arguments;
                    if (typeof rawArgs === 'string') {
                        try { args = JSON.parse(rawArgs || '{}'); } catch (e) { /* ignore */ }
                    } else if (rawArgs && typeof rawArgs === 'object') {
                        args = rawArgs;
                    }
                    return await handler(args);
                }
            }
        }
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
                // Anchor inside .chat-container so the panel fills it 100%.
                // Fall back to chat body / input area if not found.
                const anchor = document.querySelector('.chat-container')
                    || document.querySelector('#chatBody, .chat-body')
                    || document.querySelector('.input-area, .chat-input-area, #chat-input-area, main .bottom, .message-input-area, #userInput');
                if (!anchor) {
                    setTimeout(tryInject, 400);
                    return;
                }
                if (document.getElementById('pa-coding-panel')) {
                    resolve();
                    return;
                }

                const panel = document.createElement('div');
                panel.id = 'pa-coding-panel';
                panel.className = 'pa-coding';
                panel.innerHTML = `
                    <div class="pa-coding-header">
                        <span class="pa-coding-title">👨‍💻 Coding Agent</span>
                        <span class="pa-coding-status" id="pa-coding-status">verbinde…</span>
                        <label class="pa-coding-filter" title="Nur Coding-relevante Tools verwenden">
                            <input type="checkbox" id="pa-coding-filter" checked> <span>nur Code-Tools</span>
                        </label>
                        <button class="pa-coding-errors" id="pa-coding-errors" title="Browser-Error als Kontext senden">🐛 <span id="pa-coding-errorcount">0</span></button>
                        <button class="pa-coding-fullsize" id="pa-coding-fullsize" title="Volle Größe (Chat-Body ersetzen)">⛶</button>
                        <button class="pa-coding-toggle" id="pa-coding-toggle" title="Tools anzeigen/verbergen">🧰 <span id="pa-coding-toolcount">0</span></button>
                        <button class="pa-coding-clear" id="pa-coding-clear" title="History löschen">🗑️</button>
                    </div>
                    <div class="pa-coding-config">
                        <label class="pa-coding-config-field" title="Base URL des OpenAI-kompatiblen Endpunkts (z. B. http://localhost:8090/api/pa:6455e06a)">
                            <span>Base URL</span>
                            <input type="text" id="pa-coding-baseurl" placeholder="http://localhost:8090/api/pa:6455e06a">
                        </label>
                        <label class="pa-coding-config-field" title="Model (leer = Model aus dem Haupt-Chat)">
                            <span>Model</span>
                            <input type="text" id="pa-coding-model" placeholder="auto (Chat-Model)">
                        </label>
                    </div>
                    <div class="pa-coding-body" id="pa-coding-body">
                        <div class="pa-coding-tools" id="pa-coding-tools"></div>
                        <div class="pa-coding-log" id="pa-coding-log"></div>
                        <div class="pa-coding-input-row">
                            <textarea class="pa-coding-input" id="pa-coding-input" placeholder="Code task: write, debug, refactor or explain code… (Enter = send, Shift+Enter = new line)" rows="1"></textarea>
                            <button class="pa-coding-send" id="pa-coding-send" title="Send">➤</button>
                        </div>
                        <div class="pa-coding-actions">
                            <button class="pa-coding-action" id="pa-coding-inject" title="Insert selected code/text from the page">📋 Auswahl einsetzen</button>
                        </div>
                    </div>
                `;
                anchor.childNodes.forEach((n) => { if (n.nodeType === Node.ELEMENT_NODE) n.style.display = 'none'; });
                anchor.prepend(panel);
                this._bindPanelEvents(panel);
                this._appendLog('👨‍💻 Coding Agent bereit. Nutzt deine MCP-Tools (Datei, Git, Suche, Browser-Debugging).', 'info');
                resolve();
            };
            tryInject();
        });
    },

    _bindPanelEvents(panel) {
        const input = panel.querySelector('#pa-coding-input');
        const sendBtn = panel.querySelector('#pa-coding-send');
        const toggleBtn = panel.querySelector('#pa-coding-toggle');
        const clearBtn = panel.querySelector('#pa-coding-clear');
        const errorsBtn = panel.querySelector('#pa-coding-errors');
        const filterCb = panel.querySelector('#pa-coding-filter');
        const injectBtn = panel.querySelector('#pa-coding-inject');
        const fullsizeBtn = panel.querySelector('#pa-coding-fullsize');
        const baseUrlInput = panel.querySelector('#pa-coding-baseurl');
        const modelInput = panel.querySelector('#pa-coding-model');

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this._send();
            }
        });
        sendBtn.addEventListener('click', () => this._send());
        toggleBtn.addEventListener('click', () => {
            const toolsEl = panel.querySelector('#pa-coding-tools');
            toolsEl.classList.toggle('pa-coding-tools-collapsed');
        });
        clearBtn.addEventListener('click', () => {
            const log = panel.querySelector('#pa-coding-log');
            log.innerHTML = '';
            this._appendLog('🗑️ History cleared.', 'info');
        });
        if (errorsBtn) {
            errorsBtn.addEventListener('click', () => this._sendErrors());
        }
        filterCb.addEventListener('change', () => this._renderTools());
        injectBtn.addEventListener('click', () => this._injectSelection());
        if (fullsizeBtn) {
            fullsizeBtn.addEventListener('click', () => this.toggleFullSize());
        }

        // Config persistence
        if (baseUrlInput) {
            baseUrlInput.addEventListener('change', () => this._saveConfig({ baseUrl: baseUrlInput.value.trim() }));
        }
        if (modelInput) {
            modelInput.addEventListener('change', () => this._saveConfig({ model: modelInput.value.trim() }));
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
    // Config (base URL + model) persistence
    // ----------------------------------------------------------------
    _loadConfig() {
        if (this._config) return this._config;
        try {
            this._config = JSON.parse(localStorage.getItem(this.STORAGE_KEY) || '{}') || {};
        } catch (e) {
            this._config = {};
        }
        return this._config;
    },

    _saveConfig(patch) {
        const cfg = Object.assign({}, this._loadConfig(), patch);
        this._config = cfg;
        try { localStorage.setItem(this.STORAGE_KEY, JSON.stringify(cfg)); } catch (e) { /* ignore */ }
        return cfg;
    },

    // Apply persisted config to the inputs after panel injection
    _applyConfigToInputs() {
        const cfg = this._loadConfig();
        const panel = document.getElementById('pa-coding-panel');
        if (!panel) return;
        const baseUrlInput = panel.querySelector('#pa-coding-baseurl');
        const modelInput = panel.querySelector('#pa-coding-model');
        if (baseUrlInput && cfg.baseUrl) baseUrlInput.value = cfg.baseUrl;
        if (modelInput && cfg.model) modelInput.value = cfg.model;
    },

    // ----------------------------------------------------------------
    // Full-size toggle: make the agent panel cover the whole viewport.
    // The chat body is only hidden when it does NOT contain the panel
    // (otherwise hiding it would hide the panel itself).
    // ----------------------------------------------------------------
    toggleFullSize(force) {
        const panel = document.getElementById('pa-coding-panel');
        if (!panel) return false;
        const btn = panel.querySelector('#pa-coding-fullsize');
        const shouldFull = typeof force === 'boolean' ? force : !panel.classList.contains('pa-coding-fullsize-active');

        if (shouldFull) {
            panel.classList.add('pa-coding-fullsize-active');
            if (btn) { btn.classList.add('active'); btn.title = 'Normale Größe (Panel zurücksetzen)'; }
        } else {
            panel.classList.remove('pa-coding-fullsize-active');
            if (btn) { btn.classList.remove('active'); btn.title = 'Volle Größe (Chat-Body ausfüllen)'; }
        }
        return shouldFull;
    },

    // ----------------------------------------------------------------
    // Rendering helpers
    // ----------------------------------------------------------------
    _renderTools() {
        const panel = document.getElementById('pa-coding-panel');
        if (!panel) return;
        const toolsEl = panel.querySelector('#pa-coding-tools');
        const countEl = panel.querySelector('#pa-coding-toolcount');
        const statusEl = panel.querySelector('#pa-coding-status');
        const filterCb = panel.querySelector('#pa-coding-filter');
        const allTools = this._getTools();
        const filtered = filterCb && filterCb.checked ? this._filterCodingTools(allTools) : allTools;
        const tools = filtered.length > 0 ? filtered : allTools;
        const selected = this._getSelectedToolsForAPI();

        countEl.textContent = tools.length;
        if (tools.length === 0) {
            statusEl.textContent = 'no tools';
            toolsEl.innerHTML = `<div class="pa-coding-empty">No coding tools found. Please enable MCP servers in settings and reload the page.</div>`;
            return;
        }
        statusEl.textContent = `${tools.length} Tools${filtered.length !== allTools.length ? ' (filtered)' : ''} • ${selected.length} active`;

        const selectedIds = new Set(selected.map(t => t.function?.name));
        toolsEl.innerHTML = '';
        const list = document.createElement('div');
        list.className = 'pa-coding-tool-list';
        for (const tool of tools.slice(0, 50)) {
            const isSel = selectedIds.has(tool.name);
            const item = document.createElement('button');
            item.className = 'pa-coding-tool' + (isSel ? ' selected' : '');
            item.title = tool.description || tool.name;
            item.innerHTML = `<span class="pa-coding-tool-name">${this._esc(tool.name)}</span><span class="pa-coding-tool-server">${this._esc(tool.serverName || '')}</span>`;
            item.addEventListener('click', () => {
                const input = document.getElementById('pa-coding-input');
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
        const panel = document.getElementById('pa-coding-panel');
        if (!panel) return;
        const log = panel.querySelector('#pa-coding-log');
        const line = document.createElement('div');
        line.className = 'pa-coding-line ' + cls;
        line.innerHTML = text;
        log.appendChild(line);
        log.scrollTop = log.scrollHeight;
        return line;
    },

    // Append a status/log line with optional step marker.
    // Returns a wrapper element that can be updated in place via
    // `.update(html)` without creating a new DOM node.
    _appendStatus(html, cls) {
        const panel = document.getElementById('pa-coding-panel');
        if (!panel) return null;
        const log = panel.querySelector('#pa-coding-log');
        if (!log) return null;

        const line = document.createElement('div');
        line.className = 'pa-coding-line ' + (cls || 'info');
        line.innerHTML = html;
        log.appendChild(line);

        this._logIdCounter = (this._logIdCounter || 0) + 1;
        line.dataset.logId = 'pa-coding-log-' + this._logIdCounter;

        this._scrollToLogIfNeeded();
        return line;
    },

    // Update an existing status line in place.
    _updateStatus(line, html) {
        if (!line) return;
        if (line.dataset.logId) {
            line.dataset.logId = '';
        }
        line.innerHTML = html;
        this._scrollToLogIfNeeded();
    },

    // Render a message chunk with code-block detection + copy buttons.
    // Returns a wrapper element so callers can keep streaming into it.
    // The wrapper element keeps a prefix (e.g. the "Coding Agent:" label)
    // that is rendered as-is (safe static HTML) and never re-escaped.
    _appendCodeAware(prefixHtml, cls = '') {
        const panel = document.getElementById('pa-coding-panel');
        if (!panel) return null;
        const log = panel.querySelector('#pa-coding-log');
        const line = document.createElement('div');
        line.className = 'pa-coding-line ' + cls;
        log.appendChild(line);
        line._raw = '';
        const self = this;
        line._append = function (chunk) {
            this._raw = (this._raw || '') + chunk;
            // Re-render: static prefix + escaped/annotated streamed body
            this.innerHTML = prefixHtml + self._formatCode(this._raw);
            // Re-bind copy buttons (idempotent)
            this.querySelectorAll('.pa-coding-copy').forEach((btn) => {
                if (btn.dataset.bound) return;
                btn.dataset.bound = '1';
                btn.addEventListener('click', () => {
                    const code = btn.parentElement.querySelector('code').innerText;
                    navigator.clipboard?.writeText(code).catch(() => {});
                    btn.textContent = '✓ kopiert';
                    setTimeout(() => { btn.textContent = '⧉'; }, 1200);
                });
            });
        };
        line._append('');
        log.scrollTop = log.scrollHeight;
        return line;
    },

    // Minimal Markdown-ish code formatter: wraps ```lang … ``` blocks.
    // Everything else is plain-text escaped.
    _formatCode(raw) {
        let out = '';
        let rest = raw;
        const re = /```([a-zA-Z0-9_+-]*)\n?([\s\S]*?)```/g;
        let last = 0;
        let m;
        while ((m = re.exec(rest)) !== null) {
            out += this._esc(rest.slice(last, m.index));
            const lang = m[1] || '';
            const code = m[2];
            out += `<div class="pa-coding-codeblock"><div class="pa-coding-codeblock-head"><span>${this._esc(lang || 'code')}</span><button class="pa-coding-copy" title="Copy">⧉</button></div><pre><code class="language-${this._esc(lang)}">${this._esc(code)}</code></pre></div>`;
            last = m.index + m[0].length;
        }
        out += this._esc(rest.slice(last));
        return out.replace(/\n/g, '<br>');
    },

    // ----------------------------------------------------------------
    // Scroll helpers
    // ----------------------------------------------------------------
    _scrollToLogIfNeeded() {
        const panel = document.getElementById('pa-coding-panel');
        if (!panel) return;
        const log = panel.querySelector('#pa-coding-log');
        if (!log) return;

        const isNearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
        if (isNearBottom) {
            log.scrollTop = log.scrollHeight;
        }
    },

    _scrollToLogLine(line) {
        if (!line || !line.dataset.logId) return;
        line.dataset.logId = 'pa-coding-log-' + (++this._logIdCounter);
        line.scrollIntoView({ behavior: 'smooth', block: 'center' });
    },

    _setStatus(text) {
        const panel = document.getElementById('pa-coding-panel');
        if (!panel) return;
        const status = panel.querySelector('#pa-coding-status');
        if (status) status.textContent = text;
    },

    _esc(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },

    // ----------------------------------------------------------------
    // Browser errors (from the browser-errors addon)
    // ----------------------------------------------------------------
    _getBrowserErrors() {
        try {
            const addon = ChatAddons.get && ChatAddons.get('workspace:browser-errors');
            if (addon && typeof addon.getErrors === 'function') {
                return addon.getErrors();
            }
            if (typeof window !== 'undefined' && window._paBrowserErrors) {
                return window._paBrowserErrors.getErrors();
            }
        } catch (e) { /* ignore */ }
        return [];
    },

    _updateErrorCount() {
        const el = document.getElementById('pa-coding-errorcount');
        if (!el) return;
        const count = this._getBrowserErrors().length;
        el.textContent = count;
        const btn = document.getElementById('pa-coding-errors');
        if (btn) {
            btn.style.opacity = count > 0 ? '1' : '0.5';
            btn.title = count > 0 ? `${count} Browser-Error – klicken um an Agent zu senden` : 'Keine Browser-Error';
        }
    },

    async _sendErrors() {
        const errors = this._getBrowserErrors();
        if (errors.length === 0) {
            this._appendLog('ℹ️ Keine Browser-Error erfasst.', 'info');
            return;
        }
        const toolNames = this._getSelectedToolsForAPI(true)
            .map(t => t.function && t.function.name)
            .filter(Boolean);
        const hasTool = toolNames.some(n => /browser|error|console|get_errors/.test(n));
        if (!hasTool) {
            const line = this._appendStatus(
                'ℹ️ <b>Browser-Error sind vorhanden, aber es sind keine passenden Browser-/Error-Tools ausgewählt.</b> ' +
                'Aktiviere z. B. <b>browser.get_errors</b> oder <b>browser.clear_errors</b>, damit der Agent sie nutzen kann.',
                'warn'
            );
            return;
        }
        const errorText = errors.map(e =>
            `[${e.type}] ${e.time}: ${e.message}${e.stack ? '\n' + e.stack : ''}`
        ).join('\n\n');

        const prompt = `The following browser console errors were captured. Please analyze them and suggest fixes:\n\n${errorText}`;
        const input = document.querySelector('#pa-coding-input');
        if (input) {
            input.value = prompt;
            input.dispatchEvent(new Event('input'));
        }
        this._send();
    },

    // ----------------------------------------------------------------
    // File/snippet injection
    // ----------------------------------------------------------------
    // Inserts the currently selected text (page selection or a message
    // block) into the prompt as a code snippet.
    _injectSelection() {
        const sel = window.getSelection && window.getSelection();
        let text = sel && sel.toString ? sel.toString().trim() : '';
        if (!text) {
            // Fallback: look for the closest code block to the cursor
            const input = document.getElementById('pa-coding-input');
            const log = document.getElementById('pa-coding-log');
            if (input && input.selectionStart !== undefined && input.value) {
                text = input.value.trim();
            }
            if (!text && log) {
                const code = log.querySelector('pre code');
                if (code) text = code.innerText.trim();
            }
        }
        if (!text) {
            this._appendLog('ℹ️ Keine Auswahl gefunden. Markiere Code/Text auf der Seite oder in einer Message.', 'info');
            return;
        }
        const input = document.getElementById('pa-coding-input');
        if (!input) return;
        const snippet = `\n\`\`\`\n${text}\n\`\`\`\n`;
        if (input.value && !input.value.endsWith('\n')) input.value += '\n';
        input.value += snippet;
        input.dispatchEvent(new Event('input'));
        input.focus();
        this._appendLog('📋 Ausgewählten Code eingesetzt.', 'info');
    },

    // ----------------------------------------------------------------
    // Agent loop (coding-tuned)
    // ----------------------------------------------------------------
    async _send() {
        const panel = document.getElementById('pa-coding-panel');
        if (!panel) return;
        const input = panel.querySelector('#pa-coding-input');
        const sendBtn = panel.querySelector('#pa-coding-send');
        const text = (input.value || '').trim();
        if (!text) return;

        input.value = '';
        input.style.height = 'auto';
        sendBtn.disabled = true;

        const userLine = this._appendStatus(`<span class="pa-coding-user">🧑 <b>Du:</b></span> ${this._esc(text)}`, 'user');

        const tools = this._getSelectedToolsForAPI();
        if (tools.length === 0) {
            this._appendLog(`⚠️ <b>Keine Coding-Tools ausgewählt.</b> Aktiviere MCP-Tools in den Settings. Ich antworte trotzdem – aber ohne Werkzeuge.`, 'warn');
        }

        this._history = this._history || [];

        const browserErrors = this._getBrowserErrors();
        let userContent = text;
        if (browserErrors.length > 0) {
            const errorSummary = browserErrors.slice(-5).map(e =>
                `[${e.type}] ${e.time}: ${e.message}${e.stack ? '\n' + e.stack.split('\n').slice(0, 3).join('\n') : ''}`
            ).join('\n');
            userContent = `${text}\n\n--- Browser Console Errors (recent) ---\n${errorSummary}\n--- End Errors ---\n\nYou can use the browser.get_errors tool to get full error details, or browser.clear_errors to clear them.`;
            this._appendLog(`📋 <b>${browserErrors.length} Browser-Error</b> als Kontext hinzugefügt`, 'info');
        }

        this._history.push({ role: 'user', content: userContent });

        try {
            // The streamed response is rendered code-aware.
            this._setStatus('⏳ Response is being generated…');
            const responseLine = this._appendCodeAware(`<span class="pa-coding-ai">🤖 <b>Coding Agent:</b></span> `, 'ai');
            await this._runAgentLoop(this._history, responseLine, tools);

            this._appendLog('', 'sep');
            this._setStatus('✅ Response completed');
            if (userLine) this._scrollToLogLine(userLine);
        } catch (err) {
            console.error('[pa-coding] error', err);
            this._appendLog(`❌ <b>Error:</b> ${this._esc(err.message || String(err))}`, 'error');
            this._setStatus('❌ Error');
        } finally {
            sendBtn.disabled = false;
            this._setStatus(`${this._getSelectedToolsForAPI().length} Tools`);
            input.focus();
        }
    },

    // The streamed response itself is appended code-aware into the
    // wrapper element `streamEl` (created by _appendCodeAware).
    // Returns the final assistant text for this run.
    async _runAgentLoop(messages, streamEl, tools) {
        const MAX_ITER = 8;
        let iter = 0;
        let full = '';

        // Provider resolution: explicit base URL (if configured) else the
        // main chat provider. Model: configured model or main chat model.
        const cfg = this._loadConfig();
        const hasBaseUrl = !!(cfg.baseUrl && cfg.baseUrl.trim());
        const provider = hasBaseUrl ? 'custom' : this._getSelectedProvider();
        const model = cfg.model || this.DEFAULT_MODEL || this._getSelectedModel();
        const clientOptions = hasBaseUrl ? { baseUrl: cfg.baseUrl.trim() } : {};

        // Coding-tuned system prompt
        const sysPrompt = [
            'You are a powerful coding agent that can access MCP tools (file system, Git, search, browser debugging).',
            'You help with writing, debugging, refactoring, reviewing, and explaining code.',
            '### Core Rules',
            '- Follow user requirements strictly and to the letter.',
            '- Keep answers short and impersonal.',
            '',
            '### Role & Context',
            'You are an expert automated coding agent. ',
            '- **Gather context first:** Don\'t make assumptions. Use tools to read files ',
            'and understand the workspace before acting. Don\'t give up if a task seems ',
            'hard; explore creatively to find a solution.',
            '- **Be efficient:** Read large file chunks to minimize tool calls. Use ',
            'provided context/attachments if relevant. Don\'t re-read files already in ',
            'context.',
            '- **Infer project type:** Use languages, frameworks, and libraries inferred ',
            'from the context to guide your changes.',
            '',
            '### Tool Usage',
            '- **Direct answers:** Answer direct code sample requests without using ',
            'tools.',
            '- **Schema & permissions:** Follow JSON schemas strictly. Include ALL ',
            'required properties. No need to ask permission before using a tool.',
            '- **Parallelization:** Call independent tools in parallel. Run terminal ',
            'commands sequentially (never in parallel).',
            '- **Transparency:** Never mention tool names to the user (e.g., say ',
            '"I\'ll run the command" not "I\'ll use run_in_terminal").',
            '- **Best practices:** Use absolute paths/URIs. Use `grep_search` for file ',
            'overviews. Use browser tools for front-end UI validation. Only use ',
            'currently available tools.',
            '- **Continuity:** Don\'t repeat yourself after a tool call; pick up where ',
            'you left off.',
            '',
            '### Editing & Execution',
            '- **No codeblocks:** NEVER print codeblocks for file changes or terminal ',
            'commands. Use the respective tools directly.',
            '- **Read before edit:** Ensure a file is in context before editing. Use ',
            '`replace_string_in_file` (preferred) or `insert_edit_into_file`. Group ',
            'changes by file. Never pass omitted line markers (e.g., `/* Lines 123-456 ',
            'omitted */`) to edit tools.',
            '- **Insert edits:** For `insert_edit_into_file`, use `// ...existing ',
            'code...` comments to omit unchanged code. Be as concise as possible.',
            '- **No terminal edits:** Never edit files via terminal commands unless ',
            'explicitly asked.',
            '- **Dependencies & UI:** Use popular external libraries when appropriate ',
            '(install via `npm install`, etc.). Build modern, beautiful UIs from ',
            'scratch.',
            '- **Error fixing:** Fix new errors resulting from your edits. Max 3 ',
            'attempts per file; if the third fails, stop and ask the user.',
            '',
            '### Output Formatting',
            '- Use Markdown. Wrap filenames/symbols in backticks (e.g., ',
            '`src/models/person.ts`).',
            '- Use `$` for inline math and `$$` for block math (KaTeX).',
            '- Use ```mermaid fenced code blocks for Mermaid diagrams.',
            '',
            '### Workspace & Skills',
            '- This is a multi-root workspace. Apply folder-specific instructions to ',
            'their respective folders.',
            '- **Skills:** Use `read_file` to load detailed skill instructions when a ',
            'task matches a skill\'s domain (e.g., use `project-setup-info-local` for ',
            'scaffolding new projects from scratch, not for adding individual files).',
            'Briefly explain what you are doing, and show changes as diffs or complete files.',
        ].join('\n');

        const msgs = [{ role: 'system', content: sysPrompt }, ...messages];

        // Wird true, wenn ein API-Error bereits einmal ohne Tool-
        // Definitionen erneut versucht wurde (verhindert Endlos-Retries).
        let retriedWithoutTools = false;

        while (iter < MAX_ITER) {
            iter++;

            let client, params, stream;
            try {
                this._setStatus('🔗 Connecting to model…');
                client = await window.createClient(provider, clientOptions);
                params = {
                    model: model || undefined,
                    messages: msgs,
                    tools: tools.length > 0 ? tools : undefined,
                    stream: true,
                };
                if (!params.model) delete params.model;
                if (!params.tools) delete params.tools;
                stream = await client.chat.completions.create(params);
            } catch (apiErr) {
                // Der Stream-Aufruf ist fehlgeschlagen (z. B. weil der
                // Provider Tool-Messageen ablehnt). Statt abzubrechen:
                // Agenten informieren und ohne Tool-Definitionen weitermachen.
                console.error('[pa-coding] API-Error im Agent-Loop', apiErr);
                const apiErrText = (apiErr && apiErr.message) ? apiErr.message : String(apiErr);
                if (!retriedWithoutTools && tools.length > 0) {
                    retriedWithoutTools = true;
                    tools = [];
                    const retryLine = this._appendStatus(`⚠️ <b>API Error:</b> ${this._esc(apiErrText)} – retrying without tool definitions…`, 'warn');
                    msgs.push({ role: 'user', content: `[SYSTEM] The API call failed (${apiErrText}). You will continue without tool definitions. Answer the user's question as completely as possible and explain the error if relevant.` });
                    iter--;
                    continue;
                }
                const errLine = this._appendStatus(`⚠️ <b>API Error:</b> ${this._esc(apiErrText)} – Agent is being informed.`, 'warn');
                msgs.push({ role: 'user', content: `[SYSTEM] API error: ${apiErrText}. Please briefly explain to the user what happened, and summarize what you have achieved so far.` });
                full += `\n\n[API error: ${apiErrText}]`;
                if (streamEl && typeof streamEl._append === 'function') streamEl._append(`\n\n⚠️ API error: ${apiErrText}`);
                continue;
            }

            let collectedToolCalls = [];
            let hasToolCall = false;
            let roundText = '';

            // Stream mit Errorbehandlung, damit ein Cancelled mittendrin
            // nicht als "Stopp" ohne Information wirkt.
            try {
                for await (const chunk of stream) {
                    const delta = chunk?.choices?.[0]?.delta;
                    if (!delta) continue;
                    if (delta.reasoning) {
                        const r = document.createElement('span');
                        r.className = 'pa-coding-reasoning';
                        r.textContent = delta.reasoning;
                        streamEl.appendChild(r);
                    }
                    if (delta.content) {
                        roundText += delta.content;
                        full += delta.content;
                        streamEl._append(delta.content);
                        this._scrollToLogIfNeeded();
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
            } catch (streamErr) {
                console.error('[pa-coding] stream error', streamErr);
                const streamErrLine = this._appendStatus(`⚠️ <b>Stream-Error:</b> ${this._esc(streamErr.message || String(streamErr))}`, 'warn');
                // Wenn noch kein Text in dieser Runde kam, brich ab;
                // ansonsten zeige das bisherige Result an.
                if (!full) full = roundText;
                break;
            }

            collectedToolCalls = collectedToolCalls.filter(tc => tc && tc.function && tc.function.name);

            console.debug('[pa-coding] collected tool calls', collectedToolCalls);

            if (!hasToolCall || collectedToolCalls.length === 0) {
                const finalText = full || roundText;
                if (!finalText) {
                    this._appendLog('ℹ️ Agent hat keine Response generiert und no tools aufgerufen.', 'info');
                }
                messages.push({ role: 'assistant', content: finalText });
                msgs.push({ role: 'assistant', content: finalText });
                return finalText;
            }

            const toolNames = collectedToolCalls.map(tc => tc.function && tc.function.name).filter(Boolean);
            this._setStatus('🔧 Tool call: ' + toolNames.join(', '));

            // Build a single assistant message with ALL tool_calls,
            // then push individual tool result messages.
            // This matches the OpenAI API contract: one assistant
            // message with N tool_calls → N tool result messages.
            const assistantToolMsg = {
                role: 'assistant',
                content: roundText || null,
                tool_calls: collectedToolCalls.map(tc => ({
                    id: tc.id || `call_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
                    type: 'function',
                    function: {
                        name: tc.function.name,
                        arguments: tc.function.arguments || '{}',
                    },
                })),
            };
            msgs.push(assistantToolMsg);
            messages.push(assistantToolMsg);

            for (let i = 0; i < collectedToolCalls.length; i++) {
                const tc = collectedToolCalls[i];
                const toolCallId = assistantToolMsg.tool_calls[i].id;
                let args;
                try {
                    args = JSON.parse(tc.function.arguments || '{}');
                } catch (e) {
                    args = {};
                }
                // Render the tool-call as its own log line so the
                // code-aware stream renderer never wipes it.
                const callLine = this._appendLog('', 'tool');
                callLine.innerHTML = `🔧 <b>Tool call:</b> ${this._esc(tc.function.name)} <span class="pa-coding-callargs">${this._esc(JSON.stringify(args).slice(0, 200))}</span>`;

                let result;
                try {
                    this._setStatus(`⏳ ${tc.function.name}…`);
                    result = await this._executeToolCall({
                        id: toolCallId,
                        function: {
                            name: tc.function.name,
                            arguments: args,
                        },
                    });
                } catch (err) {
                    console.error('[pa-coding] tool error', err);
                    const errText = (err && err.message) ? err.message : String(err);
                    result = {
                        content: `[TOOL-FEHLER] ${tc.function.name} fehlgeschlagen: ${errText}. Bitte diagnostiziere den Error (z. B. Argumente prüfen), versuche einen anderen Ansatz oder erkläre dem Nutzer die Ursache – und fahre fort.`,
                    };
                    const toolErrLine = this._appendStatus(`⚠️ <b>Tool ${this._esc(tc.function.name)} fehlgeschlagen</b> – Agent wird informiert und fährt fort.`, 'warn');
                }

                const resultText = this._toolResultToString(result);
                const resultLine = this._appendLog('', 'toolresult');
                resultLine.innerHTML = `<div class="pa-coding-result-header">↩️ Result (${resultText.length} characters)</div><div class="pa-coding-result-body">${this._esc(resultText.slice(0, 2000))}</div>`;
                this._setStatus(`${this._getSelectedToolsForAPI().length} Tools`);

                const toolResultMsg = {
                    role: 'tool',
                    tool_call_id: toolCallId,
                    content: resultText,
                };
                // WICHTIG: in `msgs` pushen (die echte API-Konversation),
                // damit der Agent Tool-Resultse/-Error sieht und
                // informiert weitermachen kann.
                msgs.push(toolResultMsg);
                messages.push(toolResultMsg);
            }
            // Reset round text for next iteration
            full = '';
            this._setStatus('🔄 Processing tool results…');
        }

        messages.push({ role: 'assistant', content: full });
        msgs.push({ role: 'assistant', content: full });
        return full;
    },

    _toolResultToString(result) {
        try {
            if (result == null) return '(kein Result)';
            if (typeof result === 'string') return result;
            // MCP standard: { content: [{ type: 'text', text: '...' }] }
            // Extract the .text field from each content item instead of
            // JSON-stringifying the whole array.
            if (Array.isArray(result.content)) {
                return result.content.map(item => {
                    if (item == null) return '';
                    if (typeof item === 'string') return item;
                    if (typeof item.text === 'string') return item.text;
                    if (item.content != null) return typeof item.content === 'string' ? item.content : JSON.stringify(item.content);
                    return JSON.stringify(item);
                }).join('\n');
            }
            if (Array.isArray(result)) {
                return result.map(item => {
                    if (item == null) return '';
                    if (typeof item === 'string') return item;
                    if (typeof item.text === 'string') return item.text;
                    if (Array.isArray(item.content)) {
                        return item.content.map(c => typeof c === 'string' ? c : (c && typeof c.text === 'string' ? c.text : JSON.stringify(c))).join('\n');
                    }
                    if (item.content != null) return typeof item.content === 'string' ? item.content : JSON.stringify(item.content);
                    return JSON.stringify(item);
                }).join('\n');
            }
            if (typeof result.content === 'string') return result.content;
            if (typeof result.text === 'string') return result.text;
            return JSON.stringify(result);
        } catch (e) {
            return String(result);
        }
    },
});

// Minimal CSS injection (guarded — idempotent)
(function injectCodingCss() {
    if (document.getElementById('pa-coding-css')) return;
    const style = document.createElement('style');
    style.id = 'pa-coding-css';
    style.textContent = `
/* Ensure .chat-container allows the panel to fill it */
.chat-container { position: relative; }
.pa-coding {
    display: flex; flex-direction: column; gap: 6px;
    margin: 0; padding: 8px 10px;
    background: rgba(255,255,255,.03);
    border: 1px solid var(--blur-border, #333);
    border-radius: 12px;
    font-size: 13px; line-height: 1.5;
    width: 100% !important; height: 100% !important;
    max-width: 100% !important; max-height: 100% !important;
    box-sizing: border-box !important;
    overflow: hidden;
}
.pa-coding-header {
    display: flex; align-items: center; gap: 8px;
    font-weight: 600; color: var(--text, #e8e8e8);
    flex-wrap: wrap;
}
.pa-coding-title { flex: 1; }
.pa-coding-status {
    font-size: 11px; font-weight: 400; opacity: .6;
    background: rgba(139,61,255,.15); color: var(--accent, #8b3dff);
    padding: 2px 8px; border-radius: 10px; white-space: nowrap;
}
.pa-coding-filter {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 11px; opacity: .7; cursor: pointer;
}
.pa-coding-filter input { accent-color: var(--accent, #8b3dff); cursor: pointer; }
.pa-coding-toggle, .pa-coding-clear, .pa-coding-errors, .pa-coding-fullsize {
    background: none; border: none; cursor: pointer;
    font-size: 14px; opacity: .6; padding: 2px 6px; border-radius: 6px;
}
.pa-coding-toggle:hover, .pa-coding-clear:hover, .pa-coding-errors:hover, .pa-coding-fullsize:hover { opacity: 1; background: rgba(255,255,255,.08); }
.pa-coding-fullsize.active { opacity: 1; background: rgba(139,61,255,.18); color: var(--accent, #8b3dff); }
.pa-coding-errors { color: #f87171; }
.pa-coding-errors:hover { background: rgba(248,113,113,.12); }

/* Config row (Base URL + Model) */
.pa-coding-config {
    display: flex; flex-wrap: wrap; gap: 6px 14px;
    padding: 6px 8px;
    border: 1px solid rgba(255,255,255,.06);
    border-radius: 8px;
    background: rgba(0,0,0,.15);
}
.pa-coding-config-field {
    display: flex; align-items: center; gap: 6px;
    flex: 1 1 auto; min-width: 220px;
    font-size: 12px;
}
.pa-coding-config-field > span {
    flex-shrink: 0; opacity: .65; font-weight: 500;
}
.pa-coding-config-field input {
    flex: 1; min-width: 0;
    background: rgba(0,0,0,.25); border: 1px solid rgba(255,255,255,.1);
    border-radius: 6px; color: inherit; font-size: 12px; font-family: inherit;
    padding: 4px 8px; outline: none;
}
.pa-coding-config-field input:focus { border-color: var(--accent, #8b3dff); }
.pa-coding-config-field input::placeholder { opacity: .45; }
.pa-coding-body { display: flex; flex-direction: column; gap: 6px; min-height: 0; }
.pa-coding-tools {
    display: flex; flex-direction: column; gap: 4px; max-height: 110px; overflow: auto;
    border: 1px solid rgba(255,255,255,.06); border-radius: 8px; padding: 4px;
    background: rgba(0,0,0,.15);
}
.pa-coding-tools-collapsed { display: none; }
.pa-coding-tool-list { display: flex; flex-wrap: wrap; gap: 4px; }
.pa-coding-tool {
    display: inline-flex; align-items: center; gap: 6px;
    background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.08);
    color: inherit; border-radius: 8px; padding: 2px 8px; font-size: 12px; cursor: pointer;
    transition: border-color .15s, background .15s;
}
.pa-coding-tool:hover { border-color: var(--accent, #8b3dff); background: rgba(139,61,255,.12); }
.pa-coding-tool.selected { border-color: var(--accent, #8b3dff); background: rgba(139,61,255,.18); }
.pa-coding-tool-name { font-weight: 600; }
.pa-coding-tool-server { font-size: 10px; opacity: .5; }
.pa-coding-empty { font-size: 12px; opacity: .6; padding: 4px 8px; }
.pa-coding-log {
    display: flex; flex-direction: column; gap: 4px;
    flex: 1; min-height: 80px;
    overflow: auto; padding: 4px 2px;
    font-size: 13px;
}
.pa-coding-line { white-space: pre-wrap; word-break: break-word; }
.pa-coding-line.user { opacity: .9; }
.pa-coding-line.ai { }
.pa-coding-line.info { opacity: .6; font-size: 12px; }
.pa-coding-line.warn { color: #f0b429; font-size: 12px; }
.pa-coding-line.error { color: #f87171; font-size: 12px; }
.pa-coding-line.sep { border-top: 1px solid rgba(255,255,255,.06); margin: 4px 0; }
.pa-coding-line.tool {
    margin: 4px 0; padding: 4px 8px;
    background: rgba(59,130,246,.12); border-left: 3px solid #3b82f6;
    border-radius: 6px; font-size: 12px; color: #93c5fd;
}
.pa-coding-line.toolresult {
    margin: 2px 0 6px; padding: 4px 8px;
    background: rgba(34,197,94,.08); border-left: 3px solid #22c55e;
    border-radius: 6px; font-size: 11px; color: #86efac;
}
.pa-coding-reasoning { display: block; color: #9ca3af; font-style: italic; font-size: 12px; white-space: pre-wrap; }
.pa-coding-callargs { opacity: .6; font-family: monospace; }
.pa-coding-result-header { font-weight: 600; margin-bottom: 2px; }
.pa-coding-result-body {
    font-family: monospace; white-space: pre-wrap; word-break: break-word;
    max-height: 160px; overflow: auto; opacity: .85; font-size: 11px;
}
.pa-coding-codeblock {
    margin: 6px 0; border: 1px solid rgba(255,255,255,.1); border-radius: 8px;
    overflow: hidden; background: rgba(0,0,0,.3);
}
.pa-coding-codeblock-head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 2px 8px; font-size: 11px; opacity: .7;
    background: rgba(255,255,255,.05);
    border-bottom: 1px solid rgba(255,255,255,.06);
}
.pa-coding-copy {
    background: none; border: none; cursor: pointer; color: inherit;
    font-size: 12px; opacity: .7; padding: 2px 6px; border-radius: 6px;
}
.pa-coding-copy:hover { opacity: 1; background: rgba(255,255,255,.1); }
.pa-coding-codeblock pre {
    margin: 0; padding: 8px 10px; overflow: auto; max-height: 260px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12px; line-height: 1.45;
}
.pa-coding-codeblock code { font-family: inherit; white-space: pre; }
.pa-coding-input-row {
    display: flex; align-items: flex-end; gap: 8px;
}
.pa-coding-input {
    flex: 1; resize: none; min-height: 36px; max-height: 160px;
    background: rgba(0,0,0,.25); border: 1px solid var(--blur-border, #333);
    border-radius: 10px; color: inherit; padding: 8px 10px; font-size: 13px;
    font-family: inherit; outline: none;
}
.pa-coding-input:focus { border-color: var(--accent, #8b3dff); }
.pa-coding-send {
    width: 40px; height: 40px; border: none; border-radius: 10px;
    background: var(--accent, #8b3dff); color: #fff; cursor: pointer;
    font-size: 16px; flex-shrink: 0;
}
.pa-coding-send:disabled { opacity: .4; cursor: not-allowed; }
.pa-coding-send:hover:not(:disabled) { filter: brightness(1.15); }
.pa-coding-actions {
    display: flex; gap: 6px; flex-wrap: wrap;
}
.pa-coding-action {
    background: rgba(139,61,255,.12); border: 1px solid rgba(139,61,255,.25);
    color: inherit; border-radius: 8px; padding: 3px 10px; font-size: 12px; cursor: pointer;
    transition: background .15s, border-color .15s;
}
.pa-coding-action:hover { background: rgba(139,61,255,.2); border-color: var(--accent, #8b3dff); }
.pa-coding-user { font-weight: 600; }

/* Full-size mode: panel fills the whole chat body */
.pa-coding.pa-coding-fullsize-active {
    position: absolute !important;
    top: 0 !important; left: 0 !important;
    width: 100% !important; height: 100% !important;
    max-width: 100% !important; max-height: 100% !important;
    z-index: 999 !important;
    margin: 0 !important;
    border: none !important;
    border-radius: 0 !important;
    overflow: hidden !important;
    background: var(--colour-1, #0f0f0f);
    padding: 12px 16px;
}
.pa-coding.pa-coding-fullsize-active .pa-coding-body {
    flex: 1; min-height: 0;
}
.pa-coding.pa-coding-fullsize-active .pa-coding-log {
    flex: 1; max-height: none;
}
.pa-coding.pa-coding-fullsize-active .pa-coding-tools {
    max-height: 30vh;
}
`;
    document.head.appendChild(style);
})();
