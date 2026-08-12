/* ================================================================== *
 * Addon: Chat Full-Size Toggle
 *
 * Adds a button to toggle the `.chat-body` element between its normal
 * size and full viewport size. When full-size is active, the chat body
 * expands to cover the entire viewport with a high z-index, and a
 * floating close button appears.
 *
 * Also exposes a virtual MCP tool so the agent can toggle full-size
 * mode programmatically.
 * ================================================================== */

ChatAddons.register({
    id: 'workspace:chat-fullsize-toggle',
    name: 'Chat Full-Size Toggle',
    version: '1.0.0',
    description: 'Toggle .chat-body to full viewport size for more space.',
    author: 'g4f',
    builtin: false,
    permissions: ['dom:read', 'dom:write', 'dom:query', 'ui:notify'],

    STORAGE_KEY: 'chat.addons.data.pa-chat-fullsize.state',
    _isFullSize: false,
    _origStyles: null,

    // ----------------------------------------------------------------
    // Lifecycle
    // ----------------------------------------------------------------
    load() {
        return (async () => {
            this._injectCSS();
            this._injectButton();
            this._registerVirtualMCP();

            // Restore saved state
            const saved = this._loadState();
            if (saved?.isFullSize) {
                this.toggleFullSize(true);
            }
        })();
    },

    unload() {
        this.toggleFullSize(false);
        const btn = document.getElementById('pa-chat-fullsize-btn');
        if (btn) btn.remove();
        const css = document.getElementById('pa-chat-fullsize-css');
        if (css) css.remove();
        this._unregisterVirtualMCP();
    },

    // ----------------------------------------------------------------
    // CSS
    // ----------------------------------------------------------------
    _injectCSS() {
        if (document.getElementById('pa-chat-fullsize-css')) return;
        const style = document.createElement('style');
        style.id = 'pa-chat-fullsize-css';
        style.textContent = `
.pa-chat-fullsize-btn {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 4px 10px; border: 1px solid var(--blur-border, #333);
    border-radius: 8px; background: rgba(255,255,255,.05);
    color: var(--text, #e8e8e8); cursor: pointer; font-size: 13px;
    transition: background .15s, border-color .15s;
}
.pa-chat-fullsize-btn:hover {
    background: rgba(139,61,255,.15);
    border-color: var(--accent, #8b3dff);
}
.pa-chat-fullsize-btn.active {
    background: rgba(139,61,255,.25);
    border-color: var(--accent, #8b3dff);
    color: var(--accent, #8b3dff);
}

/* Full-size mode — applied to .chat-body */
.chat-body.pa-chat-fullsize {
    position: fixed !important;
    top: 0 !important; left: 0 !important;
    width: 100vw !important; height: 100vh !important;
    max-width: 100vw !important; max-height: 100vh !important;
    z-index: 999999 !important;
    border-radius: 0 !important;
    margin: 0 !important;
    box-sizing: border-box !important;
    overflow: auto !important;
    background: var(--colour-1);
}
`;
        document.head.appendChild(style);
    },

    // ----------------------------------------------------------------
    // Button injection
    // ----------------------------------------------------------------
    _injectButton() {
        const tryInject = () => {
            // Try to find a suitable toolbar/header area near the chat
            let anchor = document.querySelector('.chat-header, .chat-toolbar, .chat-input-container, .input-area');
            if (!anchor) {
                // Fallback: create a floating button
                if (document.getElementById('pa-chat-fullsize-btn')) return;
                const btn = document.createElement('button');
                btn.id = 'pa-chat-fullsize-btn';
                btn.className = 'pa-chat-fullsize-btn';
                btn.innerHTML = '⛶';
                btn.title = 'Chat full-size toggle';
                btn.style.cssText = 'position:fixed; top:8px; right:8px; z-index:999998;';
                btn.addEventListener('click', () => this.toggleFullSize());
                document.body.appendChild(btn);
                return;
            }
            if (anchor.querySelector('#pa-chat-fullsize-btn')) return;

            const btn = document.createElement('button');
            btn.id = 'pa-chat-fullsize-btn';
            btn.className = 'pa-chat-fullsize-btn';
            btn.innerHTML = '⛶ Full-size';
            btn.title = 'Toggle chat body full viewport size';
            btn.addEventListener('click', () => this.toggleFullSize());
            anchor.appendChild(btn);
        };

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', tryInject);
        } else {
            tryInject();
            // Retry in case the chat UI loads dynamically
            setTimeout(tryInject, 1000);
            setTimeout(tryInject, 3000);
        }
    },

    // ----------------------------------------------------------------
    // Toggle logic
    // ----------------------------------------------------------------
    toggleFullSize(force) {
        const chatBody = document.querySelector('.chat-body');
        if (!chatBody) {
            ChatAddonHost.notify('.chat-body nicht gefunden', 'error', 3000);
            return false;
        }

        const shouldFull = typeof force === 'boolean' ? force : !this._isFullSize;

        if (shouldFull) {
            // Save original styles
            this._origStyles = {
                position: chatBody.style.position,
                top: chatBody.style.top,
                left: chatBody.style.left,
                width: chatBody.style.width,
                height: chatBody.style.height,
                maxWidth: chatBody.style.maxWidth,
                maxHeight: chatBody.style.maxHeight,
                zIndex: chatBody.style.zIndex,
                borderRadius: chatBody.style.borderRadius,
                margin: chatBody.style.margin,
                overflow: chatBody.style.overflow,
            };
            chatBody.classList.add('pa-chat-fullsize');
            this._isFullSize = true;
            this._updateButton(true);
            this._addExitButton();
        } else {
            chatBody.classList.remove('pa-chat-fullsize');
            this._isFullSize = false;
            this._updateButton(false);
            this._removeExitButton();
        }

        this._saveState({ isFullSize: this._isFullSize });
        return this._isFullSize;
    },

    _updateButton(active) {
        const btn = document.getElementById('pa-chat-fullsize-btn');
        if (!btn) return;
        if (active) {
            btn.classList.add('active');
            btn.innerHTML = '⛶ Exit full-size';
        } else {
            btn.classList.remove('active');
            btn.innerHTML = '⛶ Full-size';
        }
    },

    _addExitButton() {
        if (document.getElementById('pa-chat-fullsize-exit')) return;
        const exit = document.createElement('button');
        exit.id = 'pa-chat-fullsize-exit';
        exit.innerHTML = '✕ Exit full-size';
        exit.style.cssText = `
            position: fixed; bottom: 16px; right: 16px; z-index: 1000000;
            padding: 8px 16px; border: 1px solid var(--accent, #8b3dff);
            border-radius: 10px; background: var(--accent, #8b3dff); color: #fff;
            cursor: pointer; font-size: 14px; font-weight: 600;
            box-shadow: 0 4px 12px rgba(0,0,0,.4);
        `;
        exit.addEventListener('click', () => this.toggleFullSize(false));
        document.body.appendChild(exit);
    },

    _removeExitButton() {
        const exit = document.getElementById('pa-chat-fullsize-exit');
        if (exit) exit.remove();
    },

    // ----------------------------------------------------------------
    // Storage
    // ----------------------------------------------------------------
    _saveState(state) {
        try { localStorage.setItem(this.STORAGE_KEY, JSON.stringify(state)); } catch (e) { /* ignore */ }
    },

    _loadState() {
        try { return JSON.parse(localStorage.getItem(this.STORAGE_KEY) || '{}'); } catch (e) { return {}; }
    },

    // ----------------------------------------------------------------
    // Virtual MCP tool registration
    // ----------------------------------------------------------------
    _registerVirtualMCP() {
        const self = this;
        const server = {
            name: 'browser-ui',
            tools: [
                {
                    name: 'browser.toggle_fullsize',
                    description: 'Toggle the chat body (.chat-body) between normal and full viewport size. Pass enabled=true to force full-size, enabled=false to force normal, or omit to toggle.',
                    inputSchema: {
                        type: 'object',
                        properties: {
                            enabled: { type: 'boolean', description: 'true = full-size, false = normal, omit = toggle' },
                        },
                    },
                    async handler(args) {
                        const force = args && typeof args.enabled === 'boolean' ? args.enabled : undefined;
                        const isFull = self.toggleFullSize(force);
                        return { content: [{ type: 'text', text: `Chat body is now ${isFull ? 'full-size' : 'normal size'}.` }] };
                    },
                },
            ],
        };

        this._virtualServer = server;

        const mcp = this._getMCP();
        if (mcp && typeof mcp.registerVirtualServer === 'function') {
            mcp.registerVirtualServer(server);
        } else {
            if (!window._paVirtualServers) window._paVirtualServers = [];
            window._paVirtualServers.push(server);
        }
    },

    _unregisterVirtualMCP() {
        if (window._paVirtualServers && this._virtualServer) {
            window._paVirtualServers = window._paVirtualServers.filter(s => s !== this._virtualServer);
        }
    },

    _getMCP() {
        try {
            if (typeof mcpClient !== 'undefined' && mcpClient) return mcpClient;
            if (typeof window !== 'undefined' && window.mcpClient) return window.mcpClient;
        } catch (e) { /* ignore */ }
        return null;
    },
});